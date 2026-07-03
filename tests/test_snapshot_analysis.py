"""Retention analysis: replica datasets (com.sun:auto-snapshot=false) must not
be compared against the local --keep -- their snapshot counts follow the
source host's retention."""

import time
from app.snapshot_analysis import analyze_snapshots


def _ages(now, count, step=86400):
    """count timestamps ending `step` seconds ago, spaced `step` apart."""
    return [now - step * (i + 1) for i in range(count)][::-1]


def _data(now, datasets):
    """datasets: {name: daily_count} -> get_snapshot_ages()-shaped input."""
    out = {}
    for ds, n in datasets.items():
        ts = _ages(now, n)
        out[ds] = {"daily": {"count": n, "oldest": ts[0], "newest": ts[-1],
                             "timestamps": ts}}
    return {"datasets": out, "manual": {}}


NOW = int(time.time())
CFG = {"daily": 14}


def test_local_dataset_mismatch_still_reported():
    data = _data(NOW, {"rpool/data/vm-100": 10})
    a = analyze_snapshots(data, CFG)
    lg = a["per_label"]["daily"]
    assert len(lg["count_mismatches"]) == 1
    assert lg["count_mismatches"][0]["dataset"] == "rpool/data/vm-100"
    assert lg["count_mismatch_excluded"] == 0


def test_replica_dataset_excluded_from_mismatch():
    data = _data(NOW, {"rpool/repl/rpool/ROOT/pve-1": 10})
    a = analyze_snapshots(data, CFG,
                          autosnap_disabled={"rpool/repl/rpool/ROOT/pve-1"})
    lg = a["per_label"]["daily"]
    assert lg["count_mismatches"] == []
    assert lg["count_mismatch_excluded"] == 1


def test_mixed_local_and_replica():
    data = _data(NOW, {"rpool/data/vm-100": 10,
                       "rpool/repl/rpool/data/vm-100": 10})
    a = analyze_snapshots(data, CFG,
                          autosnap_disabled={"rpool/repl/rpool/data/vm-100"})
    lg = a["per_label"]["daily"]
    assert [m["dataset"] for m in lg["count_mismatches"]] == ["rpool/data/vm-100"]
    assert lg["count_mismatch_excluded"] == 1


def test_replica_with_matching_count_not_counted_as_excluded():
    # no mismatch at all -> nothing to exclude
    data = _data(NOW, {"rpool/repl/x": 14})
    a = analyze_snapshots(data, CFG, autosnap_disabled={"rpool/repl/x"})
    lg = a["per_label"]["daily"]
    assert lg["count_mismatches"] == []
    assert lg["count_mismatch_excluded"] == 0


def test_replica_stale_detection_still_applies():
    # exclusion is ONLY for the count comparison; a stale replica must still
    # be flagged (it means replication/source snapshots stopped).
    old = NOW - 10 * 86400
    ts = [old - 86400, old]
    data = {"datasets": {"rpool/repl/x": {"daily": {
        "count": 2, "oldest": ts[0], "newest": ts[1], "timestamps": ts}}},
        "manual": {}}
    a = analyze_snapshots(data, CFG, autosnap_disabled={"rpool/repl/x"})
    lg = a["per_label"]["daily"]
    assert any(s["dataset"] == "rpool/repl/x" for s in lg["stale_datasets"])


def test_default_no_exclusions_backwards_compatible():
    data = _data(NOW, {"a": 10, "b": 14})
    a = analyze_snapshots(data, CFG)
    lg = a["per_label"]["daily"]
    assert len(lg["count_mismatches"]) == 1
    assert lg["count_mismatch_excluded"] == 0
