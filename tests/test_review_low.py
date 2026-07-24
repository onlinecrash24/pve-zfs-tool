"""Tests for the low-priority review fixes:
  O  ssh_manager._sha256_fingerprint  -> standard OpenSSH SHA256 format
  K  autosnap.set_retention           -> no-op (missing --keep) reported as failure
  L  zfs_commands._verify_under_mount  -> fails CLOSED when realpath can't run
"""

import base64
import hashlib

from app.ssh_manager import _sha256_fingerprint
from app import autosnap as asnap
from app import zfs_commands as zc


# --- O: fingerprint format -------------------------------------------------

def test_fingerprint_is_standard_openssh_format():
    kb = b"\x00\x00\x00\x0bssh-ed25519 sample key bytes"
    fp = _sha256_fingerprint(kb)
    assert fp.startswith("SHA256:")
    body = fp[len("SHA256:"):]
    assert "=" not in body               # padding stripped
    assert ":" not in body               # base64, not colon-hex
    assert len(body) == 43               # base64 of a full 32-byte digest
    assert body == base64.b64encode(hashlib.sha256(kb).digest()).decode().rstrip("=")


# --- K: set_retention refuses a silent no-op -------------------------------

def _cron(keep_token):
    line = "zfs-auto-snapshot --quiet --syslog --label=daily"
    if keep_token:
        line += " --keep=10"
    return line + " //\n"


def test_set_retention_without_keep_token_fails(monkeypatch):
    def run(host, cmd, **kw):
        if cmd.startswith("cat "):
            return {"success": True, "stdout": _cron(keep_token=False)}
        return {"success": True, "stdout": "__OK__"}
    monkeypatch.setattr(asnap, "run_command", run)
    res = asnap.set_retention({"address": "h"}, [{"label": "daily", "keep": 5}])
    assert res["success"] is False
    assert res["results"][0]["success"] is False
    assert "keep" in res["results"][0]["error"].lower()


def test_set_retention_with_keep_token_applies(monkeypatch):
    def run(host, cmd, **kw):
        if cmd.startswith("cat "):
            return {"success": True, "stdout": _cron(keep_token=True)}
        return {"success": True, "stdout": "__OK__", "stderr": ""}
    monkeypatch.setattr(asnap, "run_command", run)
    res = asnap.set_retention({"address": "h"}, [{"label": "daily", "keep": 5}])
    assert res["success"] is True
    assert res["results"][0]["success"] is True


# --- L: path-escape guard fails closed -------------------------------------

def test_verify_under_mount_inside_ok(monkeypatch):
    monkeypatch.setattr(zc, "run_command",
                        lambda h, c, **k: {"success": True, "stdout": "/mnt/snap/etc/hosts\n"})
    assert zc._verify_under_mount({}, "/mnt/snap/etc/hosts", "/mnt/snap") is None


def test_verify_under_mount_escape_denied(monkeypatch):
    monkeypatch.setattr(zc, "run_command",
                        lambda h, c, **k: {"success": True, "stdout": "/etc/shadow\n"})
    res = zc._verify_under_mount({}, "/mnt/snap/link", "/mnt/snap")
    assert res and res["success"] is False


def test_verify_under_mount_realpath_failure_denies(monkeypatch):
    # THE fix: a realpath error must DENY, not fall through to ls/cat/cp
    monkeypatch.setattr(zc, "run_command",
                        lambda h, c, **k: {"success": False, "stdout": "", "stderr": "boom"})
    res = zc._verify_under_mount({}, "/mnt/snap/x", "/mnt/snap")
    assert res and res["success"] is False
