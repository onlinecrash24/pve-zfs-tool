"""A pool destroyed/exported while DEGRADED must not haunt pool_health
(-> bad_pools) forever. Cleanup only runs on a *verified* pool listing and
announces (not silently forgets) a vanished bad pool."""

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


def _pool(name, health="ONLINE"):
    return {"name": name, "health": health}


def test_ghost_degraded_pool_is_cleared_and_announced(temp_db, notes):
    monitor._state_set("pool_health", "10.0.0.1:tank", "DEGRADED")
    monitor._state_set("capacity", "10.0.0.1:tank", "above")
    monitor._state_set("pool_errors", "10.0.0.1:tank", "5")

    # tank was destroyed; only rpool remains
    monitor.clear_vanished_pool_state(HOST, [_pool("rpool")])

    assert monitor._state_get("pool_health", "10.0.0.1:tank") == (None, None)
    assert monitor._state_get("capacity", "10.0.0.1:tank") == (None, None)
    assert monitor._state_get("pool_errors", "10.0.0.1:tank") == (None, None)
    # the vanished DEGRADED pool was announced, not silently dropped
    assert len(notes) == 1
    assert "tank" in notes[0][0][1]


def test_vanished_online_pool_cleared_silently(temp_db, notes):
    monitor._state_set("pool_health", "10.0.0.1:old", "ONLINE")
    monitor.clear_vanished_pool_state(HOST, [_pool("rpool")])
    assert monitor._state_get("pool_health", "10.0.0.1:old") == (None, None)
    assert notes == []          # intentional removal -> no alarm


def test_existing_pools_untouched(temp_db, notes):
    monitor._state_set("pool_health", "10.0.0.1:rpool", "DEGRADED")
    monitor.clear_vanished_pool_state(HOST, [_pool("rpool", "DEGRADED")])
    assert monitor._state_get("pool_health", "10.0.0.1:rpool")[0] == "DEGRADED"
    assert notes == []


def test_other_hosts_state_untouched(temp_db, notes):
    monitor._state_set("pool_health", "10.0.0.2:tank", "DEGRADED")
    monitor.clear_vanished_pool_state(HOST, [_pool("rpool")])
    assert monitor._state_get("pool_health", "10.0.0.2:tank")[0] == "DEGRADED"


def test_run_checks_without_pools_valid_does_not_clean(temp_db, notes):
    # zpool list failed -> pools=[] but pools_valid=False: state must survive
    monitor._state_set("pool_health", "10.0.0.1:tank", "DEGRADED")
    monitor.run_checks(HOST, [], reachable=True, pools_valid=False)
    assert monitor._state_get("pool_health", "10.0.0.1:tank")[0] == "DEGRADED"


def test_run_checks_with_pools_valid_cleans(temp_db, notes, monkeypatch):
    # avoid the auto-snapshot check SSH-ing anywhere
    monkeypatch.setattr(monitor, "check_auto_snapshots", lambda h: None)
    monitor._state_set("pool_health", "10.0.0.1:tank", "DEGRADED")
    monitor.run_checks(HOST, [_pool("rpool")], reachable=True, pools_valid=True)
    assert monitor._state_get("pool_health", "10.0.0.1:tank") == (None, None)


def test_prefix_no_false_match_on_similar_address(temp_db, notes):
    # 10.0.0.1x must not be treated as 10.0.0.1 (prefix includes the colon)
    monitor._state_set("pool_health", "10.0.0.11:tank", "DEGRADED")
    monitor.clear_vanished_pool_state(HOST, [_pool("rpool")])
    assert monitor._state_get("pool_health", "10.0.0.11:tank")[0] == "DEGRADED"