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


def test_matrix_attaches_backups_for_the_selected_host_only(client, monkeypatch):
    import time
    now = int(time.time())
    matrix = {"guests": [
        {"vmid": "100", "source_host": "10.0.0.1", "copies": [], "copy_count": 0},
        {"vmid": "200", "source_host": "10.0.0.2", "copies": [], "copy_count": 0},
    ], "hosts": ["10.0.0.1", "10.0.0.2"]}
    monkeypatch.setattr("app.replication_inventory.collect_inventory",
                        lambda hosts: dict(matrix))
    monkeypatch.setattr("app.replication_inventory.filter_matrix",
                        lambda mx, source_host=None, only_when_replicating=True: dict(mx))
    monkeypatch.setattr("app.replication_inventory.source_hosts", lambda mx: ["10.0.0.1"])
    monkeypatch.setattr(m, "load_hosts", lambda: [{"address": "10.0.0.1"}])
    _route(monkeypatch, {
        "hostname": {"success": True, "stdout": "pve251\n", "stderr": ""},
        "/storage --content backup": {"success": True, "stdout": STORAGES_OK, "stderr": ""},
        "/storage/pbs-main/content": {"success": True, "stderr": "",
                                      "stdout": _pbs_content("100", now - 3600)},
        "jobs.cfg": {"success": True, "stdout": JOBS_ALL, "stderr": ""},
    })
    body = client.get("/api/inventory/matrix?host=10.0.0.1").get_json()
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
                        lambda hosts: dict(matrix))
    monkeypatch.setattr("app.replication_inventory.filter_matrix",
                        lambda mx, source_host=None, only_when_replicating=True: dict(mx))
    monkeypatch.setattr("app.replication_inventory.source_hosts", lambda mx: [])
    monkeypatch.setattr(m, "load_hosts", lambda: [{"address": "10.0.0.1"}])

    def boom(host, vmids=None, cache_ttl=300):
        raise RuntimeError("ssh gone")
    monkeypatch.setattr("app.backups.host_backup_states", boom)

    body = client.get("/api/inventory/matrix?host=10.0.0.1").get_json()
    assert body["guests"][0]["vmid"] == "100"
    assert body["backup_readable"] is False
    assert "ssh gone" in body["backup_error"]
