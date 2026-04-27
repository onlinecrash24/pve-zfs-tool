"""Replication health monitor.

Runs piggy-backed on the existing metrics sampler (every 15 min by default),
inspects every per-source bashclub-zsync config we can find on a host, and
classifies its health into ``ok | warn | crit | pending | no_schedule``
based on the lag between *now* and the newest snapshot under the replica
target.

State is kept in the shared ``monitor_state`` SQLite table under
scope ``"repl"`` keyed by ``"<host_addr>::<config_path>"``. On a status
transition we fire a notification of event type ``replication_lag``.

Public surface:

- ``run_checks_for_host(host)`` — called once per sample round per host
- ``health_snapshot()``         — returns the current per-pair view used
                                   by the /api/replication/health endpoint
- ``cron_interval_seconds(expr)`` — exposed for testing

Note: this never raises. All SSH / parsing errors are swallowed and logged
so a bad host can't break the sampler loop.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import time
from typing import Any, Dict, List, Optional

from app.database import get_conn
from app.notifications import send_notification
from app.ssh_manager import run_command, load_hosts
from app.replication import (
    list_configs, _parse_config, get_cron, config_path_for, _extract_ip,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cron expression → expected interval (seconds)
# ---------------------------------------------------------------------------

def _expand_field(field: str, lo: int, hi: int) -> List[int]:
    """Expand a single cron field into the list of triggered values.
    Supports *, */N, A-B, A-B/N and comma-separated combinations.
    """
    out: List[int] = []
    for part in field.split(","):
        step = 1
        if "/" in part:
            base, step_s = part.split("/", 1)
            step = max(int(step_s), 1)
        else:
            base = part
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = int(a), int(b)
        else:
            v = int(base)
            start, end = v, v
        for v in range(start, end + 1, step):
            if lo <= v <= hi:
                out.append(v)
    return sorted(set(out))


def cron_interval_seconds(expr: str) -> Optional[int]:
    """Approximate the spacing between firings for a 5-field cron expression.

    Returns the smallest gap (in seconds) between consecutive firings within
    a 24 h window, which is a useful proxy for "expected sync interval".
    Returns None on unparseable input.
    """
    if not expr or not isinstance(expr, str):
        return None
    parts = expr.strip().split()
    if len(parts) != 5:
        return None
    try:
        minutes = _expand_field(parts[0], 0, 59)
        hours = _expand_field(parts[1], 0, 23)
    except Exception:
        return None
    if not minutes or not hours:
        return None
    # All firing times within one day, in seconds since 00:00
    times = sorted(h * 3600 + m * 60 for h in hours for m in minutes)
    if len(times) == 1:
        return 86400  # fires once per day
    gaps = [times[i + 1] - times[i] for i in range(len(times) - 1)]
    # also consider the wrap from the last firing to the first of the next day
    gaps.append(86400 - times[-1] + times[0])
    return min(gaps)


# ---------------------------------------------------------------------------
# Per-pair check
# ---------------------------------------------------------------------------

# Status thresholds in multiples of the cron interval.
WARN_FACTOR = 2.0
CRIT_FACTOR = 4.0
# Hard floor when no cron is set yet — assume "should run hourly".
DEFAULT_INTERVAL_SECONDS = 3600
# Minimum cooldown between successive notifications for the same pair.
ALERT_COOLDOWN_SECONDS = 6 * 3600


def _newest_replica_snapshot(host: Dict[str, Any], target_ds: str) -> Optional[Dict[str, Any]]:
    """Return ``{name, creation_ts}`` of the newest snapshot under the target,
    or None if there's no snapshot yet."""
    if not target_ds or not re.match(r"^[A-Za-z0-9._:/-]+$", target_ds):
        return None
    cmd = (
        f"zfs list -H -t snapshot -o name,creation -p -s creation "
        f"-r {shlex.quote(target_ds)} 2>/dev/null | tail -n 1"
    )
    r = run_command(host, cmd, timeout=15)
    if not r.get("success"):
        return None
    line = (r.get("stdout") or "").strip()
    if not line:
        return None
    parts = line.split("\t")
    if len(parts) < 2:
        return None
    try:
        return {"name": parts[0], "creation_ts": int(parts[1])}
    except ValueError:
        return None


def _classify(lag_seconds: Optional[int], interval_seconds: int) -> str:
    if lag_seconds is None:
        return "pending"
    if interval_seconds <= 0:
        interval_seconds = DEFAULT_INTERVAL_SECONDS
    if lag_seconds <= interval_seconds * WARN_FACTOR:
        return "ok"
    if lag_seconds <= interval_seconds * CRIT_FACTOR:
        return "warn"
    return "crit"


def _state_key(host_addr: str, cfg_path: str) -> str:
    return f"{host_addr}::{cfg_path}"


def _load_state(state_key: str) -> Dict[str, Any]:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT value, last_alert_ts FROM monitor_state WHERE scope=? AND key=?",
            ("repl", state_key),
        ).fetchone()
        if not row:
            return {"value": None, "last_alert_ts": None}
        try:
            value = json.loads(row["value"]) if row["value"] else None
        except Exception:
            value = None
        return {"value": value, "last_alert_ts": row["last_alert_ts"]}
    finally:
        conn.close()


def _save_state(state_key: str, value: Dict[str, Any], last_alert_ts: Optional[int]) -> None:
    now = int(time.time())
    conn = get_conn()
    try:
        conn.execute(
            """INSERT INTO monitor_state (scope, key, value, last_alert_ts, updated_ts)
               VALUES (?,?,?,?,?)
               ON CONFLICT(scope, key) DO UPDATE SET
                 value=excluded.value,
                 last_alert_ts=excluded.last_alert_ts,
                 updated_ts=excluded.updated_ts""",
            ("repl", state_key, json.dumps(value, default=str),
             last_alert_ts if last_alert_ts is not None else None,
             now),
        )
        conn.commit()
    finally:
        conn.close()


def _check_pair(host: Dict[str, Any], cfg_entry: Dict[str, Any]) -> Dict[str, Any]:
    """Inspect one pair and return its current health snapshot."""
    cfg_path = cfg_entry.get("path", "")
    target_ds = cfg_entry.get("target", "").strip()
    source = cfg_entry.get("source", "").strip()

    # Cron expression for this config (defines the expected interval)
    cron_info = get_cron(host, source=source)
    cron_entry = cron_info.get("entry") or {}
    schedule = cron_entry.get("schedule")
    interval = cron_interval_seconds(schedule) if schedule else None

    # Newest replica snapshot
    newest = _newest_replica_snapshot(host, target_ds) if target_ds else None
    last_sync_ts = newest.get("creation_ts") if newest else None
    now = int(time.time())
    lag = (now - last_sync_ts) if last_sync_ts else None

    # Classify
    if not schedule:
        status = "no_schedule" if not last_sync_ts else _classify(lag, DEFAULT_INTERVAL_SECONDS)
    else:
        status = _classify(lag, interval or DEFAULT_INTERVAL_SECONDS)

    return {
        "host_address": host.get("address"),
        "host_name": host.get("name"),
        "config_path": cfg_path,
        "source": source,
        "target": target_ds,
        "schedule": schedule,
        "interval_seconds": interval,
        "newest_snapshot": newest.get("name") if newest else None,
        "last_sync_ts": last_sync_ts,
        "lag_seconds": lag,
        "status": status,
        "checked_at": now,
    }


def _is_default_template(cfg_entry: Dict[str, Any]) -> bool:
    """The bashclub install ships /etc/bashclub/zsync.conf with placeholder
    source 'user@host' / target 'pool/dataset'. Skip it everywhere."""
    if (cfg_entry.get("path") or "").endswith("/zsync.conf"):
        return True
    src = (cfg_entry.get("source") or "").lower()
    tgt = (cfg_entry.get("target") or "").lower()
    return src == "user@host" or tgt == "pool/dataset"


def run_checks_for_host(host: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Check every per-source config on ``host``, persist state, fire
    notifications on transitions. Returns the per-pair snapshots."""
    out: List[Dict[str, Any]] = []
    try:
        configs = (list_configs(host).get("configs") or [])
    except Exception as e:
        log.warning("repl_monitor: list_configs failed for %s: %s",
                    host.get("address"), e)
        return out

    for cfg in configs:
        if _is_default_template(cfg):
            continue
        try:
            snapshot = _check_pair(host, cfg)
        except Exception as e:
            log.warning("repl_monitor: check failed for %s on %s: %s",
                        cfg.get("path"), host.get("address"), e)
            continue
        out.append(snapshot)
        _maybe_alert(snapshot)
    return out


def _maybe_alert(snap: Dict[str, Any]) -> None:
    key = _state_key(snap["host_address"], snap["config_path"])
    prev = _load_state(key)
    prev_status = (prev.get("value") or {}).get("status")

    new_status = snap["status"]
    transition = (prev_status != new_status)
    last_alert_ts = prev.get("last_alert_ts")

    # Persist current snapshot regardless
    _save_state(key, {"status": new_status,
                      "last_sync_ts": snap.get("last_sync_ts"),
                      "lag_seconds": snap.get("lag_seconds"),
                      "schedule": snap.get("schedule")},
                last_alert_ts)

    # Decide whether to alert
    fire = False
    title = ""
    severity = "info"
    if transition:
        if new_status in ("warn", "crit"):
            fire = True
            severity = "warning" if new_status == "warn" else "error"
            title = f"Replikation {new_status.upper()}: {snap['source']} → {snap['target']}"
        elif new_status == "ok" and prev_status in ("warn", "crit"):
            # Recovery
            fire = True
            severity = "info"
            title = f"Replikation OK: {snap['source']} → {snap['target']}"

    # Anti-spam: even without transition, re-fire on crit after the cooldown
    if not fire and new_status == "crit":
        if not last_alert_ts or (int(time.time()) - int(last_alert_ts)) >= ALERT_COOLDOWN_SECONDS:
            fire = True
            severity = "error"
            title = f"Replikation weiterhin CRIT: {snap['source']} → {snap['target']}"

    if not fire:
        return

    lag_h = (snap.get("lag_seconds") or 0) // 3600
    msg_lines = [
        f"Host: {snap.get('host_name') or snap.get('host_address')}",
        f"Config: {snap.get('config_path')}",
        f"Quelle: {snap.get('source')}",
        f"Ziel: {snap.get('target')}",
        f"Status: {new_status.upper()}",
        f"Lag: {lag_h} h" if snap.get("lag_seconds") else "Lag: kein Replikat-Snapshot",
        f"Cron: {snap.get('schedule') or '(kein Cron)'}",
        f"Letzter Sync: " + (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(snap["last_sync_ts"]))
            if snap.get("last_sync_ts") else "—"
        ),
    ]
    try:
        send_notification("replication_lag", title, "\n".join(msg_lines),
                          priority=8 if new_status == "crit" else 5)
    except Exception as e:
        log.warning("repl_monitor: send_notification failed: %s", e)
    # Mark alert ts
    _save_state(key, {"status": new_status,
                      "last_sync_ts": snap.get("last_sync_ts"),
                      "lag_seconds": snap.get("lag_seconds"),
                      "schedule": snap.get("schedule")},
                int(time.time()))


# ---------------------------------------------------------------------------
# Aggregated view for the API / UI
# ---------------------------------------------------------------------------

def health_snapshot() -> Dict[str, Any]:
    """Run a fresh check across every registered host and return a flat list.

    This is on-demand (not cached) so the UI sees the latest state. It also
    triggers _maybe_alert() so a manual visit to the dashboard counts as a
    sample round.
    """
    pairs: List[Dict[str, Any]] = []
    for host in load_hosts():
        try:
            pairs.extend(run_checks_for_host(host))
        except Exception as e:
            log.warning("repl_monitor: host %s failed: %s",
                        host.get("address"), e)
    summary = {"ok": 0, "warn": 0, "crit": 0, "pending": 0, "no_schedule": 0}
    for p in pairs:
        s = p.get("status", "pending")
        summary[s] = summary.get(s, 0) + 1
    return {"pairs": pairs, "summary": summary,
            "checked_at": int(time.time())}
