"""Announcing a pool that one device failure would destroy.

The hard part is not detecting it -- it is announcing it exactly once. A pool
without redundancy is a structural fact, not an event: it is as true tomorrow as
today. Repeating it every sample round would be the permanent alarm people learn
to filter out, and they take the real ones with them when they do.
"""

import pytest

from app import database
from app import monitor


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(database, "_initialized", False)
    database.init_db()
    yield


@pytest.fixture
def notes(monkeypatch):
    sent = []
    monkeypatch.setattr(monitor, "send_notification",
                        lambda *a, **k: sent.append((a, k)))
    return sent


HOST = {"address": "10.0.0.1", "name": "pve1"}

HEALTHY = ("  pool: tank\n state: ONLINE\nconfig:\n\n"
           "\tNAME        STATE     READ WRITE CKSUM\n"
           "\ttank        ONLINE       0     0     0\n"
           "\t  mirror-0  ONLINE       0     0     0\n"
           "\t    sda     ONLINE       0     0     0\n"
           "\t    sdb     ONLINE       0     0     0\n"
           "\nerrors: No known data errors\n")

BARE_SPECIAL = ("  pool: tank\n state: ONLINE\nconfig:\n\n"
                "\tNAME        STATE     READ WRITE CKSUM\n"
                "\ttank        ONLINE       0     0     0\n"
                "\t  mirror-0  ONLINE       0     0     0\n"
                "\t    sda     ONLINE       0     0     0\n"
                "\t    sdb     ONLINE       0     0     0\n"
                "\tspecial\n"
                "\t  nvme0n1   ONLINE       0     0     0\n"
                "\nerrors: No known data errors\n")

BARE_SLOG = ("  pool: tank\n state: ONLINE\nconfig:\n\n"
             "\tNAME        STATE     READ WRITE CKSUM\n"
             "\ttank        ONLINE       0     0     0\n"
             "\t  mirror-0  ONLINE       0     0     0\n"
             "\t    sda     ONLINE       0     0     0\n"
             "\t    sdb     ONLINE       0     0     0\n"
             "\tlogs\n"
             "\t  nvme0n1   ONLINE       0     0     0\n"
             "\nerrors: No known data errors\n")


def _status(text):
    return {"tank": {"status_text": text}}


def test_an_existing_risk_is_recorded_silently_on_first_sight(temp_db, notes):
    # The check must not fire for every pool that already looks like this the
    # moment it ships -- that would arrive as a wave of alarms about nothing new.
    monitor.check_pool_topology(HOST, _status(BARE_SPECIAL))
    assert notes == []


def test_a_risk_that_appears_later_is_announced(temp_db, notes):
    monitor.check_pool_topology(HOST, _status(HEALTHY))     # baseline: fine
    monitor.check_pool_topology(HOST, _status(BARE_SPECIAL))
    assert len(notes) == 1
    args, kwargs = notes[0]
    assert args[0] == "health_warning"
    assert "nvme0n1" in args[1]
    assert "entire pool" in args[2]


def test_it_is_announced_once_and_then_never_again(temp_db, notes):
    monitor.check_pool_topology(HOST, _status(HEALTHY))
    monitor.check_pool_topology(HOST, _status(BARE_SPECIAL))
    for _ in range(5):
        monitor.check_pool_topology(HOST, _status(BARE_SPECIAL))
    assert len(notes) == 1


def test_fixing_it_and_breaking_it_again_announces_again(temp_db, notes):
    monitor.check_pool_topology(HOST, _status(HEALTHY))
    monitor.check_pool_topology(HOST, _status(BARE_SPECIAL))
    monitor.check_pool_topology(HOST, _status(HEALTHY))       # mirrored, quiet
    monitor.check_pool_topology(HOST, _status(BARE_SPECIAL))
    assert len(notes) == 2


def test_a_healthy_pool_never_announces(temp_db, notes):
    for _ in range(3):
        monitor.check_pool_topology(HOST, _status(HEALTHY))
    assert notes == []


def test_a_bare_slog_is_not_announced(temp_db, notes):
    # Shown in the pool view, but the pool survives its loss -- this channel is
    # for the ones that end you.
    monitor.check_pool_topology(HOST, _status(HEALTHY))
    monitor.check_pool_topology(HOST, _status(BARE_SLOG))
    assert notes == []


def test_missing_or_broken_status_text_is_ignored(temp_db, notes):
    monitor.check_pool_topology(HOST, {"tank": {"error_totals": {"read": 0}}})
    monitor.check_pool_topology(HOST, {"tank": {"status_text": ""}})
    monitor.check_pool_topology(HOST, {"tank": None})
    monitor.check_pool_topology(HOST, {})
    monitor.check_pool_topology(HOST, None)
    assert notes == []


def test_the_state_is_cleared_when_the_pool_vanishes(temp_db, notes):
    # Otherwise a destroyed pool's risk lingers and re-announces if a new pool
    # of the same name appears -- the ghost clear_vanished_pool_state exists for.
    monitor.check_pool_topology(HOST, _status(HEALTHY))
    monitor.check_pool_topology(HOST, _status(BARE_SPECIAL))
    assert monitor._state_get("pool_topology", "10.0.0.1:tank")[0]

    monitor.clear_vanished_pool_state(HOST, [])
    assert monitor._state_get("pool_topology", "10.0.0.1:tank")[0] is None


def test_removing_the_host_clears_it_too(temp_db, notes):
    monitor.check_pool_topology(HOST, _status(BARE_SPECIAL))
    assert monitor._state_get("pool_topology", "10.0.0.1:tank")[0] is not None
    monitor.clear_host_state("10.0.0.1")
    assert monitor._state_get("pool_topology", "10.0.0.1:tank")[0] is None
