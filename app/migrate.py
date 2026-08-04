"""Near-live guest migration between two Proxmox hosts that are NOT clustered.

Proxmox's own live migration needs cluster membership. This module does what
crossover (https://github.com/lephisto/crossover) does for Ceph, but for ZFS:
pre-copy the guest's disks with ``zfs send`` while it keeps running, repeat
incrementally to shrink the delta, then take a short cutover (shutdown -> final
incremental -> config -> start on the target).

That is *near*-live, not zero-downtime: the RAM state is not transferred, so the
guest is restarted on the target. Downtime is the shutdown + the final delta --
seconds to a couple of minutes. For guests that are already replicated
(bashclub-zsync), the pre-copy is effectively done already.

Safety model:
- Only registered hosts; no ad-hoc password targets.
- The guest must never run on both sides: the target is only started after the
  source is confirmed stopped, and the source config gets ``lock: migrate``.
- A target VMID that already exists is never overwritten.
- An existing target dataset without a common snapshot is refused (a ``recv -F``
  would destroy it).
- The source is left fully intact -- stopped and locked -- until the operator
  explicitly cleans it up, so a rollback is always possible.

The parsing/rewriting helpers are pure functions so they are unit tested without
any SSH.
"""

from __future__ import annotations

import re
import shlex
import time
from typing import Any, Dict, List, Optional, Tuple

from app.ssh_manager import run_command
from app.tasks import start_task
from app.validators import validate_vmid, validate_zfs_name

# Disk keys per guest type. qemu: scsiN/virtioN/sataN/ideN + efidisk/tpmstate;
# lxc: rootfs + mpN. "unusedN" is deliberately NOT included -- those volumes are
# detached and must not drag the migration along.
_QEMU_DISK_KEY_RE = re.compile(r"^(?:scsi|virtio|sata|ide)\d+$|^(?:efidisk|tpmstate)\d+$")
_LXC_DISK_KEY_RE = re.compile(r"^(?:rootfs|mp\d+)$")

_SNAP_NAME_RE = re.compile(r"^migrate-\d{8}-\d{6}$")
_DS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:/-]*$")

SSH_OPTS = "-o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def split_main_section(text: str) -> str:
    """The guest config up to the first ``[snapshot]`` section.

    PVE appends one ``[name]`` block per PVE snapshot; only the leading block
    describes the current state, so disk discovery must stop there.
    """
    out = []
    for line in (text or "").splitlines():
        if line.startswith("["):
            break
        out.append(line)
    return "\n".join(out)


def config_snapshot_sections(text: str) -> List[str]:
    """Names of the PVE snapshot sections in a guest config (``[name]``)."""
    return [m.group(1) for m in re.finditer(r"^\[([^\]]+)\]\s*$", text or "",
                                            re.MULTILINE)]


def parse_guest_config_disks(text: str, gtype: str) -> List[Tuple[str, str]]:
    """[(config_key, volid)] for every attached disk volume.

    Skips CD-ROM/none entries, detached ``unusedN`` volumes and everything in
    PVE snapshot sections.
    """
    key_re = _LXC_DISK_KEY_RE if gtype == "lxc" else _QEMU_DISK_KEY_RE
    disks: List[Tuple[str, str]] = []
    for line in split_main_section(text).splitlines():
        m = re.match(r"^([a-z]+\d*):\s*(.+)$", line.strip())
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if not key_re.match(key):
            continue
        volid = value.split(",", 1)[0].strip()
        if not volid or volid == "none" or "media=cdrom" in value:
            continue
        if ":" not in volid:          # e.g. a raw size-only entry
            continue
        disks.append((key, volid))
    return disks


def parse_config_bridges(text: str) -> List[str]:
    """Bridge names referenced by the guest's netN entries."""
    return sorted({m.group(1) for m in
                   re.finditer(r"bridge=([A-Za-z0-9_.-]+)",
                               split_main_section(text) or "")})


def dataset_from_pvesm_path(path: str) -> str:
    """Map ``pvesm path <volid>`` output to a ZFS dataset name.

    zvol  -> /dev/zvol/<dataset>
    subvol-> /<dataset>   (PVE mounts ZFS subvols at their dataset path)
    """
    p = (path or "").strip()
    if not p:
        return ""
    if p.startswith("/dev/zvol/"):
        return p[len("/dev/zvol/"):]
    if p.startswith("/"):
        return p.lstrip("/")
    return ""


def volume_basename(volid_or_dataset: str) -> str:
    """``local-zfs:vm-100-disk-0`` / ``rpool/data/vm-100-disk-0`` -> ``vm-100-disk-0``."""
    s = (volid_or_dataset or "").split(":", 1)[-1]
    return s.rsplit("/", 1)[-1]


def config_storage_ids(text: str, gtype: str) -> List[str]:
    """Storage IDs the guest's disks currently live on (``local-zfs:…`` -> ``local-zfs``)."""
    return sorted({v.split(":", 1)[0] for _, v in parse_guest_config_disks(text, gtype)
                   if ":" in v})


def parse_zfs_storages(text: str) -> List[Dict[str, Any]]:
    """``zfspool`` entries of a PVE storage.cfg.

    Sections look like::

        zfspool: local-zfs
                pool rpool/data
                content images,rootdir
                nodes pve1,pve2

    Returns ``[{storage, pool, content, nodes}]`` -- ``pool`` is the ZFS dataset
    the storage writes into, which is what has to match the migration's target
    dataset root.
    """
    out: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for raw in (text or "").splitlines():
        m = re.match(r"^(\w+):\s*(\S+)\s*$", raw)
        if m:
            cur = {"storage": m.group(2), "pool": "", "content": [], "nodes": []}
            if m.group(1) == "zfspool":
                out.append(cur)
            else:
                cur = None          # different storage type -- skip its body
            continue
        if cur is None or not raw.strip():
            continue
        p = re.match(r"^\s+(\w+)\s+(.*)$", raw)
        if not p:
            continue
        key, val = p.group(1), p.group(2).strip()
        if key == "pool":
            cur["pool"] = val
        elif key == "content":
            cur["content"] = [c.strip() for c in val.split(",") if c.strip()]
        elif key == "nodes":
            cur["nodes"] = [n.strip() for n in val.split(",") if n.strip()]
    return out


def usable_guest_storages(storages: List[Dict[str, Any]],
                          node: str = "") -> List[Dict[str, Any]]:
    """ZFS storages that can actually hold guest disks on ``node``.

    Needs ``images`` (VM disks) or ``rootdir`` (CT volumes) in its content
    types, and must not be restricted away from this node.
    """
    out = []
    for s in storages or []:
        if not s.get("pool"):
            continue
        content = s.get("content") or []
        if content and not ({"images", "rootdir"} & set(content)):
            continue
        nodes = s.get("nodes") or []
        if nodes and node and node not in nodes:
            continue
        out.append(s)
    return out


def read_zfs_storages(host: Dict[str, Any]) -> List[Dict[str, Any]]:
    """ZFS storages defined on the host that can hold guest disks."""
    r = run_command(host, "cat /etc/pve/storage.cfg 2>/dev/null", timeout=15)
    if not r.get("success"):
        return []
    node = ""
    hr = run_command(host, "hostname", timeout=10)
    if hr.get("success"):
        node = (hr.get("stdout") or "").strip().split(".")[0]
    return usable_guest_storages(parse_zfs_storages(r.get("stdout") or ""), node)


# A dataset that IS a guest disk -- never a sensible destination root.
_GUEST_DISK_DS_RE = re.compile(r"/(?:vm|subvol|base|basevol)-\d+-disk-\d+$")


def candidate_target_roots(datasets: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Filesystems that can sensibly hold migrated guest disks.

    Drops volumes (zvols are disks, not containers for disks), the guest disks
    themselves, and the ``<pool>/ROOT`` subtree that holds the PVE root
    filesystem. ``<pool>/data`` -- where PVE's default local-zfs storage puts
    guests -- is offered first.
    """
    out: List[Dict[str, str]] = []
    for d in datasets or []:
        name = (d or {}).get("name", "")
        if not name or (d.get("type") or "") != "filesystem":
            continue
        if _GUEST_DISK_DS_RE.search(name):
            continue
        parts = name.split("/")
        if len(parts) > 1 and parts[1] == "ROOT":
            continue
        out.append({"name": name, "avail": d.get("avail", "")})
    out.sort(key=lambda x: (0 if x["name"].endswith("/data") else 1, x["name"]))
    return out


def rewrite_guest_config(text: str, storage_map: Optional[Dict[str, str]] = None,
                         bridge_map: Optional[Dict[str, str]] = None,
                         old_vmid: Optional[str] = None,
                         new_vmid: Optional[str] = None) -> str:
    """Adapt a guest config for the target host.

    Structured replacements instead of free-form regex rewrites (crossover's
    ``--rewrite``), so the transformation stays reviewable:
      * storage_map: storage ID prefix of a volid (``local-zfs:`` -> ``tank:``)
      * bridge_map:  ``bridge=vmbr0`` -> ``bridge=vmbr1``
      * new_vmid:    renames the volume names (``vm-100-disk-0`` ->
                     ``vm-110-disk-0``); the receive side uses the same names.
    ``lock:`` is always dropped -- the target must not start out locked.
    """
    storage_map = storage_map or {}
    bridge_map = bridge_map or {}
    out = []
    for line in (text or "").splitlines():
        if re.match(r"^lock:\s*", line):
            continue
        for old, new in storage_map.items():
            if old and new:
                line = re.sub(rf"(^|[\s:,]){re.escape(old)}:(?=\S)",
                              lambda m: f"{m.group(1)}{new}:", line)
        for old, new in bridge_map.items():
            if old and new:
                line = re.sub(rf"bridge={re.escape(old)}\b", f"bridge={new}", line)
        if old_vmid and new_vmid and str(old_vmid) != str(new_vmid):
            line = re.sub(rf"\b(vm|subvol|base|basevol)-{re.escape(str(old_vmid))}-disk-",
                          rf"\1-{new_vmid}-disk-", line)
        out.append(line)
    return "\n".join(out) + ("\n" if text and not text.endswith("\n") else "")


def make_snapshot_name(now: Optional[float] = None) -> str:
    """Migration snapshot name: ``migrate-YYYYmmdd-HHMMSS``."""
    return "migrate-" + time.strftime("%Y%m%d-%H%M%S",
                                      time.localtime(now if now else time.time()))


def build_send_recv(source_addr: str, source_user: str, source_port: int,
                    target_addr: str, target_user: str, target_port: int,
                    src_snapshot: str, target_dataset: str,
                    base_snapshot: Optional[str], pull: bool) -> str:
    """The transfer command, run on the target (pull) or the source (push).

    ``set -o pipefail`` so a failing ``zfs send`` is not masked by a receiver
    that exits 0 -- the same trap that made a broken DR resend look successful.
    """
    incr = f"-i {shlex.quote(base_snapshot)} " if base_snapshot else ""
    send = f"zfs send {incr}{shlex.quote(src_snapshot)}"
    recv = f"zfs recv -F {shlex.quote(target_dataset)}"
    if pull:
        remote = f"ssh {SSH_OPTS} -p {int(source_port)} " \
                 f"{shlex.quote(source_user + '@' + source_addr)} {shlex.quote(send)}"
        pipeline = f"{remote} | {recv}"
    else:
        remote = f"ssh {SSH_OPTS} -p {int(target_port)} " \
                 f"{shlex.quote(target_user + '@' + target_addr)} {shlex.quote(recv)}"
        pipeline = f"{send} | {remote}"
    return f"set -o pipefail 2>/dev/null; {pipeline} 2>&1; echo __exit=$?"


def parse_exit_marker(out: str) -> Optional[int]:
    m = re.search(r"__exit=(\d+)\s*$", (out or "").strip())
    return int(m.group(1)) if m else None


def _hostref(h: Dict[str, Any]) -> Tuple[str, str, int]:
    return (h.get("address", ""), h.get("user", "root") or "root",
            int(h.get("port", 22) or 22))


# ---------------------------------------------------------------------------
# Discovery (SSH)
# ---------------------------------------------------------------------------

def read_guest_config(host: Dict[str, Any], vmid: str, gtype: str) -> Dict[str, Any]:
    """Raw ``<vmid>.conf`` from the host (via the pmxcfs path, not qm config, so
    we get the file verbatim including snapshot sections)."""
    try:
        vmid = str(validate_vmid(vmid))
    except ValueError as e:
        return {"success": False, "error": str(e)}
    subdir = "lxc" if gtype == "lxc" else "qemu-server"
    path = f"/etc/pve/{subdir}/{vmid}.conf"
    r = run_command(host, f"cat {shlex.quote(path)} 2>/dev/null", timeout=15)
    text = r.get("stdout") or ""
    if not r.get("success") or not text.strip():
        return {"success": False, "error": f"guest config {path} not found"}
    return {"success": True, "content": text, "path": path}


def discover_guest_disks(host: Dict[str, Any], vmid: str, gtype: str,
                         config_text: Optional[str] = None) -> Dict[str, Any]:
    """Resolve every attached disk of the guest to its ZFS dataset.

    Primary route is ``pvesm path <volid>``; a dataset that does not resolve (or
    is not ZFS) is reported so the caller can refuse instead of silently
    migrating a partial guest.
    """
    if config_text is None:
        cfg = read_guest_config(host, vmid, gtype)
        if not cfg.get("success"):
            return {"success": False, "error": cfg.get("error", "no config")}
        config_text = cfg["content"]

    disks: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, str]] = []
    for key, volid in parse_guest_config_disks(config_text, gtype):
        r = run_command(host, f"pvesm path {shlex.quote(volid)} 2>/dev/null", timeout=20)
        ds = dataset_from_pvesm_path(r.get("stdout", "") if r.get("success") else "")
        if ds and _DS_RE.match(ds):
            chk = run_command(host, f"zfs list -H -o name,used {shlex.quote(ds)} 2>/dev/null",
                              timeout=15)
            parts = (chk.get("stdout") or "").strip().split("\t")
            if chk.get("success") and parts and parts[0] == ds:
                disks.append({"key": key, "volid": volid, "dataset": ds,
                              "used": parts[1] if len(parts) > 1 else ""})
                continue
        unresolved.append({"key": key, "volid": volid})
    return {"success": not unresolved, "disks": disks, "unresolved": unresolved}


def guest_is_running(host: Dict[str, Any], vmid: str, gtype: str) -> bool:
    cmd = (f"qm status {shlex.quote(str(vmid))}" if gtype != "lxc"
           else f"pct status {shlex.quote(str(vmid))}")
    r = run_command(host, f"{cmd} 2>/dev/null", timeout=15)
    return "running" in (r.get("stdout") or "").lower()


def vmid_in_use(host: Dict[str, Any], vmid: str) -> bool:
    """True if either guest type already owns this VMID on the host."""
    r = run_command(
        host,
        f"([ -e /etc/pve/qemu-server/{int(vmid)}.conf ] || "
        f"[ -e /etc/pve/lxc/{int(vmid)}.conf ]) && echo __USED__ || echo __FREE__",
        timeout=15)
    return "__USED__" in (r.get("stdout") or "")


def list_snapshots(host: Dict[str, Any], dataset: str) -> List[str]:
    """Snapshot short names of one dataset, oldest first."""
    r = run_command(host, f"zfs list -H -o name -t snapshot -d 1 {shlex.quote(dataset)} "
                          f"-s creation 2>/dev/null", timeout=20)
    out = []
    for ln in (r.get("stdout") or "").splitlines():
        ln = ln.strip()
        if "@" in ln:
            out.append(ln.split("@", 1)[1])
    return out


def dataset_exists(host: Dict[str, Any], dataset: str) -> bool:
    r = run_command(host, f"zfs list -H -o name {shlex.quote(dataset)} 2>/dev/null",
                    timeout=15)
    return (r.get("stdout") or "").strip() == dataset


def host_bridges(host: Dict[str, Any]) -> List[str]:
    r = run_command(host, "ls /sys/class/net 2>/dev/null", timeout=15)
    return [b for b in (r.get("stdout") or "").split() if b.startswith("vmbr")]


def probe_ssh(from_host: Dict[str, Any], to_host: Dict[str, Any]) -> bool:
    """Can ``from_host`` reach ``to_host`` over SSH non-interactively?

    Determines the transfer direction: bashclub-zsync sets up target->source
    (the target pulls), so pull mode usually works while push does not.
    """
    addr, user, port = _hostref(to_host)
    r = run_command(from_host,
                    f"ssh {SSH_OPTS} -p {port} {shlex.quote(user + '@' + addr)} "
                    f"true 2>/dev/null && echo __SSH_OK__", timeout=40)
    return "__SSH_OK__" in (r.get("stdout") or "")


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def check_storage_match(storages: List[Dict[str, Any]], target_storage: str,
                        target_root: str) -> Tuple[bool, str, List[str]]:
    """Does the chosen target storage write into the chosen target dataset?

    This is the cross-datastore trap: send the disks to ``tank/data`` but leave
    the config pointing at a storage backed by ``rpool/data`` and PVE simply
    cannot find them -- the guest fails to start, long after the migration
    reported success. Returns ``(ok, detail, suggestions)``.
    """
    matching = [s["storage"] for s in storages if s.get("pool") == target_root]
    if not target_storage:
        if matching:
            return False, "pick the target storage (matching: " + ", ".join(matching) + ")", matching
        return (False, f"no ZFS storage on the target writes into {target_root} -- "
                       "create one in PVE first, or pick a different target dataset", [])
    st = next((s for s in storages if s.get("storage") == target_storage), None)
    if st is None:
        return False, f"storage {target_storage} does not exist on the target", matching
    if st.get("pool") != target_root:
        return (False, f"storage {target_storage} writes into {st.get('pool')}, "
                       f"but the disks go to {target_root} -- the guest would not "
                       "find them", matching)
    return True, f"{target_storage} → {target_root}", matching


def preflight(source: Dict[str, Any], target: Dict[str, Any], vmid: str,
              gtype: str, target_root: str,
              new_vmid: Optional[str] = None,
              target_storage: str = "") -> Dict[str, Any]:
    """Everything that must hold before a migration is allowed to start.

    Returns ``{checks: [{id, ok, level, detail}], can_precopy, can_cutover, ...}``
    -- ``level`` is "error" (blocks) or "warn" (proceed with care).
    """
    checks: List[Dict[str, Any]] = []

    def add(cid, ok, detail, level="error"):
        checks.append({"id": cid, "ok": bool(ok), "detail": detail,
                       "level": "ok" if ok else level})

    try:
        vmid = str(validate_vmid(vmid))
        tgt_vmid = str(validate_vmid(new_vmid)) if new_vmid else vmid
        if not _DS_RE.match(target_root or ""):
            raise ValueError("invalid target dataset root")
    except ValueError as e:
        return {"success": False, "error": str(e), "checks": []}

    cfg = read_guest_config(source, vmid, gtype)
    if not cfg.get("success"):
        add("config", False, cfg.get("error", "guest config not readable"))
        return {"success": False, "checks": checks, "can_precopy": False,
                "can_cutover": False}
    text = cfg["content"]
    add("config", True, cfg["path"])

    disc = discover_guest_disks(source, vmid, gtype, config_text=text)
    disks = disc.get("disks", [])
    if disc.get("unresolved"):
        add("disks", False, "not on ZFS / unresolvable: " +
            ", ".join(f"{d['key']}={d['volid']}" for d in disc["unresolved"]))
    elif not disks:
        add("disks", False, "no disks found in the guest config")
    else:
        add("disks", True, ", ".join(d["dataset"] for d in disks))

    running = guest_is_running(source, vmid, gtype)
    add("running", True, "running (pre-copy possible while it runs)"
        if running else "stopped", level="warn")

    if vmid_in_use(target, tgt_vmid):
        add("vmid", False, f"VMID {tgt_vmid} is already in use on the target")
    else:
        add("vmid", True, f"VMID {tgt_vmid} is free on the target")

    if dataset_exists(target, target_root):
        add("target_root", True, target_root)
    else:
        add("target_root", False, f"target dataset root {target_root} does not exist")

    snaps = config_snapshot_sections(text)
    if snaps:
        add("pve_snapshots", False,
            "guest has PVE snapshots (" + ", ".join(snaps[:5]) +
            ") -- their vmstate volumes are not migrated", level="warn")

    # Storage: the disks land in target_root, but the guest config keeps
    # pointing at a storage ID. Both must agree or the guest won't find them.
    storages = read_zfs_storages(target)
    ok_st, detail_st, suggestions = check_storage_match(storages, target_storage,
                                                        target_root)
    add("storage", ok_st, detail_st)

    src_bridges = parse_config_bridges(text)
    tgt_bridges = host_bridges(target)
    missing = [b for b in src_bridges if b not in tgt_bridges]
    if missing:
        add("bridges", False, "missing on the target: " + ", ".join(missing) +
            " (remap them before the cutover)", level="warn")
    elif src_bridges:
        add("bridges", True, ", ".join(src_bridges))

    pull = probe_ssh(target, source)
    push = False if pull else probe_ssh(source, target)
    if pull or push:
        add("ssh", True, "target pulls from source" if pull else "source pushes to target")
    else:
        add("ssh", False, "no non-interactive SSH between the hosts in either "
                          "direction -- set up replication or install the key first")

    # Per disk: does the target already hold a copy, and is there a common
    # snapshot to send incrementally from?
    plan: List[Dict[str, Any]] = []
    for d in disks:
        base = volume_basename(d["dataset"])
        if new_vmid and str(new_vmid) != str(vmid):
            base = re.sub(rf"^(vm|subvol|base|basevol)-{re.escape(vmid)}-",
                          rf"\1-{new_vmid}-", base)
        tds = f"{target_root}/{base}"
        exists = dataset_exists(target, tds)
        common = ""
        if exists:
            src_snaps = set(list_snapshots(source, d["dataset"]))
            for s in reversed(list_snapshots(target, tds)):
                if s in src_snaps:
                    common = s
                    break
        plan.append({"key": d["key"], "source_dataset": d["dataset"],
                     "target_dataset": tds, "used": d.get("used", ""),
                     "target_exists": exists, "common_snapshot": common,
                     "mode": "incremental" if common else "full"})

    blocked = [p for p in plan if p["target_exists"] and not p["common_snapshot"]]
    if blocked:
        add("target_datasets", False,
            "target dataset exists without a common snapshot (a forced receive "
            "would destroy it): " + ", ".join(p["target_dataset"] for p in blocked))
    elif any(p["common_snapshot"] for p in plan):
        add("target_datasets", True, "pre-seeded, incremental transfer possible")
    elif plan:
        add("target_datasets", True, "target is empty, full transfer")

    errors = [c for c in checks if not c["ok"] and c["level"] == "error"]
    return {"success": True, "checks": checks, "plan": plan,
            "running": running, "target_vmid": tgt_vmid,
            "bridges": src_bridges, "target_bridges": tgt_bridges,
            "source_storages": config_storage_ids(text, gtype),
            "target_storages": storages,
            "storage_suggestions": suggestions,
            "target_storage": target_storage,
            "pull": pull, "push": push,
            "can_precopy": not errors, "can_cutover": not errors}


# ---------------------------------------------------------------------------
# Transfer
# ---------------------------------------------------------------------------

def _transfer_disk(progress, source, target, src_dataset, target_dataset,
                   snap_name, base_snapshot, pull):
    s_addr, s_user, s_port = _hostref(source)
    t_addr, t_user, t_port = _hostref(target)
    cmd = build_send_recv(s_addr, s_user, s_port, t_addr, t_user, t_port,
                          f"{src_dataset}@{snap_name}", target_dataset,
                          base_snapshot, pull)
    driver = target if pull else source
    mode = "incremental from " + base_snapshot if base_snapshot else "full"
    progress(f"{src_dataset} -> {target_dataset} ({mode}) …")
    r = run_command(driver, cmd, timeout=12 * 3600)
    out = r.get("stdout") or ""
    code = parse_exit_marker(out)
    ok = bool(r.get("success")) and code == 0
    return {"source_dataset": src_dataset, "target_dataset": target_dataset,
            "snapshot": snap_name, "base": base_snapshot, "success": ok,
            "exit_code": code,
            "output": re.sub(r"__exit=\d+\s*$", "", out).strip()[-2000:]}


def _snapshot_all(host, datasets, snap_name):
    """One recursive-free snapshot per disk dataset (guest-consistent enough for
    a pre-copy; the cutover snapshot is taken with the guest stopped)."""
    made = []
    for ds in datasets:
        r = run_command(host, f"zfs snapshot {shlex.quote(ds)}@{shlex.quote(snap_name)}",
                        timeout=60)
        made.append({"dataset": ds, "success": bool(r.get("success")),
                     "stderr": (r.get("stderr") or "").strip()[:200]})
    return made


def _run_transfer(progress, source, target, plan, pull, snap_name):
    """Snapshot every disk, then send each one (incremental where possible)."""
    progress(f"Creating snapshot @{snap_name} on the source …")
    snaps = _snapshot_all(source, [p["source_dataset"] for p in plan], snap_name)
    failed = [s for s in snaps if not s["success"]]
    if failed:
        return {"success": False, "error": "snapshot failed: " +
                ", ".join(f"{s['dataset']}: {s['stderr']}" for s in failed),
                "results": []}

    results = []
    for p in plan:
        base = p.get("common_snapshot") or None
        res = _transfer_disk(progress, source, target, p["source_dataset"],
                             p["target_dataset"], snap_name, base, pull)
        results.append(res)
        if not res["success"]:
            progress(f"FAILED: {p['source_dataset']}", ok=False)
            return {"success": False, "results": results, "snapshot": snap_name,
                    "error": f"transfer of {p['source_dataset']} failed"}
        progress(f"OK: {p['target_dataset']}")
    return {"success": True, "results": results, "snapshot": snap_name}


def precopy_async(source: Dict[str, Any], target: Dict[str, Any],
                  plan: List[Dict[str, Any]], pull: bool) -> str:
    """Background pre-copy: the guest keeps running. Repeatable -- each run
    sends only the delta since the previous migration snapshot."""

    def _job(progress, src, tgt, pl, pl_pull):
        snap_name = make_snapshot_name()
        res = _run_transfer(progress, src, tgt, pl, pl_pull, snap_name)
        progress("Pre-copy finished" if res.get("success") else "Pre-copy failed",
                 ok=res.get("success", False))
        return res

    return start_task("guest-migration-precopy", _job, source, target, plan, pull,
                      prefix="migrate")


def set_guest_lock(host: Dict[str, Any], vmid: str, gtype: str,
                   lock: Optional[str]) -> Dict[str, Any]:
    """Set or clear the ``lock:`` line in a guest config.

    Edited in the config file directly because ``pct set`` has no --lock; the
    lock is what stops someone starting the guest on the source after cutover.
    """
    try:
        vmid = str(validate_vmid(vmid))
    except ValueError as e:
        return {"success": False, "error": str(e)}
    subdir = "lxc" if gtype == "lxc" else "qemu-server"
    path = f"/etc/pve/{subdir}/{vmid}.conf"
    script = f"sed -i '/^lock:/d' {shlex.quote(path)}"
    if lock:
        script += f" && sed -i '1i lock: {shlex.quote(lock)}' {shlex.quote(path)}"
    script += " && echo __OK__"
    r = run_command(host, script, timeout=20)
    return {"success": "__OK__" in (r.get("stdout") or ""),
            "stderr": (r.get("stderr") or "").strip()[:200]}


def _stop_guest(progress, host, vmid, gtype, timeout=120):
    """Graceful shutdown, escalating to stop. Returns True when it is down."""
    sd = f"qm shutdown {vmid} --timeout {timeout}" if gtype != "lxc" \
        else f"pct shutdown {vmid} --timeout {timeout}"
    progress("Shutting the guest down on the source …")
    run_command(host, sd, timeout=timeout + 60)
    if not guest_is_running(host, vmid, gtype):
        return True
    progress("Graceful shutdown timed out -- forcing stop …")
    st = f"qm stop {vmid}" if gtype != "lxc" else f"pct stop {vmid}"
    run_command(host, st, timeout=120)
    return not guest_is_running(host, vmid, gtype)


def cutover_async(source: Dict[str, Any], target: Dict[str, Any], vmid: str,
                  gtype: str, plan: List[Dict[str, Any]], pull: bool,
                  new_vmid: Optional[str] = None,
                  storage_map: Optional[Dict[str, str]] = None,
                  bridge_map: Optional[Dict[str, str]] = None,
                  start_on_target: bool = True,
                  shutdown_timeout: int = 120) -> str:
    """Background cutover: stop the guest, send the final delta, move the
    config, lock the source, start on the target."""

    def _job(progress, src, tgt, _vmid, _gtype, pl, _pull, _new_vmid,
             _smap, _bmap, _start, _sd_timeout):
        tgt_vmid = str(_new_vmid) if _new_vmid else str(_vmid)

        # Never overwrite an existing guest on the target.
        if vmid_in_use(tgt, tgt_vmid):
            return {"success": False, "error": f"VMID {tgt_vmid} is in use on the target"}

        cfg = read_guest_config(src, _vmid, _gtype)
        if not cfg.get("success"):
            return {"success": False, "error": cfg.get("error", "no guest config")}

        if not _stop_guest(progress, src, _vmid, _gtype, _sd_timeout):
            return {"success": False, "error": "guest could not be stopped on the source"}
        progress("Guest is stopped -- sending the final delta …")

        snap_name = make_snapshot_name()
        res = _run_transfer(progress, src, tgt, pl, _pull, snap_name)
        if not res.get("success"):
            progress("Final transfer failed -- source is untouched, restart it there",
                     ok=False)
            return {"success": False, "error": res.get("error", "final transfer failed"),
                    "results": res.get("results", []), "stopped_source": True}

        progress("Writing the guest config on the target …")
        from app.dr import restore_guest_config
        new_text = rewrite_guest_config(cfg["content"], _smap, _bmap,
                                        old_vmid=_vmid, new_vmid=_new_vmid)
        wr = restore_guest_config(tgt, _gtype, tgt_vmid, new_text, force=False)
        if not wr.get("success"):
            return {"success": False, "error": "config write failed: " +
                    (wr.get("error") or wr.get("stderr") or ""),
                    "stopped_source": True, "transferred": True}

        lk = set_guest_lock(src, _vmid, _gtype, "migrate")
        progress("Source locked" if lk.get("success")
                 else "Warning: could not lock the source config")

        started = False
        if _start:
            progress(f"Starting the guest on the target (VMID {tgt_vmid}) …")
            act = f"qm start {tgt_vmid}" if _gtype != "lxc" else f"pct start {tgt_vmid}"
            sr = run_command(tgt, act, timeout=300)
            started = bool(sr.get("success"))
            if not started:
                progress("Start on the target failed: " +
                         (sr.get("stderr") or "")[:200], ok=False)

        progress("Cutover finished", ok=True)
        return {"success": True, "target_vmid": tgt_vmid, "snapshot": snap_name,
                "results": res.get("results", []), "started": started,
                "source_locked": lk.get("success", False)}

    return start_task("guest-migration-cutover", _job, source, target, str(vmid),
                      gtype, plan, pull, new_vmid, storage_map, bridge_map,
                      start_on_target, int(shutdown_timeout), prefix="migrate")


# ---------------------------------------------------------------------------
# Rollback / cleanup
# ---------------------------------------------------------------------------

def rollback(source: Dict[str, Any], target: Dict[str, Any], vmid: str,
             gtype: str, target_vmid: Optional[str] = None,
             start_source: bool = True) -> Dict[str, Any]:
    """Undo a cutover: stop + remove the guest on the target (its datasets are
    kept), unlock the source and start it there again."""
    try:
        vmid = str(validate_vmid(vmid))
        tgt_vmid = str(validate_vmid(target_vmid)) if target_vmid else vmid
    except ValueError as e:
        return {"success": False, "error": str(e)}

    steps = []
    stop = f"qm stop {tgt_vmid}" if gtype != "lxc" else f"pct stop {tgt_vmid}"
    run_command(target, f"{stop} 2>/dev/null", timeout=120)
    steps.append({"step": "stop_target", "success": not guest_is_running(target, tgt_vmid, gtype)})

    subdir = "lxc" if gtype == "lxc" else "qemu-server"
    rm = run_command(target, f"rm -f /etc/pve/{subdir}/{int(tgt_vmid)}.conf && echo __OK__",
                     timeout=20)
    steps.append({"step": "remove_target_config",
                  "success": "__OK__" in (rm.get("stdout") or "")})

    lk = set_guest_lock(source, vmid, gtype, None)
    steps.append({"step": "unlock_source", "success": lk.get("success", False)})

    started = False
    if start_source:
        act = f"qm start {vmid}" if gtype != "lxc" else f"pct start {vmid}"
        sr = run_command(source, act, timeout=300)
        started = bool(sr.get("success"))
    steps.append({"step": "start_source", "success": started or not start_source})

    return {"success": all(s["success"] for s in steps), "steps": steps,
            "source_started": started}


def cleanup_source(source: Dict[str, Any], vmid: str, gtype: str,
                   datasets: List[str]) -> Dict[str, Any]:
    """Remove the migrated guest from the source -- explicit, never automatic.

    Destroys the guest config and the listed datasets. Refuses while the guest
    is still running there.
    """
    try:
        vmid = str(validate_vmid(vmid))
        for ds in datasets or []:
            validate_zfs_name(ds, "Dataset")
    except ValueError as e:
        return {"success": False, "error": str(e)}
    if guest_is_running(source, vmid, gtype):
        return {"success": False, "error": "guest is still running on the source"}

    results = []
    set_guest_lock(source, vmid, gtype, None)
    destroy = f"qm destroy {vmid} --purge" if gtype != "lxc" \
        else f"pct destroy {vmid} --purge"
    r = run_command(source, f"{destroy} 2>&1", timeout=600)
    results.append({"step": "destroy_guest", "success": bool(r.get("success")),
                    "output": (r.get("stdout") or "")[-500:]})

    for ds in datasets or []:
        if dataset_exists(source, ds):
            d = run_command(source, f"zfs destroy -r {shlex.quote(ds)} 2>&1", timeout=300)
            results.append({"step": f"destroy_dataset {ds}",
                            "success": bool(d.get("success")),
                            "output": (d.get("stdout") or "")[-300:]})
    return {"success": all(x["success"] for x in results), "results": results}
