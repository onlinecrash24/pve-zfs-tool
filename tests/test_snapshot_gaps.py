"""Retention gap analysis must not report a migrated/replicated dataset's
cold-start emptiness as a snapshot outage.

A held replication-base snapshot travels with the send stream and keeps its
source creation, which predates the local dataset; it sits far before the recent
frequent snapshots. The hole between them is the dataset's pre-existence period
on this host, not a '90 days without rollback' gap. Regression for
tank/data/vm-113-disk-0 (base snapshot ~90 days before the current burst)."""

from app.snapshot_analysis import analyze_snapshots

DAY = 86400
VM = "tank/data/vm-113-disk-0"


def _label(timestamps):
    ts = sorted(timestamps)
    return {"count": len(ts), "oldest": ts[0], "newest": ts[-1], "timestamps": ts}


def _data(ds_name, timestamps):
    return {"datasets": {ds_name: {"frequent": _label(timestamps)}}, "manual": {}}


def _gaps(res):
    return res["per_label"]["frequent"]["gaps"]


def test_leading_gap_after_migration_is_suppressed():
    # base snapshot ~1.5h before the local creation (source time carried over),
    # then a 90-day hole, then a recent 15-min frequent burst.
    creation = 1_777_120_000
    base = creation - 90 * 60
    recent = creation + 90 * DAY
    ts = [base, recent, recent + 900, recent + 1800, recent + 2700]
    res = analyze_snapshots(_data(VM, ts), retention_cfg={"frequent": 12},
                            dataset_creation={VM: creation})
    assert _gaps(res) == []


def test_without_creation_the_gap_is_still_reported():
    # backward compatibility: no creation known -> old behaviour (hole reported)
    creation = 1_777_120_000
    base = creation - 90 * 60
    recent = creation + 90 * DAY
    ts = [base, recent, recent + 900, recent + 1800, recent + 2700]
    res = analyze_snapshots(_data(VM, ts), retention_cfg={"frequent": 12})
    gaps = _gaps(res)
    assert len(gaps) == 1
    assert gaps[0]["from_epoch"] == base


def test_genuine_mid_life_outage_is_kept():
    # created long ago, snapshots fine, then a real 5-day outage in steady state
    creation = 1_777_120_000
    start = creation + 200 * DAY
    normal = [start + i * 900 for i in range(4)]      # 15-min cadence
    after = normal[-1] + 5 * DAY                       # real hole
    ts = normal + [after, after + 900, after + 1800]
    res = analyze_snapshots(_data(VM, ts), retention_cfg={"frequent": 12},
                            dataset_creation={VM: creation})
    gaps = _gaps(res)
    assert len(gaps) == 1
    assert gaps[0]["from_epoch"] == normal[-1]


def test_outage_shortly_but_clearly_after_creation_is_kept():
    # pre-gap snapshot is 10 days past creation -> well outside the cold-start
    # tolerance -> a real gap, still reported even though creation is provided.
    creation = 1_777_120_000
    normal = [creation + 10 * DAY + i * 900 for i in range(4)]
    after = normal[-1] + 3 * DAY
    ts = normal + [after, after + 900, after + 1800]
    res = analyze_snapshots(_data("pool/live", ts), retention_cfg={"frequent": 12},
                            dataset_creation={"pool/live": creation})
    assert len(_gaps(res)) == 1


def test_unknown_dataset_creation_does_not_suppress():
    # creation map present but missing this dataset -> treated as unknown ->
    # gap reported (no accidental suppression from an empty lookup).
    creation = 1_777_120_000
    base = creation - 90 * 60
    recent = creation + 90 * DAY
    ts = [base, recent, recent + 900, recent + 1800, recent + 2700]
    res = analyze_snapshots(_data(VM, ts), retention_cfg={"frequent": 12},
                            dataset_creation={"some/other/dataset": creation})
    assert len(_gaps(res)) == 1
