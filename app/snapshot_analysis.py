"""Snapshot health analysis — used by both AI reports and the web UI.

Provides per-dataset, per-label retention checks including:
- Stale snapshot detection (newest snapshot too old)
- Gap detection (holes in the snapshot chain)
- Count mismatches (actual vs configured --keep)
- Missing label detection (expected labels absent)
- Manual/irregular snapshot detection
"""

import time

# Max allowed age per label (seconds) before warning
MAX_AGE = {
    "frequent": 3600,     # 1 hour
    "hourly":   7200,     # 2 hours
    "daily":    90000,    # 25 hours
    "weekly":   691200,   # 8 days
    "monthly":  2764800,  # 32 days
}

# Gap detection: a hole larger than GAP_FACTOR x the dataset's OWN typical
# cadence (the median gap between its snapshots) is suspicious. The cadence is
# derived empirically per dataset+label instead of hardcoded, so it adapts to
# the real cron schedule (15 vs 30 min etc.) and never flags the normal
# spacing as a gap.
GAP_FACTOR = 1.5

# Need at least this many gaps (>= this+1 snapshots) to estimate a reliable
# median cadence; below it, a single hole would skew the median itself.
GAP_MIN_DELTAS = 3

# Replica datasets get double the stale threshold: their newest snapshot is
# inherently older than local ones (source snapshot up to 1 interval old +
# replication up to 1 interval later), so at 1x they flicker stale right
# before each replication run (e.g. hourly 2h 2m vs 2h 0m). 2x still catches
# a genuinely stalled replication, matching the replication monitor's WARN.
REPLICA_STALE_FACTOR = 2

# A snapshot at (or before) a dataset's own creation is a base / received
# snapshot -- e.g. the held replication-base snapshot a migration leaves behind,
# whose creation travels with the send stream and predates the local dataset.
# The empty stretch between such a base snapshot and the first regularly
# scheduled snapshot is the dataset's cold-start / pre-existence period on this
# host, not a snapshot outage, so that "gap" must not be reported. The small
# tolerance absorbs source/destination clock skew (a received snapshot's
# creation is source time; the local dataset creation is the receive time).
LEADING_GAP_TOLERANCE = 3600  # 1 hour


def format_age(seconds):
    """Format seconds into a human-readable age string."""
    seconds = int(seconds)
    if seconds < 0:
        return "0s"
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days}d {hours}h"


def analyze_snapshots(snap_age_data, retention_cfg=None, autosnap_disabled=None,
                      dataset_creation=None):
    """Analyze snapshot health from get_snapshot_ages() output.

    Args:
        snap_age_data: Return value of get_snapshot_ages()
            {"datasets": {ds: {label: {count, oldest, newest, timestamps}}}, "manual": {...}}
        retention_cfg: dict of configured --keep values per label from cron
            e.g. {"frequent": 12, "hourly": 96, "daily": 10}
        autosnap_disabled: set of datasets with com.sun:auto-snapshot=false
            (e.g. zsync replication targets). Their snapshot counts follow the
            *source* host's retention, not the local cron --keep, so they are
            excluded from the count-mismatch comparison (counted per label in
            ``excluded_datasets`` -- every present disabled dataset, not just
            the mismatching ones). Stale detection still applies but with
            REPLICA_STALE_FACTOR x the threshold (replication lag).
        dataset_creation: optional {dataset: creation_epoch} (from
            get_dataset_creations()). Used to suppress a dataset's cold-start
            gap: when the snapshot before a hole is at/before the dataset's
            creation it is a base / received snapshot (e.g. a held
            replication-base snapshot), so the emptiness is the pre-existence
            period on this host, not an outage. Without it, gap detection
            behaves as before (every hole reported).

    Returns:
        dict with: per_label, missing_labels, manual_snapshots, datasets_analyzed
    """
    if retention_cfg is None:
        retention_cfg = {}
    if autosnap_disabled is None:
        autosnap_disabled = set()
    if dataset_creation is None:
        dataset_creation = {}

    now_epoch = int(time.time())
    snap_ages = snap_age_data.get("datasets", {})
    manual_snaps = snap_age_data.get("manual", {})

    label_global = {}
    datasets_without_labels = {}
    all_expected_labels = set(retention_cfg.keys())

    for ds, labels_data in snap_ages.items():
        present_labels = set(labels_data.keys())

        # Check for missing labels
        missing = all_expected_labels - present_labels
        if missing:
            for ml in missing:
                if ml not in datasets_without_labels:
                    datasets_without_labels[ml] = []
                datasets_without_labels[ml].append(ds)

        for label, info in labels_data.items():
            count = info["count"]
            newest_epoch = info["newest"]
            age_sec = now_epoch - newest_epoch
            timestamps = info.get("timestamps", [])

            # Global label stats
            if label not in label_global:
                label_global[label] = {
                    "total_snapshots": 0,
                    "dataset_count": 0,
                    "configured_keep": retention_cfg.get(label, None),
                    "oldest_age_sec": 0,
                    "newest_age_sec": age_sec,
                    "count_mismatches": [],
                    "excluded_datasets": 0,
                    "stale_datasets": [],
                    "gaps": [],
                }
            lg = label_global[label]
            lg["total_snapshots"] += count
            lg["dataset_count"] += 1

            if age_sec < lg["newest_age_sec"]:
                lg["newest_age_sec"] = age_sec
            if age_sec > lg["oldest_age_sec"]:
                lg["oldest_age_sec"] = age_sec

            # Datasets with com.sun:auto-snapshot=false (replication targets or
            # manually disabled) follow the source's retention / a manual
            # setting, not the local --keep. Exclude them from the count check
            # entirely and count them per label so the exclusion is visible at
            # EVERY level -- not only where a count happened to mismatch.
            if ds in autosnap_disabled:
                lg["excluded_datasets"] += 1
            else:
                configured = retention_cfg.get(label)
                if configured and count != configured:
                    lg["count_mismatches"].append({
                        "dataset": ds,
                        "actual": count,
                        "configured": configured,
                    })

            # Check: newest snapshot too old? Replicas get 2x (see
            # REPLICA_STALE_FACTOR) to absorb inherent replication lag.
            threshold = MAX_AGE.get(label, 2764800)
            if ds in autosnap_disabled:
                threshold *= REPLICA_STALE_FACTOR
            if age_sec > threshold:
                lg["stale_datasets"].append({
                    "dataset": ds,
                    "age": format_age(age_sec),
                    "age_sec": age_sec,
                    "threshold": format_age(threshold),
                    "threshold_sec": threshold,
                })

            # Gap detection: a hole > GAP_FACTOR x this dataset's median cadence.
            deltas = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
            if len(deltas) >= GAP_MIN_DELTAS:
                cadence = sorted(deltas)[len(deltas) // 2]   # robust median-ish
                gap_threshold = cadence * GAP_FACTOR
                ds_creation = dataset_creation.get(ds)
                if gap_threshold > 0:
                    for idx, delta in enumerate(deltas):
                        if delta <= gap_threshold:
                            continue
                        # Skip a dataset's cold-start gap: when the snapshot
                        # before the hole is at/before the dataset's creation it
                        # is a base / received snapshot (e.g. a held
                        # replication-base snapshot), so the emptiness is the
                        # period before regular snapshotting began here, not an
                        # outage. Migrated / replicated disks are the usual cause.
                        if (ds_creation is not None
                                and timestamps[idx] <= ds_creation + LEADING_GAP_TOLERANCE):
                            continue
                        lg["gaps"].append({
                            "dataset": ds,
                            "gap_hours": round(delta / 3600, 1),
                            "from_epoch": timestamps[idx],
                            "to_epoch": timestamps[idx + 1],
                            "from_age": format_age(now_epoch - timestamps[idx]),
                            "to_age": format_age(now_epoch - timestamps[idx + 1]),
                            "threshold_hours": round(gap_threshold / 3600, 1),
                        })

    # Build summary
    for label, lg in label_global.items():
        lg["per_dataset_avg"] = round(lg["total_snapshots"] / max(lg["dataset_count"], 1))
        lg["newest_age_human"] = format_age(lg["newest_age_sec"])

    # Missing labels summary
    missing_labels_summary = {}
    for label, ds_list in datasets_without_labels.items():
        missing_labels_summary[label] = {
            "count": len(ds_list),
            "examples": ds_list[:10],
        }

    # Manual / irregular snapshots
    manual_summary = {}
    if manual_snaps:
        total_manual = sum(len(v) for v in manual_snaps.values())
        manual_details = []
        for ds, snaps in list(manual_snaps.items())[:15]:
            for s in snaps[:5]:
                age_s = now_epoch - s["creation"]
                manual_details.append({
                    "dataset": ds,
                    "name": s["name"],
                    "age": format_age(age_s),
                    "age_sec": age_s,
                    "creation": s["creation"],
                })
        manual_summary = {
            "total_count": total_manual,
            "dataset_count": len(manual_snaps),
            "examples": manual_details[:20],
        }

    return {
        "per_label": label_global,
        "missing_labels": missing_labels_summary,
        "manual_snapshots": manual_summary,
        "datasets_analyzed": len(snap_ages),
        "timestamp": now_epoch,
    }


def truncate_for_ai(analysis):
    """Truncate analysis lists to avoid token bloat for AI reports."""
    result = dict(analysis)
    result["per_label"] = {}
    for label, lg in analysis.get("per_label", {}).items():
        lg_copy = dict(lg)
        for key in ("stale_datasets", "count_mismatches", "gaps"):
            items = lg_copy.get(key, [])
            if len(items) > 5:
                total = len(items)
                lg_copy[key] = items[:5]
                lg_copy[key].append({"note": f"... and {total - 5} more"})
        result["per_label"][label] = lg_copy
    if "manual_snapshots" in result and result["manual_snapshots"]:
        ms = dict(result["manual_snapshots"])
        if len(ms.get("examples", [])) > 10:
            ms["examples"] = ms["examples"][:10]
        result["manual_snapshots"] = ms
    return result
