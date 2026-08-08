"""Which Proxmox product is on a host: PVE, PBS, or both.

Everything the tool does today assumes a Proxmox VE node. PBS support is coming,
and the first thing it needs is to know what it is talking to -- a datastore
lives on a PBS host, a guest lives on a PVE node, and a machine can be both
(the ``proxmox-backup-server`` package installs happily on a PVE node).

The probe is one shell command emitting ``KEY=value`` lines, and the
classification is a pure function over those lines, so the interesting part is
testable without SSH.

One trap worth naming: ``proxmox-backup-client`` is installed on ordinary PVE
nodes so they can write to a PBS. It is NOT evidence of a backup server, and
classifying on it would label most PVE nodes "PVE+PBS". Only the server side
counts -- the ``proxmox-backup-manager`` binary, the ``proxmox-backup-server``
package, or ``/etc/proxmox-backup``. The client is recorded as a marker because
it is useful context, never as a role.
"""

import logging
import re
import time

from app.ssh_manager import run_command

log = logging.getLogger(__name__)

ROLE_PVE = "pve"
ROLE_PBS = "pbs"
ROLE_BOTH = "pve+pbs"
ROLE_UNKNOWN = "unknown"

# Markers that make a host a PVE node / a PBS server. Keep these two lists as
# the single source of truth for the classification below.
_PVE_MARKERS = ("PVE_BIN", "PVE_DIR", "PVE_PKG")
_PBS_MARKERS = ("PBS_BIN", "PBS_DIR", "PBS_PKG")

# The trailing PROBE_OK line tells "reachable, probe ran to the end" apart from
# "no output because the connection died" -- without it, a truncated answer
# would look like a host with no Proxmox on it.
IDENTITY_PROBE = r"""
{
  command -v pveversion >/dev/null 2>&1 && printf 'PVE_BIN=1\n'
  [ -d /etc/pve ] && printf 'PVE_DIR=1\n'
  command -v proxmox-backup-manager >/dev/null 2>&1 && printf 'PBS_BIN=1\n'
  [ -d /etc/proxmox-backup ] && printf 'PBS_DIR=1\n'
  [ -f /etc/proxmox-backup/datastore.cfg ] && printf 'PBS_DATASTORE_CFG=1\n'
  command -v proxmox-backup-client >/dev/null 2>&1 && printf 'PBS_CLIENT=1\n'
  pveversion 2>/dev/null | head -n 1 | sed 's/^/PVE_VERSION=/'
  dpkg-query -W -f='${Status}|${Version}\n' pve-manager 2>/dev/null \
    | sed -n 's/^install ok installed|/PVE_PKG=/p'
  dpkg-query -W -f='${Status}|${Version}\n' proxmox-backup-server 2>/dev/null \
    | sed -n 's/^install ok installed|/PBS_PKG=/p'
  hostname 2>/dev/null | sed 's/^/HOSTNAME=/'
  ( . /etc/os-release 2>/dev/null && printf 'OS=%s\n' "$PRETTY_NAME" )
  printf 'PROBE_OK=1\n'
} 2>/dev/null
true
"""


# Two fields on one line, e.g. "PVE_PKG=8.2.4-1PBS_PKG=3.2.7-1". Commands that
# print without a trailing newline (dpkg-query is one) glue their output to
# whatever the probe emits next. The probe asks dpkg-query for the newline, but
# recovering here too matters: the mangled version would otherwise trim to a
# plausible-looking "8.2.4" while the following fields vanished silently.
# The lookbehind is what keeps this from cutting keys in half: without it the
# split also fires one character into "PVE_BIN=" (on "VE_BIN=") and every field
# turns to nonsense. A key never follows a letter or underscore, but it does
# follow the digit at the end of a glued version.
_GLUED_FIELD = re.compile(r"(?<![A-Z_])(?=[A-Z][A-Z0-9_]{2,}=)")


def _parse_lines(stdout):
    """``KEY=value`` lines to a dict. Unknown keys are kept -- the probe grows."""
    fields = {}
    for raw in (stdout or "").splitlines():
        for line in _GLUED_FIELD.split(raw.strip()):
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key:
                fields[key] = value.strip()
    return fields


def _short_pve_version(raw):
    """``pve-manager/8.2.4/abc (running kernel: ...)`` -> ``8.2.4``.

    Falls back to the raw string when the format is not the familiar one, so an
    unexpected pveversion output shows up in the UI instead of vanishing.
    """
    if not raw:
        return None
    m = re.search(r"pve-manager/([0-9][^/\s]*)", raw)
    if m:
        return m.group(1)
    return raw or None


def _short_pkg_version(raw):
    """Debian version ``3.2.7-1`` / ``8.2.4-1+deb12u1`` -> ``3.2.7`` / ``8.2.4``."""
    if not raw:
        return None
    m = re.match(r"(?:\d+:)?([0-9][0-9.]*)", raw.strip())
    return m.group(1) if m else raw.strip()


def classify(fields):
    """Decide the role from parsed probe fields. Pure, no I/O.

    A host counts as PVE on the ``pveversion`` binary, ``/etc/pve`` or the
    ``pve-manager`` package, and as PBS on ``proxmox-backup-manager``,
    ``/etc/proxmox-backup`` or the ``proxmox-backup-server`` package. Both sets
    can be true at once, which is a real and supported configuration.
    """
    pve = any(fields.get(m) for m in _PVE_MARKERS)
    pbs = any(fields.get(m) for m in _PBS_MARKERS)
    if pve and pbs:
        role = ROLE_BOTH
    elif pve:
        role = ROLE_PVE
    elif pbs:
        role = ROLE_PBS
    else:
        role = ROLE_UNKNOWN
    return role, pve, pbs


def parse_identity(stdout):
    """Turn probe output into the identity dict. Pure, no I/O.

    ``reachable`` reflects whether the probe ran to its end (PROBE_OK), not
    whether anything Proxmox was found: a reachable Debian box with neither
    product is ``reachable=True, role="unknown"``, which is a very different
    situation from a host that never answered.
    """
    fields = _parse_lines(stdout)
    reachable = bool(fields.get("PROBE_OK"))
    role, pve, pbs = classify(fields)
    pve_version = _short_pve_version(fields.get("PVE_VERSION")) or \
        _short_pkg_version(fields.get("PVE_PKG"))
    return {
        "reachable": reachable,
        "role": role if reachable else ROLE_UNKNOWN,
        "pve": pve if reachable else False,
        "pbs": pbs if reachable else False,
        "pve_version": pve_version if reachable else None,
        "pbs_version": _short_pkg_version(fields.get("PBS_PKG")) if reachable else None,
        "pve_version_raw": fields.get("PVE_VERSION"),
        "pbs_version_raw": fields.get("PBS_PKG"),
        "hostname": fields.get("HOSTNAME"),
        "os": fields.get("OS"),
        "has_datastore_cfg": bool(fields.get("PBS_DATASTORE_CFG")),
        "backup_client": bool(fields.get("PBS_CLIENT")),
        "markers": sorted(k for k in fields if k != "PROBE_OK" and fields[k]),
    }


def detect(host, timeout=20, cache_ttl=600):
    """Probe a host (registered or transient dict) and classify it.

    Never raises: an unreachable host comes back as ``reachable=False`` with the
    SSH error, because the caller needs to tell "not a Proxmox host" apart from
    "could not ask".
    """
    r = run_command(host, IDENTITY_PROBE, timeout=timeout, cache_ttl=cache_ttl)
    identity = parse_identity(r.get("stdout", ""))
    identity["checked"] = int(time.time())
    if not identity["reachable"]:
        err = (r.get("stderr") or "").strip() or "no answer from host"
        identity["error"] = err.splitlines()[0][:300]
        log.info("identity probe failed for %s: %s", host.get("address"), identity["error"])
    else:
        identity["error"] = None
    return identity


def persisted_fields(identity):
    """The subset stored in hosts.json, so the UI can render a role without SSH.

    Versions and role only -- OS and hostname are probe context, not state worth
    keeping (and a stale stored hostname would be worse than none).
    """
    return {
        "role": identity.get("role", ROLE_UNKNOWN),
        "pve_version": identity.get("pve_version"),
        "pbs_version": identity.get("pbs_version"),
        "identity_checked": identity.get("checked") or int(time.time()),
    }


def is_supported(identity):
    """Whether the tool manages this host: PVE, PBS, or both -- nothing else."""
    return identity.get("role") in (ROLE_PVE, ROLE_PBS, ROLE_BOTH)


def admission(identity, force=False):
    """May this host be registered? Returns ``(allowed, code)``.

    The policy, in one place because it is the whole point of the feature:

    * PVE / PBS / both -> in.
    * The host answered and is neither -> ``not_proxmox``, and ``force`` does
      NOT override it. The tool manages Proxmox hosts; a file server in the list
      would only produce failing commands and misleading dashboards.
    * The host could not be asked -> ``unverified``. Nothing was established, so
      this is overridable: the SSH key may not be installed yet, and hosts that
      are powered off most of the time are a supported case (standby). Such a
      host is stored with role ``unknown`` and identified on the next probe.
    """
    if is_supported(identity):
        return True, None
    if identity.get("reachable"):
        return False, "not_proxmox"
    return (True, None) if force else (False, "unverified")
