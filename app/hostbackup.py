"""Proxmox host config backups.

Creates a small CONFIG-level backup of a Proxmox host (not VM disk data):
the cluster filesystem /etc/pve, network config, and a set of command
captures (pveversion, package selections, ip/zfs/zpool state). The archive
is pulled into the tool's data volume via SFTP and can be downloaded or
restored manually in a worst case.

SECURITY: /etc/pve/priv contains cluster secrets (the cluster CA private
key etc.). It is EXCLUDED by default; including it (include_priv=True) makes
the stored archive highly sensitive. Downloads are auth-gated and the UI
warns about this.

Schedules (daily/weekly/monthly + keep N) are evaluated by a background
thread, mirroring the AI-report scheduler.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import threading
import time
from datetime import datetime

from app.timezone import now as tz_now
from app.ssh_manager import run_command, fetch_file, load_hosts

log = logging.getLogger(__name__)

DATA_DIR = "/app/data"
BACKUP_DIR = os.path.join(DATA_DIR, "host_backups")
CONFIG_FILE = os.path.join(DATA_DIR, "host_backup_config.json")

# A stored backup file name: pve-backup-<ts>[-withpriv].tar.gz
_FILE_RE = re.compile(r"^pve-backup-\d{8}-\d{6}(?:-withpriv)?\.tar\.gz$")
_FILE_TS_RE = re.compile(r"^pve-backup-(\d{8})-(\d{6})")

_INTERVAL_SECONDS = {"daily": 86400, "weekly": 604800, "monthly": 2592000}

_sched_thread = None
_sched_stop = threading.Event()
_sched_start_lock = threading.Lock()
# Per-host timestamp of the last create attempt -- throttles retries after a
# failure so a persistently unreachable host isn't hammered every poll.
_last_attempt = {}
_RETRY_THROTTLE_SEC = 1800   # 30 min


# ---------------------------------------------------------------------------
# Paths / helpers (pure)
# ---------------------------------------------------------------------------

def _safe_addr(address: str) -> str:
    """Filesystem-safe per-host subdir name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", address or "unknown")


def host_backup_dir(address: str) -> str:
    return os.path.join(BACKUP_DIR, _safe_addr(address))


def is_valid_backup_name(name: str) -> bool:
    return bool(name) and "/" not in name and ".." not in name and bool(_FILE_RE.match(name))


def select_prunable(filenames, keep: int):
    """Given backup filenames (newest-first not assumed), return the list to
    DELETE so that ``keep`` newest remain. Ordering is by the embedded
    timestamp in the name (lexicographic == chronological for our format)."""
    valid = sorted([f for f in filenames if is_valid_backup_name(f)])  # oldest first
    if keep <= 0:
        return list(valid)
    if len(valid) <= keep:
        return []
    return valid[:len(valid) - keep]


# ---------------------------------------------------------------------------
# Backup script
# ---------------------------------------------------------------------------

def _build_backup_script(include_priv: bool, dest: str) -> str:
    """Shell script run on the host: stage curated config + command captures,
    tar them up to ``dest``. ``--exclude=priv`` drops /etc/pve/priv unless
    include_priv is set."""
    priv_flag = "1" if include_priv else "0"
    qdest = shlex.quote(dest)
    return f"""
set -e
INCLUDE_PRIV={priv_flag}
STAGE=$(mktemp -d)
mkdir -p "$STAGE/etc" "$STAGE/cmd"

# /etc/pve cluster filesystem (with or without priv secrets)
if [ -d /etc/pve ]; then
  mkdir -p "$STAGE/etc/pve"
  if [ "$INCLUDE_PRIV" = "1" ]; then
    tar -C /etc/pve -cf - . 2>/dev/null | tar -C "$STAGE/etc/pve" -xf - 2>/dev/null || true
  else
    tar -C /etc/pve --exclude=priv --exclude=./priv -cf - . 2>/dev/null | tar -C "$STAGE/etc/pve" -xf - 2>/dev/null || true
  fi
fi

# APT repositories + signing keys (public) so a restore brings the package
# sources back. auth.conf* may hold repo passwords -> deliberately excluded.
if [ -d /etc/apt ]; then
  mkdir -p "$STAGE/etc/apt"
  tar -C /etc/apt --exclude=auth.conf --exclude=./auth.conf \
      --exclude=auth.conf.d --exclude=./auth.conf.d \
      -cf - . 2>/dev/null | tar -C "$STAGE/etc/apt" -xf - 2>/dev/null || true
fi

# ZFS-tool-relevant ancillary configs so all tool features survive a restore:
# zfs-auto-snapshot retention (its cron files ARE the policy), the bashclub-zsync
# replication config + cron, and the ARC limit.
[ -d /etc/cron.d ] && cp -a --parents /etc/cron.d "$STAGE" 2>/dev/null || true
for f in /etc/cron.hourly/zfs-auto-snapshot /etc/cron.daily/zfs-auto-snapshot \
         /etc/cron.weekly/zfs-auto-snapshot /etc/cron.monthly/zfs-auto-snapshot \
         /etc/modprobe.d/zfs.conf; do
  [ -e "$f" ] && cp -a --parents "$f" "$STAGE" 2>/dev/null || true
done
[ -d /etc/bashclub ] && cp -a --parents /etc/bashclub "$STAGE" 2>/dev/null || true

# Network + base config files
for f in /etc/network/interfaces /etc/hosts /etc/resolv.conf /etc/hostname; do
  [ -e "$f" ] && cp -a --parents "$f" "$STAGE" 2>/dev/null || true
done
[ -d /etc/network/interfaces.d ] && cp -a --parents /etc/network/interfaces.d "$STAGE" 2>/dev/null || true

# Root's authorized_keys (PUBLIC keys only) -- lets a restore bring back all
# trusted SSH access at once (incl. this tool's key) so a rebuilt host is
# reachable again. `cat` dereferences the cluster symlink to priv/authorized_keys
# so the content lands as a plain file. Private keys are deliberately NOT captured.
if [ -e /root/.ssh/authorized_keys ]; then
  mkdir -p "$STAGE/root/.ssh"
  cat /root/.ssh/authorized_keys > "$STAGE/root/.ssh/authorized_keys" 2>/dev/null || true
fi

# NIC naming artifacts -- a major upgrade can rename interfaces (the classic
# "host offline after PVE upgrade" pitfall); persistent-name rules plus the
# MAC/driver/path identity captured below let you reconstruct the mapping.
for f in /etc/udev/rules.d/*net*.rules /etc/systemd/network/*.link /lib/systemd/network/*.link; do
  [ -e "$f" ] && cp -a --parents "$f" "$STAGE" 2>/dev/null || true
done

# Command captures (best-effort)
pveversion -v          > "$STAGE/cmd/pveversion.txt"        2>&1 || true
dpkg --get-selections  > "$STAGE/cmd/dpkg-selections.txt"   2>&1 || true
ip -d address show     > "$STAGE/cmd/ip-address.txt"        2>&1 || true
ip route show          > "$STAGE/cmd/ip-route.txt"          2>&1 || true
zpool status           > "$STAGE/cmd/zpool-status.txt"      2>&1 || true
zpool list             > "$STAGE/cmd/zpool-list.txt"        2>&1 || true
zfs list -o name,used,avail,refer,mountpoint > "$STAGE/cmd/zfs-list.txt" 2>&1 || true
pvecm status           > "$STAGE/cmd/pvecm-status.txt"      2>&1 || true
ls -l /sys/class/net/  > "$STAGE/cmd/net-devices.txt"       2>&1 || true
for n in /sys/class/net/*; do
  dev=$(basename "$n"); [ "$dev" = "lo" ] && continue
  echo "=== $dev ==="
  echo "mac=$(cat "$n/address" 2>/dev/null)"
  ethtool -i "$dev" 2>/dev/null || true
  udevadm info -q property "$n" 2>/dev/null | grep -E "^(ID_NET_NAME|ID_PATH|ID_MODEL|ID_VENDOR|INTERFACE)" || true
done > "$STAGE/cmd/nic-identity.txt" 2>&1 || true
{{ echo "host=$(hostname -f 2>/dev/null || hostname)"; echo "date=$(date -Is)"; echo "include_priv=$INCLUDE_PRIV"; }} > "$STAGE/cmd/_meta.txt" 2>&1 || true

tar -C "$STAGE" -czf {qdest} . 2>/dev/null
rm -rf "$STAGE"
echo "__SIZE__=$(stat -c %s {qdest} 2>/dev/null || echo 0)"
echo "__OK__"
""".strip()


# ---------------------------------------------------------------------------
# Create / list / delete / prune
# ---------------------------------------------------------------------------

def create_backup(host, include_priv: bool = False):
    """Create a host config backup and pull it into the data volume.

    Returns {success, filename, bytes, include_priv, error}.
    """
    address = host.get("address", "")
    ts = tz_now().strftime("%Y%m%d-%H%M%S")
    suffix = "-withpriv" if include_priv else ""
    filename = f"pve-backup-{ts}{suffix}.tar.gz"
    remote_tmp = f"/tmp/{filename}"

    out_dir = host_backup_dir(address)
    os.makedirs(out_dir, exist_ok=True)
    local_path = os.path.join(out_dir, filename)

    t0 = time.monotonic()

    # 1. Build the archive on the host.
    script = _build_backup_script(include_priv, remote_tmp)
    r = run_command(host, f"bash -s <<'__EOF__'\n{script}\n__EOF__", timeout=600)
    t_build = time.monotonic() - t0
    if "__OK__" not in (r.get("stdout") or ""):
        return {"success": False, "filename": None, "bytes": 0,
                "include_priv": include_priv,
                "error": "archive build failed on host: " +
                         ((r.get("stderr") or r.get("stdout") or "unknown").strip()[:300])}

    # 2. Pull it down via SFTP.
    fr = fetch_file(host, remote_tmp, local_path, timeout=600)
    t_fetch = time.monotonic() - t0 - t_build

    # 3. Clean up the host temp file regardless.
    run_command(host, f"rm -f {shlex.quote(remote_tmp)}", timeout=15)

    if not fr.get("success"):
        # leave no partial local file
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
        except OSError:
            pass
        return {"success": False, "filename": None, "bytes": 0,
                "include_priv": include_priv,
                "error": "SFTP fetch failed: " + fr.get("error", "")[:300]}

    duration = time.monotonic() - t0
    log.info("Host backup %s: build %.1fs, fetch %.1fs, total %.1fs (%d bytes)",
             address, t_build, t_fetch, duration, fr.get("bytes", 0))
    return {"success": True, "filename": filename, "bytes": fr.get("bytes", 0),
            "include_priv": include_priv, "error": "",
            "duration_sec": round(duration, 1)}


def list_backups(host):
    """List stored backups for a host, newest first."""
    address = host.get("address", "")
    out_dir = host_backup_dir(address)
    items = []
    if os.path.isdir(out_dir):
        for name in os.listdir(out_dir):
            if not is_valid_backup_name(name):
                continue
            path = os.path.join(out_dir, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            items.append({
                "filename": name,
                "bytes": st.st_size,
                "created_ts": int(st.st_mtime),
                "include_priv": name.endswith("-withpriv.tar.gz"),
            })
    items.sort(key=lambda x: x["filename"], reverse=True)
    return {"backups": items}


def list_all_backups(hosts):
    """Aggregate stored backups across all registered hosts, newest first.

    ``hosts`` is the list of host dicts (from load_hosts). Each item carries
    the host address+name so the consolidated UI block can show who was
    backed up.
    """
    out = []
    for h in hosts or []:
        addr = h.get("address", "")
        name = h.get("name") or addr
        for b in list_backups(h).get("backups", []):
            out.append({**b, "host_address": addr, "host_name": name})
    # The filename embeds the backup timestamp (pve-backup-<YYYYMMDD-HHMMSS>),
    # so lexicographic-desc on the name is chronological + deterministic
    # regardless of file mtime.
    out.sort(key=lambda x: x.get("filename", ""), reverse=True)
    return {"backups": out}


def delete_backup(host, filename):
    if not is_valid_backup_name(filename):
        return {"success": False, "error": "invalid filename"}
    path = os.path.join(host_backup_dir(host.get("address", "")), filename)
    if not os.path.isfile(path):
        return {"success": False, "error": "not found"}
    try:
        os.remove(path)
        return {"success": True}
    except OSError as e:
        return {"success": False, "error": str(e)}


def backup_path(host, filename):
    """Return the absolute path of a stored backup if the name is valid and
    the file exists, else None. Used by the download route."""
    if not is_valid_backup_name(filename):
        return None
    path = os.path.join(host_backup_dir(host.get("address", "")), filename)
    return path if os.path.isfile(path) else None


def prune_backups(host, keep: int):
    """Delete oldest backups beyond ``keep``. Returns count deleted."""
    address = host.get("address", "")
    out_dir = host_backup_dir(address)
    if not os.path.isdir(out_dir):
        return 0
    names = [n for n in os.listdir(out_dir) if is_valid_backup_name(n)]
    deleted = 0
    for name in select_prunable(names, keep):
        try:
            os.remove(os.path.join(out_dir, name))
            deleted += 1
        except OSError:
            pass
    return deleted


# ---------------------------------------------------------------------------
# Config / schedules
# ---------------------------------------------------------------------------

def load_config():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_FILE):
        return {"schedules": {}}
    try:
        with open(CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        cfg.setdefault("schedules", {})
        return cfg
    except Exception:
        return {"schedules": {}}


def save_config(cfg):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def get_schedule(address):
    return load_config().get("schedules", {}).get(address) or {
        "enabled": False, "interval": "weekly", "hour": 3, "weekday": 0,
        "keep": 8, "include_priv": False,
    }


def set_schedule(address, sched):
    cfg = load_config()
    cfg.setdefault("schedules", {})[address] = {
        "enabled": bool(sched.get("enabled")),
        "interval": sched.get("interval", "weekly"),
        "hour": int(sched.get("hour", 3)),
        "weekday": int(sched.get("weekday", 0)),
        "keep": max(0, int(sched.get("keep", 8))),
        "include_priv": bool(sched.get("include_priv", False)),
    }
    save_config(cfg)
    return cfg["schedules"][address]


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def backup_filename_dt(filename):
    """Parse the local timestamp embedded in a backup filename, or None."""
    m = _FILE_TS_RE.match(filename or "")
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _same_period(dt, now, interval):
    """Whether ``dt`` falls in the same day/week/month as ``now``."""
    if interval == "weekly":
        return dt.isocalendar()[:2] == now.isocalendar()[:2]
    if interval == "monthly":
        return (dt.year, dt.month) == (now.year, now.month)
    return dt.date() == now.date()


def backup_due(newest_dt, now, sched):
    """Decide whether a scheduled backup should run now (pure, testable).

    Robust against restarts and a missed target-hour window:
      * skip if a backup already exists for the current period,
      * otherwise fire at/after the preferred hour (and weekday for weekly),
      * OR catch up whenever the newest backup is clearly overdue (older than
        1.5x the interval), regardless of the hour -- so a window missed while
        the container was down is backfilled as soon as it runs again.
    ``now`` is naive local time; ``newest_dt`` is naive local or None.
    """
    interval = sched.get("interval", "daily")
    if newest_dt is not None and _same_period(newest_dt, now, interval):
        return False

    target_hour = int(sched.get("hour", 3))
    if interval == "weekly":
        on_day = now.weekday() >= int(sched.get("weekday", 0))
    else:                       # daily / monthly: any day of the period
        on_day = True
    preferred = on_day and now.hour >= target_hour

    interval_secs = _INTERVAL_SECONDS.get(interval, 86400)
    overdue = (newest_dt is None
               or (now - newest_dt).total_seconds() > interval_secs * 1.5)
    return preferred or overdue


def _newest_backup_dt(host):
    for b in list_backups(host).get("backups", []):   # newest first
        dt = backup_filename_dt(b.get("filename", ""))
        if dt:
            return dt
    return None


def _scheduler_loop():
    log.info("Host backup scheduler started")
    while not _sched_stop.is_set():
        try:
            now = tz_now().replace(tzinfo=None)   # naive local for comparisons
            cfg = load_config()
            hosts = {h["address"]: h for h in load_hosts()}
            for address, sched in (cfg.get("schedules") or {}).items():
                if not sched.get("enabled"):
                    continue
                host = hosts.get(address)
                if not host:
                    continue
                if not backup_due(_newest_backup_dt(host), now, sched):
                    continue
                # Throttle retries so a failing host isn't hammered every poll.
                if time.time() - _last_attempt.get(address, 0) < _RETRY_THROTTLE_SEC:
                    continue
                _last_attempt[address] = time.time()
                log.info("Scheduled host backup for %s (%s)", address,
                         sched.get("interval", "daily"))
                try:
                    res = create_backup(host, include_priv=bool(sched.get("include_priv")))
                    if res.get("success"):
                        deleted = prune_backups(host, int(sched.get("keep", 8)))
                        log.info("Host backup for %s ok in %ss (%s bytes, pruned %d)",
                                 address, res.get("duration_sec"), res.get("bytes"), deleted)
                    else:
                        log.error("Host backup for %s failed: %s", address, res.get("error"))
                        _notify_failure(host, res.get("error", ""))
                except Exception as e:
                    log.error("Host backup for %s crashed: %s", address, e)
                    _notify_failure(host, str(e))
            _sched_stop.wait(300)
        except Exception as e:
            log.error("Host backup scheduler error: %s", e)
            _sched_stop.wait(300)
    log.info("Host backup scheduler stopped")


def _notify_failure(host, error):
    try:
        from app.notifications import send_notification
        send_notification(
            "host_backup_failed",
            f"Host-Backup fehlgeschlagen: {host.get('name') or host.get('address')}",
            f"Host: {host.get('address')}\nFehler: {error[:500]}",
            priority=7,
        )
    except Exception as e:
        log.warning("host backup failure notification failed: %s", e)


def start_scheduler():
    global _sched_thread
    with _sched_start_lock:
        if _sched_thread and _sched_thread.is_alive():
            return
        # No in-memory seeding needed: backup_due() decides from the newest
        # stored backup's timestamp (persistent across restarts).
        _sched_stop.clear()
        _sched_thread = threading.Thread(target=_scheduler_loop, daemon=True,
                                         name="host-backup-scheduler")
        _sched_thread.start()
