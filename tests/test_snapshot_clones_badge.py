"""A snapshot with dependent clones says so in the listing.

The destroy path already refuses when foreign clones exist, and
zfs-auto-snapshot silently leaves such a snapshot alone -- but nothing
showed the holder beforehand. get_snapshots now carries the `clones`
property in the same `zfs list` call, parsed into a list; the UI renders it
as a "held by" badge. No extra round trip, and a host whose output lacks
the column still parses.
"""

from app import zfs_commands as zc

HOST = {"address": "h"}

LISTING = (
    "tank/vm-100-disk-0@base\t1.2G\t20G\tMon Oct 13 10:00 2026\ttank/vm-100-disk-0_CLONE,tank/recovery/vm-900-disk-0\n"
    "tank/vm-100-disk-0@daily\t300M\t20G\tMon Oct 12 10:00 2026\t-\n"
    "tank/data@manual\t0B\t5G\tSun Oct 12 09:00 2026\t\n"
)


def _stub(monkeypatch, listing=LISTING):
    calls = []

    def run(host, cmd, **kw):
        calls.append(cmd)
        if cmd.startswith("zfs list -t snapshot"):
            return {"success": True, "stdout": listing, "stderr": ""}
        return {"success": True, "stdout": "tank/vm-100-disk-0\tvolume\ntank/data\tfilesystem\n", "stderr": ""}

    monkeypatch.setattr(zc, "run_command", run)
    return calls


def test_clones_ride_along_in_the_same_list_call(monkeypatch):
    calls = _stub(monkeypatch)
    zc.get_snapshots(HOST)
    listing_calls = [c for c in calls if c.startswith("zfs list -t snapshot")]
    assert len(listing_calls) == 1
    assert "-o name,used,refer,creation,clones" in listing_calls[0]


def test_the_holders_are_a_list_and_a_dash_is_none(monkeypatch):
    _stub(monkeypatch)
    by_name = {s["full_name"]: s for s in zc.get_snapshots(HOST)}
    assert by_name["tank/vm-100-disk-0@base"]["clones"] == [
        "tank/vm-100-disk-0_CLONE", "tank/recovery/vm-900-disk-0"]
    assert by_name["tank/vm-100-disk-0@daily"]["clones"] == []
    assert by_name["tank/data@manual"]["clones"] == []


def test_the_other_columns_are_unchanged_by_the_extra_field(monkeypatch):
    _stub(monkeypatch)
    by_name = {s["full_name"]: s for s in zc.get_snapshots(HOST)}
    assert by_name["tank/vm-100-disk-0@base"]["creation"] == "Mon Oct 13 10:00 2026"
    assert by_name["tank/vm-100-disk-0@base"]["used"] == "1.2G"
    assert by_name["tank/vm-100-disk-0@base"]["ds_type"] == "volume"


def test_a_listing_without_the_column_still_parses(monkeypatch):
    _stub(monkeypatch, "tank/data@old\t0B\t5G\tSun Oct 12 09:00 2026\n")
    snaps = zc.get_snapshots(HOST)
    assert len(snaps) == 1
    assert snaps[0]["clones"] == []
    assert snaps[0]["creation"] == "Sun Oct 12 09:00 2026"
