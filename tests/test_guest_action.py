"""Guest lifecycle actions (qm/pct start/shutdown/stop/reboot) — whitelisted
commands guarded by vmid/type validation; anything else must be refused
before any SSH happens."""

import pytest
from app import zfs_commands as z


@pytest.fixture
def captured(monkeypatch):
    calls = []

    def fake_run(host, cmd, timeout=30, **kw):
        calls.append(cmd)
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(z, "run_command", fake_run)
    monkeypatch.setattr(z, "_invalidate_cache", lambda addr: calls.append(f"INVALIDATE {addr}"))
    return calls


HOST = {"address": "10.0.0.1"}


@pytest.mark.parametrize("vm_type,action,expected", [
    ("qemu", "start",    "qm start 101"),
    ("qemu", "shutdown", "qm shutdown 101 --timeout 60"),
    ("qemu", "stop",     "qm stop 101"),
    ("qemu", "reboot",   "qm reboot 101"),
    ("lxc",  "start",    "pct start 101"),
    ("lxc",  "shutdown", "pct shutdown 101"),
    ("lxc",  "stop",     "pct stop 101"),
    ("lxc",  "reboot",   "pct reboot 101"),
])
def test_command_mapping(captured, vm_type, action, expected):
    r = z.guest_action(HOST, "101", vm_type, action)
    assert r["success"] is True
    assert captured[0] == expected


def test_success_invalidates_cache(captured):
    z.guest_action(HOST, "101", "qemu", "start")
    assert "INVALIDATE 10.0.0.1" in captured


def test_unsupported_action_refused_before_ssh(captured):
    r = z.guest_action(HOST, "101", "qemu", "destroy")
    assert r["success"] is False and "unsupported" in r["error"]
    assert captured == []          # no SSH, no cache invalidation


@pytest.mark.parametrize("bad_vmid", ["101; rm -rf /", "abc", "", "-1"])
def test_invalid_vmid_refused(captured, bad_vmid):
    r = z.guest_action(HOST, bad_vmid, "qemu", "start")
    assert r["success"] is False
    assert captured == []


def test_invalid_type_refused(captured):
    r = z.guest_action(HOST, "101", "docker", "start")
    assert r["success"] is False
    assert captured == []


def test_failure_reports_stderr_and_skips_invalidation(monkeypatch):
    calls = []
    monkeypatch.setattr(z, "run_command",
                        lambda h, c, timeout=30, **k: {"success": False, "stdout": "",
                                                       "stderr": "VM is locked (backup)"})
    monkeypatch.setattr(z, "_invalidate_cache", lambda addr: calls.append(addr))
    r = z.guest_action(HOST, "101", "qemu", "start")
    assert r["success"] is False
    assert "locked" in r["error"]
    assert calls == []
