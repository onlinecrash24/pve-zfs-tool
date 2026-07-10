"""Snapshot-tag discovery + per-host selection: multiple replications bring
multiple tags; the check must be configurable about which ones count."""

import pytest
from app import snaptags as st


# --- extract_tag -----------------------------------------------------------

@pytest.mark.parametrize("name,tag", [
    ("zfs-auto-snap_daily-2026-07-05-1200", "daily"),
    ("zfs-auto-snap_frequent-2026-07-05-1215", "frequent"),
    ("bashclub-zfs_2026-07-05", "bashclub-zfs"),
    ("backup-zfs-2026-07-05-0300", "backup-zfs"),
    ("mytool-hourly-2026-07-05-1200", "hourly"),          # known tag inside name
    ("replica_siteB_20260705", "replica_siteB"),          # generic prefix
    ("presync-2026-07-05", "presync"),                    # generic prefix
])
def test_extract_tag(name, tag):
    assert st.extract_tag(name) == tag


def test_extract_tag_manual_names_yield_none():
    assert st.extract_tag("before-upgrade") is None       # no timestamp digits
    assert st.extract_tag("test") is None


# --- discover_tags ---------------------------------------------------------

def test_discover_counts():
    names = ["zfs-auto-snap_daily-2026-07-05-1200",
             "zfs-auto-snap_daily-2026-07-04-1200",
             "zfs-auto-snap_hourly-2026-07-05-1100",
             "presync-2026-07-05",
             "oddball"]
    counts = st.discover_tags(names)
    assert counts == {"daily": 2, "hourly": 1, "presync": 1}


# --- build_label_regex -----------------------------------------------------

def test_regex_matches_only_selected():
    rx = st.build_label_regex(["daily", "presync"])
    assert rx.search("zfs-auto-snap_daily-2026-07-05-1200")
    assert rx.search("presync-2026-07-05")
    assert not rx.search("zfs-auto-snap_hourly-2026-07-05-1100")


def test_regex_longest_tag_wins_and_escaping():
    rx = st.build_label_regex(["backup", "backup-zfs"])
    m = rx.search("backup-zfs-2026")
    assert m.group(0) == "backup-zfs"     # longer alternative first
    # regex metacharacters in a tag must not blow up
    assert st.build_label_regex(["a.b"]).search("xa.by")


def test_regex_empty_selection_falls_back_to_defaults():
    rx = st.build_label_regex([])
    assert rx.search("zfs-auto-snap_daily-2026-07-05-1200")


# --- persistence -----------------------------------------------------------

@pytest.fixture
def tags_store(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(st, "TAGS_FILE", str(tmp_path / "snapcheck_tags.json"))
    yield


def test_selection_roundtrip(tags_store):
    assert st.load_tag_selection("10.0.0.1") is None
    st.save_tag_selection("10.0.0.1", ["daily", "presync", "daily"])
    assert st.load_tag_selection("10.0.0.1") == ["daily", "presync"]
    assert st.effective_tags("10.0.0.1") == ["daily", "presync"]
    # other hosts unaffected -> defaults
    assert st.effective_tags("10.0.0.2") == list(st.DEFAULT_TAGS)


def test_empty_selection_resets_to_defaults(tags_store):
    st.save_tag_selection("10.0.0.1", ["daily"])
    st.save_tag_selection("10.0.0.1", [])
    assert st.load_tag_selection("10.0.0.1") is None
    assert st.effective_tags("10.0.0.1") == list(st.DEFAULT_TAGS)


def test_invalid_tags_dropped_on_save(tags_store):
    saved = st.save_tag_selection("10.0.0.1", ["ok-tag", "bad;rm -rf", "", "-lead"])
    assert saved == ["ok-tag"]


# --- visible_tags (only real tags shown, not blanket defaults) ------------

def test_visible_tags_hides_zero_count_defaults():
    # yearly/backup-zfs/bashclub-zfs (defaults, not present) must NOT appear
    out = st.visible_tags({"daily": 310, "hourly": 2976}, None)
    assert {t["tag"] for t in out} == {"daily", "hourly"}
    assert all(t["selected"] for t in out)          # standard labels pre-checked


def test_visible_tags_custom_tag_unchecked_by_default():
    out = {t["tag"]: t for t in st.visible_tags({"daily": 5, "mybackup": 12}, None)}
    assert out["daily"]["selected"] is True         # default label
    assert out["mybackup"]["selected"] is False     # custom -> off until chosen
    assert out["mybackup"]["count"] == 12


def test_visible_tags_saved_selection_drives_checkboxes():
    out = {t["tag"]: t for t in st.visible_tags({"daily": 5, "mybackup": 12}, ["mybackup"])}
    assert out["mybackup"]["selected"] is True
    assert out["daily"]["selected"] is False


def test_visible_tags_keeps_selected_tag_even_at_zero():
    # a saved tag whose snapshots are all gone stays toggleable
    out = {t["tag"]: t for t in st.visible_tags({"daily": 5}, ["daily", "gone"])}
    assert "gone" in out and out["gone"]["count"] == 0 and out["gone"]["selected"] is True


def test_visible_tags_empty_when_nothing_discovered():
    assert st.visible_tags({}, None) == []


# --- integration: get_snapshot_ages respects the selection ----------------

def test_snapshot_ages_uses_selected_tags(tags_store, monkeypatch):
    from app import zfs_commands as z
    out = "\n".join([
        "rpool/data@zfs-auto-snap_daily-2026-07-05-1200\t1751709600",
        "rpool/data@presync-2026-07-05\t1751709700",
        "rpool/data@zfs-auto-snap_hourly-2026-07-05-1100\t1751706000",
    ])
    monkeypatch.setattr(z, "run_command",
                        lambda h, c, timeout=30, **k: {"success": True, "stdout": out, "stderr": ""})
    st.save_tag_selection("10.0.0.1", ["daily", "presync"])
    ages = z.get_snapshot_ages({"address": "10.0.0.1"})
    labels = set(ages["datasets"]["rpool/data"].keys())
    assert labels == {"daily", "presync"}
    # hourly was deselected -> lands under manual, not silently dropped
    manual_names = [m["name"] for m in ages["manual"].get("rpool/data", [])]
    assert any("hourly" in n for n in manual_names)