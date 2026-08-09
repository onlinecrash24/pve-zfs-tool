"""Declared exceptions: guests deliberately left without a backup or a replica.

Some guests are unprotected on purpose -- a scratch container, a VM that handles
its own dumps, something rebuilt from a template in five minutes. Reporting them
as critical every single time is the worst kind of false alarm: permanent,
unfixable, and it teaches people to ignore the whole column, including the rows
where the warning is real.

So the declaration lives in Proxmox itself, as a guest tag. Two reasons that
beat keeping a list inside this tool: it is visible in the PVE web UI to anyone
managing the guest, including people who never open this tool, and it travels
with the guest when it migrates. The tool stores the *reason* alongside -- who
decided, when, and why -- which a bare flag cannot carry and which is exactly
what a later review needs.

Hierarchy that follows from that: **the tag decides.** A note here without a
matching tag is orphaned and ignored, so removing the tag in PVE ends the
exception immediately. A tag without a note still counts -- but is reported as
"declared in PVE, no reason recorded", so a foreign or accidental tag surfaces
at the next review instead of quietly hiding a real gap.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import threading
import time
from typing import Any, Dict, List, Optional

from app.ssh_manager import run_command

DATA_DIR = "/app/data"
EXCEPTIONS_FILE = os.path.join(DATA_DIR, "guest_exceptions.json")

_lock = threading.RLock()

# The PVE guest tags this tool reacts to. PVE only accepts
# [a-zA-Z0-9_][a-zA-Z0-9_\-\+\.]* for a tag, so a colon or slash namespace
# separator is not available.
TAG_NO_BACKUP = "no-backup"
TAG_NO_REPLICATION = "no-replication"

KIND_BACKUP = "backup"
KIND_REPLICATION = "replication"
KINDS = (KIND_BACKUP, KIND_REPLICATION)

# What PVE itself accepts as a tag.
_TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-+.]*$")

# Guest config paths. /etc/pve/qemu-server is a symlink into
# /etc/pve/nodes/<node>/, which the glob follows.
_READ_TAGS_CMD = r"""
for f in /etc/pve/qemu-server/*.conf /etc/pve/lxc/*.conf; do
  [ -e "$f" ] || continue
  awk '/^\[/{exit} /^tags:/{print FILENAME"\t"$0}' "$f"
done 2>/dev/null
"""


def tag_names(config: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """{kind: tag} -- overridable, because some estates already have a
    convention of their own."""
    cfg = (config or {}).get("exception_tags") or {}
    return {
        KIND_BACKUP: _valid_tag(cfg.get(KIND_BACKUP)) or TAG_NO_BACKUP,
        KIND_REPLICATION: _valid_tag(cfg.get(KIND_REPLICATION)) or TAG_NO_REPLICATION,
    }


def _valid_tag(tag) -> Optional[str]:
    tag = (tag or "").strip()
    return tag if tag and _TAG_RE.match(tag) else None


# ---------------------------------------------------------------------------
# Parsing -- pure, no I/O
# ---------------------------------------------------------------------------

def parse_tag_lines(stdout: str) -> Dict[str, List[str]]:
    """``<path>\\ttags: a;b`` lines to ``{vmid: [tags]}``.

    The VMID comes from the filename. Only the guest's CURRENT tags are of
    interest, which is why the reading command stops at the first ``[`` --
    PVE copies the whole config, tags included, into a ``[snapname]`` block for
    every snapshot, so a plain ``grep '^tags:'`` would return several lines per
    guest and read a snapshot's stale tags as the present state. The same trap
    is handled for disks by ``migrate.split_main_section``.
    """
    out: Dict[str, List[str]] = {}
    for line in (stdout or "").splitlines():
        if "\t" not in line:
            continue
        path, _, rest = line.partition("\t")
        rest = rest.strip()
        if not rest.startswith("tags:"):
            continue
        m = re.search(r"/(\d+)\.conf$", path.strip())
        if not m:
            continue
        vmid = m.group(1)
        tags = [t.strip() for t in re.split(r"[;,]", rest[len("tags:"):]) if t.strip()]
        # A guest with two disks still has one config; last write wins is fine
        # because there is only ever one line per file after the awk.
        out[vmid] = tags
    return out


def exceptions_from_tags(tags_by_vmid: Dict[str, List[str]],
                         names: Optional[Dict[str, str]] = None) -> Dict[str, Dict[str, bool]]:
    """``{vmid: {no_backup, no_replication}}`` for the guests that declare one."""
    names = names or tag_names()
    want_bk = names[KIND_BACKUP]
    want_repl = names[KIND_REPLICATION]
    out: Dict[str, Dict[str, bool]] = {}
    for vmid, tags in (tags_by_vmid or {}).items():
        lowered = {t.lower() for t in tags}
        no_backup = want_bk.lower() in lowered
        no_repl = want_repl.lower() in lowered
        if no_backup or no_repl:
            out[str(vmid)] = {"no_backup": no_backup, "no_replication": no_repl}
    return out


def apply_tag(current: List[str], tag: str, enable: bool) -> List[str]:
    """The guest's new tag list with ``tag`` added or removed.

    Read-modify-write is not optional: ``qm set --tags`` replaces the entire
    list, so writing only our own tag would silently delete every tag the user
    had. Order of the existing tags is preserved (they show up in that order in
    the PVE UI), and the operation is idempotent in both directions.
    """
    tag_l = (tag or "").strip().lower()
    kept = [t for t in (current or []) if t.strip() and t.strip().lower() != tag_l]
    if enable:
        kept.append(tag)
    return kept


def parse_current_tags(stdout: str) -> List[str]:
    """Tags of a single guest, from that guest's config text."""
    for line in (stdout or "").splitlines():
        if line.startswith("["):
            break                                   # snapshot section -- stop
        if line.startswith("tags:"):
            return [t.strip() for t in re.split(r"[;,]", line[len("tags:"):]) if t.strip()]
    return []


# ---------------------------------------------------------------------------
# Reasons (who, when, why) -- the tag stays the truth
# ---------------------------------------------------------------------------

def _key(host: str, vmid) -> str:
    return f"{host}/{vmid}"


def _load_all() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(EXCEPTIONS_FILE):
        return {}
    try:
        with open(EXCEPTIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_all(data: Dict[str, Dict[str, Any]]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = f"{EXCEPTIONS_FILE}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, EXCEPTIONS_FILE)


def load_reasons(host: str) -> Dict[str, Dict[str, Any]]:
    """``{vmid: {reason, by, at, kinds}}`` for one host."""
    prefix = f"{host}/"
    out = {}
    for k, v in _load_all().items():
        if k.startswith(prefix) and isinstance(v, dict):
            out[k[len(prefix):]] = v
    return out


def save_reason(host: str, vmid, kinds: List[str], reason: str = "",
                by: str = "") -> Dict[str, Any]:
    """Record why a guest is deliberately unprotected."""
    rec = {
        "kinds": sorted({k for k in (kinds or []) if k in KINDS}),
        "reason": (reason or "").strip()[:500],
        "by": (by or "").strip()[:100],
        "at": int(time.time()),
    }
    with _lock:
        data = _load_all()
        if rec["kinds"]:
            data[_key(host, vmid)] = rec
        else:
            data.pop(_key(host, vmid), None)
        _save_all(data)
    return rec


def drop_reason(host: str, vmid) -> bool:
    with _lock:
        data = _load_all()
        existed = data.pop(_key(host, vmid), None) is not None
        if existed:
            _save_all(data)
    return existed


# ---------------------------------------------------------------------------
# Collection + writing (SSH)
# ---------------------------------------------------------------------------

def collect_exceptions(host: Dict[str, Any], cache_ttl: int = 300) -> Dict[str, Any]:
    """Declared exceptions for every guest on a host.

    Never raises: an unreadable host yields no exceptions, which is the safe
    direction -- guests then keep whatever protection verdict they had, rather
    than a failed read silently excusing them.
    """
    addr = host.get("address", "")
    out: Dict[str, Any] = {"exceptions": {}, "readable": False, "error": ""}
    r = run_command(host, _READ_TAGS_CMD, timeout=30, cache_ttl=cache_ttl)
    if not r.get("success"):
        out["error"] = ((r.get("stderr") or "").strip().splitlines()
                        or ["could not read guest configs"])[0][:300]
        return out
    out["readable"] = True

    tags = parse_tag_lines(r.get("stdout", ""))
    declared = exceptions_from_tags(tags)
    reasons = load_reasons(addr)
    for vmid, flags in declared.items():
        note = reasons.get(vmid) or {}
        out["exceptions"][vmid] = {
            **flags,
            "reason": note.get("reason", ""),
            "by": note.get("by", ""),
            "at": note.get("at"),
            # A tag somebody set in PVE without going through this tool: still
            # an exception, but one nobody has justified. Named so it surfaces.
            "documented": bool(note.get("reason")),
        }
    return out


def set_exception(host: Dict[str, Any], vmid, kinds: List[str],
                  reason: str = "", by: str = "") -> Dict[str, Any]:
    """Set/clear the tags for a guest and record the reason. Returns a result dict.

    ``kinds`` is the complete desired state: a kind that is absent gets its tag
    removed, so unchecking a box in the UI actually withdraws that exception.
    """
    vmid_s = str(vmid).strip()
    if not vmid_s.isdigit():
        return {"success": False, "error": "invalid vmid"}
    kinds = [k for k in (kinds or []) if k in KINDS]

    gtype = _guest_type(host, vmid_s)
    if not gtype:
        return {"success": False, "error": f"guest {vmid_s} not found on this host"}

    cur = _read_guest_tags(host, vmid_s, gtype)
    if cur is None:
        return {"success": False, "error": "could not read the guest's current tags"}

    names = tag_names()
    new = list(cur)
    for kind, tag in names.items():
        new = apply_tag(new, tag, kind in kinds)

    if sorted(t.lower() for t in new) != sorted(t.lower() for t in cur):
        cmd = ("qm" if gtype == "qemu" else "pct")
        r = run_command(host,
                        f"{cmd} set {shlex.quote(vmid_s)} --tags {shlex.quote(';'.join(new))}",
                        timeout=30)
        if not r.get("success"):
            return {"success": False,
                    "error": (r.get("stderr") or "tag update failed").strip()[:300]}
        from app.cache import invalidate_host
        invalidate_host(host.get("address", ""))

    if kinds:
        save_reason(host.get("address", ""), vmid_s, kinds, reason, by)
    else:
        drop_reason(host.get("address", ""), vmid_s)
    return {"success": True, "tags": new, "kinds": kinds}


def _guest_type(host: Dict[str, Any], vmid: str) -> str:
    """``qemu`` / ``lxc`` / '' -- which config file this guest has."""
    r = run_command(
        host,
        f"[ -e /etc/pve/qemu-server/{shlex.quote(vmid)}.conf ] && echo qemu || "
        f"{{ [ -e /etc/pve/lxc/{shlex.quote(vmid)}.conf ] && echo lxc; }}",
        timeout=15)
    return (r.get("stdout") or "").strip() if r.get("success") else ""


def _read_guest_tags(host: Dict[str, Any], vmid: str, gtype: str) -> Optional[List[str]]:
    sub = "qemu-server" if gtype == "qemu" else "lxc"
    r = run_command(host, f"cat /etc/pve/{sub}/{shlex.quote(vmid)}.conf 2>/dev/null",
                    timeout=15)
    if not r.get("success"):
        return None
    return parse_current_tags(r.get("stdout", ""))
