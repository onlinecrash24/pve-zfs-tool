"""Ad-hoc/password SSH: run_command routing, known_hosts refresh, pubkey install."""

import paramiko
import pytest

from app import ssh_manager as sm


def test_run_command_password_bypasses_pool(monkeypatch):
    monkeypatch.setattr(sm, "_exec_direct", lambda h, c, t: {"via": "direct", "success": True})
    monkeypatch.setattr(sm, "_exec_pooled", lambda h, c, t: {"via": "pooled", "success": True})
    assert sm.run_command({"address": "h", "password": "p"}, "cmd")["via"] == "direct"
    assert sm.run_command({"address": "h"}, "cmd")["via"] == "pooled"


def test_get_host_fingerprint_bounds_connect_with_timeout(monkeypatch):
    # An unreachable host must fail fast, not hang on the OS-default TCP
    # timeout: the connect has to go through socket.create_connection WITH a
    # timeout (not the timeout-less paramiko.Transport((addr, port)) tuple).
    calls = {}
    def fake_create_connection(addr, timeout=None):
        calls["addr"] = addr
        calls["timeout"] = timeout
        raise OSError("network unreachable")
    monkeypatch.setattr(sm.socket, "create_connection", fake_create_connection)
    r = sm.get_host_fingerprint("10.99.99.99", 22, timeout=3)
    assert r["success"] is False
    assert calls["addr"] == ("10.99.99.99", 22)
    assert calls["timeout"] == 3


def test_add_host_adds_even_when_fingerprint_unreachable(tmp_path, monkeypatch):
    # Adding an unreachable host still registers it (TOFU happens later on the
    # first real connection); it must not hang or fail on the fingerprint step.
    monkeypatch.setattr(sm, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sm, "HOSTS_FILE", str(tmp_path / "hosts.json"))
    monkeypatch.setattr(sm, "KNOWN_HOSTS", str(tmp_path / "known_hosts"))
    monkeypatch.setattr(sm, "get_host_fingerprint",
                        lambda addr, port=22: {"success": False, "error": "timeout"})
    ok, _ = sm.add_host("pve9", "10.99.99.99", 22, "root")
    assert ok is True
    assert any(h["address"] == "10.99.99.99" for h in sm.load_hosts())


def test_forget_host_key_removes_only_that_address(tmp_path, monkeypatch):
    kh = str(tmp_path / "known_hosts")
    monkeypatch.setattr(sm, "KNOWN_HOSTS", kh)
    hk = paramiko.HostKeys()
    hk.add("10.0.0.1", "ssh-rsa", paramiko.RSAKey.generate(2048))
    hk.add("10.0.0.2", "ssh-rsa", paramiko.RSAKey.generate(2048))
    hk.save(kh)

    sm._forget_host_key("10.0.0.1")

    after = paramiko.HostKeys()
    after.load(kh)
    assert "10.0.0.1" not in after
    assert "10.0.0.2" in after


def test_forget_host_key_no_file_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "KNOWN_HOSTS", str(tmp_path / "missing"))
    sm._forget_host_key("10.0.0.1")   # must not raise


def test_install_pubkey_appends_public_key(monkeypatch):
    monkeypatch.setattr(sm, "get_public_key", lambda: "ssh-ed25519 AAAAkey tool@host")
    captured = {}

    def fake_run(host, cmd, timeout=30):
        captured["cmd"] = cmd
        captured["host"] = host
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(sm, "run_command", fake_run)
    r = sm.install_pubkey({"address": "10.0.0.1", "password": "x"})
    assert r["success"] is True
    assert "ssh-ed25519 AAAAkey tool@host" in captured["cmd"]
    assert "authorized_keys" in captured["cmd"]


def test_install_pubkey_without_key_fails(monkeypatch):
    monkeypatch.setattr(sm, "get_public_key", lambda: None)
    r = sm.install_pubkey({"address": "h"})
    assert r["success"] is False
