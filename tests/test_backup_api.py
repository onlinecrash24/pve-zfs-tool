"""The backup endpoints as the UI sees them.

test_backups covers the decisions; this covers the wiring: that a guest with no
backup reaches the client as red, that an unreadable storage reaches it as
unknown rather than red, and that the inventory matrix carries the backup field
only for the selected source host.
"""

import json

import pytest

from app import backups as b
from app import main as m

NOW_VOL = 1_800_000_000


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    monkeypatch.setattr(m, "_find_host",
                        lambda addr: {"address": addr} if addr else None)
    # The matrix also reads declared exceptions, which is its own SSH call in
    # its own module. Left unstubbed it attempts a real connection and the test
    # waits out the TCP timeout.
    monkeypatch.setattr("app.guest_intent.collect_exceptions",
                        lambda host, cache_ttl=300: {"exceptions": {},
                                                     "readable": True, "error": ""})
    m.app.config["TESTING"] = True
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["csrf_token"] = "tok"
    return c


def _route(monkeypatch, responses):
    def run(host, command, timeout=30, cache_ttl=0):
        for needle, result in responses.items():
            if needle in command:
                return result
        return {"success": False, "stdout": "", "stderr": "unexpected: " + command}
    monkeypatch.setattr(b, "run_command", run)


def _guests(monkeypatch, vms=(), cts=()):
    monkeypatch.setattr("app.zfs_commands.get_pve_vms", lambda h: list(vms))
    monkeypatch.setattr("app.zfs_commands.get_pve_cts", lambda h: list(cts))


def _pbs_content(vmid, ctime, verify="ok"):
    vol = {"volid": f"pbs-main:backup/vm/{vmid}/x", "vmid": vmid, "ctime": ctime,
           "size": 1, "format": "pbs-vm", "subtype": "qemu"}
    if verify:
        vol["verification"] = {"state": verify}
    return json.dumps([vol])


STORAGES_OK = json.dumps([{"storage": "pbs-main", "type": "pbs",
                           "enabled": 1, "active": 1}])
JOBS_ALL = "vzdump: j1\n\tstorage pbs-main\n\tall 1\n\tenabled 1\n"


def test_guest_backups_reports_each_guest(client, monkeypatch):
    import time
    now = int(time.time())
    _guests(monkeypatch, vms=[{"vmid": "100"}, {"vmid": "101"}])
    _route(monkeypatch, {
        "hostname": {"success": True, "stdout": "pve251\n", "stderr": ""},
        "/storage --content backup": {"success": True, "stdout": STORAGES_OK, "stderr": ""},
        "/storage/pbs-main/content": {"success": True, "stderr": "",
                                      "stdout": _pbs_content("100", now - 3600)},
        "jobs.cfg": {"success": True, "stdout": JOBS_ALL, "stderr": ""},
    })
    r = client.get("/api/pve/guest-backups?host=10.0.0.1")
    assert r.status_code == 200
    body = r.get_json()
    assert body["states"]["100"]["state"] == "green"
    # 101 has a job covering it but no backup on the storage -- that is the gap
    # the page exists to show.
    assert body["states"]["101"]["state"] == "red"
    assert body["states"]["101"]["reason"] == "none"
    assert body["node"] == "pve251"


def test_a_dead_pbs_yields_unknown_not_red(client, monkeypatch):
    _guests(monkeypatch, vms=[{"vmid": "100"}])
    _route(monkeypatch, {
        "hostname": {"success": True, "stdout": "pve251\n", "stderr": ""},
        "/storage --content backup": {"success": False, "stdout": "",
                                      "stderr": "connection refused"},
        "jobs.cfg": {"success": True, "stdout": JOBS_ALL, "stderr": ""},
    })
    body = client.get("/api/pve/guest-backups?host=10.0.0.1").get_json()
    assert body["states"]["100"]["state"] == "unknown"
    assert body["readable"] is False
    assert "connection refused" in body["error"]


def test_an_inactive_storage_is_named_in_the_response(client, monkeypatch):
    _guests(monkeypatch, vms=[{"vmid": "100"}])
    _route(monkeypatch, {
        "hostname": {"success": True, "stdout": "pve251\n", "stderr": ""},
        "/storage --content backup": {"success": True, "stderr": "", "stdout": json.dumps(
            [{"storage": "nfs-bk", "type": "nfs", "enabled": 1, "active": 0}])},
        "jobs.cfg": {"success": True, "stdout": JOBS_ALL, "stderr": ""},
    })
    body = client.get("/api/pve/guest-backups?host=10.0.0.1").get_json()
    assert body["unreadable"] == [{"storage": "nfs-bk", "error": "inactive"}]
    assert body["states"]["100"]["state"] == "unknown"


def test_thresholds_travel_with_the_response(client, monkeypatch):
    _guests(monkeypatch, vms=[])
    _route(monkeypatch, {
        "hostname": {"success": True, "stdout": "pve251\n", "stderr": ""},
        "/storage --content backup": {"success": True, "stdout": "[]", "stderr": ""},
        "jobs.cfg": {"success": True, "stdout": "", "stderr": ""},
    })
    body = client.get("/api/pve/guest-backups?host=10.0.0.1").get_json()
    assert body["warn_hours"] == 36 and body["crit_hours"] == 168


def _matrix_via_task(client, host):
    """Start the collection and wait for the task, returning its result.

    The endpoint no longer answers with the matrix: on a real estate the
    collection outlasts gunicorn's request timeout, so it runs as a background
    task and the client polls /api/replication/task.
    """
    import time
    from app.tasks import get_task
    started = client.get("/api/inventory/matrix?host=" + host).get_json()
    assert started["task_id"], started
    for _ in range(200):                       # 10s ceiling; stubs finish instantly
        rec = get_task(started["task_id"])
        if rec and rec["status"] != "running":
            assert rec["status"] == "done", rec.get("error")
            return rec["result"]
        time.sleep(0.05)
    raise AssertionError("collection task did not finish")


def test_matrix_attaches_backups_for_the_selected_host_only(client, monkeypatch):
    import time
    now = int(time.time())
    matrix = {"guests": [
        {"vmid": "100", "source_host": "10.0.0.1", "copies": [], "copy_count": 0},
        {"vmid": "200", "source_host": "10.0.0.2", "copies": [], "copy_count": 0},
    ], "hosts": ["10.0.0.1", "10.0.0.2"]}
    monkeypatch.setattr("app.replication_inventory.collect_inventory",
                        lambda hosts, progress=None: dict(matrix))
    monkeypatch.setattr("app.replication_inventory.filter_matrix",
                        lambda mx, source_host=None: dict(mx))
    monkeypatch.setattr("app.replication_inventory.source_hosts", lambda mx: ["10.0.0.1"])
    monkeypatch.setattr(m, "load_hosts", lambda: [{"address": "10.0.0.1"}])
    _route(monkeypatch, {
        "hostname": {"success": True, "stdout": "pve251\n", "stderr": ""},
        "/storage --content backup": {"success": True, "stdout": STORAGES_OK, "stderr": ""},
        "/storage/pbs-main/content": {"success": True, "stderr": "",
                                      "stdout": _pbs_content("100", now - 3600)},
        "jobs.cfg": {"success": True, "stdout": JOBS_ALL, "stderr": ""},
    })
    body = _matrix_via_task(client, "10.0.0.1")
    guests = {g["vmid"]: g for g in body["guests"]}
    assert guests["100"]["backup"]["state"] == "green"
    assert "backup" not in guests["200"]        # other host, not read here
    assert body["backup_readable"] is True
    assert body["backup_unreadable"] == []


def test_matrix_still_renders_when_the_backup_read_explodes(client, monkeypatch):
    # The replication picture is worth showing even if backups cannot be read.
    matrix = {"guests": [{"vmid": "100", "source_host": "10.0.0.1",
                          "copies": [], "copy_count": 0}], "hosts": ["10.0.0.1"]}
    monkeypatch.setattr("app.replication_inventory.collect_inventory",
                        lambda hosts, progress=None: dict(matrix))
    monkeypatch.setattr("app.replication_inventory.filter_matrix",
                        lambda mx, source_host=None: dict(mx))
    monkeypatch.setattr("app.replication_inventory.source_hosts", lambda mx: [])
    monkeypatch.setattr(m, "load_hosts", lambda: [{"address": "10.0.0.1"}])

    def boom(host, vmids=None, cache_ttl=300):
        raise RuntimeError("ssh gone")
    monkeypatch.setattr("app.backups.host_backup_states", boom)

    body = _matrix_via_task(client, "10.0.0.1")
    assert body["guests"][0]["vmid"] == "100"
    assert body["backup_readable"] is False
    assert "ssh gone" in body["backup_error"]


def test_the_endpoint_answers_immediately_with_a_task_id(client, monkeypatch):
    # The point of the change: the HTTP request must return without waiting for
    # the collection, however long that takes. A slow collector here would have
    # blocked the old endpoint past gunicorn's 300s ceiling.
    import threading
    import time
    release = threading.Event()

    def slow_collect(hosts, progress=None):
        release.wait(10)
        return {"guests": [], "hosts": []}

    monkeypatch.setattr("app.replication_inventory.collect_inventory", slow_collect)
    monkeypatch.setattr("app.replication_inventory.filter_matrix",
                        lambda mx, source_host=None: dict(mx))
    monkeypatch.setattr("app.replication_inventory.source_hosts", lambda mx: [])
    monkeypatch.setattr(m, "load_hosts", lambda: [{"address": "10.0.0.1"}])

    t0 = time.time()
    body = client.get("/api/inventory/matrix?host=10.0.0.1").get_json()
    elapsed = time.time() - t0
    assert body["task_id"]
    assert elapsed < 2, f"endpoint blocked for {elapsed:.1f}s"
    release.set()


def test_progress_is_reported_per_host(client, monkeypatch):
    # A blank page for two minutes reads as a hang; the task has to say where
    # it is.
    from app.tasks import get_task
    import time
    seen = []

    def collect(hosts, progress=None):
        if progress:
            progress("1/2 10.0.0.1")
            progress("2/2 10.0.0.2")
        return {"guests": [], "hosts": []}

    monkeypatch.setattr("app.replication_inventory.collect_inventory", collect)
    monkeypatch.setattr("app.replication_inventory.filter_matrix",
                        lambda mx, source_host=None: dict(mx))
    monkeypatch.setattr("app.replication_inventory.source_hosts", lambda mx: [])
    monkeypatch.setattr(m, "load_hosts", lambda: [])

    started = client.get("/api/inventory/matrix").get_json()
    for _ in range(200):
        rec = get_task(started["task_id"])
        if rec and rec["status"] != "running":
            break
        time.sleep(0.05)
    msgs = [e["msg"] for e in rec["log"]]
    assert "hosts 1/2 10.0.0.1" in msgs
    assert "hosts 2/2 10.0.0.2" in msgs


# --- declared exceptions ---------------------------------------------------

def test_exceptions_are_listed_for_a_host(client, monkeypatch):
    monkeypatch.setattr("app.guest_intent.collect_exceptions",
                        lambda host, cache_ttl=300: {
                            "exceptions": {"253": {"no_backup": True,
                                                   "no_replication": False,
                                                   "reason": "Testcontainer",
                                                   "documented": True}},
                            "readable": True, "error": ""})
    body = client.get("/api/pve/guest-exceptions?host=10.0.0.1").get_json()
    assert body["exceptions"]["253"]["no_backup"] is True
    assert body["exceptions"]["253"]["reason"] == "Testcontainer"


def test_declaring_an_exception_reaches_the_module(client, monkeypatch):
    seen = {}

    def fake_set(host, vmid, kinds, reason="", by=""):
        seen.update(host=host["address"], vmid=vmid, kinds=kinds, reason=reason)
        return {"success": True, "tags": ["no-backup"], "kinds": kinds}

    monkeypatch.setattr("app.guest_intent.set_exception", fake_set)
    r = client.post("/api/pve/guest-exceptions?host=10.0.0.1",
                    json={"vmid": "253", "kinds": ["backup"], "reason": "Wegwerf"},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200
    assert r.get_json()["success"] is True
    assert seen == {"host": "10.0.0.1", "vmid": "253", "kinds": ["backup"],
                    "reason": "Wegwerf"}


def test_a_refused_declaration_comes_back_as_a_client_error(client, monkeypatch):
    monkeypatch.setattr("app.guest_intent.set_exception",
                        lambda host, vmid, kinds, reason="", by="": {
                            "success": False, "error": "guest 999 not found on this host"})
    r = client.post("/api/pve/guest-exceptions?host=10.0.0.1",
                    json={"vmid": "999", "kinds": ["backup"]},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 400
    assert "not found" in r.get_json()["error"]


def test_declaring_requires_the_csrf_token(client, monkeypatch):
    monkeypatch.setattr("app.guest_intent.set_exception",
                        lambda *a, **k: {"success": True})
    r = client.post("/api/pve/guest-exceptions?host=10.0.0.1",
                    json={"vmid": "253", "kinds": ["backup"]})
    assert r.status_code == 403


def test_the_matrix_carries_declared_exceptions(client, monkeypatch):
    matrix = {"guests": [{"vmid": "253", "source_host": "10.0.0.1",
                          "copies": [], "copy_count": 0}], "hosts": ["10.0.0.1"]}
    monkeypatch.setattr("app.replication_inventory.collect_inventory",
                        lambda hosts, progress=None: dict(matrix))
    monkeypatch.setattr("app.replication_inventory.filter_matrix",
                        lambda mx, source_host=None: dict(mx))
    monkeypatch.setattr("app.replication_inventory.source_hosts", lambda mx: [])
    monkeypatch.setattr(m, "load_hosts", lambda: [{"address": "10.0.0.1"}])
    monkeypatch.setattr("app.backups.host_backup_states",
                        lambda host, vmids=None, cache_ttl=300: {
                            "states": {"253": {"state": "red", "reason": "none"}},
                            "unreadable": [], "readable": True, "error": "",
                            "node": "n", "warn_hours": 36, "crit_hours": 168})
    monkeypatch.setattr("app.guest_intent.collect_exceptions",
                        lambda host, cache_ttl=300: {
                            "exceptions": {"253": {"no_backup": True,
                                                   "no_replication": True,
                                                   "reason": "gewollt",
                                                   "documented": True}},
                            "readable": True, "error": ""})
    body = _matrix_via_task(client, "10.0.0.1")
    g = body["guests"][0]
    assert g["exception"]["no_backup"] is True
    # No copy and no backup, but both gaps declared -- not a finding.
    assert b.protection_state(g) == "accepted"


def test_a_failed_exception_read_leaves_the_matrix_intact(client, monkeypatch):
    # The safe direction: guests keep their plain verdict rather than a failed
    # read excusing a gap nobody declared.
    matrix = {"guests": [{"vmid": "253", "source_host": "10.0.0.1",
                          "copies": [], "copy_count": 0}], "hosts": ["10.0.0.1"]}
    monkeypatch.setattr("app.replication_inventory.collect_inventory",
                        lambda hosts, progress=None: dict(matrix))
    monkeypatch.setattr("app.replication_inventory.filter_matrix",
                        lambda mx, source_host=None: dict(mx))
    monkeypatch.setattr("app.replication_inventory.source_hosts", lambda mx: [])
    monkeypatch.setattr(m, "load_hosts", lambda: [{"address": "10.0.0.1"}])
    monkeypatch.setattr("app.backups.host_backup_states",
                        lambda host, vmids=None, cache_ttl=300: {
                            "states": {}, "unreadable": [], "readable": True,
                            "error": "", "node": "n", "warn_hours": 36,
                            "crit_hours": 168})

    def boom(host, cache_ttl=300):
        raise RuntimeError("ssh gone")
    monkeypatch.setattr("app.guest_intent.collect_exceptions", boom)

    body = _matrix_via_task(client, "10.0.0.1")
    assert body["guests"][0]["vmid"] == "253"
    assert "exception" not in body["guests"][0]
