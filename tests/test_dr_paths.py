"""Disaster-recovery path mapping: replica dataset -> original source path."""

from app import dr


def test_strip_replica_root_basic():
    assert dr._strip_replica_root(
        "rpool/repl/rpool/data/subvol-111-disk-0", "rpool/repl"
    ) == "rpool/data/subvol-111-disk-0"


def test_strip_replica_root_direct_child():
    assert dr._strip_replica_root("rpool/repl/rpool", "rpool/repl") == "rpool"


def test_strip_replica_root_not_under_root_returns_empty():
    assert dr._strip_replica_root("tank/other/ds", "rpool/repl") == ""


def test_strip_replica_root_equal_returns_empty():
    # the root itself has no source path under it
    assert dr._strip_replica_root("rpool/repl", "rpool/repl") == ""
