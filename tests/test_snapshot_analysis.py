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
    assert lg["excluded_datasets"] == 0


def test_replica_dataset_excluded_from_mismatch():
    data = _data(NOW, {"rpool/repl/rpool/ROOT/pve-1": 10})
    a = analyze_snapshots(data, CFG,
                          autosnap_disabled={"rpool/repl/rpool/ROOT/pve-1"})
    lg = a["per_label"]["daily"]
    assert lg["count_mismatches"] == []
    assert lg["excluded_datasets"] == 1


def test_mixed_local_and_replica():
    data = _data(NOW, {"rpool/data/vm-100": 10,
                       "rpool/repl/rpool/data/vm-100": 10})
    a = analyze_snapshots(data, CFG,
                          autosnap_disabled={"rpool/repl/rpool/data/vm-100"})
    lg = a["per_label"]["daily"]
    assert [m["dataset"] for m in lg["count_mismatches"]] == ["rpool/data/vm-100"]
    assert lg["excluded_datasets"] == 1


def test_excluded_counted_even_when_count_matches():
    # the whole point of the fix: a disabled dataset present at a level is
    # excluded/counted there even when its count equals the local keep, so the
    # hint shows consistently (hourly/monthly) and not only where it mismatched
    data = _data(NOW, {"rpool/repl/x": 14})
    a = analyze_snapshots(data, CFG, autosnap_disabled={"rpool/repl/x"})
    lg = a["per_label"]["daily"]
    assert lg["count_mismatches"] == []
    assert lg["excluded_datasets"] == 1


def test_excluded_counted_per_label_across_levels():
    # one disabled dataset with hourly + daily snapshots -> counted at BOTH,
    # regardless of whether each level's count matches its keep
    now = NOW
    hts = _ages(now, 96, step=3600)
    dts = _ages(now, 14, step=86400)
    data = {"datasets": {"rpool/repl/x": {
        "hourly": {"count": 96, "oldest": hts[0], "newest": hts[-1], "timestamps": hts},
        "daily": {"count": 14, "oldest": dts[0], "newest": dts[-1], "timestamps": dts},
    }}, "manual": {}}
    a = analyze_snapshots(data, {"hourly": 96, "daily": 14},
                          autosnap_disabled={"rpool/repl/x"})
    assert a["per_label"]["hourly"]["excluded_datasets"] == 1
    assert a["per_label"]["daily"]["excluded_datasets"] == 1


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


def _hourly_data(newest_age_sec, ds="rpool/repl/x"):
    newest = NOW - newest_age_sec
    ts = [newest - 3600, newest]
    return {"datasets": {ds: {"hourly": {
        "count": 2, "oldest": ts[0], "newest": ts[1], "timestamps": ts}}},
        "manual": {}}


def test_replica_slightly_over_threshold_not_stale():
    # 2h 2m on a replica: inherent replication lag (source snapshot up to 1h
    # old + replication up to 1h later) -- must NOT flicker stale.
    a = analyze_snapshots(_hourly_data(2 * 3600 + 120), {},
                          autosnap_disabled={"rpool/repl/x"})
    assert a["per_label"]["hourly"]["stale_datasets"] == []


def test_replica_beyond_double_threshold_is_stale():
    # beyond 2x (hourly: 4h) the replication has genuinely stalled
    a = analyze_snapshots(_hourly_data(4 * 3600 + 120), {},
                          autosnap_disabled={"rpool/repl/x"})
    stale = a["per_label"]["hourly"]["stale_datasets"]
    assert len(stale) == 1
    # reported threshold reflects the doubled value
    assert stale[0]["threshold_sec"] == 4 * 3600


def test_local_dataset_keeps_original_threshold():
    # 2h 2m locally means the hourly cron missed a run -> still flagged
    a = analyze_snapshots(_hourly_data(2 * 3600 + 120, ds="rpool/data/vm"), {})
    assert len(a["per_label"]["hourly"]["stale_datasets"]) == 1


def test_default_no_exclusions_backwards_compatible():
    data = _data(NOW, {"a": 10, "b": 14})
    a = analyze_snapshots(data, CFG)
    lg = a["per_label"]["daily"]
    assert len(lg["count_mismatches"]) == 1
    assert lg["excluded_datasets"] == 0
