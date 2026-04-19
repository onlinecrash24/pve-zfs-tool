"""Proactive monitoring — fires notifications on state changes.

Called from the metrics sampler after each round. Uses a small
``monitor_state`` SQLite table to remember previous values per
``(scope, key)`` so we only alert on change (and throttle repeated
alerts via ``last_alert_ts``).

Events produced (must match keys in notifications.DEFAULT_CONFIG.events):

- ``pool_error``      — pool health transitioned ONLINE → DEGRADED/FAULTED/UNAVAIL/REMOVED
                        (also fires on recovery → ONLINE with a positive message)
- ``health_warning``  — capacity crossed 90 % upward, or read/write/cksum
                        errors > 0 appeared
- ``host_offline``    — SSH probe failed where it previously succeeded
                        (also fires on recovery)
- ``auto_snapshot``   — newest auto-snap per (pool, label) older than its
                        expected max-age (stale). Throttled to once per day
                        per (host, pool, label) to avoid daily spam.
"""

import json
import logging
import time

from app.database import get_conn
from app.notifications import send_notification

log = logging.getLogger(__name__)

# Health values we consider "bad"
BAD_HEALTH = {"DEGRADED", "FAULTED", "UNAVAIL", "REMOVED", "SUSPENDED"}

# Capacity warning threshold (%)
CAPACITY_WARN_PCT = 90.0

# Auto-snap staleness thresholds (seconds). Entries not listed here are
# ignored. Generous multipliers keep false positives down.
STALE_THRESHOLDS = {
    "frequent": 2 * 3600,       # expected every 15 min, alert after 2 h
    "hourly":   4 * 3600,       # expected hourly, alert after 4 h
    "daily":    30 * 3600,      # alert after 30 h
    "weekly":   9 * 86400,      # alert after 9 d
    "monthly":  33 * 86400,     # alert after 33 d
}

# Anti-spam: suppress repeat alerts per (host, pool, label) within N seconds
STALE_ALERT_COOLDOWN = 24 * 3600
CAPACITY_ALERT_COOLDOWN = 12 * 3600
ERROR_ALERT_COOLDOWN = 6 * 3600


# ---------------------------------------------------------------------------
# State store helpers
# ---------------------------------------------------------------------------

def _state_get(scope, key):
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value, last_alert_ts FROM monitor_state WHERE scope=? AND key=?",
            (scope, key),
        ).fetchone()
        if not row:
            return None, None
        return row["value"], row["last_alert_ts"]
    finally:
        conn.close()


def _state_set(scope, key, value, last_alert_ts=None):
    now = int(time.time())
    conn = get_conn()
    try:
        existing_alert = None
        if last_alert_ts is None:
            row = conn.execute(
                "SELECT last_alert_ts FROM monitor_state WHERE scope=? AND key=?",
                (scope, key),
            ).fetchone()
            if row:
                existing_alert = row["last_alert_ts"]
        conn.execute(
            """INSERT INTO monitor_state (scope, key, value, last_alert_ts, updated_ts)
               VALUES (?,?,?,?,?)
               ON CONFLICT(scope, key) DO UPDATE SET
                 value=excluded.value,
                 last_alert_ts=excluded.last_alert_ts,
                 updated_ts=excluded.updated_ts""",
            (scope, key, value,
             last_alert_ts if last_alert_ts is not None else existing_alert,
             now),
        )
        conn.commit()
    finally:
        conn.close()


def _mark_alerted(scope, key):
    """Bump last_alert_ts without changing value."""
    now = int(time.time())
    conn = get_conn()
    try:
        conn.execute(
            "UPDATE monitor_state SET last_alert_ts=?, updated_ts=? WHERE scope=? AND key=?",
            (now, now, scope, key),
        )
        conn.commit()
    finally:
        conn.close()


def _cooldown_ok(last_alert_ts, cooldown):
    if not last_alert_ts:
        return True
    return (int(time.time()) - int(last_alert_ts)) >= cooldown


# ---------------------------------------------------------------------------
# Checks — called per host per sample round
# ---------------------------------------------------------------------------

def check_host_reachability(host, reachable):
    """Fire host_offline on transition up↔down."""
    scope = "host"
    key = host["address"]
    prev, _ = _state_get(scope, key)
    new = "up" if reachable else "down"
    if prev is None:
        _state_set(scope, key, new)
        return
    if prev == new:
        return
    name = host.get("name") or host["address"]
    if not reachable:
        send_notification(
            "host_offline",
            "Host Offline",
            f"{name} ({host['address']}) is not reachable via SSH.",
            priority=8,
        )
    else:
        send_notification(
            "host_offline",
            "Host Back Online",
            f"{name} ({host['address']}) is reachable again.",
            priority=3,
        )
    _state_set(scope, key, new, last_alert_ts=int(time.time()))


def check_pool_health(host, pools):
    """Fire pool_error on health change (bad ↔ good)."""
    scope = "pool_health"
    for p in pools:
        pool_name = p.get("name", "")
        health = (p.get("health") or "").upper()
        if not pool_name or not health:
            continue
        key = f"{host['address']}:{pool_name}"
        prev, _ = _state_get(scope, key)
        if prev is None:
            _state_set(scope, key, health)
            continue
        if prev == health:
            continue
        name = host.get("name") or host["address"]
        if health in BAD_HEALTH:
            send_notification(
                "pool_error",
                f"Pool {pool_name}: {health}",
                f"Pool '{pool_name}' on {name} transitioned "
                f"from {prev} to {health}.\n\n"
                f"Run 'zpool status {pool_name}' on the host for details.",
                priority=9,
            )
        elif prev in BAD_HEALTH and health == "ONLINE":
            send_notification(
                "pool_error",
                f"Pool {pool_name}: Recovered",
                f"Pool '{pool_name}' on {name} recovered: "
                f"{prev} → {health}.",
                priority=4,
            )
        _state_set(scope, key, health, last_alert_ts=int(time.time()))


def check_capacity(host, pools):
    """Fire health_warning on capacity crossing upward past CAPACITY_WARN_PCT."""
    scope = "capacity"
    for p in pools:
        pool_name = p.get("name", "")
        cap_raw = p.get("cap", "")
        if not pool_name or not cap_raw:
            continue
        try:
            cap = float(str(cap_raw).rstrip("%"))
        except (ValueError, TypeError):
            continue
        key = f"{host['address']}:{pool_name}"
        prev, last_alert = _state_get(scope, key)
        prev_above = prev == "above"
        now_above = cap >= CAPACITY_WARN_PCT
        new_val = "above" if now_above else "below"
        if prev is None:
            _state_set(scope, key, new_val)
            continue
        # Upward crossing — alert if cooldown allows
        if now_above and not prev_above and _cooldown_ok(last_alert, CAPACITY_ALERT_COOLDOWN):
            name = host.get("name") or host["address"]
            send_notification(
                "health_warning",
                f"Capacity Warning: {pool_name}",
                f"Pool '{pool_name}' on {name} is at {cap:.0f}% "
                f"(threshold {CAPACITY_WARN_PCT:.0f}%).",
                priority=7,
            )
            _state_set(scope, key, new_val, last_alert_ts=int(time.time()))
        else:
            _state_set(scope, key, new_val)


def check_pool_errors(host, pools_status):
    """Fire health_warning when read/write/cksum errors > 0 appear.

    ``pools_status`` is a dict: pool_name → parsed zpool status dict that
    contains ``errors`` counters. We tolerate missing data silently.
    """
    scope = "pool_errors"
    for pool_name, status in (pools_status or {}).items():
        if not isinstance(status, dict):
            continue
        # zpool status exposes per-vdev read/write/cksum; sum them if the
        # caller hands us a precomputed total, else look for a flat field.
        totals = status.get("error_totals") or {}
        r = int(totals.get("read", 0) or 0)
        w = int(totals.get("write", 0) or 0)
        c = int(totals.get("cksum", 0) or 0)
        total = r + w + c
        key = f"{host['address']}:{pool_name}"
        prev, last_alert = _state_get(scope, key)
        prev_n = int(prev) if prev and prev.isdigit() else 0
        if total == prev_n:
            continue
        if total > prev_n and total > 0 and _cooldown_ok(last_alert, ERROR_ALERT_COOLDOWN):
            name = host.get("name") or host["address"]
            send_notification(
                "health_warning",
                f"I/O Errors: {pool_name}",
                f"Pool '{pool_name}' on {name} reports "
                f"read={r} write={w} cksum={c}.",
                priority=8,
            )
            _state_set(scope, key, str(total), last_alert_ts=int(time.time()))
        else:
            _state_set(scope, key, str(total))


def check_auto_snapshots(host):
    """Fire auto_snapshot when the newest snap per label is stale.

    Pipeline: get_snapshot_ages(host) + retention_policy (from cron)
              → analyze_snapshots() → per_label[label].stale_datasets
    Throttled per (host, label) via STALE_ALERT_COOLDOWN.
    """
    try:
        from app.zfs_commands import get_snapshot_ages, get_auto_snapshot_status
        from app.snapshot_analysis import analyze_snapshots
    except Exception:
        return
    try:
        snap_age_data = get_snapshot_ages(host)
        if not isinstance(snap_age_data, dict) or not snap_age_data.get("datasets"):
            return
        retention_cfg = {}
        try:
            st = get_auto_snapshot_status(host)
            if isinstance(st, dict):
                retention_cfg = st.get("retention_policy") or {}
        except Exception:
            pass
        analysis = analyze_snapshots(snap_age_data, retention_cfg)
    except Exception as e:
        log.debug("auto-snap analysis failed for %s: %s", host.get("address"), e)
        return

    per_label = (analysis or {}).get("per_label") or {}
    if not isinstance(per_label, dict):
        return

    name = host.get("name") or host["address"]
    now = int(time.time())
    scope = "stale_snap"
    for label, info in per_label.items():
        if label not in STALE_THRESHOLDS or not isinstance(info, dict):
            continue
        stale = info.get("stale_datasets") or []
        if not stale:
            continue
        # stale_datasets entries are dicts {dataset, newest_age, ...}
        # (analyze_snapshots in snapshot_analysis.py). Be defensive anyway.
        norm = []
        for e in stale:
            if isinstance(e, str):
                norm.append({"dataset": e})
            elif isinstance(e, dict) and e.get("dataset"):
                norm.append(e)
        if not norm:
            continue
        key = f"{host['address']}:{label}"
        _prev, last_alert = _state_get(scope, key)
        if not _cooldown_ok(last_alert, STALE_ALERT_COOLDOWN):
            continue
        ds_list = ", ".join(e["dataset"] for e in norm[:6])
        more = f" (+{len(norm) - 6} more)" if len(norm) > 6 else ""
        send_notification(
            "auto_snapshot",
            f"Stale {label} snapshots on {name}",
            f"{len(norm)} dataset(s) have no recent '{label}' snapshot "
            f"on {name} ({host['address']}):\n{ds_list}{more}",
            priority=5,
        )
        _state_set(scope, key, json.dumps({"count": len(norm)}),
                   last_alert_ts=now)


# ---------------------------------------------------------------------------
# Entry point used by the metrics sampler
# ---------------------------------------------------------------------------

def run_checks(host, pools, reachable, pools_status=None):
    """Run all checks for one host. Never raises."""
    try:
        check_host_reachability(host, reachable)
    except Exception as e:
        log.warning("monitor: host reachability check failed: %s", e)
    if not reachable:
        return
    try:
        check_pool_health(host, pools or [])
    except Exception as e:
        log.warning("monitor: pool_health check failed: %s", e)
    try:
        check_capacity(host, pools or [])
    except Exception as e:
        log.warning("monitor: capacity check failed: %s", e)
    try:
        check_pool_errors(host, pools_status or {})
    except Exception as e:
        log.warning("monitor: pool_errors check failed: %s", e)
    try:
        check_auto_snapshots(host)
    except Exception as e:
        log.warning("monitor: auto_snapshots check failed: %s", e)
