"""How much space do the snapshots occupy?

Adding up the per-snapshot `used` column answers a different question: that
value is what destroying THAT ONE snapshot would free, so blocks referenced by
several snapshots belong to none of them. Three snapshots taken before the data
was deleted each report 0 while the dataset's usedbysnapshots is the full
amount -- the sum would tell the user "0" while the snapshots hold everything.
The dataset property is the honest number.
"""

from app import zfs_commands as zc


SPACE_OUT = "rpool\t0\nrpool/data\t1024\nrpool/data/subvol-253-disk-0\t524288000\ntank/vm\t2048\n"


def test_parse_used_by_snapshots():
    rows = zc.parse_snapshot_space(SPACE_OUT)
    assert ("rpool/data/subvol-253-disk-0", 524288000) in rows
    assert len(rows) == 4


def test_parse_skips_garbage():
    assert zc.parse_snapshot_space("broken line\nrpool\tnotanumber\nrpool/x\t5") == [("rpool/x", 5)]
    assert zc.parse_snapshot_space("") == []
    assert zc.parse_snapshot_space(None) == []


def test_totals_are_grouped_per_pool():
    by_pool = zc.snapshot_space_by_pool(zc.parse_snapshot_space(SPACE_OUT))
    assert by_pool["rpool"] == 0 + 1024 + 524288000
    assert by_pool["tank"] == 2048


def test_uses_the_dataset_property_not_the_snapshot_column(monkeypatch):
    seen = {}

    def fake_run(host, cmd, **kw):
        seen["cmd"] = cmd
        return {"success": True, "stdout": SPACE_OUT}

    monkeypatch.setattr(zc, "run_command", fake_run)
    res = zc.get_snapshot_space({"address": "h"})
    # usedsnap == usedbysnapshots, queried on datasets -- NOT `zfs list -t snapshot`
    assert "usedsnap" in seen["cmd"]
    assert "-t filesystem,volume" in seen["cmd"]
    assert "-t snapshot" not in seen["cmd"]
    assert "-Hp" in seen["cmd"]            # parsable, exact bytes so they sum
    assert res["total"] == 524291072
    assert res["by_pool"]["tank"] == 2048


def test_failure_is_reported_without_fake_zeros(monkeypatch):
    monkeypatch.setattr(zc, "run_command",
                        lambda *a, **k: {"success": False, "stderr": "boom"})
    res = zc.get_snapshot_space({"address": "h"})
    assert res["success"] is False
    assert res["total"] == 0 and res["datasets"] == []


def test_invalid_dataset_is_rejected(monkeypatch):
    calls = []
    monkeypatch.setattr(zc, "run_command",
                        lambda *a, **k: calls.append(a) or {"success": True, "stdout": ""})
    res = zc.get_snapshot_space({"address": "h"}, "rpool; rm -rf /")
    assert res["success"] is False
    assert not calls
