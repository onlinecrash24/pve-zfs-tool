"""Config restore: browse a host-config backup, categorize files, map target
paths (with node remap), and bulk-restore guest configs."""

import io
import tarfile
import pytest

from app import dr


def _make_backup(tmp_path, members):
    p = tmp_path / "pve-backup-20260101-000000.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return str(p)


BACKUP = {
    "./etc/pve/storage.cfg": "zfspool: local-zfs\n\tpool rpool/data\n",
    "./etc/network/interfaces": "auto vmbr0\niface vmbr0 inet static\n",
    "./etc/pve/user.cfg": "user:root@pam:1:0:\n",
    "./etc/pve/firewall/cluster.fw": "[OPTIONS]\nenable: 1\n",
    "./etc/pve/jobs.cfg": "vzdump: x\n",
    "./etc/pve/nodes/pve251/qemu-server/100.conf": "cores: 2\n",
    "./etc/pve/nodes/pve251/lxc/253.conf": "arch: amd64\n",
    "./etc/apt/sources.list.d/pve.list": "deb http://download.proxmox.com/debian/pve trixie pve-no-subscription\n",
    "./etc/apt/sources.list.d/bashclub.sources": "Types: deb\nSigned-By: /usr/share/keyrings/bashclub-archive-keyring.gpg\n",
    "./usr/share/keyrings/bashclub-archive-keyring.gpg": "FAKEGPGBINARY",
    "./etc/fstab": "UUID=abc / zfs defaults 0 0\n",
    "./etc/vzdump.conf": "compress: zstd\n",
    "./etc/cron.d/zfs-auto-snapshot": "*/15 * * * * root zfs-auto-snapshot ...\n",
    "./etc/bashclub/192.168.66.70.conf": "SOURCE=root@192.168.66.70\n",
    "./root/.ssh/authorized_keys": "ssh-ed25519 AAAAC3Nz tool@host\n",
    "./cmd/dpkg-selections.txt": "pve-manager\tinstall\nvim\tinstall\nsl\tdeinstall\nzfsutils-linux\thold\n",
    "./cmd/pveversion.txt": "pve 9\n",
}


# --- list + categorize ----------------------------------------------------

def test_list_categorizes_and_flags_restorable(tmp_path):
    path = _make_backup(tmp_path, BACKUP)
    files = {f["path"]: f for f in dr.list_backup_contents(path)["files"]}
    assert files["etc/pve/storage.cfg"]["category"] == "storage"
    assert files["etc/network/interfaces"]["category"] == "network"
    assert files["etc/pve/user.cfg"]["category"] == "access"
    assert files["etc/pve/firewall/cluster.fw"]["category"] == "firewall"
    assert files["etc/pve/jobs.cfg"]["category"] == "jobs"
    assert files["etc/pve/nodes/pve251/qemu-server/100.conf"]["category"] == "guests"
    assert files["etc/pve/nodes/pve251/lxc/253.conf"]["category"] == "guests"
    assert files["cmd/dpkg-selections.txt"]["category"] == "info"
    assert files["root/.ssh/authorized_keys"]["category"] == "ssh"
    assert files["etc/apt/sources.list.d/pve.list"]["category"] == "apt"
    # signing keyrings OUTSIDE /etc/apt (deb822 convention) belong to apt too
    assert files["usr/share/keyrings/bashclub-archive-keyring.gpg"]["category"] == "apt"
    assert files["usr/share/keyrings/bashclub-archive-keyring.gpg"]["restorable"] is True
    assert files["etc/fstab"]["category"] == "storage"
    assert files["etc/fstab"]["restorable"] is True
    assert files["etc/vzdump.conf"]["category"] == "jobs"
    assert files["etc/cron.d/zfs-auto-snapshot"]["category"] == "jobs"
    assert files["etc/bashclub/192.168.66.70.conf"]["category"] == "jobs"
    # command captures are info-only; config files + authorized_keys restorable
    assert files["cmd/dpkg-selections.txt"]["restorable"] is False
    assert files["etc/pve/storage.cfg"]["restorable"] is True
    assert files["root/.ssh/authorized_keys"]["restorable"] is True
    assert files["etc/apt/sources.list.d/pve.list"]["restorable"] is True


# --- target path mapping --------------------------------------------------

def test_target_path_and_node_remap():
    assert dr._backup_target_path("etc/pve/storage.cfg", "pve") == "/etc/pve/storage.cfg"
    assert dr._backup_target_path("etc/network/interfaces", "pve") == "/etc/network/interfaces"
    # /etc/pve/nodes/<oldnode>/... -> local node
    assert dr._backup_target_path("etc/pve/nodes/OLD/qemu-server/100.conf", "newnode") \
        == "/etc/pve/nodes/newnode/qemu-server/100.conf"
    # authorized_keys maps back to /root/.ssh
    assert dr._backup_target_path("root/.ssh/authorized_keys", "x") == "/root/.ssh/authorized_keys"
    # APT signing keyrings under /usr/share/keyrings (outside /etc) restore too
    assert dr._backup_target_path("usr/share/keyrings/bashclub-archive-keyring.gpg", "x") \
        == "/usr/share/keyrings/bashclub-archive-keyring.gpg"
    assert dr._backup_target_path("usr/share/keyrings/key.asc", "x") == "/usr/share/keyrings/key.asc"
    # ... but only key files, and nothing else under /usr
    assert dr._backup_target_path("usr/share/keyrings/evil.sh", "x") is None
    assert dr._backup_target_path("usr/bin/evil", "x") is None
    # not restorable
    assert dr._backup_target_path("cmd/pveversion.txt", "x") is None
    assert dr._backup_target_path("var/lib/x", "x") is None
    assert dr._backup_target_path("root/.ssh/id_ed25519", "x") is None  # private key stays out
    assert dr._backup_target_path("", "x") is None


# --- read member ----------------------------------------------------------

def test_read_member(tmp_path):
    path = _make_backup(tmp_path, BACKUP)
    r = dr.read_backup_member(path, "./etc/pve/storage.cfg")
    assert r["found"] and "local-zfs" in r["content"]
    assert dr.read_backup_member(path, "./does/not/exist")["found"] is False


# --- restore (mocked SSH) -------------------------------------------------

def _fake_run(exists=False, hostname="newnode"):
    def run(host, cmd, timeout=10):
        if cmd.strip() == "hostname":
            return {"stdout": hostname + "\n", "success": True}
        if "base64 -d" in cmd:
            return {"stdout": "__OK__", "success": True, "stderr": ""}
        return {"stdout": "__EXISTS__" if exists else "__NO__", "success": True}
    return run


def test_restore_file_writes_to_mapped_path(tmp_path, monkeypatch):
    path = _make_backup(tmp_path, BACKUP)
    monkeypatch.setattr(dr, "run_command", _fake_run(exists=False, hostname="newnode.dom"))
    r = dr.restore_backup_file({"address": "h"}, path,
                               "./etc/pve/nodes/pve251/qemu-server/100.conf")
    assert r["success"] is True
    assert r["dest"] == "/etc/pve/nodes/newnode/qemu-server/100.conf"


def test_restore_file_refuses_existing_without_force(tmp_path, monkeypatch):
    path = _make_backup(tmp_path, BACKUP)
    monkeypatch.setattr(dr, "run_command", _fake_run(exists=True))
    r = dr.restore_backup_file({"address": "h"}, path, "./etc/network/interfaces",
                               force=False)
    assert r["success"] is False and r["exists"] is True


def test_restore_file_rejects_info_member(tmp_path, monkeypatch):
    path = _make_backup(tmp_path, BACKUP)
    monkeypatch.setattr(dr, "run_command", _fake_run(exists=False))
    r = dr.restore_backup_file({"address": "h"}, path, "./cmd/pveversion.txt")
    assert r["success"] is False


def test_restore_all_guests(tmp_path, monkeypatch):
    path = _make_backup(tmp_path, BACKUP)
    monkeypatch.setattr(dr, "run_command", _fake_run(exists=False))
    r = dr.restore_all_guest_configs({"address": "h"}, path)
    assert r["total"] == 2 and r["restored"] == 2 and r["skipped"] == 0
    assert {x["vmid"] for x in r["results"]} == {"100", "253"}


def test_restore_all_guests_skips_existing(tmp_path, monkeypatch):
    path = _make_backup(tmp_path, BACKUP)
    monkeypatch.setattr(dr, "run_command", _fake_run(exists=True))
    r = dr.restore_all_guest_configs({"address": "h"}, path, force=False)
    assert r["restored"] == 0 and r["skipped"] == 2


# --- package reinstall helpers --------------------------------------------

def test_read_dpkg_selections(tmp_path):
    path = _make_backup(tmp_path, BACKUP)
    sel = dr.read_dpkg_selections(path)
    assert "pve-manager" in sel and "zfsutils-linux" in sel


def test_filter_selections_keeps_install_hold_only():
    text = ("pve-manager\tinstall\nvim\tinstall\nsl\tdeinstall\n"
            "zfsutils-linux\thold\ngarbage\n")
    out = dr._filter_selections(text)
    lines = out.splitlines()
    assert "pve-manager\tinstall" in lines
    assert "zfsutils-linux\thold" in lines
    # deinstall/purge and malformed lines dropped -> never removes packages
    assert not any("deinstall" in ln for ln in lines)
    assert not any(ln.startswith("garbage") for ln in lines)


# --- executable-bit preservation ------------------------------------------

def _run_capturing(captured, exists=False):
    def run(host, cmd, timeout=10):
        if cmd.strip() == "hostname":
            return {"stdout": "n", "success": True}
        if "base64 -d" in cmd:
            captured["cmd"] = cmd
            return {"stdout": "__OK__", "success": True}
        return {"stdout": "__EXISTS__" if exists else "__NO__", "success": True}
    return run


def test_restore_preserves_exec_bit(tmp_path, monkeypatch):
    # cron run-parts scripts must stay executable after restore
    p = tmp_path / "b.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        data = b"#!/bin/sh\nexec zfs-auto-snapshot --label=hourly --keep=24 //\n"
        info = tarfile.TarInfo("./etc/cron.hourly/zfs-auto-snapshot")
        info.size = len(data)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(data))
    captured = {}
    monkeypatch.setattr(dr, "run_command", _run_capturing(captured))
    r = dr.restore_backup_file({"address": "h"}, str(p), "./etc/cron.hourly/zfs-auto-snapshot")
    assert r["success"] is True and r["dest"] == "/etc/cron.hourly/zfs-auto-snapshot"
    assert "chmod +x" in captured["cmd"]


def test_restore_non_exec_file_no_chmod(tmp_path, monkeypatch):
    path = _make_backup(tmp_path, {"./etc/pve/storage.cfg": "zfspool: x\n"})
    captured = {}
    monkeypatch.setattr(dr, "run_command", _run_capturing(captured))
    dr.restore_backup_file({"address": "h"}, path, "./etc/pve/storage.cfg")
    assert "chmod +x" not in captured["cmd"]


# --- category bulk restore --------------------------------------------------

def test_restore_backup_category_apt_includes_keyrings(tmp_path, monkeypatch):
    path = _make_backup(tmp_path, BACKUP)
    written = []
    def run(host, cmd, timeout=10):
        if cmd.strip() == "hostname":
            return {"stdout": "n", "success": True}
        if "base64 -d" in cmd:
            written.append(cmd)
            return {"stdout": "__OK__", "success": True}
        return {"stdout": "__NO__", "success": True}
    monkeypatch.setattr(dr, "run_command", run)
    r = dr.restore_backup_category({"address": "h"}, path, "apt")
    # both sources files AND the keyring outside /etc/apt got restored
    assert r["total"] == 3 and r["restored"] == 3 and r["failed"] == 0
    assert any("/usr/share/keyrings/bashclub-archive-keyring.gpg" in c for c in written)


def test_restore_backup_category_skips_existing(tmp_path, monkeypatch):
    path = _make_backup(tmp_path, BACKUP)
    monkeypatch.setattr(dr, "run_command", _fake_run(exists=True))
    r = dr.restore_backup_category({"address": "h"}, path, "apt", force=False)
    assert r["restored"] == 0 and r["skipped"] == 3 and r["success"] is True


def test_restore_backup_category_empty(tmp_path, monkeypatch):
    path = _make_backup(tmp_path, {"./cmd/pveversion.txt": "pve 9\n"})
    monkeypatch.setattr(dr, "run_command", _fake_run(exists=False))
    r = dr.restore_backup_category({"address": "h"}, path, "apt")
    assert r["total"] == 0 and r["success"] is True


# --- restore all configs (everything except guests + info) ------------------

def test_restore_all_configs_covers_everything_but_guests(tmp_path, monkeypatch):
    path = _make_backup(tmp_path, BACKUP)
    written = []
    def run(host, cmd, timeout=10):
        if cmd.strip() == "hostname":
            return {"stdout": "n", "success": True}
        if "base64 -d" in cmd:
            written.append(cmd)
            return {"stdout": "__OK__", "success": True}
        return {"stdout": "__NO__", "success": True}
    monkeypatch.setattr(dr, "run_command", run)
    r = dr.restore_all_configs({"address": "h"}, path)
    # every restorable non-guest, non-info file gets written; the 2 guest
    # confs and the 2 cmd/ captures are left out.
    assert r["failed"] == 0 and r["restored"] == r["total"] and r["total"] >= 10
    # guest configs are NOT touched here (own button)
    assert not any("/qemu-server/100.conf" in c for c in written)
    assert not any("/lxc/253.conf" in c for c in written)
    # but the important recovery bits ARE
    assert any("/etc/network/interfaces" in c for c in written)
    assert any("/usr/share/keyrings/bashclub-archive-keyring.gpg" in c for c in written)
    assert any("/etc/fstab" in c for c in written)


def test_restore_all_configs_excludes_guests_and_info(tmp_path, monkeypatch):
    # a backup of ONLY guest configs + info captures -> nothing for all-configs
    path = _make_backup(tmp_path, {
        "./etc/pve/nodes/pve1/qemu-server/100.conf": "cores: 2\n",
        "./cmd/pveversion.txt": "pve 9\n",
    })
    monkeypatch.setattr(dr, "run_command", _fake_run(exists=False))
    r = dr.restore_all_configs({"address": "h"}, path)
    assert r["total"] == 0 and r["success"] is True


# --- install-package-name extraction (honest still-missing check) -----------

def test_install_package_names_install_only_arch_stripped():
    sel = "pve-manager\tinstall\nvim:amd64\tinstall\nzfsutils-linux\thold\n"
    names = dr._install_package_names(sel)
    # only 'install' lines (hold excluded from the must-be-installed check),
    # arch suffix stripped, sorted+unique
    assert names == ["pve-manager", "vim"]


# --- reboot the restore target ----------------------------------------------

def test_reboot_target_backgrounds_the_reboot(monkeypatch):
    seen = {}
    def run(host, cmd, timeout=10):
        seen["cmd"] = cmd
        return {"success": True, "stdout": "__reboot_scheduled__\n"}
    monkeypatch.setattr(dr, "run_command", run)
    r = dr.reboot_target({"address": "h"})
    assert r["success"] is True
    # backgrounded with a delay so the SSH call returns instead of dying with
    # the connection the reboot tears down
    assert "nohup" in seen["cmd"] and "sleep 2" in seen["cmd"]
    assert "systemctl reboot" in seen["cmd"]


def test_reboot_target_reports_failure(monkeypatch):
    monkeypatch.setattr(dr, "run_command",
                        lambda host, cmd, timeout=10: {"success": False, "stdout": "",
                                                       "stderr": "permission denied"})
    r = dr.reboot_target({"address": "h"})
    assert r["success"] is False and "permission denied" in r["error"]


# --- apt-mark showmanual capture (preferred reinstall source) ---------------

def test_read_apt_manual_present(tmp_path):
    path = _make_backup(tmp_path, {
        "./cmd/apt-manual.txt": "mc\nntfs-3g\nvim:amd64\nmc\n",
    })
    # arch stripped, sorted, unique
    assert dr.read_apt_manual(path) == ["mc", "ntfs-3g", "vim"]


def test_read_apt_manual_absent_returns_empty(tmp_path):
    # older backup without the apt-mark capture -> [] (reinstall falls back to
    # the full dpkg selection)
    path = _make_backup(tmp_path, BACKUP)
    assert dr.read_apt_manual(path) == []
