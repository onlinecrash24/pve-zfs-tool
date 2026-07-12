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
    "./root/.ssh/authorized_keys": "ssh-ed25519 AAAAC3Nz tool@host\n",
    "./cmd/dpkg-selections.txt": "pve-manager\tinstall\n",
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
    # command captures are info-only; config files + authorized_keys restorable
    assert files["cmd/dpkg-selections.txt"]["restorable"] is False
    assert files["etc/pve/storage.cfg"]["restorable"] is True
    assert files["root/.ssh/authorized_keys"]["restorable"] is True


# --- target path mapping --------------------------------------------------

def test_target_path_and_node_remap():
    assert dr._backup_target_path("etc/pve/storage.cfg", "pve") == "/etc/pve/storage.cfg"
    assert dr._backup_target_path("etc/network/interfaces", "pve") == "/etc/network/interfaces"
    # /etc/pve/nodes/<oldnode>/... -> local node
    assert dr._backup_target_path("etc/pve/nodes/OLD/qemu-server/100.conf", "newnode") \
        == "/etc/pve/nodes/newnode/qemu-server/100.conf"
    # authorized_keys maps back to /root/.ssh
    assert dr._backup_target_path("root/.ssh/authorized_keys", "x") == "/root/.ssh/authorized_keys"
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
