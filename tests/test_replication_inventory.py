"""Correlating a guest's copies across hosts by snapshot GUID.

`zfs send`/`recv` carries the guid across, so the same snapshot has the same
guid on the source and on every replica -- that is how `zfs send -i` finds its
common base. Two datasets sharing guids are therefore the same lineage no matter
what they are called or which pool they live in, and the host holding the newest
snapshot is the source.
"""

from app import replication_inventory as ri


def _rows(dataset, guids_and_times):
    return "\n".join(f"{dataset}@snap{g}\t{g}\t{t}" for g, t in guids_and_times)


# --- parsing ---------------------------------------------------------------

def test_parse_rows():
    out = ri.parse_snapshot_guids("rpool/data/vm-100-disk-0@daily\t123\t1700000000\n")
    assert out == [{"dataset": "rpool/data/vm-100-disk-0", "snapshot": "daily",
                    "guid": "123", "creation": 1700000000,
                    "full": "rpool/data/vm-100-disk-0@daily"}]


def test_parse_skips_garbage():
    text = ("broken\n"
            "no-at-sign\t1\t2\n"
            "ds@s\tguid\tnotanumber\n"
            "ds@s\t\t1700000000\n"
            "ds@ok\t42\t1700000000\n")
    out = ri.parse_snapshot_guids(text)
    assert [r["snapshot"] for r in out] == ["ok"]


def test_parse_empty():
    assert ri.parse_snapshot_guids("") == []
    assert ri.parse_snapshot_guids(None) == []


# --- guid index and lineages ----------------------------------------------

def test_shared_guid_links_differently_named_datasets():
    # the whole point: names and pools differ, the guids do not
    per_host = {
        "srcH": ri.parse_snapshot_guids(_rows("rpool/data/vm-100-disk-0",
                                              [("a", 100), ("b", 200)])),
        "dstH": ri.parse_snapshot_guids(_rows("tank/backup/othername",
                                              [("a", 100)])),
    }
    lineages = ri.group_lineages(per_host)
    assert len(lineages) == 1
    assert {c["host"] for c in lineages[0]["copies"]} == {"srcH", "dstH"}


def test_unrelated_datasets_stay_separate():
    per_host = {
        "h1": ri.parse_snapshot_guids(_rows("rpool/a", [("x", 1)])),
        "h2": ri.parse_snapshot_guids(_rows("rpool/b", [("y", 2)])),
    }
    assert len(ri.group_lineages(per_host)) == 2


def test_migration_snapshots_do_not_link_lineages():
    # migrate-* exists on both sides by construction; treating it as evidence
    # would fuse datasets that are not replicas of each other
    src = "rpool/a@migrate-20260804-101319\tm1\t100"
    dst = "tank/b@migrate-20260804-101319\tm1\t100"
    per_host = {"h1": ri.parse_snapshot_guids(src), "h2": ri.parse_snapshot_guids(dst)}
    assert len(ri.group_lineages(per_host)) == 2


# --- source detection ------------------------------------------------------

def test_newest_snapshot_holder_is_the_source():
    copies = [
        {"host": "b", "dataset": "d", "newest": 500, "snapshot_count": 9, "guids": set()},
        {"host": "a", "dataset": "d", "newest": 900, "snapshot_count": 3, "guids": set()},
    ]
    assert ri.detect_source(copies)["host"] == "a"


def test_source_tie_is_broken_deterministically():
    copies = [
        {"host": "b", "dataset": "d", "newest": 900, "snapshot_count": 5, "guids": set()},
        {"host": "a", "dataset": "d", "newest": 900, "snapshot_count": 9, "guids": set()},
    ]
    # same timestamp -> more snapshots wins, so repeated runs agree
    assert ri.detect_source(copies)["host"] == "a"


def test_detect_source_without_data():
    assert ri.detect_source([]) is None
    assert ri.detect_source([{"host": "a", "newest": None, "snapshot_count": 0}]) is None


# --- copy comparison -------------------------------------------------------

def test_copy_behind_the_source():
    src = {"guids": {"a", "b", "c"}, "newest": 1000}
    cp = {"guids": {"a", "b"}, "newest": 700}
    got = ri.compare_copy(src, cp)
    assert got == {"shared_snapshots": 2, "missing_from_source": 1,
                   "excluded_labels": [], "lag_seconds": 300, "in_sync": False}


def test_copy_in_sync():
    src = {"guids": {"a", "b"}, "newest": 1000}
    got = ri.compare_copy(src, {"guids": {"a", "b"}, "newest": 1000})
    assert got["in_sync"] is True and got["lag_seconds"] == 0


def _labelled(pairs):
    """Copy dict as group_lineages builds it, from (guid, label) pairs."""
    by_guid = dict(pairs)
    return {"guids": set(by_guid), "by_guid": by_guid,
            "labels": {l for l in by_guid.values() if l}, "newest": 1000}


def test_labels_the_copy_never_receives_are_not_missing():
    # replication filters usually exclude frequent snapshots on purpose;
    # counting them as missing reports a healthy copy as broken
    src = _labelled([("f1", "frequent"), ("f2", "frequent"),
                     ("h1", "hourly"), ("h2", "hourly")])
    cp = _labelled([("h1", "hourly"), ("h2", "hourly")])
    got = ri.compare_copy(src, cp)
    assert got["missing_from_source"] == 0
    assert got["excluded_labels"] == ["frequent"]
    assert got["in_sync"] is True


def test_a_gap_within_a_replicated_label_still_counts():
    src = _labelled([("h1", "hourly"), ("h2", "hourly"), ("h3", "hourly"),
                     ("f1", "frequent")])
    cp = _labelled([("h1", "hourly")])
    got = ri.compare_copy(src, cp)
    assert got["missing_from_source"] == 2       # h2, h3 -- frequent excluded
    assert got["excluded_labels"] == ["frequent"]
    assert got["in_sync"] is False


def test_unlabelled_snapshots_fall_back_to_counting_everything():
    src = {"guids": {"a", "b"}, "by_guid": {"a": None, "b": None},
           "labels": set(), "newest": 1000}
    cp = {"guids": {"a"}, "by_guid": {"a": None}, "labels": set(), "newest": 900}
    assert ri.compare_copy(src, cp)["missing_from_source"] == 1


def test_lag_never_negative():
    # a replica reporting a newer timestamp than its source is clock skew,
    # not a negative delay
    got = ri.compare_copy({"guids": set(), "newest": 100},
                          {"guids": set(), "newest": 500})
    assert got["lag_seconds"] == 0


# --- matrix ----------------------------------------------------------------

def _matrix():
    per_host = {
        "pve251": ri.parse_snapshot_guids(_rows("rpool/data/subvol-253-disk-0",
                                                [("g1", 1000), ("g2", 2000), ("g3", 3000)])),
        "pve250": ri.parse_snapshot_guids(_rows("tank/repl/subvol-253-disk-0",
                                                [("g1", 1000), ("g2", 2000)])),
    }
    guests = {"pve251": [{"vmid": "253", "name": "test253", "type": "lxc"}]}
    return ri.build_matrix(per_host, guests)


def test_matrix_identifies_guest_source_and_copy():
    g = _matrix()["guests"][0]
    assert g["vmid"] == "253" and g["guest_name"] == "test253" and g["guest_type"] == "lxc"
    assert g["source_host"] == "pve251"
    assert g["copy_count"] == 1
    copy = [c for c in g["copies"] if not c["is_source"]][0]
    assert copy["host"] == "pve250"
    assert copy["missing_from_source"] == 1
    assert copy["lag_seconds"] == 1000


def test_source_row_comes_first():
    assert _matrix()["guests"][0]["copies"][0]["is_source"] is True


def test_guest_without_a_copy_is_visible():
    per_host = {"pve251": ri.parse_snapshot_guids(
        _rows("rpool/data/vm-100-disk-0", [("g1", 1000)]))}
    g = ri.build_matrix(per_host, {})["guests"][0]
    assert g["copy_count"] == 0


def test_config_mismatch_when_a_target_holds_the_newest():
    # pve250 is configured to pull FROM pve251, yet holds the newest snapshots
    per_host = {
        "pve251": ri.parse_snapshot_guids(_rows("rpool/data/vm-100-disk-0",
                                                [("g1", 1000)])),
        "pve250": ri.parse_snapshot_guids(_rows("tank/repl/vm-100-disk-0",
                                                [("g1", 1000), ("g2", 9000)])),
    }
    configured = [{"target_host": "pve250", "source": "pve251", "target": "tank/repl"}]
    g = ri.build_matrix(per_host, {}, configured)["guests"][0]
    assert g["source_host"] == "pve250"
    assert "configured as a replication target" in g["config_mismatch"]


def test_no_mismatch_when_direction_matches():
    per_host = {
        "pve251": ri.parse_snapshot_guids(_rows("rpool/data/vm-100-disk-0",
                                                [("g1", 1000), ("g2", 9000)])),
        "pve250": ri.parse_snapshot_guids(_rows("tank/repl/vm-100-disk-0",
                                                [("g1", 1000)])),
    }
    configured = [{"target_host": "pve250", "source": "pve251", "target": "tank/repl"}]
    assert ri.build_matrix(per_host, {}, configured)["guests"][0]["config_mismatch"] == ""


# --- only guests, scoped to one source host --------------------------------

def test_non_guest_datasets_are_not_listed():
    # rpool, rpool/ROOT/pve-1, var-lib-vz etc. are replicated too but are not
    # VMs or containers; listing them as "? (VM)" buries the real entries
    per_host = {
        "h1": ri.parse_snapshot_guids(
            _rows("rpool", [("a", 1)]) + "\n" +
            _rows("rpool/ROOT/pve-1", [("b", 2)]) + "\n" +
            _rows("rpool/var-lib-vz", [("c", 3)]) + "\n" +
            _rows("rpool/data/subvol-253-disk-0", [("d", 4)])),
    }
    guests = ri.build_matrix(per_host, {})["guests"]
    assert [g["vmid"] for g in guests] == ["253"]


def test_filter_keeps_only_the_selected_source_host():
    per_host = {
        "h1": ri.parse_snapshot_guids(_rows("rpool/data/vm-100-disk-0", [("a", 9)])),
        "h2": ri.parse_snapshot_guids(_rows("rpool/data/vm-200-disk-0", [("b", 9)])),
    }
    m = ri.build_matrix(per_host, {})
    got = ri.filter_matrix(m, source_host="h1", only_when_replicating=False)
    assert [g["vmid"] for g in got["guests"]] == ["100"]
    assert got["source_host"] == "h1"


def test_a_replicating_host_also_shows_its_unreplicated_guests():
    # a guest missing from an otherwise working replication set is exactly the
    # omission worth catching, so it belongs in the list, not hidden from it
    per_host = {
        "h1": ri.parse_snapshot_guids(
            _rows("rpool/data/vm-100-disk-0", [("a", 1), ("b", 2)]) + "\n" +
            _rows("rpool/data/vm-101-disk-0", [("solo", 5)])),
        "h2": ri.parse_snapshot_guids(_rows("tank/repl/vm-100-disk-0", [("a", 1)])),
    }
    got = ri.filter_matrix(ri.build_matrix(per_host, {}), source_host="h1")
    assert [g["vmid"] for g in got["guests"]] == ["100", "101"]   # replicated first
    assert got["replicated_count"] == 1
    assert got["without_copy_count"] == 1
    assert got["without_copy_guests"][0]["vmid"] == "101"


def test_guest_with_no_snapshots_at_all_is_still_listed():
    # a guest that never got a snapshot appears in no snapshot listing, so it
    # would be invisible -- yet that is the worst case: no rollback and nothing
    # that could ever have been replicated
    per_host = {
        "h1": ri.parse_snapshot_guids(_rows("rpool/data/subvol-253-disk-0",
                                            [("a", 1), ("b", 2)])),
        "h2": ri.parse_snapshot_guids(_rows("tank/repl/subvol-253-disk-0", [("a", 1)])),
    }
    guests = {"h1": [{"vmid": "253", "name": "test253", "type": "lxc"},
                     {"vmid": "254", "name": "test254", "type": "lxc"}]}
    got = ri.filter_matrix(ri.build_matrix(per_host, guests), source_host="h1")
    by_id = {g["vmid"]: g for g in got["guests"]}
    assert set(by_id) == {"253", "254"}
    assert by_id["254"]["no_snapshots"] is True
    assert by_id["254"]["guest_name"] == "test254"
    assert by_id["253"]["no_snapshots"] is False


def test_a_host_that_replicates_nothing_yields_nothing():
    # a standalone machine has no replication story; flagging every guest on it
    # would drown out the hosts that do replicate
    per_host = {"h1": ri.parse_snapshot_guids(
        _rows("rpool/data/vm-100-disk-0", [("solo", 5)]))}
    got = ri.filter_matrix(ri.build_matrix(per_host, {}), source_host="h1")
    assert got["guests"] == []
    assert got["replicated_count"] == 0


def test_source_hosts_lists_only_origins_of_replicated_guests():
    per_host = {
        "h1": ri.parse_snapshot_guids(_rows("rpool/data/vm-100-disk-0", [("a", 9)])),
        "h2": ri.parse_snapshot_guids(_rows("tank/repl/vm-100-disk-0", [("a", 9), ("z", 1)])),
        "h3": ri.parse_snapshot_guids(_rows("rpool/data/vm-999-disk-0", [("solo", 5)])),
    }
    # h1/h2 share a lineage, h3 stands alone -> h3 is not a replication source
    assert "h3" not in ri.source_hosts(ri.build_matrix(per_host, {}))


# --- condensation ----------------------------------------------------------

def test_condense_drops_the_raw_snapshot_list():
    # only counts and timestamps reach the model, never individual snapshots --
    # at 15-minute cadence the raw listing would blow the prompt budget
    payload = ri.condense_for_report(_matrix())
    text = repr(payload)
    for name in ("snapg1", "snapg2", "snapg3"):
        assert name not in text
    assert payload["guests"][0]["copies"][0]["snapshots"] == 3
    assert payload["guest_count"] == 1
    assert payload["guests"][0]["copy_count"] == 1


def test_condense_counts_guests_without_a_copy():
    per_host = {"h": ri.parse_snapshot_guids(_rows("rpool/data/vm-1-disk-0", [("g", 1)]))}
    payload = ri.condense_for_report(ri.build_matrix(per_host, {}))
    assert payload["guests_without_copy"] == 1


def test_condense_caps_the_guest_list():
    per_host = {"h": ri.parse_snapshot_guids("\n".join(
        f"rpool/data/vm-{i}-disk-0@s\t{i}\t{1000 + i}" for i in range(30)))}
    payload = ri.condense_for_report(ri.build_matrix(per_host, {}), max_guests=10)
    assert len(payload["guests"]) == 10
    assert payload["truncated"] is True
    assert payload["guest_count"] == 30
