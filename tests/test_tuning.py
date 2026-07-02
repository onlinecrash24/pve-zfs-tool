"""ARC limit config parse/build -- the bytes that land in
/etc/modprobe.d/zfs.conf, so a regression here mis-sizes the cache on boot."""

import pytest
from app import tuning as tn

GIB = 1024 ** 3


# --- _parse_arc_probe (single-round-trip read output) --------------------

def test_parse_probe_typical():
    out = "\n".join([
        "RUNTIME_MAX=17179869184",
        "RUNTIME_MIN=536870912",
        "MEMTOTAL_KB=65536000",
        "ARC_SIZE=16000000000",
        "POOL_SUM=4831838208",
        "__ZFSCONF__",
        "options zfs zfs_arc_max=17179869184",
    ])
    p = tn._parse_arc_probe(out)
    assert p["runtime_max"] == 17179869184
    assert p["runtime_min"] == 536870912
    assert p["total_ram_bytes"] == 65536000 * 1024
    assert p["current_size"] == 16000000000
    assert p["pool_sum"] == 4831838208
    assert p["conf_text"] == "options zfs zfs_arc_max=17179869184"


def test_parse_probe_missing_values_are_none():
    # runtime files absent -> empty values; no conf
    out = "RUNTIME_MAX=\nRUNTIME_MIN=\nMEMTOTAL_KB=\nARC_SIZE=\nPOOL_SUM=0\n__ZFSCONF__\n"
    p = tn._parse_arc_probe(out)
    assert p["runtime_max"] is None
    assert p["runtime_min"] is None
    assert p["total_ram_bytes"] is None
    assert p["current_size"] is None
    assert p["pool_sum"] == 0
    assert p["conf_text"] == ""


def test_parse_probe_multiline_conf_preserved():
    out = "\n".join([
        "POOL_SUM=0", "__ZFSCONF__",
        "# comment", "options zfs zfs_arc_max=1 zfs_prefetch_disable=1",
    ])
    p = tn._parse_arc_probe(out)
    assert "# comment" in p["conf_text"]
    assert "zfs_prefetch_disable=1" in p["conf_text"]


def test_parse_probe_empty_output():
    p = tn._parse_arc_probe("")
    assert p["pool_sum"] == 0
    assert p["conf_text"] == ""
    assert p["total_ram_bytes"] is None


def test_probe_script_reads_everything_in_one_shot():
    s = tn._arc_probe_script()
    for token in ("zfs_arc_max", "MemTotal", "arcstats", "zpool list", "__ZFSCONF__"):
        assert token in s


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


# --- suggest_arc_floor (Proxmox 2 GiB + 1 GiB/TiB) -----------------------

TIB = 1024 ** 4


def test_floor_two_gib_base_plus_one_per_tib():
    assert tn.suggest_arc_floor(1 * TIB) == 3 * GIB
    assert tn.suggest_arc_floor(8 * TIB) == 10 * GIB   # Proxmox wiki example


def test_floor_none_for_unknown_pool_size():
    assert tn.suggest_arc_floor(0) is None
    assert tn.suggest_arc_floor(None) is None


def test_floor_includes_the_2gib_base_for_tiny_pools():
    # even a tiny pool gets at least the 2 GiB base (well above the 64 MiB floor)
    assert tn.suggest_arc_floor(1 * GIB) >= 2 * GIB


# --- arc_suggestions (min / balanced / max) ------------------------------

def test_suggestions_typical_box():
    # 8 TiB pool, 64 GiB RAM
    s = tn.arc_suggestions(8 * TIB, 64 * GIB)
    assert s["min"] == 10 * GIB          # Proxmox floor
    assert s["balanced"] == 16 * GIB     # 25% RAM
    assert s["max"] == 32 * GIB          # 50% RAM
    assert s["min"] <= s["balanced"] <= s["max"]


def test_suggestions_unknown_pool_keeps_ram_based_values():
    s = tn.arc_suggestions(None, 64 * GIB)
    assert s["min"] is None
    assert s["balanced"] == 16 * GIB
    assert s["max"] == 32 * GIB


def test_suggestions_unknown_ram_keeps_only_floor():
    s = tn.arc_suggestions(8 * TIB, None)
    assert s["min"] == 10 * GIB
    assert s["balanced"] is None
    assert s["max"] is None


def test_suggestions_stay_ordered_when_floor_exceeds_ram():
    # tiny RAM box, huge pool: floor would exceed 50% RAM -> clamp & keep order
    s = tn.arc_suggestions(100 * TIB, 8 * GIB)
    assert s["min"] <= s["balanced"] <= s["max"]
    assert s["max"] == 4 * GIB           # 50% of 8 GiB
    assert s["min"] <= s["max"]
