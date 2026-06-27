"""zfs-auto-snapshot retention editor.

On Proxmox/Debian, zfs-auto-snapshot has no central config file -- the cron
entries ARE the retention policy. Each level lives in its own file:

  frequent  /etc/cron.d/zfs-auto-snapshot        (crontab line, every 15 min)
  hourly    /etc/cron.hourly/zfs-auto-snapshot   (run-parts script)
  daily     /etc/cron.daily/zfs-auto-snapshot
  weekly    /etc/cron.weekly/zfs-auto-snapshot
  monthly   /etc/cron.monthly/zfs-auto-snapshot

Each carries a ``zfs-auto-snapshot ... --label=<level> --keep=<N> ...`` command.
This module reads those files, exposes the per-level ``keep`` value and whether
the level is enabled (its command line is not commented out), and writes the
two editable bits back in place -- the ``--keep=N`` number and the
enabled/disabled (commented) state -- preserving everything else and keeping a
timestamped backup.

The parsing/rewriting helpers are pure functions (no SSH) so they're unit
tested; the get_/set_ wrappers do the SSH I/O.
"""

from __future__ import annotations

import base64
import re
import shlex
from typing import Any, Dict, List, Optional

from app.ssh_manager import run_command

LEVELS = ["frequent", "hourly", "daily", "weekly", "monthly"]

LEVEL_FILES = {
    "frequent": "/etc/cron.d/zfs-auto-snapshot",
    "hourly": "/etc/cron.hourly/zfs-auto-snapshot",
    "daily": "/etc/cron.daily/zfs-auto-snapshot",
    "weekly": "/etc/cron.weekly/zfs-auto-snapshot",
    "monthly": "/etc/cron.monthly/zfs-auto-snapshot",
}

# Human-readable cadence for the UI.
LEVEL_INTERVAL = {
    "frequent": "every 15 min",
    "hourly": "hourly",
    "daily": "daily",
    "weekly": "weekly",
    "monthly": "monthly",
}

KEEP_MIN = 0
KEEP_MAX = 100000

_KEEP_RE = re.compile(r"--keep[=\s]+(\d+)")


def _is_command_line(line: str, label: str) -> bool:
    """True if this line is the zfs-auto-snapshot command for ``label``
    (commented or not)."""
    body = line.lstrip().lstrip("#").strip()
    return "zfs-auto-snapshot" in body and f"--label={label}" in body


def _line_is_commented(line: str) -> bool:
    return line.lstrip().startswith("#")


def parse_level(content: str, label: str) -> Optional[Dict[str, Any]]:
    """Parse one level's file content. Returns ``{keep, enabled}`` or None if
    no matching command line is found.

    If both a commented and an uncommented command line exist (e.g. an example
    plus the active one), the uncommented one wins for the enabled/keep values.
    """
    candidate = None
    for line in (content or "").splitlines():
        if not _is_command_line(line, label):
            continue
        commented = _line_is_commented(line)
        m = _KEEP_RE.search(line)
        keep = int(m.group(1)) if m else None
        entry = {"keep": keep, "enabled": not commented}
        if not commented:
            return entry  # active line is authoritative
        candidate = candidate or entry
    return candidate


def parse_retention(files: Dict[str, str]) -> List[Dict[str, Any]]:
    """Turn ``{label: file_content}`` into the ordered per-level list the UI
    consumes. Missing files / unparsable levels are marked installed=False."""
    out: List[Dict[str, Any]] = []
    for label in LEVELS:
        content = files.get(label)
        parsed = parse_level(content, label) if content is not None else None
        out.append({
            "label": label,
            "interval": LEVEL_INTERVAL[label],
            "file": LEVEL_FILES[label],
            "installed": parsed is not None,
            "keep": parsed["keep"] if parsed else None,
            "enabled": parsed["enabled"] if parsed else False,
        })
    return out


def _set_line_enabled(line: str, enabled: bool) -> str:
    """Comment/uncomment a command line while preserving its content."""
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    commented = stripped.startswith("#")
    if enabled and commented:
        # Drop leading '#' plus one optional following space.
        body = stripped[1:]
        if body.startswith(" "):
            body = body[1:]
        return indent + body
    if not enabled and not commented:
        return indent + "# " + stripped
    return line


def update_level_content(content: str, label: str,
                         keep: Optional[int] = None,
                         enabled: Optional[bool] = None) -> str:
    """Return the file content with this level's keep value and/or enabled
    state updated. Only the matching command line is touched."""
    lines = (content or "").split("\n")
    for i, line in enumerate(lines):
        if not _is_command_line(line, label):
            continue
        new = line
        if keep is not None and _KEEP_RE.search(new):
            new = _KEEP_RE.sub(f"--keep={int(keep)}", new)
        if enabled is not None:
            new = _set_line_enabled(new, enabled)
        lines[i] = new
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# SSH I/O
# ---------------------------------------------------------------------------

def get_retention(host: Dict[str, Any]) -> Dict[str, Any]:
    """Read the per-level retention policy from the host's cron files."""
    installed_r = run_command(
        host, "command -v zfs-auto-snapshot >/dev/null 2>&1 && echo INSTALLED || echo NO",
        timeout=10)
    installed = "INSTALLED" in (installed_r.get("stdout") or "")

    # Pull all five files in one round-trip, delimited so we can split them.
    parts = []
    for label, path in LEVEL_FILES.items():
        parts.append(
            f"echo '<<<{label}>>>'; cat {shlex.quote(path)} 2>/dev/null; echo '<<<END>>>'"
        )
    r = run_command(host, "; ".join(parts), timeout=20)
    raw = r.get("stdout", "") or ""

    files: Dict[str, str] = {}
    cur = None
    buf: List[str] = []
    for line in raw.split("\n"):
        m = re.match(r"^<<<(\w+)>>>$", line.strip())
        if m and m.group(1) in LEVEL_FILES:
            cur = m.group(1)
            buf = []
            continue
        if line.strip() == "<<<END>>>":
            if cur is not None:
                text = "\n".join(buf)
                # Only record as present if the file actually had content.
                if text.strip():
                    files[cur] = text
            cur = None
            continue
        if cur is not None:
            buf.append(line)

    return {"installed": installed, "levels": parse_retention(files)}


def set_retention(host: Dict[str, Any], changes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Apply per-level keep/enabled changes.

    ``changes`` is a list of ``{label, keep?, enabled?}``. Each affected file is
    backed up (``.bak.<timestamp>``) before being rewritten. Returns a per-level
    result list.
    """
    results: List[Dict[str, Any]] = []
    all_ok = True

    for ch in changes:
        label = ch.get("label")
        if label not in LEVEL_FILES:
            results.append({"label": label, "success": False, "error": "unknown level"})
            all_ok = False
            continue

        keep = ch.get("keep")
        enabled = ch.get("enabled")
        if keep is not None:
            try:
                keep = int(keep)
            except (TypeError, ValueError):
                results.append({"label": label, "success": False, "error": "keep not an integer"})
                all_ok = False
                continue
            if not (KEEP_MIN <= keep <= KEEP_MAX):
                results.append({"label": label, "success": False,
                                "error": f"keep out of range ({KEEP_MIN}-{KEEP_MAX})"})
                all_ok = False
                continue
        if enabled is not None:
            enabled = bool(enabled)

        path = LEVEL_FILES[label]
        r_read = run_command(host, f"cat {shlex.quote(path)} 2>/dev/null", timeout=10)
        content = r_read.get("stdout", "") if r_read.get("success") else ""
        if not content.strip():
            results.append({"label": label, "success": False,
                            "error": "cron file not found (level not installed)"})
            all_ok = False
            continue

        new_content = update_level_content(content, label, keep=keep, enabled=enabled)
        if new_content == content:
            # Nothing changed (e.g. no --keep token to update); treat as success.
            results.append({"label": label, "success": True, "unchanged": True})
            continue

        b64 = base64.b64encode(new_content.encode("utf-8")).decode("ascii")
        script = (
            f"cp -a {shlex.quote(path)} {shlex.quote(path)}.bak.$(date +%Y%m%d%H%M%S) && "
            f"echo {shlex.quote(b64)} | base64 -d > {shlex.quote(path)} && echo __OK__"
        )
        rw = run_command(host, script, timeout=15)
        ok = "__OK__" in (rw.get("stdout") or "")
        if not ok:
            all_ok = False
        results.append({
            "label": label,
            "success": ok,
            "keep": keep,
            "enabled": enabled,
            "stderr": (rw.get("stderr") or "").strip()[:200],
        })

    return {"success": all_ok, "results": results}
