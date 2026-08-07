"""Analytics: capacity forecasting and dashboard aggregation.

- ``forecast_days_until_full(host, pool)``: linear regression on the last
  ``FORECAST_WINDOW_DAYS`` of ``pool_metrics`` to estimate when the pool's
  allocated bytes will reach ``size_bytes``. Returns ``None`` if the pool
  is shrinking, flat, or has too few samples for a useful fit.

- ``dashboard()``: aggregated view (hosts, pool health, capacity
  warnings, stale auto-snapshots, recent audit failures) used by the
  Home-page dashboard and the Prometheus exporter.
"""

import json
import logging
import time

from app.database import get_conn

log = logging.getLogger(__name__)

# A 30-day least-squares fit is almost frozen: at 15-minute sampling that is
# ~2880 points, so one new sample moves the slope by ~0.03 % and the projection
# never visibly reacts to what the pool is doing now. It is also skewed by the
# sawtooth that auto-snapshots create (allocation jumps up, pruning drops it).
# So: prefer a short recent window, widen only when there is not enough data,
# collapse the samples into hourly medians (kills the sawtooth), and take a
# median-of-slopes rather than least squares (immune to the remaining spikes).
FORECAST_WINDOW_DAYS = 30         # fallback window when recent data is thin
FORECAST_PRIMARY_DAYS = 7         # preferred window: reacts within days
FORECAST_MIN_SAMPLES = 8          # need at least ~2 h of data @ 15 min
FORECAST_MIN_BUCKETS = 6          # hourly medians needed for a usable slope
FORECAST_BUCKET_SECONDS = 3600
FORECAST_MAX_DAYS = 3650          # clamp absurd long tails


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------

def _linreg(xs, ys):
    """Simple least-squares linear regression. Returns (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return None, None
    sx = sum(xs)
    sy = sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return None, None
    slope = (n * sxy - sx * sy) / denom
    intercept = (sy - slope * sx) / n
    return slope, intercept


def bucket_medians(points, bucket_seconds=FORECAST_BUCKET_SECONDS):
    """[(timestamp, value)] collapsed into per-bucket medians.

    Auto-snapshots make allocation jump up and drop again between samples; the
    median of each hour is what the pool actually holds, without that noise.
    """
    buckets = {}
    for ts, val in points or []:
        buckets.setdefault(int(ts) // bucket_seconds, []).append(float(val))
    out = []
    for b in sorted(buckets):
        vals = sorted(buckets[b])
        mid = len(vals) // 2
        median = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0
        out.append((b * bucket_seconds, median))
    return out


def theil_sen_slope(points):
    """Median of all pairwise slopes -- robust where least squares is not.

    A single snapshot burst or a big delete drags a least-squares line along
    with it; the median of slopes ignores such outliers, which matters because
    the whole point is to describe the underlying trend.
    """
    n = len(points or [])
    if n < 2:
        return None
    slopes = []
    for i in range(n - 1):
        x1, y1 = points[i]
        for j in range(i + 1, n):
            x2, y2 = points[j]
            dx = x2 - x1
            if dx > 0:
                slopes.append((y2 - y1) / dx)
    if not slopes:
        return None
    slopes.sort()
    mid = len(slopes) // 2
    return slopes[mid] if len(slopes) % 2 else (slopes[mid - 1] + slopes[mid]) / 2.0


def _fetch_pool_series(host_addr, pool, window_days):
    since = int(time.time()) - window_days * 86400
    conn = get_conn()
    try:
        return conn.execute(
            """SELECT timestamp, alloc_bytes, size_bytes
               FROM pool_metrics
               WHERE host=? AND pool=?
                 AND timestamp >= ?
                 AND alloc_bytes IS NOT NULL
                 AND size_bytes IS NOT NULL
               ORDER BY timestamp""",
            (host_addr, pool, since),
        ).fetchall()
    finally:
        conn.close()


def forecast_detail(host_addr, pool):
    """Growth trend and projected time to full.

    Returns ``{days, bytes_per_day, window_days, reason}``. ``days`` is None
    when no projection is possible; ``reason`` says why, so the UI can explain
    a dash instead of leaving the user guessing.
    """
    out = {"days": None, "bytes_per_day": None,
           "window_days": FORECAST_PRIMARY_DAYS, "reason": "no_data"}

    # Prefer the recent window; widen only if it is too thin to fit.
    rows = _fetch_pool_series(host_addr, pool, FORECAST_PRIMARY_DAYS)
    buckets = bucket_medians([(r["timestamp"], r["alloc_bytes"]) for r in rows])
    if len(rows) < FORECAST_MIN_SAMPLES or len(buckets) < FORECAST_MIN_BUCKETS:
        rows = _fetch_pool_series(host_addr, pool, FORECAST_WINDOW_DAYS)
        buckets = bucket_medians([(r["timestamp"], r["alloc_bytes"]) for r in rows])
        out["window_days"] = FORECAST_WINDOW_DAYS
    if len(rows) < FORECAST_MIN_SAMPLES or len(buckets) < 2:
        return out

    # x in hours -> slope is bytes/hour
    t0 = buckets[0][0]
    slope = theil_sen_slope([((t - t0) / 3600.0, v) for t, v in buckets])
    if slope is None:
        return out
    out["bytes_per_day"] = slope * 24.0

    size_bytes = float(rows[-1]["size_bytes"])
    latest_alloc = float(rows[-1]["alloc_bytes"])
    if latest_alloc >= size_bytes:
        out.update(days=0, reason="full")
        return out
    if slope <= 0:
        out["reason"] = "no_growth"
        return out

    days = ((size_bytes - latest_alloc) / slope) / 24.0
    if days <= 0:
        out.update(days=0, reason="full")
    elif days > FORECAST_MAX_DAYS:
        out["reason"] = "too_far"
    else:
        out.update(days=round(days, 1), reason="ok")
    return out


def forecast_days_until_full(host_addr, pool, window_days=None):
    """Days until the pool is projected to reach 100 % allocated, or None."""
    return forecast_detail(host_addr, pool)["days"]


# ---------------------------------------------------------------------------
# Latest per-pool metrics
# ---------------------------------------------------------------------------

# Only treat a pool as "current" if it was sampled within this window. The
# sampler writes a fresh row for every existing pool every ~15 min, so a pool
# that hasn't appeared in 6 h has been destroyed (or its host has been offline
# that long, in which case it shows as offline anyway). Without this filter a
# destroyed pool lingered in the dashboard/Prometheus for the full 90-day
# metrics retention.
LATEST_POOL_MAX_AGE = 6 * 3600


def latest_pool_rows(max_age_seconds=LATEST_POOL_MAX_AGE):
    """Return the most recent pool_metrics row per (host, pool), restricted to
    pools seen within ``max_age_seconds`` so destroyed pools don't linger.

    Used by both the dashboard and the Prometheus exporter so each data
    point comes from the same sample. Rows are dicts (sqlite3.Row-like).
    Pass ``max_age_seconds=None`` to disable the recency filter.
    """
    conn = get_conn()
    try:
        if max_age_seconds is None:
            rows = conn.execute(
                """SELECT pm.* FROM pool_metrics pm
                   JOIN (
                     SELECT host, pool, MAX(timestamp) AS ts
                     FROM pool_metrics GROUP BY host, pool
                   ) latest
                   ON pm.host = latest.host
                     AND pm.pool = latest.pool
                     AND pm.timestamp = latest.ts"""
            ).fetchall()
        else:
            since = int(time.time()) - int(max_age_seconds)
            rows = conn.execute(
                """SELECT pm.* FROM pool_metrics pm
                   JOIN (
                     SELECT host, pool, MAX(timestamp) AS ts
                     FROM pool_metrics
                     WHERE timestamp >= ?
                     GROUP BY host, pool
                   ) latest
                   ON pm.host = latest.host
                     AND pm.pool = latest.pool
                     AND pm.timestamp = latest.ts""",
                (since,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Dashboard aggregation
# ---------------------------------------------------------------------------

def _monitor_state_map(scope):
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT key, value, updated_ts FROM monitor_state WHERE scope=?",
            (scope,),
        ).fetchall()
        return {r["key"]: {"value": r["value"], "updated_ts": r["updated_ts"]}
                for r in rows}
    finally:
        conn.close()


def build_stale_detail(stale_state, name_by_addr):
    """Turn the ``stale_snap`` monitor-state map (key ``addr:label`` ->
    {value: '{"count": N}', updated_ts}) into the per-host:label list the
    Home tile clicks through to. Newest first, then host/label for stable
    display. Pure (no DB) so it's unit-tested."""
    out = []
    for key, v in (stale_state or {}).items():
        host_addr, _, label = key.partition(":")
        count = None
        try:
            count = json.loads(v.get("value") or "{}").get("count")
        except Exception:
            pass
        out.append({
            "host_address": host_addr,
            "host_name": (name_by_addr or {}).get(host_addr, host_addr),
            "label": label,
            "count": count,
            "updated_ts": v.get("updated_ts"),
        })
    out.sort(key=lambda d: (-(d["updated_ts"] or 0), d["host_name"], d["label"]))
    return out


def summarize_host_pools(reachable, pools):
    """Account one host's pool samples into dashboard counters.

    Returns ``(counts, annotated_pools)``. A pool on a host that is currently
    **offline** (``reachable is False``) cannot have its last sample trusted as
    current, so it is flagged ``stale`` and excluded from every counter
    (online / degraded / capacity / forecast). This stops a dead host's last
    "ONLINE" reading from inflating the POOLS tile. ``reachable`` True or None
    (not yet classified) is treated as live and counted as before.
    """
    counts = {"pools_total": 0, "pools_ok": 0, "pools_degraded": 0,
              "pools_capacity_warn": 0, "forecast_pools_critical": 0}
    # Same threshold the notification uses, so the dashboard tile and the alert
    # can never disagree about what counts as "too full".
    try:
        from app.monitor import capacity_thresholds
        warn_pct = capacity_thresholds()[0]
    except Exception:
        warn_pct = 70.0
    stale = (reachable is False)
    annotated = []
    for p in pools:
        pp = dict(p)
        pp["stale"] = stale
        annotated.append(pp)
        if stale:
            continue
        counts["pools_total"] += 1
        health = (p.get("health") or "").upper()
        if health and health != "ONLINE":
            counts["pools_degraded"] += 1
        elif health == "ONLINE":
            counts["pools_ok"] += 1
        cap = p.get("cap_pct")
        if cap is not None and cap >= warn_pct:
            counts["pools_capacity_warn"] += 1
        days = p.get("forecast_days_until_full")
        if days is not None and days < 30:
            counts["forecast_pools_critical"] += 1
    return counts, annotated


def dashboard():
    """Return a compact status snapshot for the Home-page widget."""
    from app.ssh_manager import load_hosts

    hosts_cfg = load_hosts() or []
    host_state = _monitor_state_map("host")           # key = address
    pool_health_state = _monitor_state_map("pool_health")  # key = addr:pool
    stale_state = _monitor_state_map("stale_snap")    # key = addr:label

    pool_rows = latest_pool_rows()

    # Group latest rows by host
    by_host = {}
    for r in pool_rows:
        by_host.setdefault(r["host"], []).append(r)

    hosts_out = []
    agg = {"pools_total": 0, "pools_ok": 0, "pools_degraded": 0,
           "pools_capacity_warn": 0, "hosts_online": 0, "hosts_offline": 0,
           "hosts_standby": 0, "stale_snap_labels": 0,
           "forecast_pools_critical": 0}
    # Reported so the dashboard tile can name the configured threshold instead
    # of a hardcoded percentage that stops being true once it is changed.
    try:
        from app.monitor import capacity_thresholds
        agg["capacity_warn_pct"] = int(capacity_thresholds()[0])
    except Exception:
        agg["capacity_warn_pct"] = 70

    for h in hosts_cfg:
        addr = h["address"]
        standby = bool(h.get("standby"))
        hstate = host_state.get(addr, {})
        reachable = hstate.get("value") == "up"
        if hstate.get("value") is None:
            reachable = None   # unknown until first sample
        if reachable is True:
            agg["hosts_online"] += 1
        elif reachable is False:
            # A standby host being down is its normal state — count it
            # separately so the HOSTS tile doesn't go red over it.
            if standby:
                agg["hosts_standby"] += 1
            else:
                agg["hosts_offline"] += 1

        pools_here = []
        for r in by_host.get(addr, []):
            health = (r.get("health") or "").upper()
            # Forecast (best-effort; skip on error)
            try:
                fc = forecast_detail(addr, r["pool"])
            except Exception:
                fc = {"days": None, "bytes_per_day": None,
                      "window_days": None, "reason": "error"}
            days = fc["days"]
            pools_here.append({
                "forecast_bytes_per_day": fc.get("bytes_per_day"),
                "forecast_window_days": fc.get("window_days"),
                "forecast_reason": fc.get("reason"),
                "pool": r["pool"],
                "health": health or None,
                "cap_pct": r.get("cap_pct"),
                "size_bytes": r.get("size_bytes"),
                "alloc_bytes": r.get("alloc_bytes"),
                "free_bytes": r.get("free_bytes"),
                "frag_pct": r.get("frag_pct"),
                "sample_ts": r["timestamp"],
                "forecast_days_until_full": days,
            })

        # Offline hosts: don't let a dead host's last sample count as ONLINE.
        counts, pools_annotated = summarize_host_pools(reachable, pools_here)
        for k, v in counts.items():
            agg[k] += v

        hosts_out.append({
            "address": addr,
            "name": h.get("name") or addr,
            "reachable": reachable,
            "standby": standby,
            "last_seen": hstate.get("updated_ts"),
            "pools": pools_annotated,
        })

    # Count stale-snap labels + build the per-host:label breakdown so the
    # Home tile can be clicked through to "where do I find them".
    agg["stale_snap_labels"] = len(stale_state)
    name_by_addr = {h["address"]: (h.get("name") or h["address"]) for h in hosts_cfg}
    agg["stale_snap_detail"] = build_stale_detail(stale_state, name_by_addr)

    # Recent audit failures (last 24 h)
    since = int(time.time()) - 24 * 3600
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM audit_log WHERE success=0 AND timestamp >= ?",
            (since,),
        ).fetchone()
        agg["recent_audit_failures_24h"] = row["n"] or 0
    finally:
        conn.close()

    # Pool_health state map surfaces the set of currently bad pools
    bad_pools = []
    for key, v in pool_health_state.items():
        health = (v.get("value") or "").upper()
        if health and health != "ONLINE":
            host_addr, _, pool = key.partition(":")
            bad_pools.append({"host": host_addr, "pool": pool,
                              "health": health, "updated_ts": v.get("updated_ts")})

    return {
        "aggregate": agg,
        "hosts": hosts_out,
        "bad_pools": bad_pools,
        "generated_at": int(time.time()),
    }


# ---------------------------------------------------------------------------
# Prometheus exposition
# ---------------------------------------------------------------------------

def _prom_escape(s):
    """Escape a label value per Prometheus text format spec."""
    if s is None:
        return ""
    return (str(s).replace("\\", "\\\\")
                  .replace("\n", "\\n")
                  .replace('"', '\\"'))


def _prom_line(metric, labels, value):
    if not labels:
        return f"{metric} {value}"
    parts = ",".join(f'{k}="{_prom_escape(v)}"' for k, v in labels.items())
    return f"{metric}{{{parts}}} {value}"


def prometheus_metrics():
    """Return the /metrics payload in Prometheus text exposition format."""
    from app.ssh_manager import load_hosts

    out = []
    hosts_cfg = {h["address"]: h for h in (load_hosts() or [])}
    host_state = _monitor_state_map("host")
    pool_rows = latest_pool_rows()
    err_state = _monitor_state_map("pool_errors")  # last read+write+cksum sum

    # Host reachability
    out.append("# HELP pvezfs_host_reachable 1 if SSH probe succeeded on last sample, 0 otherwise")
    out.append("# TYPE pvezfs_host_reachable gauge")
    for addr, h in hosts_cfg.items():
        v = host_state.get(addr, {}).get("value")
        val = 1 if v == "up" else 0
        out.append(_prom_line("pvezfs_host_reachable",
                              {"host": addr, "name": h.get("name") or addr},
                              val))

    out.append("# HELP pvezfs_host_last_sample_timestamp_seconds Unix ts of last reachability check")
    out.append("# TYPE pvezfs_host_last_sample_timestamp_seconds gauge")
    for addr, h in hosts_cfg.items():
        ts = host_state.get(addr, {}).get("updated_ts") or 0
        out.append(_prom_line("pvezfs_host_last_sample_timestamp_seconds",
                              {"host": addr, "name": h.get("name") or addr},
                              ts))

    # Per-pool gauges
    gauges = [
        ("pvezfs_pool_size_bytes", "size_bytes",  "Pool total size in bytes"),
        ("pvezfs_pool_alloc_bytes", "alloc_bytes", "Pool allocated bytes"),
        ("pvezfs_pool_free_bytes",  "free_bytes",  "Pool free bytes"),
        ("pvezfs_pool_capacity_percent", "cap_pct", "Pool capacity percent used"),
        ("pvezfs_pool_fragmentation_percent", "frag_pct",
         "Pool fragmentation percent (free-space frag; expected high on SSD)"),
        ("pvezfs_pool_dedup_ratio", "dedup_ratio", "Pool dedup ratio"),
    ]
    for metric, col, help_text in gauges:
        out.append(f"# HELP {metric} {help_text}")
        out.append(f"# TYPE {metric} gauge")
        for r in pool_rows:
            v = r.get(col)
            if v is None:
                continue
            out.append(_prom_line(metric,
                                  {"host": r["host"], "pool": r["pool"]}, v))

    # Health as one-hot (health="ONLINE" etc)
    out.append("# HELP pvezfs_pool_health Current pool health (one-hot per state)")
    out.append("# TYPE pvezfs_pool_health gauge")
    states = ["ONLINE", "DEGRADED", "FAULTED", "UNAVAIL", "REMOVED", "SUSPENDED", "OFFLINE"]
    for r in pool_rows:
        cur = (r.get("health") or "").upper() or "UNKNOWN"
        for s in states:
            out.append(_prom_line("pvezfs_pool_health",
                                  {"host": r["host"], "pool": r["pool"], "state": s},
                                  1 if cur == s else 0))

    # I/O error totals from monitor_state (last observed)
    out.append("# HELP pvezfs_pool_error_total_sum Sum of read+write+cksum errors from zpool status")
    out.append("# TYPE pvezfs_pool_error_total_sum gauge")
    for key, v in err_state.items():
        host_addr, _, pool = key.partition(":")
        try:
            n = int(v.get("value") or 0)
        except (TypeError, ValueError):
            n = 0
        out.append(_prom_line("pvezfs_pool_error_total_sum",
                              {"host": host_addr, "pool": pool}, n))

    # Forecast
    out.append("# HELP pvezfs_pool_forecast_days_until_full Linear forecast; -1 if unknown")
    out.append("# TYPE pvezfs_pool_forecast_days_until_full gauge")
    for r in pool_rows:
        try:
            d = forecast_days_until_full(r["host"], r["pool"])
        except Exception:
            d = None
        out.append(_prom_line("pvezfs_pool_forecast_days_until_full",
                              {"host": r["host"], "pool": r["pool"]},
                              d if d is not None else -1))

    out.append("# HELP pvezfs_sampler_pools_total Current number of pools tracked")
    out.append("# TYPE pvezfs_sampler_pools_total gauge")
    out.append(_prom_line("pvezfs_sampler_pools_total", {}, len(pool_rows)))

    out.append("# HELP pvezfs_scrape_timestamp_seconds Time when this payload was generated")
    out.append("# TYPE pvezfs_scrape_timestamp_seconds gauge")
    out.append(_prom_line("pvezfs_scrape_timestamp_seconds", {}, int(time.time())))

    return "\n".join(out) + "\n"
