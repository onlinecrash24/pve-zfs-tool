"""Input validators are the security boundary before shell execution -- the
single most important thing to keep regression-free."""

import pytest
from app import validators as v


# --- pool names -----------------------------------------------------------

@pytest.mark.parametrize("name", ["rpool", "tank", "tank-hdd", "pool.1", "p_2"])
def test_valid_pool_names(name):
    assert v.validate_pool_name(name) == name


@pytest.mark.parametrize("bad", [
    "rpool/data",    # slash not allowed in a bare pool name
    "pool@snap",     # @ not allowed
    "pool;rm -rf /", # shell metachar
    "pool name",     # space
    "$(whoami)",     # command substitution
    "`id`",
    "-leadingdash",  # must start alnum
    "",
])
def test_invalid_pool_names(bad):
    with pytest.raises(ValueError):
        v.validate_pool_name(bad)


# --- zfs / dataset / snapshot names --------------------------------------

@pytest.mark.parametrize("name", [
    "rpool/data",
    "rpool/data/subvol-111-disk-0",
    "tank/vm-100-disk-0@zfs-auto-snap_daily-2026-01-01-0000",
    "rpool:special",
])
def test_valid_zfs_names(name):
    assert v.validate_zfs_name(name) == name


@pytest.mark.parametrize("bad", [
    "rpool; reboot",
    "rpool/data && cat /etc/shadow",
    "rpool/data\nrm",
    "pool|nc evil 1",
    "$(touch x)",
    "rpool/da ta",
])
def test_invalid_zfs_names(bad):
    with pytest.raises(ValueError):
        v.validate_zfs_name(bad)


def test_zfs_name_too_long_rejected():
    with pytest.raises(ValueError):
        v.validate_zfs_name("a" * (v.MAX_INPUT_LENGTH + 1))


# --- property name / value ------------------------------------------------

def test_valid_property():
    assert v.validate_zfs_property("com.sun:auto-snapshot") == "com.sun:auto-snapshot"


@pytest.mark.parametrize("bad", ["Upper", "prop;x", "with space", "$(x)"])
def test_invalid_property(bad):
    with pytest.raises(ValueError):
        v.validate_zfs_property(bad)


@pytest.mark.parametrize("val", ["on", "off", "lz4", "1500,6000", "rpool/repl"])
def test_valid_values(val):
    assert v.validate_zfs_value(val) == val


@pytest.mark.parametrize("bad", ["a;b", "a|b", "$(x)", "`x`", "a&b"])
def test_invalid_values(bad):
    with pytest.raises(ValueError):
        v.validate_zfs_value(bad)


# --- vmid / vm type -------------------------------------------------------

def test_vmid_ok():
    assert v.validate_vmid("123") == "123"


@pytest.mark.parametrize("bad", ["12a", "1;2", "-5", "", "abc"])
def test_vmid_bad(bad):
    with pytest.raises(ValueError):
        v.validate_vmid(bad)


def test_vm_type():
    assert v.validate_vm_type("qemu") == "qemu"
    assert v.validate_vm_type("lxc") == "lxc"
    with pytest.raises(ValueError):
        v.validate_vm_type("docker")


# --- paths ----------------------------------------------------------------

def test_path_ok():
    assert v.validate_path("/etc/pve/qemu-server") == "/etc/pve/qemu-server"


@pytest.mark.parametrize("bad", [
    "/etc/../etc/shadow",   # traversal
    "/x\x00y",              # null byte
    "/a;rm",                # metachar
    "/a|b",
])
def test_path_bad(bad):
    with pytest.raises(ValueError):
        v.validate_path(bad)


# --- limit ----------------------------------------------------------------

def test_limit_default_and_bounds():
    assert v.validate_limit(None, default=200) == 200
    assert v.validate_limit("50") == 50
    with pytest.raises(ValueError):
        v.validate_limit("999999", maximum=10000)
    with pytest.raises(ValueError):
        v.validate_limit("-1")
