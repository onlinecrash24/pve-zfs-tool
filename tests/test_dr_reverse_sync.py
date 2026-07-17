"""Reverse-sync host-key refresh: a rebuilt destination has a new SSH host
key, so the sending host's stale known_hosts entry must be refreshed before
the transfer (else StrictHostKeyChecking aborts)."""

from app import dr


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
    )
    base.update(kwargs)
    dr.reverse_sync_async(**base)
    return result["value"], cmds


def _send_ok(cmd):
    if "zfs send" in cmd:
        return {"success": True, "stdout": "receiving full stream ...\n__exit=0"}
    if "ssh-keyscan" in cmd:
        return {"success": True, "stdout": "256 SHA256:abc123 192.168.1.251 (ED25519)\n"}
    return {"success": True, "stdout": ""}


def test_refresh_host_key_runs_keyscan_prep(monkeypatch):
    res, cmds = _run_job(monkeypatch, _send_ok, refresh_host_key=True)
    prep = [c for c in cmds if "ssh-keyscan" in c]
    assert prep, "expected a host-key refresh command"
    p = prep[0]
    # stale entry dropped for both bare address and [addr]:port, then re-scanned
    assert "ssh-keygen -f ~/.ssh/known_hosts -R" in p
    assert "192.168.1.251" in p
    assert "[192.168.1.251]:22" in p
    assert res["success"] is True


def test_no_refresh_when_disabled(monkeypatch):
    res, cmds = _run_job(monkeypatch, _send_ok, refresh_host_key=False)
    assert not any("ssh-keyscan" in c for c in cmds)
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
        return {"success": True, "stdout": ""}
    res, _ = _run_job(monkeypatch, run, refresh_host_key=True)
    assert res["success"] is False
    assert res["host_key_error"] is False
