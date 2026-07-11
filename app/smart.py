"""Per-disk SMART collection: temperatures + health/wear indicators.

Reads ``smartctl -j`` (JSON) for every physical disk on a host in a single SSH
round-trip and normalises the handful of fields that matter for a storage
tool:

  * temperature (the primary "is a disk cooking" signal)
  * SMART overall health (PASSED / FAILED)
  * reallocated + current-pending sectors (leading HDD-failure indicators)
  * wear / percentage-used (SSD/NVMe life consumed)
  * power-on hours + model/serial (context)

The parsing helpers are pure functions (fed a decoded ``smartctl -j`` object or
the delimited multi-disk blob) so they're unit tested without SSH; the
``collect_smart`` / ``install`` wrappers do the SSH I/O.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.ssh_manager import run_command

log = logging.getLogger(__name__)

# Delimiters emitted by the remote enumeration script.
_DEV_PREFIX = "<<<DEV:"
_DEV_SUFFIX = ">>>"
_END_MARK = "<<<END>>>"
_NOSMARTCTL = "<<<NOSMARTCTL>>>"

# lsblk lists zvols/dm/loop/etc. as TYPE=disk too -- skip anything that isn't a
# real physical disk (those have no SMART and would just add error noise).
_SKIP_PREFIXES = ("zd", "dm-", "loop", "sr", "md", "ram", "fd")

# ATA SMART attribute ids we care about.
_ATTR_REALLOC = 5      # Reallocated_Sector_Ct
_ATTR_PENDING = 197    # Current_Pending_Sector
# Vendor SSD "life left" attributes: normalised value counts DOWN from 100,
# so used% = 100 - value. Checked in order; first match wins.
_ATTR_WEAR_IDS = (231, 233, 177, 202, 173)


def _build_collect_script() -> str:
    """Remote bash: for every physical disk, print a delimited ``smartctl -j``
    blob. Emits ``<<<NOSMARTCTL>>>`` if smartctl is absent so the caller can
    offer to install it. Pure string so it can be asserted in tests.
    """
    return r"""
if ! command -v smartctl >/dev/null 2>&1; then echo '<<<NOSMARTCTL>>>'; exit 0; fi
for dev in $(lsblk -dn -o NAME,TYPE 2>/dev/null | awk '$2=="disk"{print $1}'); do
  case "$dev" in
    zd*|dm-*|loop*|sr*|md*|ram*|fd*) continue ;;
  esac
  echo "<<<DEV:$dev>>>"
  smartctl -j -a "/dev/$dev" 2>/dev/null
  echo "<<<END>>>"
done
""".strip()


def _disk_type(obj: Dict[str, Any]) -> str:
    """Classify hdd / ssd / nvme from a smartctl object.

    rotation_rate > 0 => spinning HDD; == 0 => SSD. NVMe is flagged by the
    device protocol. Unknown non-NVMe defaults to ssd (rotation_rate is
    reliably populated for HDDs, so its absence almost always means non-
    rotating).
    """
    dev = obj.get("device") or {}
    proto = (dev.get("protocol") or dev.get("type") or "").lower()
    if "nvme" in proto:
        return "nvme"
    rot = obj.get("rotation_rate")
    if isinstance(rot, (int, float)) and rot > 0:
        return "hdd"
    return "ssd"


def parse_smart_json(device: str, obj: Any) -> Optional[Dict[str, Any]]:
    """Normalise one decoded ``smartctl -j`` object into our disk record.

    Returns None when the object carries no usable SMART signal (e.g. a disk
    hidden behind a RAID controller, or a virtual disk) so such devices are
    simply skipped instead of stored as empty rows.
    """
    if not isinstance(obj, dict):
        return None

    dtype = _disk_type(obj)

    temp = None
    t = obj.get("temperature")
    if isinstance(t, dict):
        temp = t.get("current")

    poh = None
    pot = obj.get("power_on_time")
    if isinstance(pot, dict):
        poh = pot.get("hours")

    health = None
    ss = obj.get("smart_status")
    if isinstance(ss, dict) and "passed" in ss:
        health = bool(ss["passed"])

    realloc = pending = None
    ata = obj.get("ata_smart_attributes")
    table = ata.get("table", []) if isinstance(ata, dict) else []
    for a in table or []:
        aid = a.get("id")
        raw = (a.get("raw") or {}).get("value") if isinstance(a.get("raw"), dict) else None
        if aid == _ATTR_REALLOC:
            realloc = raw
        elif aid == _ATTR_PENDING:
            pending = raw

    # Wear: NVMe reports percentage_used directly; SATA SSDs expose a vendor
    # "life left" attribute whose normalised value counts down from 100.
    wear = None
    nvme = obj.get("nvme_smart_health_information_log")
    if isinstance(nvme, dict):
        wear = nvme.get("percentage_used")
    if wear is None and table:
        for a in table:
            if a.get("id") in _ATTR_WEAR_IDS:
                nv = a.get("value")
                if isinstance(nv, (int, float)):
                    wear = max(0, 100 - int(nv))
                    break

    # No temperature and no health => nothing worth trending; skip the device.
    if temp is None and health is None:
        return None

    return {
        "device": device,
        "type": dtype,
        "model": obj.get("model_name"),
        "serial": obj.get("serial_number"),
        "temp_c": temp,
        "power_on_hours": poh,
        "health_passed": health,
        "realloc_sectors": realloc,
        "pending_sectors": pending,
        "wear_pct": wear,
    }


def parse_collect_output(raw: str) -> Dict[str, Any]:
    """Split the delimited multi-disk blob and normalise each device.

    Returns ``{"installed": bool, "disks": [record, ...]}``.
    """
    raw = raw or ""
    if _NOSMARTCTL in raw:
        return {"installed": False, "disks": []}

    disks: List[Dict[str, Any]] = []
    cur_dev: Optional[str] = None
    buf: List[str] = []
    for line in raw.split("\n"):
        s = line.strip()
        if s.startswith(_DEV_PREFIX) and s.endswith(_DEV_SUFFIX):
            cur_dev = s[len(_DEV_PREFIX):-len(_DEV_SUFFIX)]
            buf = []
            continue
        if s == _END_MARK:
            if cur_dev is not None:
                text = "\n".join(buf).strip()
                if text:
                    try:
                        obj = json.loads(text)
                    except ValueError:
                        obj = None
                    rec = parse_smart_json(cur_dev, obj) if obj is not None else None
                    if rec:
                        disks.append(rec)
            cur_dev = None
            continue
        if cur_dev is not None:
            buf.append(line)

    return {"installed": True, "disks": disks}


# ---------------------------------------------------------------------------
# SSH I/O
# ---------------------------------------------------------------------------

def collect_smart(host: Dict[str, Any]) -> Dict[str, Any]:
    """Read SMART for every physical disk on the host (one round-trip)."""
    r = run_command(host, _build_collect_script(), timeout=90)
    if not r.get("success"):
        return {"installed": None, "disks": []}
    return parse_collect_output(r.get("stdout", "") or "")


def smartctl_installed(host: Dict[str, Any]) -> bool:
    r = run_command(
        host, "command -v smartctl >/dev/null 2>&1 && echo INSTALLED || echo NO",
        timeout=10)
    return "INSTALLED" in (r.get("stdout") or "")


def _build_install_script() -> str:
    """Remote bash installing ``smartmontools`` (provides smartctl).

    Stock Debian package (bookworm + trixie), so no extra repo. apt-get update
    is non-fatal; idempotent (reports ``__ALREADY__`` if present). Pure string
    for tests.
    """
    return r"""
set -e
if command -v smartctl >/dev/null 2>&1; then echo __ALREADY__; command -v smartctl; exit 0; fi
DEBIAN_FRONTEND=noninteractive apt-get update -qq || true
DEBIAN_FRONTEND=noninteractive apt-get install -y smartmontools
command -v smartctl
""".strip()


def install(host: Dict[str, Any]) -> Dict[str, Any]:
    """Install smartmontools via apt (idempotent)."""
    script = _build_install_script()
    cmd = f"bash -s <<'EOF'\n{script}\nEOF"
    r = run_command(host, cmd, timeout=180)
    stdout = r.get("stdout", "") or ""
    ok = bool(r.get("success")) and "smartctl" in stdout
    return {
        "success": ok,
        "already": "__ALREADY__" in stdout,
        "stdout": stdout,
        "stderr": (r.get("stderr", "") or ""),
    }
