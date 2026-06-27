"""latest_pool_rows must drop pools not sampled recently (destroyed pools)
instead of showing them until the 90-day metrics retention kicks in."""

import time
import pytest

from app import database
from app import analytics


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(database, "_initialized", False)
    database.init_db()
    yield


def _insert(host, pool, ts, cap=10.0, health="ONLINE"):
    conn = database.get_conn()
    try:
        conn.execute(
            "INSERT INTO pool_metrics (timestamp, host, pool, size_bytes, "
            "alloc_bytes, free_bytes, frag_pct, cap_pct, health, dedup_ratio) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ts, host, pool, 1000, 100, 900, 5.0, cap, health, 1.0),
        )
        conn.commit()
    finally:
        conn.close()


def test_recent_pool_kept_destroyed_dropped(temp_db):
    now = int(time.time())
    _insert("10.0.0.1", "rpool", now)                 # current
    _insert("10.0.0.1", "rpool", now - 3600)          # older sample of same pool
    _insert("10.0.0.1", "oldpool", now - 10 * 3600)   # destroyed >6h ago

    rows = analytics.latest_pool_rows()
    pools = {(r["host"], r["pool"]) for r in rows}
    assert ("10.0.0.1", "rpool") in pools
    assert ("10.0.0.1", "oldpool") not in pools
    # the kept row is the latest sample
    rpool = [r for r in rows if r["pool"] == "rpool"][0]
    assert rpool["timestamp"] == now


def test_disable_filter_returns_all(temp_db):
    now = int(time.time())
    _insert("10.0.0.1", "rpool", now)
    _insert("10.0.0.1", "oldpool", now - 10 * 3600)
    rows = analytics.latest_pool_rows(max_age_seconds=None)
    pools = {r["pool"] for r in rows}
    assert pools == {"rpool", "oldpool"}


def test_custom_window(temp_db):
    now = int(time.time())
    _insert("10.0.0.1", "rpool", now - 2 * 3600)   # 2h old
    # window of 1h -> the 2h-old pool drops
    assert analytics.latest_pool_rows(max_age_seconds=3600) == []
    # window of 3h -> it stays
    assert len(analytics.latest_pool_rows(max_age_seconds=3 * 3600)) == 1
