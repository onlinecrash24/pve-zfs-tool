"""Cloning a snapshot: the clone is a working copy and must behave like one.

Two things the first version got wrong, both pointed out by comparing the
tool against the aow.de article on recovery clones: the clone was created
with no properties, so zfs-auto-snapshot started snapshotting it on the next
run and a volume clone reserved its full size; and the cross-pool path ran
`zfs promote` on a received dataset that was never a clone.
"""

import pytest

from app import zfs_commands as z


def _run(recorder, fail_on=None):
    def run(host, command, timeout=30, cache_ttl=0):
        recorder.append(command)
        ok = not (fail_on and fail_on in command)
        return {"success": ok, "stdout": "", "stderr": "" if ok else "boom"}
    return run


@pytest.fixture
def quiet(monkeypatch):
    cmds = []
    monkeypatch.setattr(z, "run_command", _run(cmds))
    monkeypatch.setattr(z, "_invalidate", lambda h: None)
    return cmds


def test_a_same_pool_clone_is_excluded_from_auto_snapshot_and_reserves_nothing(quiet):
    r = z.clone_snapshot({"address": "h"}, "rpool/data/vm-100-disk-0@daily-1", "rpool/clone/vm-900-disk-0")
    assert r["success"] is True
    assert quiet == ["zfs clone -o com.sun:auto-snapshot=false -o refreservation=none "
                     "rpool/data/vm-100-disk-0@daily-1 rpool/clone/vm-900-disk-0"]


def test_a_cross_pool_copy_is_not_promoted_but_is_excluded_from_auto_snapshot(quiet):
    r = z.clone_snapshot({"address": "h"}, "rpool/data/x@s", "tank/copy/x")
    assert r["success"] is True
    assert quiet == ["zfs send rpool/data/x@s | zfs recv tank/copy/x",
                     "zfs set com.sun:auto-snapshot=false tank/copy/x"]
    assert not any("promote" in c for c in quiet)


def test_a_failed_receive_sets_nothing_afterwards(monkeypatch):
    cmds = []
    monkeypatch.setattr(z, "run_command", _run(cmds, fail_on="zfs recv"))
    monkeypatch.setattr(z, "_invalidate", lambda h: None)
    r = z.clone_snapshot({"address": "h"}, "rpool/data/x@s", "tank/copy/x")
    assert r["success"] is False
    assert len(cmds) == 1


@pytest.mark.parametrize("snap,clone", [
    ("rpool/x@s; rm -rf /", "rpool/c"),
    ("rpool/x@s", "rpool/c $(reboot)"),
    ("", "rpool/c"),
])
def test_bad_names_never_reach_the_shell(quiet, snap, clone):
    assert z.clone_snapshot({"address": "h"}, snap, clone)["success"] is False
    assert quiet == []


def test_a_failed_clone_does_not_invalidate_the_cache(monkeypatch):
    cmds, invalidated = [], []
    monkeypatch.setattr(z, "run_command", _run(cmds, fail_on="zfs clone"))
    monkeypatch.setattr(z, "_invalidate", lambda h: invalidated.append(h))
    z.clone_snapshot({"address": "h"}, "rpool/x@s", "rpool/c")
    assert invalidated == []
