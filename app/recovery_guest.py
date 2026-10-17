"""Recovery guest from a snapshot.

The aow.de pattern behind the clone function: bring a whole VM or container
up as it was at one point in time, isolated, next to the live one -- to pull
a file out, to check whether a backup actually boots, to look at a system
before it was ransomed. By hand that is one ``zfs clone`` per disk, a storage
entry so PVE can address the clones, and a config copied and edited line by
line. This module does the same steps, in the same order, and nothing else.

What it deliberately is not: a copy. The clones share blocks with the origin
snapshot and pin it (a snapshot with a clone cannot be destroyed, and
zfs-auto-snapshot cannot prune it) until the recovery guest is cleaned up.
That is why cleanup is a first-class action here and reports whether the
origin snapshots are free again.

Rules that shape the code:

* The recovery guest gets **no network interface** and ``onboot: 0``. It is a
  second copy of a machine that is still running; on the same bridge it would
  fight the original for its IP and its hostname. Whoever needs network adds
  it on purpose, in the PVE UI, after thinking about it.
* It is **not started**. Creating is reversible, starting may not be (a
  database replaying its journal, a mail server fetching mail). The human
  starts it.
* Clones live under ``<pool>/clone``, the convention of the article, on the
  **same pool** as the origin -- a clone cannot cross pools.
* Cleanup destroys **only** a guest that carries this module's marker in its
  config and whose disks all live under the recovery root. Any other guest
  is refused, however it is named.
"""

from __future__ import annotations

import re
import shlex
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote

from app.migrate import (discover_guest_disks, parse_guest_config_disks,
                         parse_zfs_storages, read_guest_config,
                         split_main_section, vmid_in_use, volume_basename)
from app.ssh_manager import run_command
from app.tasks import start_task
from app.validators import validate_vmid, validate_vm_type
from app.zfs_commands import CLONE_PROPS

# The marker was "pve-zfs-tool-recovery" in v0.9.931; guests made then carry
# it and must still be recognised by cleanup. New guests get the clone wording.
MARKER = "pve-zfs-tool-clone"
LEGACY_MARKERS = ("pve-zfs-tool-recovery",)
RECOVERY_LEAF = "clone"
HOLD_TAG = "pve-zfs-tool-recovery"

# Lines that must not travel from the origin config into the recovery guest.
#   net*      -- no NIC, see the module docstring
#   lock      -- the copy must not start out locked (backup/migrate/...)
#   parent    -- points at a PVE snapshot the copy does not have
#   onboot    -- rewritten to 0 below
#   startup   -- HA start order / delay is the original's business
#   hookscript-- runs arbitrary code on start/stop, written for the original
#   unused*   -- detached volumes are not cloned, so a reference would dangle
#   vmgenid, smbios1 -- regenerated, so Windows/AD see a new machine
#   template  -- a clone of a template is a VM, not a template
#   protection-- would make the copy undeletable and block its own cleanup
_DROP_RE = re.compile(r"^(net\d+|lock|parent|onboot|startup|hookscript|unused\d+"
                      r"|vmgenid|smbios1|template|protection):")
_STORAGE_ID_SAFE = re.compile(r"[^a-z0-9._-]")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def recovery_root(pool: str) -> str:
    return f"{pool}/{RECOVERY_LEAF}"


def recovery_storage_id(pool: str) -> str:
    """PVE storage ID for a pool's recovery root: ``rpool`` -> ``rpool-clone``."""
    sid = _STORAGE_ID_SAFE.sub("-", f"{pool}-{RECOVERY_LEAF}".lower()).strip("-.")
    if not sid[:1].isalpha():
        sid = "zfs-" + sid
    return sid[:63]


def common_snapshots(per_disk: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    """Snapshot labels across a guest's disks, in the order of the first disk.

    A recovery guest needs ONE point in time, so a label is usable only when
    every disk has it. Labels that exist on some disks are returned too, with
    the disks they are missing on -- the UI shows them greyed out and says
    which disk breaks the set, instead of hiding them.
    """
    datasets = list(per_disk)
    if not datasets:
        return []
    seen: List[str] = []
    for ds in datasets:
        for s in per_disk[ds]:
            if s not in seen:
                seen.append(s)
    out = []
    for s in seen:
        missing = [ds for ds in datasets if s not in per_disk[ds]]
        out.append({"name": s, "on_all": not missing, "missing": missing})
    return out


def clone_volume_name(dataset: str, new_vmid: str) -> str:
    """``rpool/data/vm-100-disk-2`` -> ``vm-900-disk-2``.

    Template volumes (``base-``/``basevol-``) become ordinary ones: the clone
    is a machine, not a template. The disk index is kept so the config's
    disk-N references still line up.
    """
    base = volume_basename(dataset)
    m = re.match(r"^(vm|subvol|base|basevol)-\d+-(disk-\d+)$", base)
    if not m:
        raise ValueError(f"not a PVE guest volume: {dataset}")
    kind = {"base": "vm", "basevol": "subvol"}.get(m.group(1), m.group(1))
    return f"{kind}-{new_vmid}-{m.group(2)}"


def _size_from_value(value: str) -> str:
    m = re.search(r"(?:^|,)size=([0-9]+[KMGTP]?)(?:,|$)", value or "")
    return m.group(1) if m else ""


def disk_sizes(text: str, gtype: str) -> Dict[str, str]:
    """``{volid: size}`` from the config, for the refquota of subvol clones."""
    out = {}
    for line in split_main_section(text).splitlines():
        m = re.match(r"^([a-z]+\d*):\s*(.+)$", line.strip())
        if not m:
            continue
        volid = m.group(2).split(",", 1)[0].strip()
        size = _size_from_value(m.group(2))
        if volid and size:
            out[volid] = size
    return out


def clone_command(dataset: str, snapshot: str, target: str, gtype: str,
                  size: str = "") -> str:
    """One ``zfs clone`` with the working-copy properties every clone gets.

    A container volume additionally gets what PVE sets on every subvol it
    creates (posix ACLs, xattr=sa) -- those are local properties of the
    origin dataset, and a clone inherits from its NEW parent, not from the
    origin, so without them a rootfs with ACLs would come up without them.
    refquota likewise: it is what PVE means by the volume's ``size=``.
    """
    props = CLONE_PROPS
    if gtype == "lxc":
        props += " -o acltype=posixacl -o xattr=sa"
        if size:
            props += f" -o refquota={shlex.quote(size)}"
    return (f"zfs clone {props} {shlex.quote(dataset + '@' + snapshot)} "
            f"{shlex.quote(target)}")


# Config entries that point at the HOST, not at a volume: a bind mount into a
# directory of the host, a passthrough disk, PCI or USB device, a host serial
# port. Copied 1:1 they would make the clone write into the original's live
# data (a bind-mounted upload directory, with the clone's older database) or
# fight the original for a device. Field report: Immich, Plex, Syncthing
# containers with bind mounts. These are left out of the clone and shown in
# the dialog before anything is created.
_QEMU_DISK_KEY = re.compile(r"^(?:scsi|virtio|sata|ide)\d+$")


def host_bound_entries(text: str, gtype: str) -> List[Dict[str, str]]:
    """``[{key, value, reason}]`` -- reason is bind_mount, passthrough or device."""
    out: List[Dict[str, str]] = []
    for line in split_main_section(text).splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if gtype == "lxc" and s.startswith("lxc.mount.entry:"):
            out.append({"key": "lxc.mount.entry", "value": s.split(":", 1)[1].strip(),
                        "reason": "bind_mount"})
            continue
        if gtype == "lxc" and re.match(r"^lxc\.cgroup2?\.devices\.allow:", s):
            out.append({"key": s.split(":", 1)[0], "value": s.split(":", 1)[1].strip(),
                        "reason": "device"})
            continue
        m = re.match(r"^([a-z]+\d*):\s*(.*)$", s)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        first = value.split(",", 1)[0].strip()
        if gtype == "lxc":
            if re.match(r"^mp\d+$", key) and ":" not in first:
                out.append({"key": key, "value": value, "reason": "bind_mount"})
            elif re.match(r"^dev\d+$", key):
                out.append({"key": key, "value": value, "reason": "device"})
        else:
            if re.match(r"^hostpci\d+$", key):
                out.append({"key": key, "value": value, "reason": "passthrough"})
            elif re.match(r"^usb\d+$", key) and re.search(r"(^|,)(host|mapping)=", value):
                out.append({"key": key, "value": value, "reason": "passthrough"})
            elif _QEMU_DISK_KEY.match(key) and first.startswith("/") and "media=cdrom" not in value:
                out.append({"key": key, "value": value, "reason": "passthrough"})
            elif re.match(r"^(serial|parallel)\d+$", key) and first.startswith("/dev/"):
                out.append({"key": key, "value": value, "reason": "device"})
    return out


def marker_line(origin_vmid: str, snapshot: str, now: Optional[float] = None) -> str:
    """The config comment that makes a guest recognisable as a recovery guest.

    PVE keeps ``#`` lines as the guest's description, so this shows up in
    the Notes panel of the PVE UI -- a human reads what this guest is, and
    cleanup reads the same line to decide whether it may destroy it.
    """
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now or time.time()))
    return f"#{MARKER} origin={origin_vmid} snapshot={snapshot} created={ts}"


def parse_marker(text: str) -> Optional[Dict[str, str]]:
    """The marker's fields from a config, or None if it is not a recovery guest.

    PVE re-encodes description lines when the notes are edited in its UI
    (spaces become ``%20``), so the line is URL-decoded before matching.
    """
    for line in (text or "").splitlines():
        if not line.startswith("#"):
            continue
        dec = unquote(line[1:])
        if not dec.startswith((MARKER,) + LEGACY_MARKERS):
            continue
        fields = dict(re.findall(r"(\w+)=(\S+)", dec))
        return {"origin": fields.get("origin", ""), "snapshot": fields.get("snapshot", ""),
                "created": fields.get("created", "")}
    return None


def build_recovery_config(text: str, gtype: str, new_vmid: str,
                          volid_map: Dict[str, str], origin_vmid: str,
                          snapshot: str, now: Optional[float] = None) -> str:
    """The recovery guest's config from the origin's.

    Hardware is kept verbatim (BIOS, machine type, controllers, CPU, memory,
    mount points, features); the lines in ``_DROP_RE`` are left out; every
    attached volume is pointed at its clone via ``volid_map``; PVE snapshot
    sections are not carried over (the clone has none of them).
    """
    out = [marker_line(origin_vmid, snapshot, now)]
    left_out = {(e["key"], e["value"]) for e in host_bound_entries(text, gtype)}
    for line in split_main_section(text).splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if _DROP_RE.match(s):
            continue
        k, _, v = s.partition(":")
        if (k.strip(), v.strip()) in left_out:
            continue
        m = re.match(r"^([a-z]+\d*):\s*(.*)$", s)
        if m:
            key, value = m.group(1), m.group(2)
            volid = value.split(",", 1)[0].strip()
            if volid in volid_map:
                rest = value[len(volid):]
                out.append(f"{key}: {volid_map[volid]}{rest}")
                continue
            if key == "name" and gtype != "lxc":
                out.append(f"name: {value.strip()}-clone")
                continue
            if key == "hostname" and gtype == "lxc":
                out.append(f"hostname: {value.strip()}-clone")
                continue
        out.append(s)
    out.append("onboot: 0")
    if gtype != "lxc":
        out.append(f"smbios1: uuid={uuid.uuid4()}")
        out.append(f"vmgenid: {uuid.uuid4()}")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# SSH readers
# ---------------------------------------------------------------------------

def _snapshots_of(host, dataset: str) -> List[str]:
    """Snapshot labels of one dataset, newest first."""
    r = run_command(host, f"zfs list -H -o name -t snapshot -d 1 {shlex.quote(dataset)} "
                          f"-S creation 2>/dev/null", timeout=20)
    out = []
    for ln in (r.get("stdout") or "").splitlines():
        if "@" in ln:
            out.append(ln.strip().split("@", 1)[1])
    return out


def _next_vmid(host) -> str:
    r = run_command(host, "pvesh get /cluster/nextid 2>/dev/null", timeout=15)
    m = re.search(r"\b(\d{3,})\b", r.get("stdout") or "") if r.get("success") else None
    return m.group(1) if m else ""


def suggest_vmid(host, vmid: str) -> str:
    """``9`` + the original's VMID (2015 -> 92015) when that is free and within
    PVE's range, so the clone sorts next to its original; else the next free
    ID. Field convention, reported as what people type anyway."""
    cand = "9" + str(vmid)
    if len(cand) <= 9 and not vmid_in_use(host, cand):
        return cand
    return _next_vmid(host)


def _node_name(host) -> str:
    r = run_command(host, "hostname", timeout=10)
    return (r.get("stdout") or "").strip().split(".")[0] if r.get("success") else ""


def _storage_for_root(host, root: str) -> str:
    """ID of a zfspool storage whose pool is exactly ``root``, or ""."""
    r = run_command(host, "cat /etc/pve/storage.cfg 2>/dev/null", timeout=15)
    if not r.get("success"):
        return ""
    for s in parse_zfs_storages(r.get("stdout") or ""):
        if s.get("pool") == root:
            return s["storage"]
    return ""


def _dataset_exists(host, ds: str) -> bool:
    r = run_command(host, f"zfs list -H -o name {shlex.quote(ds)} 2>/dev/null", timeout=15)
    return (r.get("stdout") or "").strip() == ds


def _guest_name(text: str, gtype: str) -> str:
    key = "hostname" if gtype == "lxc" else "name"
    m = re.search(rf"^{key}:\s*(.+)$", split_main_section(text), re.M)
    return m.group(1).strip() if m else ""


def preview(host: Dict[str, Any], vmid: str, gtype: str) -> Dict[str, Any]:
    """Everything the dialog shows before anything is created.

    Disks and their datasets, the snapshot labels and which of them exist on
    every disk, the recovery root and storage (existing or to be created),
    a free VMID proposal, and the reasons -- if any -- this guest cannot be
    recovered this way (disks on different pools, an unresolvable volume).
    """
    try:
        vmid = validate_vmid(vmid)
        gtype = validate_vm_type(gtype)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    cfg = read_guest_config(host, vmid, gtype)
    if not cfg.get("success"):
        return {"success": False, "error": cfg.get("error", "no guest config")}
    if parse_marker(cfg["content"]):
        return {"success": False, "error": "this guest is itself a snapshot clone"}
    disc = discover_guest_disks(host, vmid, gtype, cfg["content"])
    disks = disc.get("disks") or []
    blockers: List[str] = []
    if disc.get("unresolved"):
        blockers.append("unresolved: " + ", ".join(
            f"{u['key']}={u['volid']}" for u in disc["unresolved"]))
    if not disks:
        blockers.append("no disks")
    pools = sorted({d["dataset"].split("/")[0] for d in disks})
    if len(pools) > 1:
        blockers.append("cross_pool: " + ", ".join(pools))
    pool = pools[0] if pools else ""
    root = recovery_root(pool) if pool else ""
    per_disk = {d["dataset"]: _snapshots_of(host, d["dataset"]) for d in disks}
    snaps = common_snapshots(per_disk)
    storage = _storage_for_root(host, root) if root else ""
    return {
        "success": True, "vmid": vmid, "gtype": gtype,
        "name": _guest_name(cfg["content"], gtype),
        "disks": disks, "pool": pool, "root": root,
        "root_exists": _dataset_exists(host, root) if root else False,
        "storage": storage or (recovery_storage_id(pool) if pool else ""),
        "storage_exists": bool(storage),
        "snapshots": snaps,
        "usable": [s["name"] for s in snaps if s["on_all"]],
        "next_vmid": suggest_vmid(host, vmid),
        "left_out": host_bound_entries(cfg["content"], gtype),
        "blockers": blockers,
    }


def list_recovery_guests(host: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Guests on the host that carry the marker -- for the badge and the
    cleanup button. One grep over both config directories."""
    pattern = "|".join(re.escape(m) for m in (MARKER,) + LEGACY_MARKERS)
    r = run_command(host, f"grep -lE {shlex.quote(pattern)} /etc/pve/qemu-server/*.conf "
                          f"/etc/pve/lxc/*.conf 2>/dev/null", timeout=15)
    out = []
    for path in (r.get("stdout") or "").split():
        m = re.match(r"^/etc/pve/(qemu-server|lxc)/(\d+)\.conf$", path)
        if not m:
            continue
        gtype = "lxc" if m.group(1) == "lxc" else "qemu"
        cfg = run_command(host, f"cat {shlex.quote(path)} 2>/dev/null", timeout=10)
        mk = parse_marker(cfg.get("stdout") or "") if cfg.get("success") else None
        if not mk:
            continue
        out.append({"vmid": m.group(2), "gtype": gtype, **mk})
    return out


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def _run(host, cmd: str, timeout: int = 60) -> Dict[str, Any]:
    r = run_command(host, cmd, timeout=timeout)
    return r if isinstance(r, dict) else {"success": False, "stderr": "no result"}


def _destroy_quietly(host, datasets: List[str]) -> None:
    for ds in reversed(datasets):
        _run(host, f"zfs destroy {shlex.quote(ds)} 2>&1")


def _holds_left(host, snaps: List[str]) -> List[str]:
    """The snapshots that still carry our hold tag."""
    left = []
    for s in snaps:
        r = _run(host, f"zfs holds -H {shlex.quote(s)} 2>/dev/null")
        tags = [ln.split("\t")[1] for ln in (r.get("stdout") or "").splitlines()
                if ln.count("\t") >= 1]
        if HOLD_TAG in tags:
            left.append(s)
    return left


def _release_holds(host, snaps: List[str]) -> List[str]:
    """Release our hold on every snapshot and return the ones still held.

    A release that fails silently would pin the origin forever, invisible
    until someone wonders why a snapshot never prunes -- so the result is
    verified with ``zfs holds`` and reported, not assumed.
    """
    for s in snaps:
        _run(host, f"zfs release {HOLD_TAG} {shlex.quote(s)} 2>/dev/null")
    return _holds_left(host, snaps)


# 2. The recovery root must not inherit a replication tag. A bashclub:* user
# property set on the pool root (seen in the field: bashclub:<tag>=subvols
# directly on tank) is inherited by <pool>/clone, and the next zsync pull
# would replicate every clone as a full copy. Every bashclub:* property the
# root sees is set to "exclude" -- upstream's own word for "not this one".
def _exclude_replication_tags(host, root: str) -> List[str]:
    r = _run(host, f"zfs get -H -o property,value,source all {shlex.quote(root)} 2>/dev/null")
    done = []
    for ln in (r.get("stdout") or "").splitlines():
        parts = ln.split("\t")
        if len(parts) < 2 or not parts[0].startswith("bashclub:"):
            continue
        if parts[1] == "exclude":
            continue
        if _run(host, f"zfs set {shlex.quote(parts[0])}=exclude {shlex.quote(root)} 2>&1").get("success"):
            done.append(parts[0])
    return done


def create_job(progress: Callable, host: Dict[str, Any], vmid: str, gtype: str,
               snapshot: str, new_vmid: str = "") -> Dict[str, Any]:
    """The whole creation, in order; every failure undoes what was created.

    Holds are taken on the origin snapshots before the first clone and
    released after the last one: in between, zfs-auto-snapshot could prune a
    snapshot that only later disks would have cloned. Once every clone
    exists, the clones themselves pin their origins and the holds would
    only be one more thing to forget at cleanup.
    """
    vmid = validate_vmid(vmid)
    gtype = validate_vm_type(gtype)
    snapshot = (snapshot or "").strip()
    if not snapshot or not re.match(r"^[A-Za-z0-9_.:-]+$", snapshot):
        return {"success": False, "error": "invalid snapshot label"}

    cfg = read_guest_config(host, vmid, gtype)
    if not cfg.get("success"):
        return {"success": False, "error": cfg.get("error", "no guest config")}
    if parse_marker(cfg["content"]):
        return {"success": False, "error": "this guest is itself a snapshot clone"}
    disc = discover_guest_disks(host, vmid, gtype, cfg["content"])
    if not disc.get("success") or not disc.get("disks"):
        return {"success": False, "error": "not every disk resolves to a ZFS dataset: "
                + ", ".join(u["volid"] for u in disc.get("unresolved") or []) }
    disks = disc["disks"]
    pools = sorted({d["dataset"].split("/")[0] for d in disks})
    if len(pools) > 1:
        return {"success": False, "error": "disks on different pools (" + ", ".join(pools)
                + ") -- a clone cannot cross pools"}
    pool = pools[0]
    root = recovery_root(pool)

    # The point in time must exist on every disk, checked now, not trusted
    # from the preview: a prune may have run in between.
    for d in disks:
        if snapshot not in _snapshots_of(host, d["dataset"]):
            return {"success": False, "error": f"snapshot {snapshot} missing on {d['dataset']}"}

    new_vmid = str(new_vmid or "").strip() or suggest_vmid(host, vmid)
    if not new_vmid:
        return {"success": False, "error": "no free VMID could be determined"}
    new_vmid = validate_vmid(new_vmid)
    if new_vmid == vmid or vmid_in_use(host, new_vmid):
        return {"success": False, "error": f"VMID {new_vmid} is in use"}

    # 1. recovery root
    if not _dataset_exists(host, root):
        progress(f"Creating {root} …")
        r = _run(host, f"zfs create -o com.sun:auto-snapshot=false {shlex.quote(root)} 2>&1")
        if not r.get("success"):
            return {"success": False, "error": f"zfs create {root} failed: "
                    + (r.get("stderr") or r.get("stdout") or "")[:300]}
    excluded = _exclude_replication_tags(host, root)
    if excluded:
        progress(f"Excluded {root} from replication: " + ", ".join(excluded))

    # 2. storage entry, so PVE can address the clones
    storage = _storage_for_root(host, root)
    if not storage:
        storage = recovery_storage_id(pool)
        node = _node_name(host)
        cmd = (f"pvesm add zfspool {shlex.quote(storage)} --pool {shlex.quote(root)} "
               f"--content images,rootdir")
        if node:
            cmd += f" --nodes {shlex.quote(node)}"
        progress(f"Registering storage {storage} for {root} …")
        r = _run(host, cmd + " 2>&1")
        if not r.get("success"):
            return {"success": False, "error": f"pvesm add {storage} failed: "
                    + (r.get("stderr") or r.get("stdout") or "")[:300]}

    # 3. clones, under holds
    sizes = disk_sizes(cfg["content"], gtype)
    origins = [f"{d['dataset']}@{snapshot}" for d in disks]
    for o in origins:
        _run(host, f"zfs hold {HOLD_TAG} {shlex.quote(o)} 2>/dev/null")
    created: List[str] = []
    volid_map: Dict[str, str] = {}
    try:
        for d in disks:
            target = f"{root}/{clone_volume_name(d['dataset'], new_vmid)}"
            progress(f"Cloning {d['dataset']}@{snapshot} -> {target}")
            r = _run(host, clone_command(d["dataset"], snapshot, target, gtype,
                                         sizes.get(d["volid"], "")) + " 2>&1", timeout=120)
            if not r.get("success"):
                _destroy_quietly(host, created)
                return {"success": False, "error": f"zfs clone {target} failed: "
                        + (r.get("stderr") or r.get("stdout") or "")[:300],
                        "undone": created}
            created.append(target)
            volid_map[d["volid"]] = f"{storage}:{target.rsplit('/', 1)[-1]}"
    finally:
        holds_left = _release_holds(host, origins)
        if holds_left:
            progress("Hold could not be released on: " + ", ".join(holds_left), ok=False)

    # 4. config -- never over an existing one
    from app.dr import restore_guest_config
    text = build_recovery_config(cfg["content"], gtype, new_vmid, volid_map, vmid, snapshot)
    progress(f"Writing config for VMID {new_vmid} …")
    wr = restore_guest_config(host, gtype, new_vmid, text, force=False)
    if not wr.get("success"):
        _destroy_quietly(host, created)
        return {"success": False, "error": "config write failed: "
                + (wr.get("error") or wr.get("stderr") or ""), "undone": created}

    progress(f"Snapshot clone {new_vmid} is ready -- not started, no network", ok=True)
    return {"success": True, "new_vmid": new_vmid, "gtype": gtype, "origin_vmid": vmid,
            "snapshot": snapshot, "storage": storage, "clones": created,
            "config": wr.get("dest", ""), "holds_left": holds_left,
            "left_out": host_bound_entries(cfg["content"], gtype),
            "tags_excluded": excluded}


def create_async(host: Dict[str, Any], vmid: str, gtype: str, snapshot: str,
                 new_vmid: str = "") -> str:
    return start_task("recovery-guest-create", create_job, host, str(vmid), gtype,
                      snapshot, str(new_vmid or ""), prefix="recovery")


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def _origin_of(host, clone_ds: str) -> str:
    r = _run(host, f"zfs get -H -o value origin {shlex.quote(clone_ds)} 2>/dev/null")
    v = (r.get("stdout") or "").strip()
    return v if r.get("success") and v and v != "-" else ""


def _clones_of(host, snap: str) -> List[str]:
    r = _run(host, f"zfs get -H -o value clones {shlex.quote(snap)} 2>/dev/null")
    v = (r.get("stdout") or "").strip()
    return [c for c in v.split(",") if c and c != "-"] if r.get("success") else []


def destroy_job(progress: Callable, host: Dict[str, Any], vmid: str,
                gtype: str) -> Dict[str, Any]:
    """Destroy a recovery guest and its clones, then report on the origins.

    Two proofs before anything is destroyed: the config carries the marker,
    and every attached disk lives under the recovery root. A guest that
    fails either test is not touched -- a VMID typed wrong must not take a
    production guest with it.
    """
    vmid = validate_vmid(vmid)
    gtype = validate_vm_type(gtype)
    cfg = read_guest_config(host, vmid, gtype)
    if not cfg.get("success"):
        return {"success": False, "error": cfg.get("error", "no guest config")}
    mk = parse_marker(cfg["content"])
    if not mk:
        return {"success": False, "error": f"VMID {vmid} is not a snapshot clone "
                                           f"(no {MARKER} marker) -- refusing"}
    disc = discover_guest_disks(host, vmid, gtype, cfg["content"])
    disks = disc.get("disks") or []
    if disc.get("unresolved"):
        return {"success": False, "error": "a disk does not resolve: "
                + ", ".join(u["volid"] for u in disc["unresolved"]) + " -- refusing"}
    for d in disks:
        parts = d["dataset"].split("/")
        if len(parts) != 3 or parts[1] != RECOVERY_LEAF:
            return {"success": False, "error": f"{d['dataset']} is not under a recovery "
                                               f"root -- refusing"}
    origins = [o for o in (_origin_of(host, d["dataset"]) for d in disks) if o]

    tool = "pct" if gtype == "lxc" else "qm"
    st = _run(host, f"{tool} status {vmid} 2>/dev/null")
    if "running" in (st.get("stdout") or "").lower():
        progress(f"Stopping {vmid} …")
        _run(host, f"{tool} stop {vmid} 2>&1", timeout=180)

    progress(f"Destroying guest {vmid} and its volumes …")
    r = _run(host, f"{tool} destroy {vmid} --purge 2>&1", timeout=300)
    if not r.get("success"):
        return {"success": False, "error": f"{tool} destroy failed: "
                + (r.get("stderr") or r.get("stdout") or "")[:300]}
    # qm/pct destroy removes the volumes it knows; make sure none survived.
    leftovers = [d["dataset"] for d in disks if _dataset_exists(host, d["dataset"])]
    for ds in leftovers:
        progress(f"Destroying leftover {ds} …")
        _run(host, f"zfs destroy {shlex.quote(ds)} 2>&1")

    # A hold left behind by an interrupted creation would pin the origin
    # after the clone is gone; release ours and say if that did not work.
    holds_left = _release_holds(host, origins) if origins else []
    still_held = {o: c for o in origins if (c := _clones_of(host, o))}
    progress("Cleanup finished" + (" -- some origins still held" if still_held or holds_left else ""),
             ok=not (still_held or holds_left))
    return {"success": True, "vmid": vmid, "origins": origins,
            "released": [o for o in origins if o not in still_held and o not in holds_left],
            "still_held": still_held, "holds_left": holds_left, "marker": mk}


def destroy_async(host: Dict[str, Any], vmid: str, gtype: str) -> str:
    return start_task("recovery-guest-destroy", destroy_job, host, str(vmid), gtype,
                      prefix="recovery")
