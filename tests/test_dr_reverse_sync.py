"""Reverse sync: what may be sent, and which new host key may be trusted.

A rebuilt destination has a new SSH host key, so the sending host's stale
known_hosts entry must be replaced before the transfer (else strict checking
aborts). Since the review of 18.10. (finding G) the new key is no longer taken
on trust: the user confirms a fingerprint first, and only a key with exactly
that fingerprint is written. And the replica must be one this host keeps --
its root one of the host's own zsync targets.
"""

import pytest

from app import dr
import app.main as m

# A real key; its fingerprint is what `ssh-keygen -lf` prints for it.
KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIO6QhstceG7SNB92ae/bn2uqUkZmcvzCqT8qu3dmZCQR"
FP = "SHA256:UvWgyqUVV+7LxYo3b693l0s3E0hHq7Ag6Rzrq5wbUwA"
OTHER_KEY = "AAAAC3NzaC1lZDI1NTE5AAAAIBUSbzVwOR0F5jsHbVCAaeAgJr6uO5O6iCoTQ5eSbo/S"
SCAN = f"# 192.168.1.251:22 SSH-2.0-OpenSSH_10.0\n192.168.1.251 ssh-ed25519 {KEY}\n"


def _run_job(monkeypatch, run_impl, **kwargs):
    """Invoke reverse_sync_async but run its job synchronously, returning the
    job result. ``run_impl(cmd)`` supplies each run_command's output."""
    cmds = []

    def fake_run(host, cmd, timeout=None):
        cmds.append(cmd)
        return run_impl(cmd)

    result = {}

    def fake_start_task(name, fn, *args, prefix=None):
        result["value"] = fn(lambda *a, **k: None, *args)
        return "task-1"

    monkeypatch.setattr(dr, "run_command", fake_run)
    monkeypatch.setattr(dr, "start_task", fake_start_task)

    base = dict(
        target_host={"address": "10.0.0.9"},
        replica_dataset="rpool/data/vm-100-disk-0",
        replica_root="rpool/data",
        source_address="192.168.1.251",
        source_port=22,
        source_user="root",
        source_dataset="tank/vm-100-disk-0",
        snapshot="zsync_2026",
        known_roots=["rpool/data"],
    )
    base.update(kwargs)
    dr.reverse_sync_async(**base)
    return result["value"], cmds


def _send_ok(cmd):
    if "zfs send" in cmd:
        return {"success": True, "stdout": "receiving full stream ...\n__exit=0"}
    if cmd.startswith("ssh-keyscan"):
        return {"success": True, "stdout": SCAN}
    if "known_hosts" in cmd:
        return {"success": True, "stdout": "__KEY_OK__\n"}
    return {"success": True, "stdout": ""}


# --- fingerprints ------------------------------------------------------------------

def test_the_fingerprint_matches_ssh_keygen():
    assert dr.host_key_fingerprint(KEY) == FP


def test_keyscan_output_is_parsed_and_comments_skipped():
    keys = dr.parse_keyscan(SCAN + "[10.0.0.1]:2222 ecdsa-sha2-nistp256 not!base64\n")
    assert keys == [{"host": "192.168.1.251", "type": "ssh-ed25519", "fingerprint": FP,
                     "line": f"192.168.1.251 ssh-ed25519 {KEY}"}]


def test_the_scan_writes_nothing(monkeypatch):
    cmds = []
    monkeypatch.setattr(dr, "run_command",
                        lambda h, c, **k: cmds.append(c) or {"success": True, "stdout": SCAN})
    res = dr.scan_host_key({"address": "10.0.0.9"}, "192.168.1.251", 22)
    assert res["keys"] == [{"type": "ssh-ed25519", "fingerprint": FP}]
    assert cmds == ["ssh-keyscan -T 10 -p 22 192.168.1.251 2>/dev/null"]


@pytest.mark.parametrize("addr,port", [("a;b", 22), ("", 22), ("h", -1), ("h", 70000), ("h", "x")])
def test_the_scan_refuses_odd_input(monkeypatch, addr, port):
    monkeypatch.setattr(dr, "run_command", lambda *a, **k: pytest.fail("must not run"))
    assert dr.scan_host_key({"address": "x"}, addr, port)["success"] is False


# --- the confirmed key, and only that one -------------------------------------------------

def test_the_confirmed_key_replaces_the_stale_entry(monkeypatch):
    res, cmds = _run_job(monkeypatch, _send_ok, refresh_host_key=True, accepted_fingerprint=FP)
    assert res["success"] is True
    write = [c for c in cmds if "known_hosts" in c]
    assert len(write) == 1
    w = write[0]
    assert "ssh-keygen -f ~/.ssh/known_hosts -R 192.168.1.251" in w
    assert "ssh-keygen -f ~/.ssh/known_hosts -R '[192.168.1.251]:22'" in w
    assert "ssh-keyscan" not in w and "tee -a" not in w          # no blind append any more
    import base64
    b64 = w.split("echo ", 1)[1].split(" ", 1)[0].strip("'")
    assert base64.b64decode(b64).decode() == f"192.168.1.251 ssh-ed25519 {KEY}\n"


def test_a_different_key_aborts_before_anything_is_written_or_sent(monkeypatch):
    def run(cmd):
        if cmd.startswith("ssh-keyscan"):
            return {"success": True, "stdout": f"192.168.1.251 ssh-ed25519 {OTHER_KEY}\n"}
        return _send_ok(cmd)
    res, cmds = _run_job(monkeypatch, run, refresh_host_key=True, accepted_fingerprint=FP)
    assert res["success"] is False and res["host_key_mismatch"] is True
    assert res["accepted_fingerprint"] == FP
    assert res["scanned"][0]["fingerprint"] != FP
    assert not any("known_hosts" in c for c in cmds)
    assert not any("zfs send" in c for c in cmds)


def test_refresh_without_a_confirmed_fingerprint_is_refused(monkeypatch):
    with pytest.raises(ValueError, match="fingerprint"):
        _run_job(monkeypatch, _send_ok, refresh_host_key=True)


def test_a_malformed_fingerprint_is_refused(monkeypatch):
    with pytest.raises(ValueError, match="fingerprint"):
        _run_job(monkeypatch, _send_ok, refresh_host_key=True, accepted_fingerprint="SHA256:x; rm -rf /")


def test_no_refresh_when_disabled(monkeypatch):
    res, cmds = _run_job(monkeypatch, _send_ok, refresh_host_key=False)
    assert not any("ssh-keyscan" in c or "known_hosts" in c for c in cmds)
    assert res["success"] is True


def test_host_key_error_is_flagged(monkeypatch):
    def run(cmd):
        if "zfs send" in cmd:
            return {"success": True, "stdout":
                    ("@@@ WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED! @@@\n"
                     "Host key verification failed.\n__exit=255")}
        return {"success": True, "stdout": ""}
    # user left the box off; the failure should still be recognized
    res, _ = _run_job(monkeypatch, run, refresh_host_key=False)
    assert res["success"] is False
    assert res["exit_code"] == 255
    assert res["host_key_error"] is True


def test_clean_failure_not_flagged_as_hostkey(monkeypatch):
    def run(cmd):
        if "zfs send" in cmd:
            return {"success": True, "stdout":
                    "cannot receive: destination has snapshots\n__exit=1"}
        return _send_ok(cmd)
    res, _ = _run_job(monkeypatch, run, refresh_host_key=True, accepted_fingerprint=FP)
    assert res["success"] is False
    assert res["host_key_error"] is False


# --- only replicas this host keeps -------------------------------------------------------

@pytest.mark.parametrize("dataset,root,known,msg", [
    ("rpool/data/vm-100-disk-0", "rpool/data", [], "no replication target"),
    ("rpool/data/vm-100-disk-0", "rpool/data", ["tank/repl"], "not a replication target"),
    ("rpool/ROOT/pve-1", "tank/repl", ["tank/repl"], "not under the replica root"),
    ("tank/replica-evil", "tank/repl", ["tank/repl"], "not under the replica root"),
])
def test_a_dataset_outside_a_known_replica_root_is_refused(monkeypatch, dataset, root, known, msg):
    with pytest.raises(ValueError, match=msg):
        _run_job(monkeypatch, lambda c: pytest.fail("must not run"),
                 replica_dataset=dataset, replica_root=root, known_roots=known)


def test_the_roots_are_looked_up_on_the_host_when_not_given(monkeypatch):
    monkeypatch.setattr(dr, "_replica_roots_for_host",
                        lambda h: [{"target": "tank/repl", "source": "root@10.0.0.5"}])
    with pytest.raises(ValueError, match="not a replication target"):
        _run_job(monkeypatch, _send_ok, known_roots=None)
    res, _ = _run_job(monkeypatch, _send_ok, known_roots=None,
                      replica_root="tank/repl", replica_dataset="tank/repl/rpool/data/vm-100-disk-0")
    assert res["success"] is True


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(m, "_find_host", lambda addr: {"address": addr} if addr else None)
    m.app.config["TESTING"] = True
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["sid"] = m.session_open("admin")
        s["csrf_token"] = "tok"
    return c


def test_the_route_refuses_a_foreign_root_and_starts_nothing(client, monkeypatch):
    monkeypatch.setattr(dr, "_replica_roots_for_host", lambda h: [{"target": "tank/repl"}])
    monkeypatch.setattr(dr, "start_task", lambda *a, **k: pytest.fail("must not start"))
    r = client.post("/api/dr/reverse-sync?host=10.0.0.9", headers={"X-CSRF-Token": "tok"}, json={
        "replica_dataset": "rpool/ROOT/pve-1", "replica_root": "rpool/ROOT",
        "source_address": "203.0.113.5", "source_dataset": "rpool/ROOT/pve-1"})
    assert r.status_code == 400 and "not a replication target" in r.get_json()["error"]


def test_the_scan_route(client, monkeypatch):
    monkeypatch.setattr(dr, "run_command", lambda h, c, **k: {"success": True, "stdout": SCAN})
    r = client.post("/api/dr/scan-host-key?host=10.0.0.9", headers={"X-CSRF-Token": "tok"},
                    json={"address": "192.168.1.251", "port": 22})
    assert r.status_code == 200 and r.get_json()["keys"][0]["fingerprint"] == FP
