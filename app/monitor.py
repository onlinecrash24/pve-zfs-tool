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
import threading
import time

from app.database import get_conn
from app.notifications import send_notification

log = logging.getLogger(__name__)

# Serialize the whole alert pass: the metrics sampler thread and request threads
# (e.g. POST /api/metrics/sample-now) must not both run the check-then-set-then-
# notify sequence on the same monitor_state row concurrently, or a transition
# fires twice (duplicate notification, lost cooldown timestamp).
_alert_lock = threading.Lock()

# Health values we consider "bad"
BAD_HEALTH = {"DEGRADED", "FAULTED", "UNAVAIL", "REMOVED", "SUSPENDED"}

# Capacity thresholds (%). Defaults live in notifications.DEFAULT_CONFIG so they
# are configurable; these are the fallbacks when the config cannot be read.
CAPACITY_WARN_PCT = 70.0
CAPACITY_CRIT_PCT = 80.0

_LEVEL_ORDER = {"below": 0, "warn": 1, "crit": 2}


def capacity_thresholds():
    """(warn, crit) from the notification config, sanitised.

    A warn level above crit would make the warning unreachable, so it is pulled
    back down; both are clamped to a sane range.
    """
    warn, crit = CAPACITY_WARN_PCT, CAPACITY_CRIT_PCT
    try:
        from app.notifications import load_config
        th = (load_config() or {}).get("thresholds") or {}
        warn = float(th.get("capacity_warn_pct", warn))
        crit = float(th.get("capacity_crit_pct", crit))
    except Exception:
        pass
    warn = min(max(warn, 1.0), 100.0)
    crit = min(max(crit, 1.0), 100.0)
    if warn > crit:
        warn = crit
    return warn, crit


def capacity_level(cap, warn_pct, crit_pct):
    """Which band a fill percentage falls into."""
    if cap >= crit_pct:
        return "crit"
    if cap >= warn_pct:
        return "warn"
    return "below"


def should_alert_capacity(prev_level, now_level):
    """Whether a capacity change warrants a notification.

    The first observation of a pool used to be recorded silently, so a pool that
    was ALREADY above the threshold when the tool was installed never notified
    at all -- it only alerted on an upward crossing that had happened before
    anyone was watching. Now an unseen pool that is already at/above the warn
    level alerts straight away; afterwards only upward changes do.
    """
    if now_level == "below":
        return False
    if prev_level is None or prev_level not in _LEVEL_ORDER:
        return True
    return _LEVEL_ORDER[now_level] > _LEVEL_ORDER[prev_level]

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


def _state_delete(scope, key):
    """Remove a state row. Used to clear a condition that has recovered so
    aggregate counts (e.g. the stale-snapshot-labels dashboard tile) reflect
    current reality instead of accumulating forever."""
    conn = get_conn()
    try:
        conn.execute("DELETE FROM monitor_state WHERE scope=? AND key=?", (scope, key))
        conn.commit()
    finally:
        conn.close()


def _state_keys(scope, key_prefix):
    """All state keys in a scope starting with key_prefix, with their values.
    Used to find entries for pools that no longer exist on a host."""
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT key, value FROM monitor_state WHERE scope=? AND key LIKE ?",
            (scope, key_prefix + "%"),
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def _cooldown_ok(last_alert_ts, cooldown):
    if not last_alert_ts:
        return True
    return (int(time.time()) - int(last_alert_ts)) >= cooldown


# ---------------------------------------------------------------------------
# Checks — called per host per sample round
# ---------------------------------------------------------------------------

def clear_host_state(address):
    """Drop ALL monitor state for a host — called when the host is removed
    from the tool, so ghost entries (offline flag, pool health, stale-snapshot
    counts, replication-lag state) can't linger on the dashboard or re-alert.

    Covers scope 'host' (key = the bare address) plus every per-object scope
    whose keys are prefixed 'address:' or 'address::' (pool_health, capacity,
    pool_errors, pool_topology, stale_snap, repl). Returns the number of rows removed.
    """
    # '_' is a LIKE wildcard and legal in hostnames — escape so 'my_host'
    # can't accidentally match 'myXhost:...'.
    esc = address.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    conn = get_conn()
    try:
        cur = conn.execute(
            "DELETE FROM monitor_state WHERE (scope='host' AND key=?) "
            "OR key LIKE ? ESCAPE '\\'",
            (address, esc + ":%"),
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def check_host_reachability(host, reachable):
    """Fire host_offline on transition up↔down.

    Hosts flagged ``standby`` (expected offline — e.g. a wake-on-demand backup
    server that is powered off most of the time) never notify: their up/down
    flapping is normal operation. The state is still recorded so the dashboard
    shows the real current status.
    """
    scope = "host"
    key = host["address"]
    new = "up" if reachable else "down"
    if host.get("standby"):
        _state_set(scope, key, new)
        return
    prev, _ = _state_get(scope, key)
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


def clear_vanished_pool_state(host, pools):
    """Drop per-pool state rows for pools that no longer exist on the host.

    Fixes the "ghost" where a pool destroyed/exported while DEGRADED stayed
    in pool_health forever (surfacing in bad_pools). Only call this with a
    *verified* pool listing (zpool list succeeded on a reachable host) --
    never on a fetch failure, where an empty list would wipe real state.
    A vanished pool whose last known health was bad is announced rather than
    silently forgotten, so the cleanup can't suppress a real problem.
    """
    addr = host["address"]
    prefix = f"{addr}:"
    current = {p.get("name") for p in pools if p.get("name")}
    for scope in ("pool_health", "capacity", "pool_errors", "pool_topology"):
        for key, value in _state_keys(scope, prefix).items():
            pool_name = key[len(prefix):]
            if pool_name in current:
                continue
            if scope == "pool_health" and (value or "").upper() in BAD_HEALTH:
                name = host.get("name") or addr
                send_notification(
                    "pool_error",
                    f"Pool {pool_name}: removed while {value}",
                    f"Pool '{pool_name}' on {name} is no longer present; its "
                    f"last known state was {value}. Monitoring state cleared.",
                    priority=6,
                )
            _state_delete(scope, key)


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


def check_pool_topology(host, pools_status):
    """Announce a pool built so that one device failure destroys all of it.

    Reported once, when it first appears -- and then never again. This is a
    structural fact, not an event: an unmirrored special vdev is exactly as
    true tomorrow as today, and repeating it every cycle would be the kind of
    permanent alarm people learn to filter out, taking the real ones with it.
    It stays visible in the pool view for as long as it holds.

    Fires only for the tiers whose loss costs the whole pool. A bare SLOG is
    shown in the UI but not announced: the pool survives it, and this channel
    is for things that end you.
    """
    from app.pool_topology import parse_topology, redundancy_findings
    scope = "pool_topology"
    for pool_name, status in (pools_status or {}).items():
        text = (status or {}).get("status_text") if isinstance(status, dict) else None
        if not text:
            continue
        findings = [f for f in redundancy_findings(parse_topology(text))
                    if f["severity"] == "crit" and f["pool"] == pool_name]
        # A stable fingerprint of the risk, so re-announcing happens when the
        # layout actually changes -- not on every restart.
        marker = ",".join(sorted(f"{f['tier']}:{f['vdev']}" for f in findings))
        key = f"{host['address']}:{pool_name}"
        prev, _ = _state_get(scope, key)
        if prev == marker:
            continue
        _state_set(scope, key, marker, last_alert_ts=int(time.time()))
        if not marker or prev is None:
            # Nothing wrong, or first sight of this pool: record and stay quiet.
            # Announcing on first sight would fire for every existing pool the
            # moment this check ships.
            continue
        name = host.get("name") or host["address"]
        vdevs = ", ".join(f["vdev"] for f in findings)
        send_notification(
            "health_warning",
            f"Pool {pool_name}: no redundancy on {vdevs}",
            f"Pool '{pool_name}' on {name} now has a vdev without redundancy: "
            f"{vdevs}.\n\n"
            f"These hold data the rest of the pool cannot be read without -- "
            f"losing one of these devices loses the entire pool, and it will "
            f"report ONLINE until it does.",
            priority=8,
        )


def check_capacity(host, pools):
    """Fire health_warning when a pool reaches the warn or critical fill level."""
    scope = "capacity"
    warn_pct, crit_pct = capacity_thresholds()
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
        level = capacity_level(cap, warn_pct, crit_pct)
        if (should_alert_capacity(prev, level)
                and _cooldown_ok(last_alert, CAPACITY_ALERT_COOLDOWN)):
            name = host.get("name") or host["address"]
            crit = level == "crit"
            limit = crit_pct if crit else warn_pct
            send_notification(
                "health_warning",
                f"Capacity {'Critical' if crit else 'Warning'}: {pool_name}",
                f"Pool '{pool_name}' on {name} is at {cap:.0f}% "
                f"(threshold {limit:.0f}%).\n\n"
                "Snapshots often account for the difference between used data "
                "and pool usage -- check the snapshot list if this is unexpected.",
                priority=9 if crit else 7,
            )
            _state_set(scope, key, level, last_alert_ts=int(time.time()))
        else:
            _state_set(scope, key, level)


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
        from app.zfs_commands import (get_snapshot_ages, get_auto_snapshot_status,
                                      get_autosnap_disabled_datasets)
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
        # Replica awareness: same exclusions/relaxed thresholds as the
        # Snapshot Check page, so the dashboard tile can't disagree with it.
        try:
            autosnap_disabled = get_autosnap_disabled_datasets(host)
        except Exception:
            autosnap_disabled = set()
        analysis = analyze_snapshots(snap_age_data, retention_cfg,
                                     autosnap_disabled=autosnap_disabled)
    except Exception as e:
        log.debug("auto-snap analysis failed for %s: %s", host.get("address"), e)
        return

    per_label = (analysis or {}).get("per_label") or {}
    if not isinstance(per_label, dict):
        return

    name = host.get("name") or host["address"]
    now = int(time.time())
    scope = "stale_snap"
    addr = host["address"]
    # Iterate the labels we actually monitor (not just whatever appears in
    # per_label) so a label that has fully recovered -- or dropped out of the
    # analysis -- gets its lingering state cleared.
    for label in STALE_THRESHOLDS:
        info = per_label.get(label)
        stale = info.get("stale_datasets") if isinstance(info, dict) else None
        norm = []
        for e in (stale or []):
            if isinstance(e, str):
                norm.append({"dataset": e})
            elif isinstance(e, dict) and e.get("dataset") and not e.get("note"):
                norm.append(e)
        key = f"{addr}:{label}"

        if not norm:
            # Not stale (anymore) -> clear any leftover state so the
            # dashboard count stops counting recovered labels.
            _state_delete(scope, key)
            continue

        _prev, last_alert = _state_get(scope, key)
        # Keep the count fresh either way so the tile/detail match.
        if not _cooldown_ok(last_alert, STALE_ALERT_COOLDOWN):
            _state_set(scope, key, json.dumps({"count": len(norm)}),
                       last_alert_ts=last_alert)
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

def run_checks(host, pools, reachable, pools_status=None, pools_valid=False):
    """Serialize alert processing under _alert_lock so the sampler thread and a
    concurrent request thread can't both observe the same state transition and
    fire duplicate notifications."""
    with _alert_lock:
        _run_checks(host, pools, reachable, pools_status, pools_valid)


def _run_checks(host, pools, reachable, pools_status=None, pools_valid=False):
    """Run all checks for one host. Never raises.

    ``pools_valid`` must only be True when the pool listing itself succeeded
    (see get_pools_result) -- it gates the vanished-pool state cleanup so a
    failed ``zpool list`` can't be mistaken for "all pools destroyed".
    """
    try:
        check_host_reachability(host, reachable)
    except Exception as e:
        log.warning("monitor: host reachability check failed: %s", e)
    if not reachable:
        return
    if pools_valid:
        try:
            clear_vanished_pool_state(host, pools or [])
        except Exception as e:
            log.warning("monitor: vanished-pool cleanup failed: %s", e)
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
        check_pool_topology(host, pools_status or {})
    except Exception as e:
        log.warning("monitor: pool_topology check failed: %s", e)
    try:
        check_auto_snapshots(host)
    except Exception as e:
        log.warning("monitor: auto_snapshots check failed: %s", e)
