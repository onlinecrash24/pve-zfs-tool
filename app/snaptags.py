"""Snapshot-tag discovery + per-host tag selection for the Snapshot Check.

Users with several replications end up with several snapshot naming tags
(zfs-auto-snap labels from different sources, custom prefixes from other
tools). The check used to know a fixed set of labels; everything else was
noise under "manual snapshots". This module discovers which tags actually
exist on a host and stores which of them the user considers relevant.

Selection is stored per host address in /app/data/snapcheck_tags.json.
No selection saved -> DEFAULT_TAGS (the previous hardcoded behavior).
"""

from __future__ import annotations

import json
import os
import re
import threading
from typing import Dict, List, Optional

DATA_DIR = "/app/data"
TAGS_FILE = os.path.join(DATA_DIR, "snapcheck_tags.json")

# The historic built-in label set (previous hardcoded behavior).
DEFAULT_TAGS = ("frequent", "hourly", "daily", "weekly", "monthly", "yearly",
                "backup-zfs", "bashclub-zfs")

# A valid tag: what we accept from discovery and from the UI.
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

_lock = threading.Lock()

# zfs-auto-snapshot convention: zfs-auto-snap_<label>-YYYY-MM-DD-HHMM
_AUTOSNAP_RE = re.compile(r"zfs-auto-snap_([A-Za-z0-9]+)")
# Generic prefix: everything before the first 4+-digit run (timestamp/year),
# with trailing separators stripped.
_PREFIX_RE = re.compile(r"^(.*?)[-_.]?\d{4}")


def is_valid_tag(tag: str) -> bool:
    return bool(tag) and bool(TAG_RE.match(tag))


def extract_tag(snap_name: str) -> Optional[str]:
    """Best-effort tag of one snapshot name (the part after '@').

    1. zfs-auto-snapshot convention -> its label.
    2. Any known default tag appearing in the name -> that tag.
    3. Generic prefix before the first timestamp-ish digit run.
    Returns None when nothing tag-like can be derived (fully manual name).
    """
    m = _AUTOSNAP_RE.search(snap_name)
    if m:
        return m.group(1)
    for tag in DEFAULT_TAGS:
        if tag in snap_name:
            return tag
    m = _PREFIX_RE.match(snap_name)
    if m:
        prefix = m.group(1).rstrip("-_.")
        if is_valid_tag(prefix):
            return prefix
    return None


def discover_tags(snapshot_names) -> Dict[str, int]:
    """Aggregate tag -> snapshot count over an iterable of snapshot names."""
    counts: Dict[str, int] = {}
    for name in snapshot_names:
        tag = extract_tag(name)
        if tag:
            counts[tag] = counts.get(tag, 0) + 1
    return counts


def build_label_regex(tags) -> "re.Pattern[str]":
    """Alternation regex over the selected tags (longest first so overlapping
    tags like 'backup-zfs' win over a plain 'backup')."""
    ordered = sorted({t for t in tags if is_valid_tag(t)}, key=len, reverse=True)
    if not ordered:
        ordered = list(DEFAULT_TAGS)
    return re.compile("|".join(re.escape(t) for t in ordered))


# ---------------------------------------------------------------------------
# Persistence (per host address)
# ---------------------------------------------------------------------------

def _load_all() -> Dict[str, List[str]]:
    if not os.path.exists(TAGS_FILE):
        return {}
    try:
        with open(TAGS_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def load_tag_selection(address: str) -> Optional[List[str]]:
    """The saved selection for a host, or None (= use DEFAULT_TAGS)."""
    sel = _load_all().get(address)
    if not isinstance(sel, list):
        return None
    valid = [t for t in sel if is_valid_tag(t)]
    return valid or None


def save_tag_selection(address: str, tags) -> List[str]:
    """Persist a host's tag selection (validated); returns what was saved."""
    valid = sorted({t for t in tags if is_valid_tag(t)})
    with _lock:
        data = _load_all()
        if valid:
            data[address] = valid
        else:
            data.pop(address, None)   # empty selection = back to defaults
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(TAGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    return valid


def effective_tags(address: str) -> List[str]:
    return load_tag_selection(address) or list(DEFAULT_TAGS)


def visible_tags(counts: Dict[str, int], selection: Optional[List[str]]):
    """Tags to offer in the selection UI as ``[{tag, count, selected}]``.

    Only tags that actually exist on the host are shown (discovered, count>0),
    plus any explicitly saved tag so a now-empty saved tag stays toggleable.
    The blanket default tags are NOT listed at count 0 -- suggesting labels
    that don't exist on the host is just clutter. Checkbox state comes from
    the saved selection, or DEFAULT_TAGS membership when nothing is saved."""
    checked = set(selection) if selection is not None else set(DEFAULT_TAGS)
    shown = set(counts) | (set(selection) if selection is not None else set())
    return [{"tag": tg, "count": counts.get(tg, 0), "selected": tg in checked}
            for tg in sorted(shown)]
