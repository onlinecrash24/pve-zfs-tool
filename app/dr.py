"""Disaster recovery helpers built on top of bashclub-zsync replicas.

Two workflows are exposed here, deliberately bounded to the replication
data we already have on a target host:

1. **Browse & restore individual files** from any replica snapshot.
   Reuses the existing snapshot mount / browse / restore plumbing from
   ``zfs_commands.py`` -- this module only provides the discovery
   functions that map a (target host, source IP) tuple onto the right
   replica subtree.

2. **Reverse sync** the replica back to a (rebuilt) source host. Runs
   ``zfs send -R <replica>@<snap> | ssh root@<source> zfs recv -F
   <source-dataset>`` as a background task using the same
   ``app.tasks`` registry as replication and AI reports. The SSH key
   bootstrapped during initial replication setup is reused -- the
   target → source direction is already trusted.

Safety rails:
- Only datasets that live underneath a ``target=`` value from one of
  the per-source bashclub configs are recognised as replicas. Random
  datasets on the target host can't be exposed via this module.
- Source / target dataset names are validated against a strict regex
  before they ever land in a shell command.
- Reverse sync defaults to ``zfs recv -F`` only when explicitly
  acknowledged by the caller -- otherwise we use the safer
  no-rollback variant which fails loudly if the destination already
  has diverging snapshots.
"""

from __future__ import annotations

import base64
import re
import shlex
import tarfile
from typing import Any, Dict, List, Optional

from app.ssh_manager import run_command, load_hosts
from app.replication import (
    list_configs, _parse_config, config_path_for, _extract_ip,
)
from app.tasks import start_task, get_task

_DS_RE = re.compile(r"^[A-Za-z0-9._:/-]+$")
_SNAP_RE = re.compile(r"^[A-Za-z0-9._:/@-]+$")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def _replica_roots_for_host(host: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return one entry per non-template config on ``host``::

        [{ source, source_ip, target, config_path }, ...]
    """
    out: List[Dict[str, Any]] = []
    try:
        configs = list_configs(host).get("configs") or []
    except Exception:
        return out
    for c in configs:
        path = c.get("path") or ""
        if path.endswith("/zsync.conf"):
            continue
        src = (c.get("source") or "").strip()
        tgt = (c.get("target") or "").strip()
        if not tgt or "/" not in tgt or src.lower() == "user@host":
            continue
        out.append({
            "source": src,
            "source_ip": _extract_ip(src),
            "target": tgt,
            "config_path": path,
        })
    return out


def list_replica_pairs() -> Dict[str, Any]:
    """Aggregated discovery view used by the DR view's first card."""
    pairs: List[Dict[str, Any]] = []
    for host in load_hosts():
        for r in _replica_roots_for_host(host):
            pairs.append({
                "host_address": host.get("address"),
                "host_name": host.get("name"),
                **r,
            })
    return {"pairs": pairs}


def list_replica_datasets(host: Dict[str, Any], replica_root: str) -> Dict[str, Any]:
    """Enumerate the replicated datasets under ``replica_root`` on ``host``.

    Replicated children typically mirror the source pools, e.g.::

        rpool/repl/rpool
        rpool/repl/rpool/data/subvol-111-disk-0
        ...

    We list every filesystem and volume under the root, plus their
    newest snapshot timestamp for the UI.
    """
    if not _DS_RE.match(replica_root or "") or "/" not in replica_root:
        return {"datasets": [], "error": "invalid replica root"}
    cmd = (
        f"zfs list -H -o name,type,used -t filesystem,volume "
        f"-r {shlex.quote(replica_root)} 2>/dev/null"
    )
    r = run_command(host, cmd, timeout=30)
    if not r.get("success"):
        return {"datasets": [],
                "error": (r.get("stderr") or r.get("stdout") or "").strip()[:200]}
    rows: List[Dict[str, Any]] = []
    for line in (r.get("stdout") or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, dtype, used = parts[0], parts[1], parts[2]
        if name == replica_root:
            continue  # caller already knows the root
        rows.append({
            "name": name,
            "type": dtype,
            "used": used,
            # The source path = relative path under the replica root.
            "source_path": name[len(replica_root) + 1:] if name.startswith(replica_root + "/") else name,
        })
    return {"datasets": rows}


def list_replica_snapshots(host: Dict[str, Any], dataset: str,
                           limit: int = 200) -> Dict[str, Any]:
    """List snapshots for a replica dataset, newest first."""
    if not _DS_RE.match(dataset or "") or "/" not in dataset:
        return {"snapshots": [], "error": "invalid dataset"}
    limit = max(1, min(int(limit or 200), 2000))
    cmd = (
        f"zfs list -H -t snapshot -o name,creation,used -p -s creation "
        f"-r -d 1 {shlex.quote(dataset)} 2>/dev/null"
    )
    r = run_command(host, cmd, timeout=30)
    if not r.get("success"):
        return {"snapshots": [],
                "error": (r.get("stderr") or r.get("stdout") or "").strip()[:200]}
    snaps: List[Dict[str, Any]] = []
    for line in (r.get("stdout") or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        try:
            snaps.append({
                "name": parts[0],
                "creation_ts": int(parts[1]),
                "used_bytes": int(parts[2]) if parts[2].isdigit() else 0,
            })
        except ValueError:
            continue
    snaps.reverse()  # newest first
    return {"snapshots": snaps[:limit]}


# ---------------------------------------------------------------------------
# Reverse sync (replica -> rebuilt source)
# ---------------------------------------------------------------------------

def _strip_replica_root(replica_dataset: str, replica_root: str) -> str:
    """Map a replica dataset back to its source path.

    ``replica_root='rpool/repl'`` and
    ``replica_dataset='rpool/repl/rpool/data/subvol-111-disk-0'`` map to
    ``rpool/data/subvol-111-disk-0`` -- i.e. the original source path.
    """
    if not replica_dataset.startswith(replica_root + "/"):
        return ""
    return replica_dataset[len(replica_root) + 1:]


def reverse_sync_async(target_host: Dict[str, Any],
                       replica_dataset: str,
                       replica_root: str,
                       source_address: str,
                       source_port: int,
                       source_user: str,
                       source_dataset: Optional[str] = None,
                       snapshot: Optional[str] = None,
                       force: bool = False) -> str:
    """Send the replica back to a (rebuilt) source host via SSH.

    ``replica_dataset`` lives on ``target_host`` (the box that holds the
    replica). ``source_dataset`` is where the data should land on
    ``source_address`` -- defaults to the path the original replication
    pulled from (derived by stripping ``replica_root``).
    ``snapshot`` is the snapshot name (without dataset prefix) to send;
    defaults to the newest snapshot under ``replica_dataset``. ``force``
    enables ``zfs recv -F`` which rolls back the destination to match
    the stream -- otherwise we use the safer no-rollback recv.
    """
    if not _DS_RE.match(replica_dataset or "") or "/" not in replica_dataset:
        raise ValueError("invalid replica dataset")
    if not _DS_RE.match(replica_root or "") or "/" not in replica_root:
        raise ValueError("invalid replica root")
    if not source_address or not re.match(r"^[A-Za-z0-9._:-]+$", source_address):
        raise ValueError("invalid source address")
    if source_user and not re.match(r"^[A-Za-z0-9_.-]+$", source_user):
        raise ValueError("invalid source user")
    try:
        source_port = int(source_port or 22)
    except (TypeError, ValueError):
        raise ValueError("invalid source port")
    if not (1 <= source_port <= 65535):
        raise ValueError("invalid source port")

    # Derive default source dataset if caller didn't override it
    if not source_dataset:
        source_dataset = _strip_replica_root(replica_dataset, replica_root)
    if not source_dataset or not _DS_RE.match(source_dataset) or "/" not in source_dataset:
        raise ValueError("could not determine valid source dataset")

    user = (source_user or "root")

    def _job(progress, _host, _replica, _root, _addr, _port, _user, _src_ds, _snap, _force):
        # Determine snapshot if not given: newest under replica
        if not _snap:
            progress("Resolving newest replica snapshot …")
            r = run_command(_host,
                            f"zfs list -H -t snapshot -o name -s creation "
                            f"-r -d 1 {shlex.quote(_replica)} 2>/dev/null | tail -n 1",
                            timeout=20)
            line = (r.get("stdout") or "").strip()
            if not line or "@" not in line:
                return {"success": False,
                        "error": "no snapshots found under " + _replica}
            _snap = line.split("@", 1)[1]
        if not _SNAP_RE.match(_snap):
            return {"success": False, "error": "invalid snapshot name"}

        full_snap = f"{_replica}@{_snap}"
        progress(f"Sending {full_snap} → {_user}@{_addr}:{_src_ds}")

        recv_flag = "-F" if _force else ""
        # ``zfs send -R`` includes descendants and properties; the receiving
        # side gets the dataset created with original properties. The pipe
        # over SSH uses BatchMode + StrictHostKeyChecking=yes because the
        # initial replication setup already populated known_hosts.
        ssh_opts = (
            "-o BatchMode=yes -o StrictHostKeyChecking=yes "
            f"-o ConnectTimeout=20 -p {_port}"
        )
        cmd = (
            f"zfs send -R {shlex.quote(full_snap)} | "
            f"ssh {ssh_opts} {shlex.quote(_user + '@' + _addr)} "
            f"'zfs recv {recv_flag} {shlex.quote(_src_ds)}' "
            f"2>&1; echo __exit=$?"
        )
        # No SSH timeout cap: a multi-TB resend can take hours.
        r = run_command(_host, cmd, timeout=12 * 3600)
        out = (r.get("stdout") or "")
        exit_code = None
        m = re.search(r"__exit=(\d+)\s*$", out.strip())
        if m:
            exit_code = int(m.group(1))
        ok = r.get("success", False) and exit_code == 0
        progress(f"Finished (exit={exit_code})", ok=ok, exit_code=exit_code)
        # Strip the trailing __exit= marker for the result tail.
        cleaned = re.sub(r"__exit=\d+\s*$", "", out).strip()
        return {
            "success": ok,
            "exit_code": exit_code,
            "snapshot": full_snap,
            "source_dataset": _src_ds,
            "source_target": f"{_user}@{_addr}:{_port}",
            "force": bool(_force),
            "log_tail": cleaned[-3000:],
        }

    return start_task(
        "reverse_sync", _job,
        target_host, replica_dataset, replica_root,
        source_address, source_port, user,
        source_dataset, snapshot, bool(force),
        prefix="dr",
    )


def reverse_sync_task(task_id: str) -> Optional[Dict[str, Any]]:
    return get_task(task_id)


def check_reverse_target(source_host: Dict[str, Any], source_dataset: str) -> Dict[str, Any]:
    """Pre-flight for a reverse sync: does the destination dataset still exist
    on the source, and does it hold snapshots?

    A full ``zfs send -R`` receiving onto an existing dataset that has its own
    snapshots is refused by ZFS ("destination has snapshots ... must destroy
    them"), even with ``recv -F`` -- that only prunes diverging snapshots on an
    *incremental* stream. So an existing dataset-with-snapshots means the
    source is intact (steady state), not a disaster: reverse sync isn't the
    right tool and forcing it would require destroying live data (which we
    never do). Returns ``{exists, snapshot_count, examples}``.
    """
    if not source_dataset or not _DS_RE.match(source_dataset) or "/" not in source_dataset:
        return {"error": "invalid dataset"}
    r = run_command(source_host,
                    f"zfs list -H -o name {shlex.quote(source_dataset)} 2>/dev/null",
                    timeout=10)
    exists = bool(r.get("success")) and (r.get("stdout") or "").strip() == source_dataset
    if not exists:
        return {"exists": False, "snapshot_count": 0, "examples": []}
    rs = run_command(source_host,
                     f"zfs list -H -t snapshot -o name -r -d 1 {shlex.quote(source_dataset)} 2>/dev/null",
                     timeout=15)
    snaps = [ln.strip() for ln in (rs.get("stdout") or "").splitlines() if ln.strip()]
    return {"exists": True, "snapshot_count": len(snaps), "examples": snaps[:5]}


# ---------------------------------------------------------------------------
# Guest config restore (from a host-config backup onto the rebuilt source)
# ---------------------------------------------------------------------------
#
# Reverse sync only brings back the ZFS dataset (the disk). Proxmox won't show
# the VM/CT until its /etc/pve/{qemu-server,lxc}/<vmid>.conf exists again. That
# config lives in the tool's host-config backup (/etc/pve is captured there),
# so we can extract it and drop it back onto the rebuilt host.

# vm-/base- -> VM (qemu), subvol-/basevol- -> LXC. VMID is the number.
_GUEST_KIND = {"vm": "qemu", "base": "qemu", "subvol": "lxc", "basevol": "lxc"}
_GUEST_DS_RE = re.compile(r"(?:^|/)(vm|subvol|base|basevol)-(\d+)-disk-\d+")


def guest_ref_from_dataset(dataset: str):
    """(gtype, vmid) parsed from a dataset name, e.g. '.../subvol-253-disk-0'
    -> ('lxc', '253'); '.../vm-100-disk-1' -> ('qemu', '100'). (None, None) if
    the name isn't a guest disk dataset."""
    m = _GUEST_DS_RE.search(dataset or "")
    if not m:
        return None, None
    return _GUEST_KIND.get(m.group(1)), m.group(2)


def extract_guest_config(backup_file: str, gtype: str, vmid: str) -> Dict[str, Any]:
    """Pull ``<vmid>.conf`` for the guest out of a host-config backup tarball.

    Handles the pmxcfs layout where ``/etc/pve/qemu-server`` is a symlink to
    ``/etc/pve/nodes/<node>/qemu-server`` -- we match any regular file whose
    path ends in ``/<subdir>/<vmid>.conf``. Returns
    ``{found, content, member, subdir}``.
    """
    subdir = "lxc" if gtype == "lxc" else "qemu-server" if gtype == "qemu" else ""
    if not subdir or not re.match(r"^\d+$", str(vmid)):
        return {"found": False, "content": "", "member": "", "subdir": subdir}
    suffix = f"/{subdir}/{vmid}.conf"
    try:
        with tarfile.open(backup_file, "r:*") as tf:
            for m in tf.getmembers():
                if m.isfile() and m.name.rstrip("/").endswith(suffix):
                    f = tf.extractfile(m)
                    if not f:
                        continue
                    content = f.read().decode("utf-8", "replace")
                    return {"found": True, "content": content,
                            "member": m.name, "subdir": subdir}
    except (tarfile.TarError, OSError) as e:
        return {"found": False, "content": "", "member": "", "subdir": subdir,
                "error": str(e)[:200]}
    return {"found": False, "content": "", "member": "", "subdir": subdir}


def restore_guest_config(source_host: Dict[str, Any], gtype: str, vmid: str,
                         content: str, force: bool = False) -> Dict[str, Any]:
    """Write ``<vmid>.conf`` into /etc/pve/{qemu-server,lxc}/ on the (rebuilt)
    source host. Refuses to overwrite an existing config unless ``force``."""
    subdir = "lxc" if gtype == "lxc" else "qemu-server" if gtype == "qemu" else ""
    if not subdir:
        return {"success": False, "error": "invalid guest type"}
    if not re.match(r"^\d+$", str(vmid)):
        return {"success": False, "error": "invalid vmid"}
    dest = f"/etc/pve/{subdir}/{vmid}.conf"

    r = run_command(source_host,
                    f"[ -e {shlex.quote(dest)} ] && echo __EXISTS__ || echo __NO__",
                    timeout=10)
    exists = "__EXISTS__" in (r.get("stdout") or "")
    if exists and not force:
        return {"success": False, "exists": True, "dest": dest,
                "error": "config already exists"}

    b64 = base64.b64encode((content or "").encode("utf-8")).decode("ascii")
    script = (
        f"echo {shlex.quote(b64)} | base64 -d > {shlex.quote(dest)} && echo __OK__"
    )
    rw = run_command(source_host, script, timeout=20)
    ok = "__OK__" in (rw.get("stdout") or "")
    return {"success": ok, "exists": exists, "dest": dest,
            "stderr": (rw.get("stderr") or "").strip()[:200]}


# ---------------------------------------------------------------------------
# Config restore: browse a host-config backup and put individual files back
# onto a freshly-installed PVE (network, storage, guests, users, firewall …).
# ---------------------------------------------------------------------------

def _rel(member: str) -> str:
    """tar member name -> repo-relative path (strip the './' the archive uses)."""
    return (member or "").lstrip("./")


def _categorize(rel: str) -> str:
    if rel == "root/.ssh/authorized_keys":
        return "ssh"
    if re.search(r"/(qemu-server|lxc)/\d+\.conf$", rel):
        return "guests"
    if (rel.startswith("etc/network/") or rel in ("etc/hosts", "etc/hostname", "etc/resolv.conf")
            or rel.startswith("etc/udev/") or rel.startswith("etc/systemd/network/")
            or rel.startswith("lib/systemd/network/")):
        return "network"
    if rel == "etc/pve/storage.cfg":
        return "storage"
    if rel.startswith("etc/apt/"):
        return "apt"
    if rel.startswith("etc/pve/firewall/") or rel.endswith("host.fw"):
        return "firewall"
    if rel in ("etc/pve/jobs.cfg", "etc/pve/vzdump.cron", "etc/pve/replication.cfg"):
        return "jobs"
    if rel in ("etc/pve/user.cfg", "etc/pve/datacenter.cfg", "etc/pve/domains.cfg") \
            or rel.startswith("etc/pve/priv/"):
        return "access"
    if rel.startswith("cmd/"):
        return "info"
    return "other"


def _backup_target_path(rel: str, local_node: str = "") -> Optional[str]:
    """Map a backup-relative path to the absolute target on the live host, or
    None if it isn't safely restorable. ``/etc/pve/nodes/<oldnode>/...`` is
    remapped to the *local* node so a rename doesn't misfile it."""
    if not rel or rel.startswith("cmd/"):
        return None
    m = re.match(r"etc/pve/nodes/[^/]+/(.+)", rel)
    if m and local_node:
        return f"/etc/pve/nodes/{local_node}/{m.group(1)}"
    if rel == "root/.ssh/authorized_keys":
        return "/root/.ssh/authorized_keys"
    if rel.startswith("etc/") or rel.startswith("lib/systemd/network/"):
        return "/" + rel
    return None


def _read_member_bytes(tf: "tarfile.TarFile", member: str) -> Optional[bytes]:
    try:
        info = tf.getmember(member)
    except KeyError:
        return None
    if not info.isfile():
        return None
    f = tf.extractfile(info)
    return f.read() if f else None


def list_backup_contents(backup_file: str) -> Dict[str, Any]:
    """List the config files in a host-config backup, categorized + flagged
    ``restorable`` (the ``cmd/`` command captures are info-only)."""
    out: List[Dict[str, Any]] = []
    try:
        with tarfile.open(backup_file, "r:*") as tf:
            for m in tf.getmembers():
                if not m.isfile():
                    continue
                rel = _rel(m.name)
                if not rel:
                    continue
                cat = _categorize(rel)
                out.append({
                    "member": m.name,
                    "path": rel,
                    "size": m.size,
                    "category": cat,
                    "restorable": _backup_target_path(rel, "x") is not None,
                })
    except (tarfile.TarError, OSError) as e:
        return {"files": [], "error": str(e)[:200]}
    out.sort(key=lambda x: (x["category"], x["path"]))
    return {"files": out}


def read_backup_member(backup_file: str, member: str) -> Dict[str, Any]:
    """Return the text content of a member for preview (capped)."""
    try:
        with tarfile.open(backup_file, "r:*") as tf:
            data = _read_member_bytes(tf, member)
    except (tarfile.TarError, OSError) as e:
        return {"found": False, "error": str(e)[:200]}
    if data is None:
        return {"found": False}
    return {"found": True, "content": data[:200000].decode("utf-8", "replace"),
            "size": len(data), "path": _rel(member)}


def _local_node_name(host: Dict[str, Any]) -> str:
    r = run_command(host, "hostname", timeout=10)
    return (r.get("stdout") or "").strip().split(".")[0] if r.get("success") else ""


def restore_backup_file(host: Dict[str, Any], backup_file: str, member: str,
                        force: bool = False) -> Dict[str, Any]:
    """Extract one file from the backup and write it to its target path on the
    host (byte-exact). Refuses to overwrite an existing file unless ``force``."""
    rel = _rel(member)
    try:
        with tarfile.open(backup_file, "r:*") as tf:
            data = _read_member_bytes(tf, member)
    except (tarfile.TarError, OSError) as e:
        return {"success": False, "error": str(e)[:200]}
    if data is None:
        return {"success": False, "error": "file not found in backup"}
    dest = _backup_target_path(rel, _local_node_name(host))
    if not dest:
        return {"success": False, "error": "file is not restorable"}

    ex = run_command(host, f"[ -e {shlex.quote(dest)} ] && echo __EXISTS__ || echo __NO__",
                     timeout=10)
    exists = "__EXISTS__" in (ex.get("stdout") or "")
    if exists and not force:
        return {"success": False, "exists": True, "dest": dest, "error": "target exists"}

    b64 = base64.b64encode(data).decode("ascii")
    parent = dest.rsplit("/", 1)[0]
    script = (f"mkdir -p {shlex.quote(parent)} 2>/dev/null; "
              f"echo {shlex.quote(b64)} | base64 -d > {shlex.quote(dest)} && echo __OK__")
    w = run_command(host, script, timeout=20)
    ok = "__OK__" in (w.get("stdout") or "")
    return {"success": ok, "exists": exists, "dest": dest,
            "stderr": (w.get("stderr") or "").strip()[:200]}


def restore_all_guest_configs(host: Dict[str, Any], backup_file: str,
                              force: bool = False) -> Dict[str, Any]:
    """Restore every guest config in the backup (skips ones already present
    unless ``force``). One tar open; writes each via restore_guest_config."""
    guests: List = []
    try:
        with tarfile.open(backup_file, "r:*") as tf:
            for m in tf.getmembers():
                mm = re.search(r"/(qemu-server|lxc)/(\d+)\.conf$", m.name)
                if not (m.isfile() and mm):
                    continue
                f = tf.extractfile(m)
                if not f:
                    continue
                gtype = "lxc" if mm.group(1) == "lxc" else "qemu"
                guests.append((gtype, mm.group(2), f.read().decode("utf-8", "replace")))
    except (tarfile.TarError, OSError) as e:
        return {"success": False, "error": str(e)[:200], "results": []}

    results = []
    for gtype, vmid, content in guests:
        res = restore_guest_config(host, gtype, vmid, content, force=force)
        results.append({"vmid": vmid, "type": gtype, **res})
    restored = sum(1 for r in results if r.get("success"))
    skipped = sum(1 for r in results if r.get("exists") and not r.get("success"))
    return {"success": all(r.get("success") for r in results) if results else True,
            "results": results, "restored": restored, "skipped": skipped,
            "total": len(results)}


def read_dpkg_selections(backup_file: str) -> str:
    """Return the captured ``dpkg --get-selections`` text from the backup, or ''."""
    try:
        with tarfile.open(backup_file, "r:*") as tf:
            for m in tf.getmembers():
                if m.isfile() and _rel(m.name) == "cmd/dpkg-selections.txt":
                    f = tf.extractfile(m)
                    return f.read().decode("utf-8", "replace") if f else ""
    except (tarfile.TarError, OSError):
        return ""
    return ""


def _filter_selections(text: str) -> str:
    """Keep only install/hold selection lines -- additive, never deinstall/purge,
    so a reinstall can't *remove* packages from the fresh host."""
    out = []
    for ln in (text or "").splitlines():
        parts = ln.split()
        if len(parts) >= 2 and parts[-1] in ("install", "hold"):
            out.append(f"{parts[0]}\t{parts[-1]}")
    return "\n".join(out)


def reinstall_packages_async(target_host: Dict[str, Any], selections_text: str) -> str:
    """Apply the captured package selection (install/hold only) and run
    ``apt-get dselect-upgrade`` as a background task. Repos must already be in
    place (restore /etc/apt first)."""
    selections = _filter_selections(selections_text)

    def _job(progress, host, sel):
        count = len(sel.splitlines())
        if not sel.strip():
            return {"success": False, "error": "no installable package selections"}
        progress(f"Applying {count} package selections …")
        b64 = base64.b64encode(sel.encode("utf-8")).decode("ascii")
        script = (
            f"echo {shlex.quote(b64)} | base64 -d | dpkg --set-selections\n"
            "DEBIAN_FRONTEND=noninteractive apt-get update -qq || true\n"
            "DEBIAN_FRONTEND=noninteractive apt-get -y dselect-upgrade 2>&1\n"
            "echo __exit=$?"
        )
        progress("Running apt-get dselect-upgrade (this can take a while) …")
        r = run_command(host, script, timeout=3 * 3600)
        out = r.get("stdout") or ""
        m = re.search(r"__exit=(\d+)\s*$", out.strip())
        exit_code = int(m.group(1)) if m else None
        ok = bool(r.get("success")) and exit_code == 0
        progress(f"Finished (exit={exit_code})", ok=ok, exit_code=exit_code)
        cleaned = re.sub(r"__exit=\d+\s*$", "", out).strip()
        return {"success": ok, "exit_code": exit_code, "count": count,
                "log_tail": cleaned[-4000:]}

    return start_task("reinstall_packages", _job, target_host, selections, prefix="dr")
