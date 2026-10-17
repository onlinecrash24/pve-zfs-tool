"""Security review round 2 (file and zvol restore), the payloads from the
report as tests.

A  shell injection as root through zvol_dev
B  HTML injection through a file name with a newline (ls without -q)
C  zfs destroy -r on any dataset through the restore-clone unmount; a
   dataset destroyed under a "snapshot.destroy" entry
D  any block device mounted through /dev/mapper/<prefix only>
E  unmount of any mount point through ".."
F  dmsetup remove on LVM-thin volumes
I/J escaping in the audit view and in attributes
K  cron log path outside the log directory
"""

import io
import os
import re

import pytest

from app import replication as r
from app import zfs_commands as zc

HOST = {"address": "h"}
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rec(monkeypatch, answers=None):
    """Record every command; answer by prefix from ``answers``."""
    cmds = []

    def run(host, cmd, **kw):
        cmds.append(cmd)
        for prefix, out in (answers or {}).items():
            if cmd.startswith(prefix):
                return {"success": True, "stdout": out, "stderr": ""}
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(zc, "run_command", run)
    return cmds


# --- A ----------------------------------------------------------------------------

def test_a_zvol_dev_is_validated_before_it_reaches_a_shell(monkeypatch):
    cmds = _rec(monkeypatch)
    res = zc.zvol_unmount(HOST, "", "/dev/zvol/x;id>/tmp/pwn@s")
    assert not res["success"]
    assert cmds == []                                   # nothing ran at all


def test_a_valid_zvol_dev_is_quoted_and_only_its_own_mappings_go(monkeypatch):
    cmds = _rec(monkeypatch, {"for p in /dev/mapper/*": "vm-100-disk-0@snap1\nvm-100-disk-0@snapp2\n"
                                                        "vm-100-disk-0@snap-other1\nvm-101-disk-0@snap1\n"})
    res = zc.zvol_unmount(HOST, "", "/dev/zvol/rpool/data/vm-100-disk-0@snap")
    assert res["success"]
    assert "zfs set snapdev=hidden rpool/data/vm-100-disk-0" in cmds
    removed = [c for c in cmds if c.startswith("dmsetup remove")]
    assert removed == ["dmsetup remove vm-100-disk-0@snap1 2>/dev/null",
                       "dmsetup remove vm-100-disk-0@snapp2 2>/dev/null"]
    assert not any("grep" in c for c in cmds)           # no pattern built from input


# --- B ----------------------------------------------------------------------------

LS = (
    "total 12\n"
    "drwxr-xr-x 2 root root 4096 2026-10-17 20:00 .\n"
    "drwxr-xr-x 3 root root 4096 2026-10-17 20:00 ..\n"
    "-rw-r--r-- 1 root root  512 2026-10-17 20:01 notes.txt\n"
    # a name with a newline, listed WITHOUT -q: the second line's columns are the guest's
    "-rw-r--r-- 1 root root    7 2026-10-17 20:02 evil\n"
    "x x x x <img src=x onerror=alert(1)> y y y\n"
    "brw-rw---- 1 root disk 8, 1 2026-10-17 20:03 sda1\n"
)


def test_b_the_listing_asks_ls_to_hide_control_characters(monkeypatch):
    monkeypatch.setattr(zc, "_validated_mount", lambda m: m)
    monkeypatch.setattr(zc, "_verify_under_mount", lambda *a: None)
    cmds = _rec(monkeypatch, {"ls ": LS})
    res = zc.snapshot_browse(HOST, "/tmp/zfs-tool-restore/x", "")
    assert cmds[0].startswith("ls -laq ")
    names = [e["name"] for e in res["entries"]]
    assert names == ["notes.txt", "evil", "sda1"]                 # the forged line is gone
    assert all("<img" not in e["size"] for e in res["entries"])
    assert {e["size"] for e in res["entries"]} == {"512", "7", "8, 1"}


def test_b_every_column_is_escaped_in_the_browser():
    js = io.open(os.path.join(ROOT, "app", "static", "js", "app.js"), encoding="utf-8").read()
    assert "${sizeDisplay}" not in js
    assert js.count("${escapeHtml(sizeDisplay)}") == 2


# --- C ----------------------------------------------------------------------------

def test_c_only_a_restore_clone_can_be_destroyed(monkeypatch):
    cmds = _rec(monkeypatch)
    assert not zc.snapshot_unmount(HOST, "rpool/data")["success"]
    assert not zc.snapshot_unmount(HOST, "rpool/data/restore-x")["success"]        # not directly under the pool
    assert cmds == []
    zc.snapshot_unmount(HOST, "rpool/restore-rpool-data-subvol-111-disk-0-restore-daily")
    assert cmds == ["zfs destroy -r rpool/restore-rpool-data-subvol-111-disk-0-restore-daily"]


def test_c_snapshot_destroy_needs_a_snapshot(monkeypatch):
    cmds = _rec(monkeypatch)
    res = zc.destroy_snapshot(HOST, "rpool/data")
    assert not res["success"] and "not a snapshot" in res["stderr"]
    assert cmds == []


# --- D ----------------------------------------------------------------------------

def test_d_only_a_zvol_backed_mapping_is_mounted(monkeypatch):
    cmds = _rec(monkeypatch, {"dmsetup deps -o devname pve-root": "1 dependencies  : (dm-3)\n",
                               "dmsetup deps -o devname vm-100-disk-0@snap1": "1 dependencies  : (zd16)\n"})
    for dev in ("/dev/mapper/../sda2", "/dev/mapper/pve-root", "/dev/sda2", "/dev/mapper/a/b"):
        res = zc.zvol_partition_mount(HOST, dev, "ext4")
        assert not res["success"], dev
    assert not any(c.startswith("mount") for c in cmds)
    res = zc.zvol_partition_mount(HOST, "/dev/mapper/vm-100-disk-0@snap1", "ext4")
    assert res["success"]
    assert any(c.startswith("mount -o ro,norecovery /dev/mapper/vm-100-disk-0@snap1 ") for c in cmds)


# --- E ----------------------------------------------------------------------------

def test_e_mount_path_must_be_one_component_under_the_base(monkeypatch):
    cmds = _rec(monkeypatch)
    for mp in ("/tmp/zfs-tool-zvol-restore/../../etc/pve", "/tmp/zfs-tool-zvol-restore-x/a",
               "/tmp/zfs-tool-zvol-restore/a/b", "/etc/pve"):
        assert not zc.zvol_unmount(HOST, mp, "")["success"], mp
    assert cmds == []
    assert zc.zvol_unmount(HOST, "/tmp/zfs-tool-zvol-restore/vm-100-disk-0@snap1", "")["success"]
    assert cmds[0] == "umount /tmp/zfs-tool-zvol-restore/vm-100-disk-0@snap1"


# --- F ----------------------------------------------------------------------------

def test_f_cleanup_removes_zvol_mappings_only(monkeypatch):
    # The host-side script already filters by dmsetup deps; the Python side
    # must not fall back to a name grep.
    cmds = _rec(monkeypatch, {"for p in /dev/mapper/*": "vm-100-disk-0@snap1\n"})
    res = zc.zvol_cleanup_all(HOST)
    assert res["mappings"] == ["vm-100-disk-0@snap1"]
    assert not any("grep -E '(vm-|zd[0-9])'" in c for c in cmds)
    assert "dmsetup deps -o devname" in zc._ZVOL_BACKED_MAPPINGS_SH and "zd[0-9]" in zc._ZVOL_BACKED_MAPPINGS_SH


def test_f_active_list_uses_the_same_criterion(monkeypatch):
    cmds = _rec(monkeypatch, {"for p in /dev/mapper/*": "vm-100-disk-0@snap1\n"})
    act = zc.zvol_list_active(HOST)
    assert act["mappings"] == ["vm-100-disk-0@snap1"]
    assert not any("grep -E '(vm-|zd[0-9])'" in c for c in cmds)


# --- I / J --------------------------------------------------------------------------

def test_ij_escape_html_covers_quotes_and_the_audit_rows_use_it():
    js = io.open(os.path.join(ROOT, "app", "static", "js", "app.js"), encoding="utf-8").read()
    body = re.search(r"function escapeHtml\(s\) \{(.*?)\n\}", js, re.S).group(1)
    assert "&quot;" in body and "&#39;" in body and "textContent" not in body
    rows = re.search(r"<td>\$\{_auditFmt\(e\.timestamp\)\}</td>(.*?)</tr>", js, re.S).group(1)
    assert not re.search(r"\$\{\(?e\.", rows), rows                # nothing raw in the audit rows
    for field in ("e.user", "e.ip", "e.host", "e.action", "e.target", "e.details"):
        assert f"escapeHtml({field}" in rows, field


# --- K ----------------------------------------------------------------------------

def test_k_cron_log_path_is_bound_to_the_log_directory(monkeypatch):
    monkeypatch.setattr(r, "run_command", lambda h, c, **k: {"success": True, "stdout": "__OK__"})
    bad = ("/root/.ssh/authorized_keys", "/var/log/bashclub-zsync/../../root/.ssh/authorized_keys",
           "/var/log/bashclub-zsync", "/var/log/bashclub-zsync/")
    for p in bad:
        assert not r.set_cron(HOST, "17 * * * *", source="root@10.0.0.5", log_path=p)["success"], p
    assert r.set_cron(HOST, "17 * * * *", source="root@10.0.0.5",
                      log_path="/var/log/bashclub-zsync/10.0.0.5.log")["success"]
