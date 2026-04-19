"""Historical pool metrics sampler and query API.

Samples pool usage/fragmentation/health periodically into SQLite and
exposes query helpers for the frontend trend charts.
"""

import logging
import threading
import time

from app.database import get_conn

log = logging.getLogger(__name__)

# Defaults — override via env if needed.
SAMPLE_INTERVAL = 900   # 15 minutes
RETENTION_DAYS = 90     # keep 90 days of samples
_FIRST_DELAY = 30       # wait 30s after startup before first sample

_thread = None
_stop = threading.Event()


# ---------------------------------------------------------------------------
# Parsers for zpool list output (e.g. "1.5T", "45%", "1.20x")
# ---------------------------------------------------------------------------

_SIZE_UNITS = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3,
               "T": 1024**4, "P": 1024**5}


def _parse_size(s):
    if not s or s in ("-", ""):
        return None
    s = s.strip().upper()
    try:
        unit = s[-1]
        if unit in _SIZE_UNITS:
            return int(float(s[:-1]) * _SIZE_UNITS[unit])
        return int(float(s))
    except (ValueError, IndexError):
        return None


def _parse_pct(s):
    if not s:
        return None
    try:
        return float(str(s).rstrip("%"))
    except ValueError:
        return None


def _parse_dedup(s):
    if not s:
        return None
    try:
        return float(str(s).rstrip("x"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def sample_host(host):
    """Take one sample of all pools on a host. Returns number of pools stored."""
    # Import lazily to avoid circular deps at module load time
    from app.zfs_commands import get_pools

    now = int(time.time())
    pools = get_pools(host)
    if not pools:
        return 0

    conn = get_conn()
    try:
        cur = conn.cursor()
        for p in pools:
            cur.execute(
                """INSERT INTO pool_metrics
                   (timestamp, host, pool, size_bytes, alloc_bytes, free_bytes,
                    frag_pct, cap_pct, health, dedup_ratio)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    now,
                    host["address"],
                    p.get("name", ""),
                    _parse_size(p.get("size")),
                    _parse_size(p.get("alloc")),
                    _parse_size(p.get("free")),
                    _parse_pct(p.get("frag")),
                    _parse_pct(p.get("cap")),
                    p.get("health") or None,
                    _parse_dedup(p.get("dedup")),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return len(pools)


def _cleanup_old():
    """Delete samples older than RETENTION_DAYS."""
    cutoff = int(time.time()) - RETENTION_DAYS * 86400
    try:
        conn = get_conn()
        conn.execute("DELETE FROM pool_metrics WHERE timestamp < ?", (cutoff,))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning("metrics cleanup failed: %s", e)


def _loop():
    from app.ssh_manager import load_hosts

    # Wait a little before first sample (give the app time to settle)
    if _stop.wait(_FIRST_DELAY):
        return

    while not _stop.is_set():
        try:
            hosts = load_hosts()
            for host in hosts:
                try:
                    n = sample_host(host)
                    log.debug("metrics: sampled %s pools from %s", n, host.get("address"))
                except Exception as e:
                    log.warning("metrics: sample failed for %s: %s",
                                host.get("address"), e)
            _cleanup_old()
        except Exception as e:
            log.error("metrics loop error: %s", e)
        _stop.wait(SAMPLE_INTERVAL)


def start_sampler():
    """Start the background sampler thread (idempotent)."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, daemon=True, name="metrics-sampler")
    _thread.start()
    log.info("Metrics sampler started (interval=%ss, retention=%sd)",
             SAMPLE_INTERVAL, RETENTION_DAYS)


def stop_sampler():
    _stop.set()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def query_pool_series(host_addr, pool=None, hours=24):
    """Return time series for pool(s). List of dicts sorted by time."""
    since = int(time.time()) - int(hours) * 3600
    conn = get_conn()
    try:
        if pool:
            rows = conn.execute(
                """SELECT timestamp, host, pool, size_bytes, alloc_bytes, free_bytes,
                          frag_pct, cap_pct, health, dedup_ratio
                   FROM pool_metrics
                   WHERE host=? AND pool=? AND timestamp >= ?
                   ORDER BY timestamp""",
                (host_addr, pool, since),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT timestamp, host, pool, size_bytes, alloc_bytes, free_bytes,
                          frag_pct, cap_pct, health, dedup_ratio
                   FROM pool_metrics
                   WHERE host=? AND timestamp >= ?
                   ORDER BY pool, timestamp""",
                (host_addr, since),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def list_pools(host_addr):
    """List pool names that have samples on this host."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT pool FROM pool_metrics WHERE host=? ORDER BY pool",
            (host_addr,),
        ).fetchall()
        return [r["pool"] for r in rows]
    finally:
        conn.close()


def summary(host_addr=None):
    """Return { pool_count, sample_count, oldest, newest } for dashboard."""
    conn = get_conn()
    try:
        if host_addr:
            row = conn.execute(
                """SELECT COUNT(*) AS n, MIN(timestamp) AS oldest,
                          MAX(timestamp) AS newest,
                          COUNT(DISTINCT pool) AS pools
                   FROM pool_metrics WHERE host=?""",
                (host_addr,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT COUNT(*) AS n, MIN(timestamp) AS oldest,
                          MAX(timestamp) AS newest,
                          COUNT(DISTINCT host || '|' || pool) AS pools
                   FROM pool_metrics"""
            ).fetchone()
        return {
            "samples": row["n"] or 0,
            "pools": row["pools"] or 0,
            "oldest": row["oldest"],
            "newest": row["newest"],
            "interval_seconds": SAMPLE_INTERVAL,
            "retention_days": RETENTION_DAYS,
        }
    finally:
        conn.close()
