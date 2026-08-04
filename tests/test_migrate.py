"""Near-live guest migration: the pure parsing/rewriting/command-building parts.

The risky bits are all here: picking the right disks out of a guest config
(a detached ``unusedN`` volume or a snapshot section must not be migrated),
rewriting the config for the target, and building a send/recv pipeline that
reports a failing ``zfs send`` instead of masking it behind a 0-exit receiver.
"""

import pytest

from app import migrate as m


LXC_CFG = """arch: amd64
hostname: web
memory: 2048
rootfs: local-zfs:subvol-253-disk-0,size=8G
mp0: local-zfs:subvol-253-disk-1,mp=/data,size=100G
net0: name=eth0,bridge=vmbr0,hwaddr=AA:BB:CC:DD:EE:FF,ip=dhcp
unused0: local-zfs:subvol-253-disk-9
lock: backup
[daily-2026-07-25]
rootfs: local-zfs:subvol-253-disk-0,size=8G
mp0: local-zfs:subvol-253-disk-1,mp=/data,size=100G
"""

QEMU_CFG = """boot: order=scsi0
cores: 4
scsi0: local-zfs:vm-100-disk-0,size=32G
scsi1: local-zfs:vm-100-disk-1,size=500G
ide2: local:iso/debian.iso,media=cdrom
efidisk0: local-zfs:vm-100-disk-2,size=4M
net0: virtio=AA:BB:CC:11:22:33,bridge=vmbr1,tag=10
net1: virtio=AA:BB:CC:11:22:34,bridge=vmbr0
unused0: local-zfs:vm-100-disk-8
"""


# --- disk discovery --------------------------------------------------------

def test_lxc_disks_only_attached_volumes():
    disks = m.parse_guest_config_disks(LXC_CFG, "lxc")
    assert disks == [("rootfs", "local-zfs:subvol-253-disk-0"),
                     ("mp0", "local-zfs:subvol-253-disk-1")]


def test_qemu_disks_skip_cdrom_and_unused():
    disks = m.parse_guest_config_disks(QEMU_CFG, "qemu")
    keys = [k for k, _ in disks]
    assert keys == ["scsi0", "scsi1", "efidisk0"]
    assert all("iso" not in v for _, v in disks)


def test_snapshot_section_is_not_scanned():
    # the [daily-...] block repeats rootfs/mp0 -- they must not be counted twice
    assert len(m.parse_guest_config_disks(LXC_CFG, "lxc")) == 2
    assert m.config_snapshot_sections(LXC_CFG) == ["daily-2026-07-25"]
    assert m.config_snapshot_sections(QEMU_CFG) == []


def test_bridges_from_main_section():
    assert m.parse_config_bridges(QEMU_CFG) == ["vmbr0", "vmbr1"]
    assert m.parse_config_bridges(LXC_CFG) == ["vmbr0"]


def test_dataset_from_pvesm_path():
    assert m.dataset_from_pvesm_path("/dev/zvol/rpool/data/vm-100-disk-0\n") == \
        "rpool/data/vm-100-disk-0"
    assert m.dataset_from_pvesm_path("/rpool/data/subvol-253-disk-0") == \
        "rpool/data/subvol-253-disk-0"
    assert m.dataset_from_pvesm_path("") == ""


def test_volume_basename():
    assert m.volume_basename("local-zfs:vm-100-disk-0") == "vm-100-disk-0"
    assert m.volume_basename("rpool/data/subvol-253-disk-1") == "subvol-253-disk-1"


# --- target dataset candidates (the picker) --------------------------------

_DATASETS = [
    {"name": "rpool", "type": "filesystem", "avail": "100G"},
    {"name": "rpool/ROOT", "type": "filesystem", "avail": "100G"},
    {"name": "rpool/ROOT/pve-1", "type": "filesystem", "avail": "100G"},
    {"name": "rpool/data", "type": "filesystem", "avail": "80G"},
    {"name": "rpool/data/subvol-253-disk-0", "type": "filesystem", "avail": "8G"},
    {"name": "rpool/data/vm-100-disk-0", "type": "volume", "avail": "-"},
    {"name": "tank", "type": "filesystem", "avail": "4T"},
    {"name": "tank/data", "type": "filesystem", "avail": "4T"},
]


def test_candidate_roots_exclude_disks_volumes_and_root():
    names = [d["name"] for d in m.candidate_target_roots(_DATASETS)]
    assert "rpool/data/subvol-253-disk-0" not in names   # is a guest disk
    assert "rpool/data/vm-100-disk-0" not in names       # zvol
    assert "rpool/ROOT" not in names                     # PVE root filesystem
    assert "rpool/ROOT/pve-1" not in names
    assert set(names) == {"rpool", "rpool/data", "tank", "tank/data"}


def test_candidate_roots_offer_data_first():
    names = [d["name"] for d in m.candidate_target_roots(_DATASETS)]
    assert names[0].endswith("/data")


def test_candidate_roots_keep_avail_and_handle_empty():
    got = m.candidate_target_roots(_DATASETS)
    assert {"name": "tank/data", "avail": "4T"} in got
    assert m.candidate_target_roots([]) == []
    assert m.candidate_target_roots(None) == []


# --- storage.cfg: the cross-datastore trap ---------------------------------

STORAGE_CFG = """dir: local
\tpath /var/lib/vz
\tcontent iso,vztmpl,backup

zfspool: local-zfs
\tpool rpool/data
\tcontent images,rootdir
\tsparse 1

zfspool: tank-zfs
\tpool tank/data
\tcontent images,rootdir

zfspool: backup-only
\tpool tank/backup
\tcontent backup

zfspool: other-node
\tpool tank/data
\tcontent images
\tnodes pve99

lvmthin: vmstore
\tthinpool data
\tcontent images
"""


def test_parse_zfs_storages_only_zfspool():
    st = m.parse_zfs_storages(STORAGE_CFG)
    assert [s["storage"] for s in st] == ["local-zfs", "tank-zfs", "backup-only", "other-node"]
    assert st[0]["pool"] == "rpool/data"
    assert st[1]["pool"] == "tank/data"
    assert st[0]["content"] == ["images", "rootdir"]
    assert st[3]["nodes"] == ["pve99"]


def test_usable_storages_filter_content_and_node():
    st = m.usable_guest_storages(m.parse_zfs_storages(STORAGE_CFG), node="pve250")
    names = [s["storage"] for s in st]
    assert "backup-only" not in names      # cannot hold guest disks
    assert "other-node" not in names       # restricted to another node
    assert names == ["local-zfs", "tank-zfs"]


def test_storage_match_accepts_matching_pair():
    st = m.usable_guest_storages(m.parse_zfs_storages(STORAGE_CFG), "pve250")
    ok, detail, _ = m.check_storage_match(st, "tank-zfs", "tank/data")
    assert ok and "tank/data" in detail


def test_storage_match_rejects_mismatch():
    # THE trap: disks go to tank/data but the config would point at rpool/data
    st = m.usable_guest_storages(m.parse_zfs_storages(STORAGE_CFG), "pve250")
    ok, detail, sugg = m.check_storage_match(st, "local-zfs", "tank/data")
    assert not ok
    assert "rpool/data" in detail and "tank/data" in detail
    assert sugg == ["tank-zfs"]


def test_storage_match_unknown_storage():
    st = m.usable_guest_storages(m.parse_zfs_storages(STORAGE_CFG), "pve250")
    ok, detail, _ = m.check_storage_match(st, "nope", "tank/data")
    assert not ok and "does not exist" in detail


def test_storage_match_suggests_when_none_picked():
    st = m.usable_guest_storages(m.parse_zfs_storages(STORAGE_CFG), "pve250")
    ok, _, sugg = m.check_storage_match(st, "", "rpool/data")
    assert not ok and sugg == ["local-zfs"]


def test_storage_match_no_storage_writes_there():
    st = m.usable_guest_storages(m.parse_zfs_storages(STORAGE_CFG), "pve250")
    ok, detail, sugg = m.check_storage_match(st, "", "rpool/elsewhere")
    assert not ok and sugg == [] and "create one" in detail


def test_suggest_storage_id_from_dataset():
    assert m.suggest_storage_id("rpool/data2") == "data2"
    assert m.suggest_storage_id("tank/guests") == "guests"


def test_suggest_storage_id_is_unique():
    assert m.suggest_storage_id("rpool/data", ["data"]) == "data-2"
    assert m.suggest_storage_id("rpool/data", ["data", "data-2"]) == "data-3"


def test_suggest_storage_id_sanitises():
    # PVE ids must start with a letter and use a restricted charset
    assert m.suggest_storage_id("rpool/2fast") .startswith("zfs-")
    assert m.suggest_storage_id("rpool/My_Data") == "my_data"
    assert m._STORAGE_ID_RE.match(m.suggest_storage_id("rpool/2fast"))


def test_pvesm_add_pins_to_the_node():
    cmd = m.build_pvesm_add("data2", "rpool/data2", "pve250")
    assert cmd.startswith("pvesm add zfspool data2 ")
    assert "--pool rpool/data2" in cmd
    assert "--content images,rootdir" in cmd
    assert "--nodes pve250" in cmd          # storage.cfg is cluster-wide
    assert cmd.rstrip().endswith("echo __exit=$?")


def test_pvesm_add_without_node():
    assert "--nodes" not in m.build_pvesm_add("data2", "rpool/data2", "")


def test_create_storage_rejects_bad_input(monkeypatch):
    monkeypatch.setattr(m, "run_command", lambda *a, **k: {"success": True, "stdout": ""})
    assert m.create_zfs_storage({}, "1bad", "rpool/data2")["success"] is False
    assert m.create_zfs_storage({}, "ok", "rpool/../etc")["success"] is False


def test_create_storage_refuses_missing_dataset(monkeypatch):
    monkeypatch.setattr(m, "dataset_exists", lambda h, d: False)
    res = m.create_zfs_storage({}, "data2", "rpool/data2")
    assert res["success"] is False and "does not exist" in res["error"]


def test_config_storage_ids():
    assert m.config_storage_ids(LXC_CFG, "lxc") == ["local-zfs"]
    assert m.config_storage_ids(QEMU_CFG, "qemu") == ["local-zfs"]


# --- config rewrite --------------------------------------------------------

def test_rewrite_storage_and_bridge():
    out = m.rewrite_guest_config(QEMU_CFG, {"local-zfs": "tank"}, {"vmbr1": "vmbr9"})
    assert "tank:vm-100-disk-0" in out
    assert "bridge=vmbr9,tag=10" in out
    assert "bridge=vmbr0" in out            # untouched bridge stays
    assert "local:iso/debian.iso" in out    # different storage ID untouched


def test_rewrite_drops_lock():
    out = m.rewrite_guest_config(LXC_CFG)
    assert "lock:" not in out
    assert "hostname: web" in out


def test_rewrite_vmid_renames_volumes():
    out = m.rewrite_guest_config(LXC_CFG, old_vmid="253", new_vmid="300")
    assert "subvol-300-disk-0" in out
    assert "subvol-253-disk" not in out


def test_rewrite_vmid_noop_when_equal():
    out = m.rewrite_guest_config(LXC_CFG, old_vmid="253", new_vmid="253")
    assert "subvol-253-disk-0" in out


def test_rewrite_without_maps_is_lossless_except_lock():
    out = m.rewrite_guest_config(QEMU_CFG)
    for line in QEMU_CFG.strip().splitlines():
        assert line in out


# --- snapshot naming -------------------------------------------------------

def test_snapshot_name_shape():
    name = m.make_snapshot_name(1_777_120_000)
    assert m._SNAP_NAME_RE.match(name), name


# --- send/recv command building -------------------------------------------

def _pull_cmd(base=None):
    return m.build_send_recv("10.0.0.1", "root", 22, "10.0.0.2", "root", 22,
                             "rpool/data/vm-100-disk-0@migrate-1",
                             "rpool/data/vm-100-disk-0", base, pull=True)


def test_pipeline_has_pipefail_and_exit_marker():
    cmd = _pull_cmd()
    assert cmd.startswith("set -o pipefail")
    assert cmd.rstrip().endswith("echo __exit=$?")


def test_pull_runs_send_remotely_and_recv_locally():
    cmd = _pull_cmd()
    assert "ssh " in cmd.split("|")[0]          # send side is the remote one
    assert cmd.split("|")[1].strip().startswith("zfs recv -F")


def test_push_runs_send_locally_and_recv_remotely():
    cmd = m.build_send_recv("10.0.0.1", "root", 22, "10.0.0.2", "root", 22,
                            "rpool/data/vm-100-disk-0@migrate-1",
                            "rpool/data/vm-100-disk-0", None, pull=False)
    assert cmd.split("|")[0].strip().startswith("set -o pipefail 2>/dev/null; zfs send")
    assert "ssh " in cmd.split("|")[1]


def test_incremental_flag_only_with_base():
    assert " -i " in _pull_cmd("migrate-0")
    assert " -i " not in _pull_cmd(None)


# --- leftover migration snapshots -----------------------------------------

def _snaps(ds, *names):
    return [{"dataset": ds, "snapshot": n, "full": f"{ds}@{n}", "used": "1M"}
            for n in names]


def test_recognises_only_migration_snapshots():
    assert m.is_migration_snapshot("migrate-20260804-101319")
    assert not m.is_migration_snapshot("zfs-auto-snap_daily-2026-08-04-0425")
    assert not m.is_migration_snapshot("migrate-nope")
    assert not m.is_migration_snapshot("")


def test_delete_all_when_not_keeping():
    s = _snaps("rpool/data/subvol-253-disk-0", "migrate-20260804-101319",
               "migrate-20260804-120000")
    assert len(m.select_snapshots_to_delete(s)) == 2


def test_keep_latest_spares_the_newest_per_dataset():
    s = (_snaps("a/x", "migrate-20260804-101319", "migrate-20260804-120000")
         + _snaps("a/y", "migrate-20260804-090000"))
    todo = m.select_snapshots_to_delete(s, keep_latest=True)
    names = {(x["dataset"], x["snapshot"]) for x in todo}
    assert ("a/x", "migrate-20260804-101319") in names   # older one goes
    assert ("a/x", "migrate-20260804-120000") not in names  # newest survives
    assert ("a/y", "migrate-20260804-090000") not in names  # only one -> survives


def test_select_handles_empty():
    assert m.select_snapshots_to_delete([]) == []
    assert m.select_snapshots_to_delete(None, keep_latest=True) == []


def test_cleanup_refuses_while_a_migration_runs(monkeypatch):
    import app.tasks as tasks
    monkeypatch.setattr(tasks, "running_tasks",
                        lambda prefix="": [{"name": "guest-migration-precopy"}])
    res = m.cleanup_migration_snapshots({}, {}, [{"source_dataset": "a/x",
                                                  "target_dataset": "b/x"}])
    assert res["success"] is False
    assert "still running" in res["error"]


def test_exit_marker_parsing():
    assert m.parse_exit_marker("blah\n__exit=0") == 0
    assert m.parse_exit_marker("boom\n__exit=1\n") == 1
    assert m.parse_exit_marker("no marker") is None
