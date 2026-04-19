"""In-memory TTL cache for SSH command results.

Used to reduce SSH traffic for frequently-polled read operations
(zpool list, zfs list, smartctl, etc.). Writes invalidate the host
entry so the next read fetches fresh data.
"""

import threading
import time

_store = {}  # (host_addr, command) -> (expires_at, result)
_lock = threading.RLock()

_stats = {"hits": 0, "misses": 0, "writes": 0, "invalidations": 0}


def get(host_addr, command):
    """Return cached result if not expired, else None."""
    with _lock:
        entry = _store.get((host_addr, command))
        if entry is None:
            _stats["misses"] += 1
            return None
        expires_at, result = entry
        if expires_at <= time.time():
            _store.pop((host_addr, command), None)
            _stats["misses"] += 1
            return None
        _stats["hits"] += 1
        return result


def set(host_addr, command, result, ttl):
    """Store result for ttl seconds. No-op if ttl <= 0."""
    if ttl <= 0:
        return
    with _lock:
        _store[(host_addr, command)] = (time.time() + ttl, result)
        _stats["writes"] += 1


def invalidate_host(host_addr):
    """Drop all cache entries for a host (after a write)."""
    with _lock:
        keys = [k for k in _store if k[0] == host_addr]
        for k in keys:
            _store.pop(k, None)
        if keys:
            _stats["invalidations"] += len(keys)


def invalidate_all():
    with _lock:
        n = len(_store)
        _store.clear()
        _stats["invalidations"] += n


def stats():
    with _lock:
        now = time.time()
        live = sum(1 for exp, _ in _store.values() if exp > now)
        total = _stats["hits"] + _stats["misses"]
        hit_rate = (_stats["hits"] / total * 100.0) if total else 0.0
        return {
            "entries": len(_store),
            "live": live,
            "hits": _stats["hits"],
            "misses": _stats["misses"],
            "writes": _stats["writes"],
            "invalidations": _stats["invalidations"],
            "hit_rate_pct": round(hit_rate, 1),
        }
