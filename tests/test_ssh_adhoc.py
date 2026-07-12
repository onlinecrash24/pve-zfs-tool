"""Ad-hoc/password SSH: run_command routing, known_hosts refresh, pubkey install."""

import paramiko
import pytest

from app import ssh_manager as sm


def test_run_command_password_bypasses_pool(monkeypatch):
    monkeypatch.setattr(sm, "_exec_direct", lambda h, c, t: {"via": "direct", "success": True})
    monkeypatch.setattr(sm, "_exec_pooled", lambda h, c, t: {"via": "pooled", "success": True})
    assert sm.run_command({"address": "h", "password": "p"}, "cmd")["via"] == "direct"
    assert sm.run_command({"address": "h"}, "cmd")["via"] == "pooled"


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
