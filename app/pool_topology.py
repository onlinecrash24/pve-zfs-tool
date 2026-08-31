"""How a pool is built -- and whether one device failure would destroy it.

The monitor asks "is anything broken right now?": health transitions, capacity,
error counters. It never asks how the pool is *built*. Those are different
questions, and the second one only gets asked after the disaster.

An unmirrored special vdev is the sharpest example. It holds the pool's metadata
and small blocks, so losing that one device loses the entire pool -- every
dataset, every snapshot, on every other disk. Until the moment it dies it
reports a cheerful ONLINE, and `zpool status` is shown to the user as raw text
that nobody parses. The same is true of a pool striped across bare disks.

Everything here is a pure function over `zpool status` output, so the parsing is
tested against real layouts without touching a host.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

# Section headers that introduce a tier of auxiliary vdevs. Singular and plural
# both appear depending on the ZFS version, so both are accepted.
_TIER_HEADERS = {
    "logs": "log", "log": "log",
    "cache": "cache",
    "special": "special",
    "dedup": "dedup",
    "spares": "spare", "spare": "spare",
}

# Device states zpool status prints in the second column. Their presence is what
# separates a device line from a section header -- headers have no state.
_STATES = {"ONLINE", "DEGRADED", "FAULTED", "UNAVAIL", "OFFLINE", "REMOVED",
           "AVAIL", "INUSE", "SUSPENDED", "SPLIT"}

_MIRROR_RE = re.compile(r"^mirror(-\d+)?$", re.IGNORECASE)
_RAIDZ_RE = re.compile(r"^raidz([123])?(-\d+)?$", re.IGNORECASE)
_DRAID_RE = re.compile(r"^draid([123])?[^\s]*$", re.IGNORECASE)

# Losing one device of these tiers costs the whole pool, so a bare device there
# is a single point of total failure rather than a redundancy question.
FATAL_TIERS = ("data", "special", "dedup")


def _indent(line: str) -> int:
    """Indent depth in the config block. zpool status uses a leading tab and
    two spaces per level; measuring the expanded prefix keeps this working even
    where the tab is absent or the spacing differs slightly."""
    expanded = line.replace("\t", "    ")
    return len(expanded) - len(expanded.lstrip(" "))


def _redundancy(name: str) -> str:
    """What a vdev container name says about redundancy."""
    if _MIRROR_RE.match(name):
        return "mirror"
    m = _RAIDZ_RE.match(name)
    if m:
        return "raidz" + (m.group(1) or "1")
    if _DRAID_RE.match(name):
        return "draid"
    return "none"


def parse_topology(status_text: str) -> Dict[str, List[Dict[str, Any]]]:
    """`zpool status` output to ``{pool: [{tier, name, redundancy, devices}]}``.

    The structure lives in the indentation, not in keywords. A tier header
    (``logs``, ``cache``, ``special``, ``dedup``, ``spares``) sits at the same
    depth as a top-level vdev but carries **no state column** -- that absence is
    the reliable way to tell the two apart, and it survives the naming
    differences between ZFS versions.

    A bare device directly under the pool (a stripe) is reported as its own
    vdev with ``redundancy: none``, because that is exactly what it is.
    """
    pools: Dict[str, List[Dict[str, Any]]] = {}
    pool = None
    in_config = False
    tier = "data"
    pool_indent = None
    vdev_indent = None
    current: Dict[str, Any] | None = None

    for raw in (status_text or "").splitlines():
        stripped = raw.strip()

        # `  pool: tank` opens a new pool; `config:` opens its device tree, and
        # anything after `errors:` belongs to neither.
        m = re.match(r"^\s*pool:\s*(\S+)", raw)
        if m:
            pool = m.group(1)
            pools.setdefault(pool, [])
            in_config = False
            tier = "data"
            pool_indent = vdev_indent = None
            current = None
            continue
        if re.match(r"^\s*config:\s*$", raw):
            in_config = True
            continue
        if re.match(r"^\s*errors:", raw):
            in_config = False
            current = None
            continue
        if not in_config or not stripped or pool is None:
            continue

        parts = stripped.split()
        name = parts[0]
        if name == "NAME":                     # column header
            continue

        depth = _indent(raw)
        state = parts[1].upper() if len(parts) > 1 else ""
        has_state = state in _STATES

        # The pool's own line: the first line carrying the pool name.
        if name == pool and has_state and pool_indent is None:
            pool_indent = depth
            continue
        if pool_indent is None:
            continue

        # A tier header: same depth as a top-level vdev, but no state column.
        if not has_state and name.lower() in _TIER_HEADERS:
            tier = _TIER_HEADERS[name.lower()]
            current = None
            continue

        if not has_state:
            continue                            # notes, wrapped text, noise

        if vdev_indent is None:
            vdev_indent = depth

        if depth <= vdev_indent:
            # A top-level entry: either a container (mirror-0, raidz2-0) or a
            # bare device standing on its own.
            red = _redundancy(name)
            current = {"tier": tier, "name": name, "redundancy": red,
                       "state": state, "devices": [] if red != "none" else [name]}
            pools[pool].append(current)
        elif current is not None:
            current["devices"].append(name)

    return pools


def redundancy_findings(topology: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Vdevs whose loss would cost more than they look like they would.

    Severity follows what the device actually holds:

    * ``special`` / ``dedup`` without redundancy -- **the whole pool is gone**.
      They carry metadata; the surviving data disks are unreadable without them.
    * ``data`` without redundancy -- a stripe. One disk takes the pool with it.
    * ``log`` without redundancy -- a warning, not a catastrophe: on any current
      ZFS the pool survives and only the last synchronous writes that had been
      acknowledged but not yet written out are at risk.
    * ``cache`` and ``spare`` -- never a finding. L2ARC holds nothing but copies,
      and a spare holds nothing at all.
    """
    out: List[Dict[str, Any]] = []
    for pool, vdevs in sorted((topology or {}).items()):
        for v in vdevs:
            if v["redundancy"] != "none":
                continue
            tier = v["tier"]
            if tier in ("cache", "spare"):
                continue
            out.append({
                "pool": pool,
                "vdev": v["name"],
                "tier": tier,
                "severity": "crit" if tier in FATAL_TIERS else "warn",
                "reason": ("pool_loss" if tier in FATAL_TIERS else "sync_writes"),
            })
    return out


def summarize(topology: Dict[str, List[Dict[str, Any]]], pool: str) -> Dict[str, Any]:
    """One pool's structure plus its findings, for the pool detail view."""
    vdevs = (topology or {}).get(pool, [])
    findings = [f for f in redundancy_findings({pool: vdevs})]
    return {
        "vdevs": vdevs,
        "findings": findings,
        "worst": ("crit" if any(f["severity"] == "crit" for f in findings)
                  else "warn" if findings else "ok"),
    }
