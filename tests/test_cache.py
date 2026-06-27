"""TTL in-memory SSH result cache."""

import time
from app import cache


def setup_function():
    cache.invalidate_all()


def test_set_and_get_hit():
    cache.set("h1", "zpool list", {"ok": True}, ttl=60)
    assert cache.get("h1", "zpool list") == {"ok": True}


def test_miss_returns_none():
    assert cache.get("h1", "never cached") is None


def test_expiry():
    cache.set("h1", "cmd", {"x": 1}, ttl=1)
    assert cache.get("h1", "cmd") == {"x": 1}
    # simulate expiry by pushing the stored entry into the past
    time.sleep(1.1)
    assert cache.get("h1", "cmd") is None


def test_invalidate_host_scoped():
    cache.set("h1", "a", {"v": 1}, ttl=60)
    cache.set("h2", "a", {"v": 2}, ttl=60)
    cache.invalidate_host("h1")
    assert cache.get("h1", "a") is None
    assert cache.get("h2", "a") == {"v": 2}


def test_stats_counts():
    cache.invalidate_all()
    cache.set("h", "c", {"v": 1}, ttl=60)
    cache.get("h", "c")        # hit
    cache.get("h", "missing")  # miss
    s = cache.stats()
    assert s["hits"] >= 1
    assert s["misses"] >= 1
