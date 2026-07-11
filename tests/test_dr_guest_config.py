"""DR guest-config restore: derive guest from dataset, extract <vmid>.conf
from a host-config backup tarball, and the write guard."""

import io
import tarfile
import pytest

from app import dr


# --- guest_ref_from_dataset -----------------------------------------------

@pytest.mark.parametrize("ds,expected", [
    ("rpool/repl/rpool/data/subvol-253-disk-0", ("lxc", "253")),
    ("rpool/data/vm-100-disk-1", ("qemu", "100")),
    ("rpool/data/base-9000-disk-0", ("qemu", "9000")),     # VM template
    ("rpool/data/basevol-9001-disk-0", ("lxc", "9001")),   # LXC template
    ("rpool/data/vm-100-cloudinit", (None, None)),          # not a disk
    ("rpool/data/vm-100-state-suspend", (None, None)),
    ("rpool/repl/rpool", (None, None)),
    ("", (None, None)),
])
def test_guest_ref_from_dataset(ds, expected):
    assert dr.guest_ref_from_dataset(ds) == expected


# --- extract_guest_config -------------------------------------------------

def _make_backup(tmp_path, members):
    p = tmp_path / "pve-backup-20260101-000000.tar.gz"
    with tarfile.open(p, "w:gz") as tf:
        for name, content in members.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return str(p)


def test_extract_from_pmxcfs_nodes_layout(tmp_path):
    # /etc/pve/qemu-server is a symlink -> nodes/<node>/qemu-server, so the real
    # file lives under nodes/<node>/... in the backup.
    path = _make_backup(tmp_path, {
        "./etc/pve/nodes/pve251/qemu-server/100.conf": "name: web\ncores: 2\n",
        "./etc/pve/nodes/pve251/lxc/253.conf": "arch: amd64\nhostname: ct\n",
        "./cmd/pveversion.txt": "pve-manager/9",
    })
    q = dr.extract_guest_config(path, "qemu", "100")
    assert q["found"] and "cores: 2" in q["content"] and q["subdir"] == "qemu-server"
    l = dr.extract_guest_config(path, "lxc", "253")
    assert l["found"] and "hostname: ct" in l["content"] and l["subdir"] == "lxc"


def test_extract_missing_vmid_or_wrong_type(tmp_path):
    path = _make_backup(tmp_path, {"./etc/pve/nodes/x/qemu-server/100.conf": "x"})
    assert dr.extract_guest_config(path, "qemu", "999")["found"] is False
    assert dr.extract_guest_config(path, "lxc", "100")["found"] is False


def test_extract_no_false_vmid_prefix_match(tmp_path):
    # 2253.conf must not satisfy a request for vmid 253
    path = _make_backup(tmp_path, {"./etc/pve/nodes/x/qemu-server/2253.conf": "other"})
    assert dr.extract_guest_config(path, "qemu", "253")["found"] is False


def test_extract_invalid_inputs(tmp_path):
    path = _make_backup(tmp_path, {"./etc/pve/nodes/x/qemu-server/100.conf": "x"})
    assert dr.extract_guest_config(path, "bogus", "100")["found"] is False
    assert dr.extract_guest_config(path, "qemu", "abc")["found"] is False


# --- restore_guest_config (write guard) -----------------------------------

def _fake_run(exists):
    def run(host, cmd, timeout=10):
        if "base64 -d" in cmd:
            return {"stdout": "__OK__", "success": True, "stderr": ""}
        return {"stdout": "__EXISTS__" if exists else "__NO__", "success": True}
    return run


def test_restore_invalid_inputs():
    h = {"address": "10.0.0.1"}
    assert dr.restore_guest_config(h, "bogus", "100", "x")["success"] is False
    assert dr.restore_guest_config(h, "qemu", "abc", "x")["success"] is False


def test_restore_refuses_existing_without_force(monkeypatch):
    calls = []
    def run(host, cmd, timeout=10):
        calls.append(cmd)
        return _fake_run(exists=True)(host, cmd, timeout)
    monkeypatch.setattr(dr, "run_command", run)
    r = dr.restore_guest_config({"address": "h"}, "qemu", "100", "data", force=False)
    assert r["success"] is False and r["exists"] is True
    assert not any("base64 -d" in c for c in calls)   # never wrote


def test_restore_overwrites_with_force(monkeypatch):
    monkeypatch.setattr(dr, "run_command", _fake_run(exists=True))
    r = dr.restore_guest_config({"address": "h"}, "lxc", "253", "arch: amd64", force=True)
    assert r["success"] is True and r["dest"] == "/etc/pve/lxc/253.conf"


def test_restore_writes_when_absent(monkeypatch):
    monkeypatch.setattr(dr, "run_command", _fake_run(exists=False))
    r = dr.restore_guest_config({"address": "h"}, "qemu", "100", "x")
    assert r["success"] is True and r["exists"] is False
    assert r["dest"] == "/etc/pve/qemu-server/100.conf"
