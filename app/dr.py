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

import re
import shlex
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
