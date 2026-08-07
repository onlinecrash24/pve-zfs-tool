"""Comparing two snapshots (A vs B) instead of only snapshot-vs-live.

`zfs diff A B` reads "from -> to": it needs both snapshots on the same dataset
and A older than B, and fails with a cryptic message otherwise. Neither
constraint should be the user's problem, so the pair is ordered by creation time
and a cross-dataset pick is refused with a clear reason.
"""

from app import zfs_commands as zc


CREATED = {
    "rpool/data@a": 1_700_000_000,
    "rpool/data@b": 1_700_009_999,
}


def test_pair_already_in_order_is_untouched():
    assert zc.order_snapshot_pair("rpool/data@a", "rpool/data@b", CREATED) == \
        ("rpool/data@a", "rpool/data@b", False)


def test_reversed_pair_is_swapped():
    older, newer, swapped = zc.order_snapshot_pair("rpool/data@b", "rpool/data@a", CREATED)
    assert (older, newer) == ("rpool/data@a", "rpool/data@b")
    assert swapped is True


def test_unknown_creation_keeps_the_given_order():
    # better to let zfs speak than to guess an order from nothing
    assert zc.order_snapshot_pair("x@1", "x@2", {}) == ("x@1", "x@2", False)
    assert zc.order_snapshot_pair("x@1", "x@2", None) == ("x@1", "x@2", False)


def test_creation_epochs_parsing(monkeypatch):
    monkeypatch.setattr(zc, "run_command", lambda *a, **k: {
        "success": True,
        "stdout": "rpool/data@a\t1700000000\nrpool/data@b\t1700009999\nbroken\n"})
    got = zc.snapshot_creation_epochs({"address": "h"}, "rpool/data")
    assert got == CREATED


def test_cross_dataset_pair_is_refused(monkeypatch):
    def fake_run(host, cmd, **kw):
        if "value type" in cmd:
            return {"success": True, "stdout": "filesystem\n"}
        if "value mounted" in cmd:
            return {"success": True, "stdout": "yes\n"}
        return {"success": True, "stdout": ""}

    monkeypatch.setattr(zc, "run_command", fake_run)
    res = zc.diff_snapshot({"address": "h"}, "rpool/data@a", "tank/other@b")
    assert res["success"] is False
    assert "same dataset" in res["stderr"]


def test_two_snapshot_diff_orders_the_pair(monkeypatch):
    seen = {}

    def fake_run(host, cmd, **kw):
        if "value type" in cmd:
            return {"success": True, "stdout": "filesystem\n"}
        if "value mounted" in cmd:
            return {"success": True, "stdout": "yes\n"}
        if cmd.startswith("zfs list -Hp -o name,creation"):
            return {"success": True,
                    "stdout": "rpool/data@a\t1700000000\nrpool/data@b\t1700009999\n"}
        seen["cmd"] = cmd
        return {"success": True, "stdout": "M\t/x\n", "stderr": ""}

    monkeypatch.setattr(zc, "run_command", fake_run)
    # asked the "wrong" way round on purpose
    res = zc.diff_snapshot({"address": "h"}, "rpool/data@b", "rpool/data@a")
    assert seen["cmd"] == "zfs diff rpool/data@a rpool/data@b"
    assert res["swapped"] is True
    assert res["from"] == "rpool/data@a" and res["to"] == "rpool/data@b"


def test_single_snapshot_diff_still_compares_against_live(monkeypatch):
    seen = {}

    def fake_run(host, cmd, **kw):
        if "value type" in cmd:
            return {"success": True, "stdout": "filesystem\n"}
        if "value mounted" in cmd:
            return {"success": True, "stdout": "yes\n"}
        seen["cmd"] = cmd
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(zc, "run_command", fake_run)
    res = zc.diff_snapshot({"address": "h"}, "rpool/data@a")
    assert seen["cmd"] == "zfs diff rpool/data@a"
    assert res["swapped"] is False
    assert "No changes since" in res["stdout"]
