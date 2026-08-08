"""The add-host endpoint refuses non-Proxmox hosts -- wiring, not just policy.

``test_host_identity`` covers the decision; this covers what the UI actually
receives: the status code and the ``code`` field it branches on, that a refused
host is not in hosts.json afterwards, and that the detected role is stored.
"""

import pytest

from app import host_identity as hid
from app import main as m
from app import ssh_manager as sm


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sm, "HOSTS_FILE", str(tmp_path / "hosts.json"))
    monkeypatch.setattr(sm, "KNOWN_HOSTS", str(tmp_path / "known_hosts"))
    monkeypatch.setattr(sm, "get_host_fingerprint",
                        lambda addr, port=22: {"success": False, "error": "skip"})
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    m.app.config["TESTING"] = True
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["csrf_token"] = "tok"
    return c


def _identity(monkeypatch, stdout):
    """Make the probe answer with fixed output instead of SSH-ing anywhere."""
    monkeypatch.setattr(hid, "run_command",
                        lambda h, c, timeout=0, cache_ttl=0: {
                            "success": True, "stdout": stdout, "stderr": ""})


def _unreachable(monkeypatch):
    monkeypatch.setattr(hid, "run_command",
                        lambda h, c, timeout=0, cache_ttl=0: {
                            "success": False, "stdout": "",
                            "stderr": "Authentication failed."})


def _post(client, payload):
    return client.post("/api/hosts", json=payload, headers={"X-CSRF-Token": "tok"})


def test_a_pve_host_is_added_with_its_role(client, monkeypatch):
    _identity(monkeypatch, "PVE_BIN=1\nPVE_PKG=8.2.4-1\nPROBE_OK=1\n")
    r = _post(client, {"name": "pve251", "address": "10.0.0.1"})
    assert r.status_code == 200
    assert r.get_json()["success"] is True
    stored = [h for h in sm.load_hosts() if h["address"] == "10.0.0.1"][0]
    assert stored["role"] == "pve"
    assert stored["pve_version"] == "8.2.4"


def test_a_standalone_backup_server_is_refused(client, monkeypatch):
    # Real Proxmox software, but nothing here reads from a backup server, so
    # registering it would buy only root SSH into the machine that should
    # survive the compromise of the systems it backs up.
    _identity(monkeypatch, "PBS_BIN=1\nPBS_PKG=3.2.7-1\nPROBE_OK=1\n")
    r = _post(client, {"name": "pbs01", "address": "10.0.0.2"})
    assert r.status_code == 400
    body = r.get_json()
    assert body["success"] is False
    assert body["code"] == "pbs_only"
    # The message must not claim it is "not Proxmox" -- it plainly is.
    assert "Backup Server" in body["message"]
    assert sm.load_hosts() == []


def test_force_cannot_smuggle_in_a_standalone_backup_server(client, monkeypatch):
    # Deliberate policy, not a connection problem: nothing to retry.
    _identity(monkeypatch, "PBS_BIN=1\nPBS_PKG=3.2.7-1\nPROBE_OK=1\n")
    r = _post(client, {"name": "pbs01", "address": "10.0.0.2", "force": True})
    assert r.status_code == 400
    assert r.get_json()["code"] == "pbs_only"
    assert sm.load_hosts() == []


def test_a_pve_node_with_pbs_installed_is_added(client, monkeypatch):
    # The co-located case stays supported -- it is still a PVE node, and every
    # feature reads through it. Only the standalone backup server is refused.
    _identity(monkeypatch,
              "PVE_BIN=1\nPVE_PKG=8.2.4-1\nPBS_BIN=1\nPBS_PKG=3.2.7-1\nPROBE_OK=1\n")
    r = _post(client, {"name": "pve-bk", "address": "10.0.0.2"})
    assert r.get_json()["success"] is True
    stored = sm.load_hosts()[0]
    assert stored["role"] == "pve+pbs"
    assert stored["pve_version"] == "8.2.4"


def test_a_plain_linux_box_is_refused(client, monkeypatch):
    _identity(monkeypatch, "HOSTNAME=fileserver\nPROBE_OK=1\n")
    r = _post(client, {"name": "nas", "address": "10.0.0.3"})
    assert r.status_code == 400
    body = r.get_json()
    assert body["success"] is False
    assert body["code"] == "not_proxmox"
    assert sm.load_hosts() == []


def test_force_cannot_smuggle_in_a_non_proxmox_host(client, monkeypatch):
    _identity(monkeypatch, "HOSTNAME=fileserver\nPROBE_OK=1\n")
    r = _post(client, {"name": "nas", "address": "10.0.0.4", "force": True})
    assert r.status_code == 400
    assert r.get_json()["code"] == "not_proxmox"
    assert sm.load_hosts() == []


def test_an_unreachable_host_reports_unverified_and_the_reason(client, monkeypatch):
    _unreachable(monkeypatch)
    r = _post(client, {"name": "pve9", "address": "10.0.0.5"})
    assert r.status_code == 400
    body = r.get_json()
    assert body["code"] == "unverified"
    # The UI shows this so the user can tell "key not installed" from "not Proxmox".
    assert "Authentication failed" in body["identity"]["error"]
    assert sm.load_hosts() == []


def test_an_unreachable_host_can_be_added_on_confirmation(client, monkeypatch):
    _unreachable(monkeypatch)
    r = _post(client, {"name": "pve9", "address": "10.0.0.6", "force": True})
    assert r.get_json()["success"] is True
    stored = sm.load_hosts()[0]
    assert stored["role"] == "unknown"      # identified on the next probe
    assert stored["pve_version"] is None


def test_identify_upgrades_a_stored_role(client, monkeypatch):
    _identity(monkeypatch, "PVE_BIN=1\nPVE_PKG=8.2.4-1\nPROBE_OK=1\n")
    _post(client, {"name": "pve251", "address": "10.0.0.7"})
    # PBS gets installed on the node later; the Hosts view re-probes.
    _identity(monkeypatch,
              "PVE_BIN=1\nPVE_PKG=8.2.4-1\nPBS_BIN=1\nPBS_PKG=3.2.7-1\nPROBE_OK=1\n")
    r = client.post("/api/hosts/identify", json={"address": "10.0.0.7", "refresh": True},
                    headers={"X-CSRF-Token": "tok"})
    assert r.get_json()["identity"]["role"] == "pve+pbs"
    assert sm.load_hosts()[0]["role"] == "pve+pbs"


def test_identify_keeps_the_stored_role_when_the_host_is_down(client, monkeypatch):
    # An offline host must not lose the role it was identified with -- the Hosts
    # view would otherwise show every powered-off node as unknown.
    _identity(monkeypatch, "PVE_BIN=1\nPVE_PKG=8.2.4-1\nPROBE_OK=1\n")
    _post(client, {"name": "pve1", "address": "10.0.0.8"})
    _unreachable(monkeypatch)
    r = client.post("/api/hosts/identify", json={"address": "10.0.0.8"},
                    headers={"X-CSRF-Token": "tok"})
    assert r.get_json()["identity"]["reachable"] is False
    assert sm.load_hosts()[0]["role"] == "pve"


def test_identify_rejects_an_unknown_address(client, monkeypatch):
    r = client.post("/api/hosts/identify", json={"address": "10.0.0.99"},
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 404
