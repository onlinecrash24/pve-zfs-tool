"""ARC limit config parse/build -- the bytes that land in
/etc/modprobe.d/zfs.conf, so a regression here mis-sizes the cache on boot."""

import pytest
from app import tuning as tn

GIB = 1024 ** 3


# --- parse_arc_conf -------------------------------------------------------

def test_parse_empty_is_none():
    p = tn.parse_arc_conf("")
    assert p == {"zfs_arc_max": None, "zfs_arc_min": None}


def test_parse_max_only():
    p = tn.parse_arc_conf("options zfs zfs_arc_max=8589934592\n")
    assert p["zfs_arc_max"] == 8 * GIB
    assert p["zfs_arc_min"] is None


def test_parse_max_and_min():
    text = "options zfs zfs_arc_min=1073741824 zfs_arc_max=8589934592\n"
    p = tn.parse_arc_conf(text)
    assert p["zfs_arc_min"] == 1 * GIB
    assert p["zfs_arc_max"] == 8 * GIB


def test_parse_ignores_other_params_and_comments():
    text = "# zfs tuning\noptions zfs zfs_prefetch_disable=1 zfs_arc_max=4294967296\n"
    p = tn.parse_arc_conf(text)
    assert p["zfs_arc_max"] == 4 * GIB


def test_parse_non_numeric_value_is_none():
    p = tn.parse_arc_conf("options zfs zfs_arc_max=auto\n")
    assert p["zfs_arc_max"] is None


# --- build_arc_conf -------------------------------------------------------

def test_build_from_empty_sets_max():
    out = tn.build_arc_conf("", 8 * GIB, None)
    assert out == "options zfs zfs_arc_max=8589934592\n"


def test_build_sets_max_and_min():
    out = tn.build_arc_conf("", 8 * GIB, 1 * GIB)
    assert "zfs_arc_max=8589934592" in out
    assert "zfs_arc_min=1073741824" in out
    assert out.startswith("options zfs ")


def test_build_preserves_other_params_and_comments():
    existing = "# my tuning\noptions zfs zfs_prefetch_disable=1 zfs_arc_max=4294967296\n"
    out = tn.build_arc_conf(existing, 2 * GIB, None)
    assert "# my tuning" in out
    assert "zfs_prefetch_disable=1" in out
    assert "zfs_arc_max=2147483648" in out
    # old value gone
    assert "4294967296" not in out


def test_build_updates_existing_max():
    out = tn.build_arc_conf("options zfs zfs_arc_max=4294967296\n", 16 * GIB, None)
    assert tn.parse_arc_conf(out)["zfs_arc_max"] == 16 * GIB


def test_build_reset_removes_arc_params_keeps_others():
    existing = "options zfs zfs_arc_max=4294967296 zfs_prefetch_disable=1\n"
    out = tn.build_arc_conf(existing, None, None)
    assert "zfs_arc_max" not in out
    assert "zfs_prefetch_disable=1" in out


def test_build_reset_with_no_other_params_is_empty():
    out = tn.build_arc_conf("options zfs zfs_arc_max=4294967296\n", 0, None)
    assert out == ""


def test_build_consolidates_multiple_options_lines():
    existing = "options zfs zfs_arc_max=1\noptions zfs zfs_prefetch_disable=1\n"
    out = tn.build_arc_conf(existing, 8 * GIB, None)
    # only one options-zfs line in the result
    assert out.count("options zfs") == 1
    assert "zfs_prefetch_disable=1" in out
    assert tn.parse_arc_conf(out)["zfs_arc_max"] == 8 * GIB


def test_build_roundtrips_through_parse():
    out = tn.build_arc_conf("", 12 * GIB, 2 * GIB)
    p = tn.parse_arc_conf(out)
    assert p["zfs_arc_max"] == 12 * GIB
    assert p["zfs_arc_min"] == 2 * GIB


# --- set_arc_limit validation (no SSH; rejects before any I/O) -----------

class _FakeHost(dict):
    pass


@pytest.mark.parametrize("bad", [
    32 * 1024 * 1024,   # below the 64 MiB floor
    "not-an-int",
])
def test_set_arc_limit_rejects_bad_max_without_ssh(bad):
    # These fail validation before get_arc_config / any run_command call,
    # so no host I/O happens.
    r = tn.set_arc_limit(_FakeHost(), bad)
    assert r["success"] is False
    assert "error" in r


def test_set_arc_limit_rejects_min_ge_max_without_ssh():
    r = tn.set_arc_limit(_FakeHost(), 1 * GIB, 2 * GIB)
    assert r["success"] is False
    assert "arc_min" in r["error"]
