"""Backup state per guest: PBS and vzdump, read through the PVE node.

The replication inventory answers "where are the ZFS copies of this guest".
That is half the question. A guest with no second ZFS copy may still be backed
up to a PBS every night, and a beautifully replicated guest with no backup is
protected against a dead disk but not against ransomware or a wrong ``rm``.
Looking at the two separately hides the actual gap, so the backup state belongs
next to the replication state.

Deliberately read from the PVE node, not from the backup server. PVE's storage
layer already holds the PBS credentials (``/etc/pve/priv/storage/<id>.pw``) and
talks to it for us, so ``pvesh`` returns the backup list -- including the PBS
verification state -- without this tool ever touching a PBS credential, and the
same code path covers vzdump archives on NFS, CIFS and plain directories.

That is also the safer order. A backup server is supposed to survive the
compromise of the machines it backs up; root SSH from this tool to a PBS would
invert that. Nothing here opens a new door to the backup server.
"""

import json
import logging
import re
import shlex
import time
from typing import Any, Dict, List, Optional

from app.ssh_manager import run_command

log = logging.getLogger(__name__)

# A daily job that misses one run warns; a whole week without a backup is
# critical. Overridable via the notification config's thresholds.
BACKUP_WARN_HOURS = 36
BACKUP_CRIT_HOURS = 168

STATE_OK = "green"
STATE_WARN = "yellow"
STATE_BAD = "red"
STATE_UNKNOWN = "unknown"

# Storage types whose volumes can carry a PBS verification result. Anywhere else
# an absent verification means "this storage has no such concept" -- reporting
# a vzdump archive on NFS as "not verified" would be noise on every single row.
VERIFYING_TYPES = ("pbs",)


# ---------------------------------------------------------------------------
# Parsers -- pure, no I/O
# ---------------------------------------------------------------------------

def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_json_list(text: str) -> List[Dict[str, Any]]:
    """Parse a pvesh JSON array. Anything unexpected yields an empty list --
    callers distinguish "nothing there" from "could not ask" through the
    unreadable list, never through an empty parse.

    Tolerates leading noise before the JSON: perl locale warnings on a PVE node
    are common enough that a strict parse would report a perfectly healthy
    storage as unreadable.
    """
    text = (text or "").strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        starts = [i for i in (text.find("["), text.find("{")) if i > 0]
        if not starts:
            return []
        try:
            data = json.loads(text[min(starts):])
        except (ValueError, TypeError):
            return []
    if isinstance(data, dict):                 # {"data": [...]} shape
        data = data.get("data")
    if not isinstance(data, list):
        return []
    return [d for d in data if isinstance(d, dict)]


def parse_storage_list(text: str) -> List[Dict[str, Any]]:
    """``pvesh get /nodes/<node>/storage --content backup`` to storage rows.

    Only storages that are enabled AND active can be listed; a disabled or
    unreachable one is kept with the flag so the caller can report it as
    unreadable instead of silently omitting its backups.
    """
    out = []
    for row in _load_json_list(text):
        sid = str(row.get("storage") or "").strip()
        if not sid:
            continue
        out.append({
            "storage": sid,
            "type": str(row.get("type") or ""),
            "enabled": bool(_as_int(row.get("enabled"), 1)),
            "active": bool(_as_int(row.get("active"), 0)),
            "total": _as_int(row.get("total")),
            "used": _as_int(row.get("used")),
            "avail": _as_int(row.get("avail")),
        })
    out.sort(key=lambda s: s["storage"])
    return out


def parse_backup_content(text: str, storage: str = "",
                         storage_type: str = "") -> List[Dict[str, Any]]:
    """``pvesh get /nodes/<n>/storage/<id>/content --content backup`` to volumes.

    ``verify`` is the PBS verification outcome: ``"ok"``, ``"failed"``, or None
    when the volume carries no verification at all. On a PBS, None means "not
    verified yet"; on a directory storage it means the feature does not exist
    there -- the caller tells them apart via ``verifies``.
    """
    out = []
    for row in _load_json_list(text):
        volid = str(row.get("volid") or "").strip()
        if not volid:
            continue
        verification = row.get("verification")
        verify = None
        if isinstance(verification, dict):
            verify = str(verification.get("state") or "").strip().lower() or None
        elif isinstance(verification, str) and verification.strip():
            verify = verification.strip().lower()
        out.append({
            "volid": volid,
            "storage": storage or volid.split(":", 1)[0],
            "storage_type": storage_type,
            "verifies": storage_type in VERIFYING_TYPES,
            "vmid": str(row.get("vmid") or "").strip(),
            "ctime": _as_int(row.get("ctime")),
            "size": _as_int(row.get("size")),
            "format": str(row.get("format") or ""),
            "subtype": str(row.get("subtype") or ""),
            "verify": verify,
            "protected": bool(_as_int(row.get("protected"), 0)),
            "notes": str(row.get("notes") or "")[:200],
        })
    out.sort(key=lambda v: (v["vmid"], -v["ctime"]))
    return out


def _split_ids(value: str) -> List[str]:
    return [p for p in re.split(r"[,;\s]+", (value or "").strip()) if p]


def parse_backup_jobs(text: str) -> List[Dict[str, Any]]:
    """``vzdump:`` sections of /etc/pve/jobs.cfg.

    Sections look like::

        vzdump: backup-8a1f
                schedule sat 02:00
                storage pbs-main
                mode snapshot
                all 1
                exclude 100,101
                enabled 1

    Same indented ``key value`` shape as a storage.cfg, so the section walk
    mirrors ``migrate.parse_zfs_storages``. Jobs of other types are skipped
    along with their bodies.
    """
    out: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for raw in (text or "").splitlines():
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(\S+)\s*$", raw)
        if m:
            if m.group(1) == "vzdump":
                cur = {"id": m.group(2), "storage": "", "schedule": "",
                       "enabled": True, "all": False, "vmid": [], "exclude": [],
                       "mode": "", "pool": ""}
                out.append(cur)
            else:
                cur = None                     # another job type -- skip body
            continue
        if cur is None or not raw.strip():
            continue
        p = re.match(r"^\s+([a-z0-9-]+)\s+(.*)$", raw)
        if not p:
            continue
        key, val = p.group(1), p.group(2).strip()
        if key == "storage":
            cur["storage"] = val
        elif key == "schedule":
            cur["schedule"] = val
        elif key == "enabled":
            cur["enabled"] = bool(_as_int(val, 1))
        elif key == "all":
            cur["all"] = bool(_as_int(val, 0))
        elif key == "vmid":
            cur["vmid"] = _split_ids(val)
        elif key == "exclude":
            cur["exclude"] = _split_ids(val)
        elif key == "mode":
            cur["mode"] = val
        elif key == "pool":
            cur["pool"] = val
    return out


def job_covers(job: Dict[str, Any], vmid: str) -> bool:
    """Whether a backup job is supposed to include this guest.

    A disabled job covers nothing -- it is exactly the case worth catching: the
    job exists, so the estate looks backed up, but it has not run since someone
    switched it off. A pool-based job is treated as not covering anything,
    because resolving pool membership needs another call and guessing would
    produce a confident wrong answer.
    """
    vmid = str(vmid or "")
    if not vmid or not job.get("enabled", True):
        return False
    if job.get("all"):
        return vmid not in [str(x) for x in job.get("exclude") or []]
    return vmid in [str(x) for x in job.get("vmid") or []]


def guest_backup_map(volumes: List[Dict[str, Any]],
                     jobs: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """Fold the volume list into one record per VMID.

    ``newest`` drives the age check, and the verification verdict is taken from
    the newest volume only: an older backup that verified fine says nothing
    about whether last night's is intact.
    """
    by_vmid: Dict[str, Dict[str, Any]] = {}
    for v in volumes:
        vmid = str(v.get("vmid") or "")
        if not vmid:
            continue
        rec = by_vmid.setdefault(vmid, {
            "count": 0, "newest": 0, "newest_volid": "", "newest_verify": None,
            "newest_verifies": False, "storages": [], "types": [],
            "verify_failed": 0, "protected": 0, "total_size": 0,
        })
        rec["count"] += 1
        rec["total_size"] += v.get("size") or 0
        if v.get("protected"):
            rec["protected"] += 1
        if v.get("verifies") and v.get("verify") == "failed":
            rec["verify_failed"] += 1
        if v.get("storage") and v["storage"] not in rec["storages"]:
            rec["storages"].append(v["storage"])
        if v.get("storage_type") and v["storage_type"] not in rec["types"]:
            rec["types"].append(v["storage_type"])
        if (v.get("ctime") or 0) > rec["newest"]:
            rec["newest"] = v.get("ctime") or 0
            rec["newest_volid"] = v.get("volid", "")
            rec["newest_verify"] = v.get("verify")
            rec["newest_verifies"] = bool(v.get("verifies"))

    for vmid, rec in by_vmid.items():
        rec["covered_by"] = [j.get("id", "") for j in (jobs or [])
                             if job_covers(j, vmid)]
        rec["storages"].sort()
        rec["types"].sort()
    return by_vmid


# ---------------------------------------------------------------------------
# Thresholds + state
# ---------------------------------------------------------------------------

def backup_thresholds():
    """(warn_hours, crit_hours) from the notification config, sanitised.

    A warn level above crit would make the warning unreachable, so it is pulled
    back down. Mirrors ``monitor.capacity_thresholds``.
    """
    warn, crit = BACKUP_WARN_HOURS, BACKUP_CRIT_HOURS
    try:
        from app.notifications import load_config
        th = (load_config() or {}).get("thresholds") or {}
        warn = float(th.get("backup_warn_hours", warn))
        crit = float(th.get("backup_crit_hours", crit))
    except Exception:
        pass
    warn = min(max(warn, 1.0), 8760.0)
    crit = min(max(crit, 1.0), 8760.0)
    if warn > crit:
        warn = crit
    return warn, crit


def backup_state(rec: Optional[Dict[str, Any]], now: Optional[float] = None,
                 warn_hours: float = BACKUP_WARN_HOURS,
                 crit_hours: float = BACKUP_CRIT_HOURS,
                 covered_known: bool = True) -> Dict[str, Any]:
    """One guest's backup verdict.

    red     no backup at all, the newest one is older than crit, or the newest
            one failed verification -- a backup that does not verify is not a
            backup.
    yellow  newest older than warn, newest still unverified on a storage that
            verifies, or no enabled backup job covers the guest.
    green   recent, verified (or on a storage without verification), covered.

    ``covered_known`` is False when the job list could not be read; the
    coverage check is then skipped rather than reported as "no job covers it".
    """
    now = time.time() if now is None else now
    if not rec or not rec.get("count"):
        return {"state": STATE_BAD, "reason": "none", "count": 0,
                "age_seconds": None, "newest": 0, "storages": [], "types": [],
                "covered": False}

    age = max(0.0, now - (rec.get("newest") or 0))
    base = {
        "count": rec.get("count", 0),
        "age_seconds": int(age),
        "newest": rec.get("newest", 0),
        "newest_volid": rec.get("newest_volid", ""),
        "storages": list(rec.get("storages") or []),
        "types": list(rec.get("types") or []),
        "protected": rec.get("protected", 0),
        "covered": bool(rec.get("covered_by")),
        "covered_by": list(rec.get("covered_by") or []),
    }

    if rec.get("newest_verifies") and rec.get("newest_verify") == "failed":
        return {**base, "state": STATE_BAD, "reason": "verify_failed"}
    if not rec.get("newest"):
        # Backups exist but none carries a creation time, so their age cannot be
        # judged. Saying "older than a week" from a missing timestamp would be
        # inventing a fault; flag it as worth a look instead.
        return {**base, "state": STATE_WARN, "reason": "no_timestamp",
                "age_seconds": None}
    if age > crit_hours * 3600:
        return {**base, "state": STATE_BAD, "reason": "stale_crit"}
    if age > warn_hours * 3600:
        return {**base, "state": STATE_WARN, "reason": "stale_warn"}
    if rec.get("newest_verifies") and not rec.get("newest_verify"):
        return {**base, "state": STATE_WARN, "reason": "verify_pending"}
    if covered_known and not base["covered"]:
        return {**base, "state": STATE_WARN, "reason": "no_job"}
    return {**base, "state": STATE_OK, "reason": "ok"}


def guest_backup_states(bmap: Dict[str, Dict[str, Any]],
                        vmids: Optional[List[str]] = None,
                        now: Optional[float] = None,
                        warn_hours: float = BACKUP_WARN_HOURS,
                        crit_hours: float = BACKUP_CRIT_HOURS,
                        covered_known: bool = True,
                        unknown: bool = False) -> Dict[str, Dict[str, Any]]:
    """``{vmid: state}`` in the same shape as ``guest_replication_states``.

    ``vmids`` adds guests that have no backup at all -- without it they simply
    would not appear, which is the opposite of what should happen to a guest
    nobody backs up.

    ``unknown`` marks every result as unreadable: when the storages could not be
    listed, "no backup found" is not a finding, it is a missing answer. Handing
    back red there would put a fault on healthy guests every time a PBS is down.
    """
    out: Dict[str, Dict[str, Any]] = {}
    keys = set(bmap.keys()) | {str(v) for v in (vmids or []) if str(v or "")}
    for vmid in keys:
        if unknown:
            out[vmid] = {"state": STATE_UNKNOWN, "reason": "unreadable",
                         "count": 0, "age_seconds": None, "newest": 0,
                         "storages": [], "types": [], "covered": False}
            continue
        out[vmid] = backup_state(bmap.get(vmid), now=now, warn_hours=warn_hours,
                                 crit_hours=crit_hours,
                                 covered_known=covered_known)
    return out


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------

def _node_name(host: Dict[str, Any]) -> str:
    r = run_command(host, "hostname", timeout=10, cache_ttl=600)
    return (r.get("stdout") or "").strip().split(".")[0] if r.get("success") else ""


def collect_backups(host: Dict[str, Any], node: str = "",
                    cache_ttl: int = 300) -> Dict[str, Any]:
    """Backup storages, their volumes and the vzdump jobs of one PVE node.

    Cached like the snapshot listing: the per-storage call goes over the network
    to the PBS and must not run on every page view.

    Never raises. A storage that cannot be listed lands in ``unreadable`` with
    its error, and ``readable`` is False when not a single storage could be
    read -- the callers turn that into an "unknown" state, never into "no
    backup".
    """
    out: Dict[str, Any] = {"node": node, "storages": [], "volumes": [],
                           "jobs": [], "unreadable": [], "readable": False,
                           "jobs_known": False, "error": ""}
    node = node or _node_name(host)
    out["node"] = node
    if not node:
        out["error"] = "could not determine the node name"
        return out

    sr = run_command(
        host,
        f"pvesh get /nodes/{shlex.quote(node)}/storage --content backup "
        f"--output-format=json",
        timeout=60, cache_ttl=cache_ttl)
    storages = parse_storage_list(sr.get("stdout", ""))
    if not storages:
        out["error"] = ((sr.get("stderr") or sr.get("stdout") or "").strip()
                        .splitlines() or ["no backup storage found"])[0][:300]
        # No storages parsed can mean either: this node has no backup storage
        # (a real answer) or pvesh failed (not an answer). The exit status is
        # what separates them.
        out["readable"] = bool(sr.get("success"))
        out["jobs"], out["jobs_known"] = _read_jobs(host, cache_ttl)
        return out
    out["storages"] = storages

    def _read_storage(st):
        """One storage's volumes, or the reason it could not be listed."""
        if not st["enabled"] or not st["active"]:
            return st, None, ("disabled" if not st["enabled"] else "inactive")
        cr = run_command(
            host,
            f"pvesh get /nodes/{shlex.quote(node)}/storage/{shlex.quote(st['storage'])}"
            f"/content --content backup --output-format=json",
            timeout=120, cache_ttl=cache_ttl)
        vols = parse_backup_content(cr.get("stdout", ""), st["storage"], st["type"])
        if not vols and not cr.get("success"):
            return st, None, ((cr.get("stderr") or cr.get("stdout") or "").strip()
                              .splitlines() or ["no answer"])[0][:300]
        return st, vols, None

    # In parallel: each of these is an independent network round trip that the
    # PVE node makes to the backup server, and a PBS listing a large datastore
    # takes seconds. Serially, three storages at a 120s timeout each could
    # outlast the whole request budget on their own. run_command keeps its SSH
    # connections thread-local, so every worker opens its own session to the
    # same host -- the same construction collect_inventory uses across hosts,
    # and four concurrent sessions sit well below sshd's MaxSessions of 10.
    workers = min(4, len(storages))
    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_read_storage, storages))
    else:
        results = [_read_storage(st) for st in storages]

    for st, vols, error in results:
        if error is not None:
            out["unreadable"].append({"storage": st["storage"], "error": error})
            continue
        out["readable"] = True
        out["volumes"].extend(vols)

    out["jobs"], out["jobs_known"] = _read_jobs(host, cache_ttl)
    return out


def _read_jobs(host: Dict[str, Any], cache_ttl: int):
    """(jobs, known) -- ``known`` is False when jobs.cfg could not be read, so
    the caller does not report "no job covers this guest" on a failed read."""
    jr = run_command(host, "cat /etc/pve/jobs.cfg 2>/dev/null", timeout=15,
                     cache_ttl=cache_ttl)
    if not jr.get("success"):
        return [], False
    return parse_backup_jobs(jr.get("stdout", "")), True


def host_backup_states(host: Dict[str, Any], vmids: Optional[List[str]] = None,
                       cache_ttl: int = 300) -> Dict[str, Any]:
    """Everything the UI needs for one host: per-VMID state plus what failed."""
    data = collect_backups(host, cache_ttl=cache_ttl)
    warn_h, crit_h = backup_thresholds()
    bmap = guest_backup_map(data["volumes"], data["jobs"])
    states = guest_backup_states(
        bmap, vmids=vmids, warn_hours=warn_h, crit_hours=crit_h,
        covered_known=data["jobs_known"], unknown=not data["readable"])
    return {
        "states": states,
        "node": data["node"],
        "storages": data["storages"],
        "unreadable": data["unreadable"],
        "readable": data["readable"],
        "error": data["error"],
        "warn_hours": warn_h,
        "crit_hours": crit_h,
    }


def merge_backups(matrix: Dict[str, Any], states: Dict[str, Dict[str, Any]],
                  source_host: str = "") -> Dict[str, Any]:
    """Attach a ``backup`` field to each guest of the replication matrix.

    Only guests of ``source_host`` are touched -- backups live where the guest
    runs, and the matrix is already scoped to one source. A guest with no entry
    in ``states`` keeps whatever it had: absent backup data must not read as
    "no backup".
    """
    guests = matrix.get("guests") or []
    without = 0
    for g in guests:
        if source_host and g.get("source_host") != source_host:
            continue
        st = states.get(str(g.get("vmid") or ""))
        if not st:
            continue
        g["backup"] = st
        if st.get("state") == STATE_BAD:
            without += 1
    matrix["backup_at_risk_count"] = without
    matrix["backup_states_present"] = bool(states)
    return matrix


# ---------------------------------------------------------------------------
# Overall protection: the two defences judged together
# ---------------------------------------------------------------------------

def protection_state(guest: Dict[str, Any]) -> str:
    """One guest's overall protection -- ``ok`` / ``accepted`` / ``warn`` / ``crit``.

    Replication and backup are independent lines of defence and the verdict is
    about how many of them actually hold. A guest with a replica but no backup
    survives a dead disk and nothing else; a guest with a backup but no replica
    is genuinely covered, just not by replication. Judging on copies alone
    reported the first as healthy and the second as at-risk -- both backwards.

    ``accepted`` is a gap somebody declared deliberate (see guest_intent): not a
    finding, but deliberately NOT ``ok`` either -- an unprotected guest is not
    the same thing as a protected one, even when that is fine. A declaration
    only excuses the gap it names; the other one stays open.

    A guest whose backups were never examined (no ``backup`` field, or state
    ``unknown``) falls back to the copy-only judgement, so a host without
    backup storage reads exactly as it did before.

    Mirrors the Zustand column in the backup overview; keep the two in step.
    """
    has_copy = (guest.get("copy_count") or 0) > 0
    bk = guest.get("backup") or {}
    state = bk.get("state")
    known = bool(bk) and state != STATE_UNKNOWN
    has_backup = known and state != STATE_BAD

    exc = guest.get("exception") or {}
    ok_without_backup = bool(exc.get("no_backup"))
    ok_without_copy = bool(exc.get("no_replication"))

    if guest.get("config_mismatch"):
        return "warn"
    if not has_copy and not has_backup:
        # Nothing protects this guest. Only a declaration covering BOTH gaps
        # makes that acceptable -- excusing one while the other stands open
        # would read as approved when half of it never was.
        if ok_without_backup and ok_without_copy:
            return "accepted"
        # Backups that could not be read are not a gap anybody declared; keep
        # the old copy-only judgement rather than excusing an unknown.
        if not known and ok_without_copy:
            return "accepted"
        return "crit"
    if not has_copy:
        # A working backup IS a second line of defence, even without a replica.
        return "ok"
    if known and state == STATE_BAD:
        return "accepted" if ok_without_backup else "warn"
    return "ok"


def overall_verdict(guests: List[Dict[str, Any]]) -> str:
    """Worst per-guest protection state across a host: ``ok``/``warn``/``crit``.

    ``accepted`` counts as ok -- a declared exception is not a finding, which is
    the whole point of declaring it. It stays visible per guest and in the
    report's own section, so nothing is swept away.
    """
    worst = "ok"
    for g in guests or []:
        s = protection_state(g)
        if s == "crit":
            return "crit"
        if s == "warn":
            worst = "warn"
    return worst


def stale_exception(guest: Dict[str, Any]) -> str:
    """A declaration that reality has outgrown, or '' if there is none.

    Somebody declared "needs no backup" and the guest has been getting backups
    ever since; the note is now a lie waiting to mislead the next person who
    reads it. Cheap to spot, and exactly the sort of leftover nobody goes
    looking for.
    """
    exc = guest.get("exception") or {}
    if not exc:
        return ""
    bk = guest.get("backup") or {}
    known = bool(bk) and bk.get("state") != STATE_UNKNOWN
    has_backup = known and bk.get("state") != STATE_BAD
    if exc.get("no_backup") and has_backup:
        return "backup"
    if exc.get("no_replication") and (guest.get("copy_count") or 0) > 0:
        return "replication"
    return ""
