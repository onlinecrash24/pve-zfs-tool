"""Recovery guest from a snapshot: the pure transformations and the two jobs.

The expensive mistakes first: a config line that must not travel (a NIC on
the same bridge, a lock, a hook script), a cleanup that could take a real
guest with it, a half-created guest that leaves clones behind.
"""

import re

import pytest

from app import recovery_guest as rg

HOST = {"address": "h"}

QEMU_CFG = """#Production web server
agent: 1
bios: ovmf
boot: order=scsi0
cores: 4
efidisk0: local-zfs:vm-100-disk-1,efitype=4m,size=4M
hookscript: local:snippets/hook.sh
ide2: local:iso/debian.iso,media=cdrom
lock: backup
machine: q35
memory: 8192
name: web
net0: virtio=AA:BB:CC:11:22:33,bridge=vmbr0,tag=10
onboot: 1
ostype: l26
parent: before-upgrade
protection: 1
scsi0: local-zfs:vm-100-disk-0,discard=on,size=32G
scsihw: virtio-scsi-single
smbios1: uuid=11111111-2222-3333-4444-555555555555
startup: order=2,up=30
tpmstate0: local-zfs:vm-100-disk-2,size=4M,version=v2.0
unused0: local-zfs:vm-100-disk-9
vmgenid: 66666666-7777-8888-9999-000000000000
[before-upgrade]
scsi0: local-zfs:vm-100-disk-0,discard=on,size=32G
vmstate: local-zfs:vm-100-state-before-upgrade
"""

LXC_CFG = """arch: amd64
cores: 2
features: nesting=1
hostname: files
memory: 1024
mp0: local-zfs:subvol-253-disk-1,mp=/data,size=100G
net0: name=eth0,bridge=vmbr0,hwaddr=AA:BB:CC:DD:EE:FF,ip=dhcp
onboot: 1
ostype: debian
rootfs: local-zfs:subvol-253-disk-0,size=8G
unprivileged: 1
"""


# --- pure: config ------------------------------------------------------------

def _cfg(text, gtype, vmap, vmid="900"):
    origin = "100" if gtype == "qemu" else "253"
    return rg.build_recovery_config(text, gtype, vmid, vmap, origin, "zfs-auto-snap_daily-2026-10-13-0025",
                                    now=1_760_000_000)


QEMU_MAP = {"local-zfs:vm-100-disk-0": "rpool-clone:vm-900-disk-0",
            "local-zfs:vm-100-disk-1": "rpool-clone:vm-900-disk-1",
            "local-zfs:vm-100-disk-2": "rpool-clone:vm-900-disk-2"}


def test_nothing_that_must_not_travel_travels():
    out = _cfg(QEMU_CFG, "qemu", QEMU_MAP)
    for key in ("net0", "lock", "parent", "hookscript", "startup", "unused0", "protection"):
        assert not re.search(rf"^{key}:", out, re.M), key
    assert "[before-upgrade]" not in out and "vmstate" not in out
    assert "#Production web server" not in out          # the origin's notes are not ours


def test_hardware_is_kept_and_disks_point_at_the_clones():
    out = _cfg(QEMU_CFG, "qemu", QEMU_MAP)
    for line in ("bios: ovmf", "machine: q35", "scsihw: virtio-scsi-single", "cores: 4",
                 "memory: 8192", "agent: 1", "boot: order=scsi0"):
        assert line in out, line
    assert "scsi0: rpool-clone:vm-900-disk-0,discard=on,size=32G" in out
    assert "efidisk0: rpool-clone:vm-900-disk-1,efitype=4m,size=4M" in out
    assert "tpmstate0: rpool-clone:vm-900-disk-2,size=4M,version=v2.0" in out
    assert "ide2: local:iso/debian.iso,media=cdrom" in out    # an ISO is not a disk


def test_the_copy_is_a_new_machine_that_stays_down():
    out = _cfg(QEMU_CFG, "qemu", QEMU_MAP)
    assert "onboot: 0" in out and "onboot: 1" not in out
    assert "name: web-recovery" in out
    assert "uuid=11111111-2222-3333-4444-555555555555" not in out
    assert re.search(r"^smbios1: uuid=[0-9a-f-]{36}$", out, re.M)
    assert re.search(r"^vmgenid: [0-9a-f-]{36}$", out, re.M)
    assert "66666666-7777-8888-9999-000000000000" not in out


def test_the_marker_is_the_first_line_and_round_trips():
    out = _cfg(QEMU_CFG, "qemu", QEMU_MAP)
    first = out.splitlines()[0]
    assert first.startswith("#pve-zfs-tool-recovery origin=100 snapshot=zfs-auto-snap_daily-2026-10-13-0025 created=")
    mk = rg.parse_marker(out)
    assert mk["origin"] == "100" and mk["snapshot"] == "zfs-auto-snap_daily-2026-10-13-0025"
    # PVE re-encodes notes edited in its UI; the marker must survive that
    encoded = out.replace(first, first.replace(" ", "%20"))
    assert rg.parse_marker(encoded)["origin"] == "100"
    assert rg.parse_marker(QEMU_CFG) is None


def test_lxc_keeps_mountpoints_and_loses_its_nic():
    out = _cfg(LXC_CFG, "lxc", {"local-zfs:subvol-253-disk-0": "rpool-clone:subvol-900-disk-0",
                                "local-zfs:subvol-253-disk-1": "rpool-clone:subvol-900-disk-1"})
    assert "rootfs: rpool-clone:subvol-900-disk-0,size=8G" in out
    assert "mp0: rpool-clone:subvol-900-disk-1,mp=/data,size=100G" in out
    assert "hostname: files-recovery" in out
    assert "net0" not in out and "unprivileged: 1" in out and "features: nesting=1" in out
    assert "smbios1" not in out and "vmgenid" not in out


# --- pure: names, commands, snapshots --------------------------------------------

def test_clone_volume_names_keep_the_disk_index_and_demote_templates():
    assert rg.clone_volume_name("rpool/data/vm-100-disk-2", "900") == "vm-900-disk-2"
    assert rg.clone_volume_name("rpool/data/subvol-253-disk-0", "900") == "subvol-900-disk-0"
    assert rg.clone_volume_name("rpool/data/base-9000-disk-0", "900") == "vm-900-disk-0"
    with pytest.raises(ValueError):
        rg.clone_volume_name("rpool/data/other", "900")


def test_clone_commands_carry_the_working_copy_props():
    vm = rg.clone_command("rpool/data/vm-100-disk-0", "s1", "rpool/clone/vm-900-disk-0", "qemu")
    assert vm == ("zfs clone -o com.sun:auto-snapshot=false -o refreservation=none "
                  "rpool/data/vm-100-disk-0@s1 rpool/clone/vm-900-disk-0")
    ct = rg.clone_command("rpool/data/subvol-253-disk-0", "s1", "rpool/clone/subvol-900-disk-0",
                          "lxc", size="8G")
    assert "-o acltype=posixacl -o xattr=sa -o refquota=8G" in ct
    assert "com.sun:auto-snapshot=false" in ct


def test_disk_sizes_come_from_the_config():
    assert rg.disk_sizes(LXC_CFG, "lxc") == {"local-zfs:subvol-253-disk-1": "100G",
                                             "local-zfs:subvol-253-disk-0": "8G"}


def test_common_snapshots_says_which_disk_breaks_the_set():
    out = rg.common_snapshots({"a": ["s3", "s2", "s1"], "b": ["s3", "s1"], "c": ["s3", "s2", "s1"]})
    by = {s["name"]: s for s in out}
    assert [s["name"] for s in out] == ["s3", "s2", "s1"]        # order of the first disk
    assert by["s3"]["on_all"] and by["s1"]["on_all"]
    assert not by["s2"]["on_all"] and by["s2"]["missing"] == ["b"]
    assert rg.common_snapshots({}) == []


def test_storage_id_is_pve_safe():
    assert rg.recovery_storage_id("rpool") == "rpool-clone"
    assert rg.recovery_storage_id("Tank_HDD") == "tank_hdd-clone"     # PVE allows _ . -
    assert rg.recovery_storage_id("tank hdd") == "tank-hdd-clone"
    assert rg.recovery_root("tank") == "tank/clone"


# --- the create job --------------------------------------------------------------

def _fake_host(monkeypatch, *, fail_clone_nr=None, snaps=("daily", "hourly"), running=False,
               next_id="900", storage_cfg="", used=False):
    """A host with VM 100 on rpool/data, two disks, both snapshotted."""
    calls = []
    state = {"clones": 0}

    def run(host, cmd, **kw):
        calls.append(cmd)
        ok = lambda out="": {"success": True, "stdout": out, "stderr": ""}
        if cmd.startswith("cat /etc/pve/qemu-server/100.conf"):
            return ok(QEMU_CFG)
        if cmd.startswith("cat /etc/pve/storage.cfg"):
            return ok(storage_cfg)
        if cmd.startswith("pvesm path"):
            vol = cmd.split("pvesm path ", 1)[1].split()[0].strip("'")
            return ok("/dev/zvol/rpool/data/" + vol.split(":")[1] + "\n")
        if cmd.startswith("zfs list -H -o name,used"):
            ds = cmd.split()[5]
            return ok(f"{ds}\t1G\n")
        if cmd.startswith("zfs list -H -o name -t snapshot -d 1"):
            ds = cmd.split()[9]
            return ok("".join(f"{ds}@{s}\n" for s in snaps))
        if cmd.startswith("zfs list -H -o name "):
            ds = cmd.split()[5]
            return ok(ds + "\n" if ds.startswith("rpool/clone/") and state["clones"] else "")
        if cmd.startswith("pvesh get /cluster/nextid"):
            return ok(next_id + "\n")
        if cmd.startswith("([ -e /etc/pve/qemu-server/"):
            return ok("__USED__" if used else "__FREE__")
        if cmd == "hostname":
            return ok("pve1\n")
        if cmd.startswith("zfs clone"):
            state["clones"] += 1
            if fail_clone_nr == state["clones"]:
                return {"success": False, "stdout": "cannot create: out of space", "stderr": ""}
            return ok()
        if "echo __EXISTS__ || echo __NO__" in cmd:
            return ok("__NO__")
        if "base64 -d >" in cmd:
            return ok("__OK__")
        return ok()

    monkeypatch.setattr(rg, "run_command", run)
    import app.migrate as m
    import app.dr as dr
    monkeypatch.setattr(m, "run_command", run)
    monkeypatch.setattr(dr, "run_command", run)
    return calls


def _progress(*a, **k):
    pass


def test_create_does_the_steps_in_order(monkeypatch):
    calls = _fake_host(monkeypatch)
    res = rg.create_job(_progress, HOST, "100", "qemu", "daily")
    assert res["success"], res
    assert res["new_vmid"] == "900" and res["storage"] == "rpool-clone"
    # config order (efidisk0 before scsi0), one clone per attached volume
    assert res["clones"] == ["rpool/clone/vm-900-disk-1", "rpool/clone/vm-900-disk-0",
                             "rpool/clone/vm-900-disk-2"]
    seq = [c for c in calls if c.startswith(("zfs create", "pvesm add", "zfs hold", "zfs clone",
                                             "zfs release")) or "base64 -d" in c]
    kinds = [c.split()[0] + " " + c.split()[1] if c.startswith("zfs") else c.split()[0]
             for c in seq]
    # root, storage, holds, clones, release, config -- nothing before its prerequisite
    assert kinds[:2] == ["zfs create", "pvesm"]
    assert kinds[2:5] == ["zfs hold"] * 3
    assert kinds[5:8] == ["zfs clone"] * 3
    assert kinds[8:11] == ["zfs release"] * 3
    assert "base64 -d" in seq[-1]
    assert "zfs create -o com.sun:auto-snapshot=false rpool/clone" in seq[0]
    assert "pvesm add zfspool rpool-clone --pool rpool/clone --content images,rootdir --nodes pve1" in seq[1]


def test_create_reuses_an_existing_storage(monkeypatch):
    calls = _fake_host(monkeypatch, storage_cfg="zfspool: rescue\n\tpool rpool/clone\n\tcontent images\n")
    res = rg.create_job(_progress, HOST, "100", "qemu", "daily")
    assert res["success"] and res["storage"] == "rescue"
    assert not any(c.startswith("pvesm add") for c in calls)


def test_a_failed_clone_undoes_the_earlier_ones_and_writes_no_config(monkeypatch):
    calls = _fake_host(monkeypatch, fail_clone_nr=2)
    res = rg.create_job(_progress, HOST, "100", "qemu", "daily")
    assert not res["success"] and "out of space" in res["error"]
    assert res["undone"] == ["rpool/clone/vm-900-disk-1"]       # the first clone, config order
    assert any(c.startswith("zfs destroy rpool/clone/vm-900-disk-1") for c in calls)
    assert not any("base64 -d" in c for c in calls)
    assert sum(c.startswith("zfs release") for c in calls) == 3     # holds never linger


def test_create_refuses_a_label_that_is_not_on_every_disk(monkeypatch):
    calls = _fake_host(monkeypatch, snaps=())
    res = rg.create_job(_progress, HOST, "100", "qemu", "daily")
    assert not res["success"] and "missing on" in res["error"]
    assert not any(c.startswith(("zfs clone", "zfs create", "pvesm add")) for c in calls)


def test_create_refuses_a_used_vmid(monkeypatch):
    calls = _fake_host(monkeypatch, used=True)
    res = rg.create_job(_progress, HOST, "100", "qemu", "daily", new_vmid="105")
    assert not res["success"] and "105" in res["error"]
    assert not any(c.startswith("zfs clone") for c in calls)


# --- the destroy job -------------------------------------------------------------

RECOVERY_CFG = """#pve-zfs-tool-recovery origin=100 snapshot=daily created=2026-10-14T10:00:00
cores: 4
name: web-recovery
onboot: 0
scsi0: rpool-clone:vm-900-disk-0,size=32G
"""


def _fake_recovery_host(monkeypatch, cfg=RECOVERY_CFG, path_prefix="/dev/zvol/rpool/clone/",
                        clones_after=""):
    calls = []

    def run(host, cmd, **kw):
        calls.append(cmd)
        ok = lambda out="": {"success": True, "stdout": out, "stderr": ""}
        if cmd.startswith("cat /etc/pve/qemu-server/900.conf"):
            return ok(cfg)
        if cmd.startswith("pvesm path"):
            vol = cmd.split("pvesm path ", 1)[1].split()[0].strip("'")
            return ok(path_prefix + vol.split(":")[1] + "\n")
        if cmd.startswith("zfs list -H -o name,used"):
            return ok(cmd.split()[5] + "\t1G\n")
        if cmd.startswith("zfs get -H -o value origin"):
            return ok("rpool/data/vm-100-disk-0@daily\n")
        if cmd.startswith("zfs get -H -o value clones"):
            return ok(clones_after + "\n")
        if cmd.startswith("zfs list -H -o name "):
            return ok("")                                   # gone after qm destroy
        if cmd.startswith("qm status"):
            return ok("status: stopped\n")
        return ok()

    monkeypatch.setattr(rg, "run_command", run)
    import app.migrate as m
    monkeypatch.setattr(m, "run_command", run)
    return calls


def test_destroy_removes_the_guest_and_reports_released_origins(monkeypatch):
    calls = _fake_recovery_host(monkeypatch)
    res = rg.destroy_job(_progress, HOST, "900", "qemu")
    assert res["success"], res
    assert any(c.startswith("qm destroy 900 --purge") for c in calls)
    assert res["released"] == ["rpool/data/vm-100-disk-0@daily"] and res["still_held"] == {}


def test_destroy_reports_an_origin_another_clone_still_holds(monkeypatch):
    _fake_recovery_host(monkeypatch, clones_after="rpool/clone/vm-901-disk-0")
    res = rg.destroy_job(_progress, HOST, "900", "qemu")
    assert res["success"] and res["released"] == []
    assert res["still_held"] == {"rpool/data/vm-100-disk-0@daily": ["rpool/clone/vm-901-disk-0"]}


def test_destroy_refuses_a_guest_without_the_marker(monkeypatch):
    calls = _fake_recovery_host(monkeypatch, cfg=RECOVERY_CFG.split("\n", 1)[1])
    res = rg.destroy_job(_progress, HOST, "900", "qemu")
    assert not res["success"] and "not a recovery guest" in res["error"]
    assert not any(c.startswith(("qm destroy", "zfs destroy", "qm stop")) for c in calls)


def test_destroy_refuses_a_marked_guest_whose_disk_is_outside_the_root(monkeypatch):
    # The marker alone is not enough: a disk that lives in rpool/data is a
    # production volume, whatever the config comment says.
    calls = _fake_recovery_host(monkeypatch, path_prefix="/dev/zvol/rpool/data/")
    res = rg.destroy_job(_progress, HOST, "900", "qemu")
    assert not res["success"] and "not under a recovery root" in res["error"]
    assert not any(c.startswith(("qm destroy", "zfs destroy")) for c in calls)


def test_preview_refuses_a_recovery_guest_as_origin(monkeypatch):
    _fake_recovery_host(monkeypatch)
    res = rg.preview(HOST, "900", "qemu")
    assert not res["success"] and "itself a recovery guest" in res["error"]
