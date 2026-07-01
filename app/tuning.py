"""ZFS ARC limit tuning.

The ARC (Adaptive Replacement Cache) defaults to ~50 % of RAM on Proxmox,
which is the #1 "ZFS ate all my memory" surprise. This module reads the
current ARC limit (runtime + persistent + total RAM) and lets the user set
it, writing both:

  * runtime  -> /sys/module/zfs/parameters/zfs_arc_max  (immediate, not
               persistent)
  * persistent -> /etc/modprobe.d/zfs.conf  (options zfs zfs_arc_max=...)
               followed by ``update-initramfs -u`` so it survives a reboot
               (ZFS loads from initramfs on Proxmox).

A reboot is required for the persistent value to fully take effect; the
runtime write makes the change visible immediately.

The conf parse/build helpers are pure (no SSH) and unit-tested.
"""

from __future__ import annotations

import base64
import re
import shlex
from typing import Any, Dict, Optional

from app.ssh_manager import run_command

ARC_CONF_PATH = "/etc/modprobe.d/zfs.conf"
SYS_ARC_MAX = "/sys/module/zfs/parameters/zfs_arc_max"
SYS_ARC_MIN = "/sys/module/zfs/parameters/zfs_arc_min"

# ZFS refuses an arc_max below 64 MiB.
ARC_MIN_BYTES = 64 * 1024 * 1024


# ---------------------------------------------------------------------------
# Pure config parse / build
# ---------------------------------------------------------------------------

def _collect_zfs_params(text: str):
    """Return (ordered dict of all `options zfs` params, list of other lines)."""
    params: Dict[str, str] = {}
    other = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("options zfs"):
            for tok in s[len("options zfs"):].split():
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    params[k] = v
        else:
            other.append(line)
    return params, other


def parse_arc_conf(text: str) -> Dict[str, Optional[int]]:
    """Extract zfs_arc_max / zfs_arc_min (bytes) from a zfs.conf, or None."""
    params, _ = _collect_zfs_params(text)

    def _int(key):
        v = params.get(key, "")
        return int(v) if v.isdigit() else None

    return {"zfs_arc_max": _int("zfs_arc_max"), "zfs_arc_min": _int("zfs_arc_min")}


def build_arc_conf(existing_text: str, arc_max: Optional[int],
                   arc_min: Optional[int]) -> str:
    """Rewrite zfs.conf with the new arc_max/arc_min.

    Other `options zfs` params and all non-options lines (comments etc.) are
    preserved. Passing 0/None for a value removes it (reset to the ZFS
    default). All `options zfs` lines are consolidated into one.
    """
    params, other = _collect_zfs_params(existing_text)
    params.pop("zfs_arc_max", None)
    params.pop("zfs_arc_min", None)
    if arc_max and int(arc_max) > 0:
        params["zfs_arc_max"] = str(int(arc_max))
    if arc_min and int(arc_min) > 0:
        params["zfs_arc_min"] = str(int(arc_min))

    out = list(other)
    if params:
        opts = " ".join(f"{k}={v}" for k, v in params.items())
        out.append(f"options zfs {opts}")
    body = "\n".join(line for line in out).strip()
    return (body + "\n") if body else ""


def suggest_arc_floor(pool_sum_bytes):
    """Proxmox minimum: 2 GiB base + 1 GiB ARC per 1 TiB of pool capacity
    (e.g. an 8 TiB pool -> 10 GiB). A conservative floor, not an optimum.
    Returns None if the pool size is unknown."""
    if not pool_sum_bytes or pool_sum_bytes <= 0:
        return None
    floor = (2 * 1024 * 1024 * 1024) + (pool_sum_bytes // 1024)  # 2 GiB + 1 GiB/TiB
    return max(floor, ARC_MIN_BYTES)


def arc_suggestions(pool_sum_bytes, total_ram_bytes):
    """Three ARC reference points (bytes), or None where inputs are unknown:

      * min      -> Proxmox floor (2 GiB + 1 GiB/TiB) -- don't go below
      * balanced -> ~25% RAM, an even split for a hypervisor (the "recommended"
                    middle; there is no universal optimum, it depends on VM
                    load and working-set size)
      * max      -> 50% RAM ceiling, leaving the other half for VMs/host

    Values are clamped to RAM and kept ordered (min <= balanced <= max)."""
    out = {"min": None, "balanced": None, "max": None}
    if total_ram_bytes:
        out["max"] = max(ARC_MIN_BYTES, total_ram_bytes // 2)
        out["balanced"] = max(ARC_MIN_BYTES, total_ram_bytes // 4)
    floor = suggest_arc_floor(pool_sum_bytes)
    if floor is not None:
        out["min"] = min(floor, total_ram_bytes) if total_ram_bytes else floor
    # Keep the three ordered even when the pool floor is large vs. RAM.
    if out["min"] and out["max"] and out["min"] > out["max"]:
        out["min"] = out["max"]
    if out["balanced"] and out["min"] and out["balanced"] < out["min"]:
        out["balanced"] = out["min"]
    if out["balanced"] and out["max"] and out["balanced"] > out["max"]:
        out["balanced"] = out["max"]
    return out


# ---------------------------------------------------------------------------
# SSH I/O
# ---------------------------------------------------------------------------

def _read_int_file(host, path):
    r = run_command(host, f"cat {shlex.quote(path)} 2>/dev/null", timeout=10)
    s = (r.get("stdout") or "").strip()
    return int(s) if s.isdigit() else None


def get_arc_config(host: Dict[str, Any]) -> Dict[str, Any]:
    """Return current ARC limits (runtime + persistent), live ARC size and
    total RAM so the UI can render sensible bounds/defaults."""
    runtime_max = _read_int_file(host, SYS_ARC_MAX)
    runtime_min = _read_int_file(host, SYS_ARC_MIN)

    r = run_command(host, f"cat {shlex.quote(ARC_CONF_PATH)} 2>/dev/null", timeout=10)
    conf_text = r.get("stdout") or ""
    persistent = parse_arc_conf(conf_text)

    total_ram = None
    rm = run_command(host, "grep MemTotal /proc/meminfo 2>/dev/null", timeout=10)
    m = re.search(r"MemTotal:\s+(\d+)\s+kB", rm.get("stdout") or "")
    if m:
        total_ram = int(m.group(1)) * 1024

    cur_size = None
    rs = run_command(host, "awk '/^size /{print $3}' /proc/spl/kstat/zfs/arcstats 2>/dev/null", timeout=10)
    ss = (rs.get("stdout") or "").strip()
    if ss.isdigit():
        cur_size = int(ss)

    # Sum of raw pool sizes -> "1 GiB ARC per 1 TiB pool" suggestion.
    pool_sum = 0
    rp = run_command(host, "zpool list -Hp -o size 2>/dev/null", timeout=10)
    for line in (rp.get("stdout") or "").splitlines():
        s = line.strip()
        if s.isdigit():
            pool_sum += int(s)

    return {
        "runtime_max": runtime_max,
        "runtime_min": runtime_min,
        "persistent_max": persistent["zfs_arc_max"],
        "persistent_min": persistent["zfs_arc_min"],
        "current_size": cur_size,
        "total_ram_bytes": total_ram,
        "pool_size_sum_bytes": pool_sum or None,
        "arc_suggest": arc_suggestions(pool_sum, total_ram),
        "conf_path": ARC_CONF_PATH,
    }


def set_arc_limit(host: Dict[str, Any], arc_max: Optional[int],
                  arc_min: Optional[int] = None) -> Dict[str, Any]:
    """Apply a new ARC limit. arc_max=0 resets to the ZFS default.

    Writes the persistent modprobe config (with backup) + update-initramfs,
    and best-effort applies the runtime value immediately. Returns what was
    done so the UI can tell the user a reboot is recommended.
    """
    # ---- validate ----
    try:
        arc_max = int(arc_max)
    except (TypeError, ValueError):
        return {"success": False, "error": "arc_max must be an integer (bytes)"}
    if arc_max != 0 and arc_max < ARC_MIN_BYTES:
        return {"success": False, "error": f"arc_max must be 0 (reset) or >= {ARC_MIN_BYTES} bytes (64 MiB)"}
    if arc_min is not None and str(arc_min) != "":
        try:
            arc_min = int(arc_min)
        except (TypeError, ValueError):
            return {"success": False, "error": "arc_min must be an integer (bytes)"}
        if arc_min < 0:
            return {"success": False, "error": "arc_min must be >= 0"}
        if arc_max != 0 and arc_min >= arc_max:
            return {"success": False, "error": "arc_min must be smaller than arc_max"}
    else:
        arc_min = None

    # Reject values larger than RAM.
    cfg = get_arc_config(host)
    total_ram = cfg.get("total_ram_bytes")
    if total_ram and arc_max > total_ram:
        return {"success": False, "error": "arc_max exceeds total RAM"}

    # ---- persistent: rewrite zfs.conf with backup ----
    r_read = run_command(host, f"cat {shlex.quote(ARC_CONF_PATH)} 2>/dev/null", timeout=10)
    existing = r_read.get("stdout") or ""
    new_text = build_arc_conf(existing, arc_max if arc_max else None, arc_min)
    b64 = base64.b64encode(new_text.encode("utf-8")).decode("ascii")
    write_script = (
        f"if [ -f {shlex.quote(ARC_CONF_PATH)} ]; then "
        f"cp -a {shlex.quote(ARC_CONF_PATH)} {shlex.quote(ARC_CONF_PATH)}.bak.$(date +%Y%m%d%H%M%S); fi && "
        f"echo {shlex.quote(b64)} | base64 -d > {shlex.quote(ARC_CONF_PATH)} && echo __WROTE__"
    )
    rw = run_command(host, write_script, timeout=15)
    persistent_written = "__WROTE__" in (rw.get("stdout") or "")
    if not persistent_written:
        return {"success": False, "error": "failed to write " + ARC_CONF_PATH + ": " +
                (rw.get("stderr") or "").strip()[:200]}

    # ---- rebuild initramfs so the persistent value survives reboot ----
    # -k all: rebuild for *every* installed kernel, not just the running one,
    # so the limit still applies after a kernel update boots a different
    # kernel (matches the bashclub proxmox-zfs-postinstall approach).
    ri = run_command(host, "update-initramfs -u -k all 2>&1; echo __EXIT=$?", timeout=300)
    initramfs_ok = "__EXIT=0" in (ri.get("stdout") or "")

    # ---- runtime: best-effort immediate apply ----
    runtime_applied = False
    runtime_err = ""
    # 0 means "reset"; at runtime we can't truly reset to default, so skip.
    if arc_max and arc_max >= ARC_MIN_BYTES:
        cmds = []
        if arc_min:
            cmds.append(f"echo {arc_min} > {SYS_ARC_MIN}")
        cmds.append(f"echo {arc_max} > {SYS_ARC_MAX}")
        rr = run_command(host, " && ".join(cmds) + " && echo __RT_OK__", timeout=15)
        runtime_applied = "__RT_OK__" in (rr.get("stdout") or "")
        if not runtime_applied:
            runtime_err = (rr.get("stderr") or rr.get("stdout") or "").strip()[:200]

    return {
        "success": True,
        "persistent_written": persistent_written,
        "initramfs_updated": initramfs_ok,
        "runtime_applied": runtime_applied,
        "runtime_error": runtime_err,
        "reboot_recommended": True,
        "arc_max": arc_max,
        "arc_min": arc_min,
        "conf_path": ARC_CONF_PATH,
    }
