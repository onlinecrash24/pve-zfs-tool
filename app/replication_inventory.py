"""Which host holds the original of a guest, and where are its copies?

Answered through snapshot GUIDs. `zfs send`/`recv` carries the guid of every
snapshot across, so the same snapshot has the same guid on the source and on
every replica -- that is exactly how `zfs send -i` finds its common base. Two
datasets sharing guids therefore *are* the same data lineage, no matter what the
datasets are called or which pool they sit in. Names, paths and pool layouts all
lie; guids do not.

The source is the host holding the newest snapshot of a lineage. Copies are
compared against it by guid: which snapshots they share, which ones the copy is
missing, and how far behind it is.

The bashclub-zsync configs are read as a second, independent signal. They say
what was *intended* (who pulls from whom); the guids say what actually happened.
Where the two disagree -- a host configured as a target but holding the newest
snapshots -- that discrepancy is itself a finding, and the kind that otherwise
only surfaces when a restore is attempted.

Everything that decides anything is a pure function here, so the correlation is
unit tested without touching a host.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.ssh_manager import run_command

# Snapshots created by the migration feature: they exist on both sides by
# design and would otherwise look like a replication relationship.
_MIGRATE_SNAP_RE = re.compile(r"^migrate-\d{8}-\d{6}$")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_snapshot_guids(stdout: str) -> List[Dict[str, Any]]:
    """Rows of ``zfs list -Hp -t snapshot -o name,guid,creation``.

    Returns ``[{dataset, snapshot, guid, creation}]``. Unparsable lines are
    skipped rather than aborting the whole host -- one odd line should not cost
    the inventory an entire machine.
    """
    out: List[Dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3 or "@" not in parts[0]:
            continue
        full = parts[0].strip()
        dataset, snapshot = full.split("@", 1)
        try:
            creation = int(parts[2])
        except (ValueError, TypeError):
            continue
        guid = parts[1].strip()
        if not guid:
            continue
        out.append({"dataset": dataset, "snapshot": snapshot,
                    "guid": guid, "creation": creation, "full": full})
    return out


# ---------------------------------------------------------------------------
# Correlation
# ---------------------------------------------------------------------------

def build_guid_index(per_host: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """guid -> every place that guid exists.

    ``per_host`` maps host address to its parsed snapshot rows.
    """
    index: Dict[str, List[Dict[str, Any]]] = {}
    for host, rows in (per_host or {}).items():
        for r in rows or []:
            index.setdefault(r["guid"], []).append({"host": host, **r})
    return index


def _lineage_key(host: str, dataset: str) -> Tuple[str, str]:
    return (host, dataset)


def group_lineages(per_host: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Group (host, dataset) pairs that share snapshot guids into lineages.

    A lineage is one guest disk and all of its copies. Union-find over the guid
    index: two datasets that share even one guid belong together, because a
    shared guid can only come from a send/recv of the same data.

    Migration snapshots are ignored as evidence -- they exist on both sides by
    construction and would link datasets that are not replicas of each other.
    """
    parent: Dict[Tuple[str, str], Tuple[str, str]] = {}

    def find(k):
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    members: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for host, rows in (per_host or {}).items():
        for r in rows or []:
            key = _lineage_key(host, r["dataset"])
            members.setdefault(key, []).append(r)
            find(key)

    index = build_guid_index(per_host)
    for guid, places in index.items():
        if len(places) < 2:
            continue
        if any(_MIGRATE_SNAP_RE.match(p["snapshot"]) for p in places):
            continue
        first = _lineage_key(places[0]["host"], places[0]["dataset"])
        for p in places[1:]:
            union(first, _lineage_key(p["host"], p["dataset"]))

    grouped: Dict[Tuple[str, str], List[Tuple[str, str]]] = {}
    for key in members:
        grouped.setdefault(find(key), []).append(key)

    out = []
    for root, keys in grouped.items():
        copies = []
        for host, dataset in sorted(keys):
            snaps = sorted(members[(host, dataset)], key=lambda r: r["creation"])
            copies.append({
                "host": host,
                "dataset": dataset,
                "snapshot_count": len(snaps),
                "oldest": snaps[0]["creation"] if snaps else None,
                "newest": snaps[-1]["creation"] if snaps else None,
                "guids": {s["guid"] for s in snaps},
                "newest_snapshot": snaps[-1]["snapshot"] if snaps else "",
            })
        out.append({"copies": copies})
    return out


def detect_source(copies: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The copy holding the newest snapshot -- that is the source.

    Ties (identical newest timestamps, e.g. a replica that just caught up) are
    broken by snapshot count, then by host name, so the answer is stable across
    runs rather than depending on dict ordering.
    """
    candidates = [c for c in (copies or []) if c.get("newest") is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda c: (c["newest"], c["snapshot_count"],
                                          c["host"]))


def compare_copy(source: Dict[str, Any], copy: Dict[str, Any]) -> Dict[str, Any]:
    """How far a copy trails its source, measured in guids and seconds."""
    src_guids = source.get("guids") or set()
    cp_guids = copy.get("guids") or set()
    shared = src_guids & cp_guids
    missing = src_guids - cp_guids
    lag = None
    if source.get("newest") is not None and copy.get("newest") is not None:
        lag = max(0, int(source["newest"]) - int(copy["newest"]))
    return {
        "shared_snapshots": len(shared),
        "missing_from_source": len(missing),
        "lag_seconds": lag,
        "in_sync": bool(shared) and not missing,
    }


# ---------------------------------------------------------------------------
# Guest mapping + matrix
# ---------------------------------------------------------------------------

def build_matrix(per_host: Dict[str, List[Dict[str, Any]]],
                 guests_by_host: Optional[Dict[str, List[Dict[str, Any]]]] = None,
                 configured: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """The full picture: one entry per guest disk with its source and copies.

    ``guests_by_host`` maps host address to ``get_pve_vms``/``get_pve_cts``
    output so copies can be labelled with the guest's name. ``configured`` is
    the list of intended source->target pairs from the replication configs,
    used only to flag disagreement.
    """
    from app.dr import guest_ref_from_dataset

    guest_names = {}
    for host, guests in (guests_by_host or {}).items():
        for g in guests or []:
            guest_names[(host, str(g.get("vmid")))] = g.get("name", "")

    entries = []
    for lineage in group_lineages(per_host):
        copies = lineage["copies"]
        source = detect_source(copies)
        if source is None:
            continue
        gtype, vmid = guest_ref_from_dataset(source["dataset"])
        # Only guest disks. Pool roots, rpool/ROOT/pve-1, var-lib-vz and the
        # like are replicated too, but they are not VMs or containers and
        # listing them as "? (VM)" buries the entries that matter.
        if not vmid:
            continue
        name = guest_names.get((source["host"], str(vmid)), "")

        rows = []
        for c in copies:
            is_src = (c["host"], c["dataset"]) == (source["host"], source["dataset"])
            cmp_ = {"shared_snapshots": c["snapshot_count"], "missing_from_source": 0,
                    "lag_seconds": 0, "in_sync": True} if is_src else compare_copy(source, c)
            rows.append({
                "host": c["host"], "dataset": c["dataset"],
                "is_source": is_src,
                "snapshot_count": c["snapshot_count"],
                "oldest": c["oldest"], "newest": c["newest"],
                "newest_snapshot": c["newest_snapshot"],
                **cmp_,
            })
        rows.sort(key=lambda r: (not r["is_source"], r["host"]))

        entries.append({
            "vmid": vmid, "guest_type": gtype, "guest_name": name,
            "source_host": source["host"], "source_dataset": source["dataset"],
            "copy_count": len(rows) - 1,
            "copies": rows,
            "config_mismatch": _config_mismatch(source, rows, configured),
        })

    # Guests PVE knows about that produced no lineage at all -- they have no
    # snapshots yet, so they never appear in a snapshot listing. That is the
    # worst case, not a reason to hide them: no snapshots means no local
    # rollback AND nothing that could ever have been replicated.
    seen = {(e["source_host"], str(e["vmid"])) for e in entries}
    for host, guests in (guests_by_host or {}).items():
        for g in guests or []:
            vmid = str(g.get("vmid") or "")
            if not vmid or (host, vmid) in seen:
                continue
            entries.append({
                "vmid": vmid,
                "guest_type": "lxc" if g.get("type") == "lxc" else "qemu",
                "guest_name": g.get("name", ""),
                "source_host": host, "source_dataset": "",
                "copy_count": 0, "copies": [], "config_mismatch": "",
                "no_snapshots": True,
            })

    for e in entries:
        e.setdefault("no_snapshots", False)
    entries.sort(key=lambda e: (e["source_host"],
                                int(e["vmid"]) if str(e["vmid"] or "").isdigit() else 0,
                                e["source_dataset"]))
    return {"guests": entries, "generated": True}


def filter_matrix(matrix: Dict[str, Any], source_host: Optional[str] = None,
                  only_when_replicating: bool = True) -> Dict[str, Any]:
    """Narrow the matrix to what is worth looking at.

    ``source_host`` keeps only guests whose ORIGIN is that host, so the view and
    the report describe one host's data and where it goes -- listing both
    directions at once shows every guest twice and reads as duplicates.

    Once a host replicates anything, ALL of its guests are listed, including the
    ones without a copy: a guest missing from an otherwise working replication
    set is precisely the omission worth catching. A host that replicates nothing
    at all has no replication story, so ``only_when_replicating`` yields an
    empty list rather than flagging every guest on a standalone machine.
    """
    guests = matrix.get("guests") or []
    if source_host:
        guests = [g for g in guests if g["source_host"] == source_host]
    replicated = [g for g in guests if g["copy_count"] > 0]
    without = [g for g in guests if g["copy_count"] == 0]
    if only_when_replicating and not replicated:
        guests = []
        without = []
    else:
        # Replicated first, then the gaps -- sorted so the omissions stand out
        # at the end instead of being scattered through the list.
        guests = replicated + without
    out = dict(matrix)
    out["guests"] = guests
    out["replicated_count"] = len(replicated)
    out["without_copy_count"] = len(without)
    out["without_copy_guests"] = [
        {"vmid": g["vmid"], "guest_name": g["guest_name"],
         "guest_type": g["guest_type"], "source_dataset": g["source_dataset"]}
        for g in without
    ]
    out["source_host"] = source_host or ""
    return out


def source_hosts(matrix: Dict[str, Any]) -> List[str]:
    """Hosts that are the origin of at least one replicated guest."""
    return sorted({g["source_host"] for g in (matrix.get("guests") or [])
                   if g["copy_count"] > 0})


def _config_mismatch(source, rows, configured) -> str:
    """Non-empty when the intended direction contradicts what the guids show.

    bashclub-zsync records a target pulling from a source; if a host that is
    only ever a target holds the newest snapshots, replication has either
    reversed or stopped -- worth naming either way.
    """
    if not configured:
        return ""
    src_host = source["host"]
    for cfg in configured:
        target_host = (cfg.get("target_host") or "").strip()
        source_host = (cfg.get("source") or "").strip()
        if not target_host or not source_host:
            continue
        if src_host == target_host and any(r["host"] == source_host for r in rows):
            return (f"holds the newest snapshots on {src_host}, which is "
                    f"configured as a replication target of {source_host}")
    return ""


# ---------------------------------------------------------------------------
# Condensation for the AI report
# ---------------------------------------------------------------------------

def condense_for_report(matrix: Dict[str, Any], max_guests: int = 120) -> Dict[str, Any]:
    """Shrink the matrix to what an LLM can actually reason about.

    At 15-minute snapshots across several hosts the raw listing runs to six
    figures; the report prompt is capped at ~30k characters. All correlation has
    already happened in Python, so the model only needs the per-guest summary --
    counts and timestamps, never individual snapshots.
    """
    guests = matrix.get("guests") or []
    out = []
    for g in guests[:max_guests]:
        out.append({
            "vmid": g["vmid"], "type": g["guest_type"], "name": g["guest_name"],
            "source": f"{g['source_host']}:{g['source_dataset']}",
            "copies": [
                {"host": c["host"], "role": "source" if c["is_source"] else "copy",
                 "snapshots": c["snapshot_count"],
                 "newest_age_seconds": None if c["newest"] is None else c["newest"],
                 "lag_seconds": c["lag_seconds"],
                 "missing_from_source": c["missing_from_source"]}
                for c in g["copies"]
            ],
            "copy_count": g["copy_count"],
            "config_mismatch": g["config_mismatch"],
        })
    return {
        "guests": out,
        "guest_count": len(guests),
        "truncated": len(guests) > max_guests,
        "guests_without_copy": sum(1 for g in guests if g["copy_count"] == 0),
    }


# ---------------------------------------------------------------------------
# Collection (SSH)
# ---------------------------------------------------------------------------

def collect_host_snapshots(host: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every snapshot on one host with its guid. One command, one round trip."""
    # Cached briefly: the listing is large and the view is clicked repeatedly,
    # while the data only changes when a new snapshot is taken.
    r = run_command(host, "zfs list -Hp -t snapshot -o name,guid,creation 2>/dev/null",
                    timeout=120, cache_ttl=60)
    if not r.get("success"):
        return []
    return parse_snapshot_guids(r.get("stdout", ""))


def collect_inventory(hosts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Gather snapshots, guests and replication configs from every host."""
    from app.zfs_commands import get_pve_vms, get_pve_cts
    from app.replication import list_configs

    per_host: Dict[str, List[Dict[str, Any]]] = {}
    guests_by_host: Dict[str, List[Dict[str, Any]]] = {}
    configured: List[Dict[str, str]] = []
    unreachable: List[str] = []

    for h in hosts or []:
        addr = h.get("address", "")
        rows = collect_host_snapshots(h)
        if not rows:
            unreachable.append(addr)
        per_host[addr] = rows
        try:
            guests_by_host[addr] = list(get_pve_vms(h) or []) + list(get_pve_cts(h) or [])
        except Exception:
            guests_by_host[addr] = []
        try:
            for cfg in (list_configs(h).get("configs") or []):
                configured.append({"target_host": addr, "source": cfg.get("source", ""),
                                   "target": cfg.get("target", "")})
        except Exception:
            pass

    matrix = build_matrix(per_host, guests_by_host, configured)
    matrix["hosts"] = list(per_host.keys())
    matrix["hosts_without_data"] = unreachable
    matrix["snapshot_count"] = sum(len(v) for v in per_host.values())
    return matrix
