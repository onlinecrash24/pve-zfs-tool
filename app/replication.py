"""bashclub-zsync replication integration.

Manages the bashclub-zsync ZFS replication tool (pull-based, cron-driven) on
remote hosts via SSH. Upstream: https://gitlab.bashclub.org/bashclub/zsync/

Scope (Phase 1):
  * Detect install status + version
  * Install via APT (bashclub repo)
  * Read / write /etc/bashclub/zsync.conf (shell-style key=value)
  * Run manually and tail log

Dataset-tagging, cron-scheduling, cross-host SSH-bootstrap and monitoring are
intentionally out-of-scope for Phase 1.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, Dict, List, Optional

from app.ssh_manager import run_command

CONFIG_PATH = "/etc/bashclub/zsync.conf"
CONFIG_DIR = "/etc/bashclub"
LOG_PATH = "/var/log/bashclub-zsync/zsync.log"
BINARY_NAME = "bashclub-zsync"
CHECKZFS_BINARY = "checkzfs"


def _extract_ip(source: str) -> str:
    """Extract bare host/IP from a source spec like ``root@1.2.3.4`` or ``1.2.3.4``."""
    s = (source or "").strip()
    if "@" in s:
        s = s.split("@", 1)[1]
    return s


def _safe_filename(name: str) -> str:
    """Sanitize a string for use as a config-file basename."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name or "")


def config_path_for(source: Optional[str] = None) -> str:
    """Return the per-source config path, or the legacy default if no source.

    The bashclub convention used in the wild is ``/etc/bashclub/<source-ip>.conf``
    so multiple replication pairs can coexist on a single target host.
    """
    if not source:
        return CONFIG_PATH
    ip = _extract_ip(source)
    if not ip:
        return CONFIG_PATH
    return f"{CONFIG_DIR}/{_safe_filename(ip)}.conf"

# Keys we surface in the UI — everything else is preserved verbatim on write.
KNOWN_KEYS = [
    "target",
    "source",
    "sshport",
    "tag",
    "snapshot_filter",
    "min_keep",
    "zfs_auto_snapshot_engine",
    "prefix",
    "suffix",
    "checkzfs_sourcepools",
    "checkzfs_prefix",
    "checkzfs_filter",
    "checkzfs_threshold_warning",
    "checkzfs_threshold_critical",
    "checkzfs_output",
]

# Shell key=value with optional quoting. Allows spaces inside quotes.
_KV_RE = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$')


# ---------------------------------------------------------------------------
# Config parse / serialize
# ---------------------------------------------------------------------------

def _parse_config(text: str) -> Dict[str, Any]:
    """Parse shell-style key=value lines. Preserves order and comments."""
    values: Dict[str, str] = {}
    lines: List[Dict[str, Any]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            lines.append({"type": "raw", "text": raw})
            continue
        m = _KV_RE.match(raw)
        if not m:
            lines.append({"type": "raw", "text": raw})
            continue
        key, val = m.group(1), m.group(2)
        # Strip one layer of matching quotes
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        values[key] = val
        lines.append({"type": "kv", "key": key, "value": val})
    return {"values": values, "lines": lines}


def _serialize_config(values: Dict[str, str], existing_lines: Optional[List[Dict[str, Any]]] = None) -> str:
    """Serialize back to text. If existing_lines is given, keep comments/order
    and replace values in place; unknown new keys are appended at the end."""
    out: List[str] = []
    written = set()
    if existing_lines:
        for ln in existing_lines:
            if ln["type"] == "raw":
                out.append(ln["text"])
            else:
                key = ln["key"]
                if key in values:
                    out.append(f'{key}="{_escape(values[key])}"')
                    written.add(key)
                else:
                    # key removed
                    pass
    # Append new keys
    for k, v in values.items():
        if k in written:
            continue
        out.append(f'{k}="{_escape(v)}"')
    # Trailing newline
    return "\n".join(out) + "\n"


def _escape(s: str) -> str:
    """Escape a value for double-quoted shell string."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`")


# ---------------------------------------------------------------------------
# Status / install
# ---------------------------------------------------------------------------

def is_pve_host(host: Dict[str, Any]) -> Dict[str, Any]:
    """Detect whether a host is a Proxmox VE server.

    Checks for ``pveversion`` binary or ``/etc/pve`` directory. Returns
    ``{is_pve: bool, version: str|None}``.
    """
    cmd = (
        "if command -v pveversion >/dev/null 2>&1; then "
        "  pveversion 2>/dev/null | head -n 1; "
        "elif [ -d /etc/pve ]; then "
        "  echo __PVE_DIR__; "
        "fi"
    )
    r = run_command(host, cmd, timeout=10)
    out = (r.get("stdout") or "").strip()
    if not out:
        return {"is_pve": False, "version": None}
    if out == "__PVE_DIR__":
        return {"is_pve": True, "version": None}
    return {"is_pve": True, "version": out}


def get_status(host: Dict[str, Any], source: Optional[str] = None) -> Dict[str, Any]:
    """Return install + config + last-run status for a host.

    If ``source`` is given, the per-source config file
    ``/etc/bashclub/<source-ip>.conf`` is inspected; otherwise the legacy
    default ``/etc/bashclub/zsync.conf``.
    """
    cfg_path = config_path_for(source)
    out: Dict[str, Any] = {
        "installed": False,
        "version": None,
        "is_pve": False,
        "pve_version": None,
        "config_exists": False,
        "config": None,
        "config_path": cfg_path,
        "log_present": False,
        "last_log_lines": [],
    }

    # PVE detection
    pve = is_pve_host(host)
    out["is_pve"] = pve["is_pve"]
    out["pve_version"] = pve["version"]

    # Installed?
    r = run_command(host, f"command -v {BINARY_NAME} 2>/dev/null && {BINARY_NAME} -v 2>/dev/null | head -n 1", timeout=10)
    if r["success"] and r["stdout"].strip():
        lines = [ln for ln in r["stdout"].splitlines() if ln.strip()]
        out["installed"] = True
        # Second line (if any) is the version banner; first is the path.
        if len(lines) >= 2:
            out["version"] = lines[1].strip()
        elif len(lines) == 1 and "/" not in lines[0]:
            out["version"] = lines[0].strip()

    # Config
    r = run_command(host, f"cat {shlex.quote(cfg_path)} 2>/dev/null", timeout=10)
    if r["success"] and r["stdout"]:
        out["config_exists"] = True
        parsed = _parse_config(r["stdout"])
        out["config"] = parsed["values"]

    # Log tail (last 5 lines for overview)
    r = run_command(host, f"tail -n 5 {shlex.quote(LOG_PATH)} 2>/dev/null", timeout=10)
    if r["success"] and r["stdout"]:
        out["log_present"] = True
        out["last_log_lines"] = r["stdout"].splitlines()

    return out


def install(host: Dict[str, Any]) -> Dict[str, Any]:
    """Install bashclub-zsync via the official bashclub APT repository.

    Writes the deb822 ``.sources`` stanza that bashclub publishes
    (https://apt.bashclub.org/release/) and the keyring at
    ``/usr/share/keyrings/bashclub-archive-keyring.gpg``. Cleans up the
    legacy ``bashclub.list`` + ``/etc/apt/keyrings/bashclub.gpg`` files
    we previously installed, so re-running upgrades the layout in place.
    Idempotent.
    """
    script = r"""
set -e
KEY=/usr/share/keyrings/bashclub-archive-keyring.gpg
SRC=/etc/apt/sources.list.d/bashclub.sources
LEGACY_LIST=/etc/apt/sources.list.d/bashclub.list
LEGACY_KEY=/etc/apt/keyrings/bashclub.gpg

# Drop the old layout if present (we previously installed bashclub.list
# pointing at /bashclub bookworm main with a key under /etc/apt/keyrings)
[ -f "$LEGACY_LIST" ] && rm -f "$LEGACY_LIST" || true
[ -f "$LEGACY_KEY"  ] && rm -f "$LEGACY_KEY"  || true

# Fetch the release key into the modern location
mkdir -p /usr/share/keyrings
if [ ! -s "$KEY" ]; then
  curl -fsSL https://apt.bashclub.org/gpg.key | gpg --dearmor -o "$KEY"
  chmod 0644 "$KEY"
fi

# Write the deb822 .sources stanza (matches bashclub's published layout)
if [ ! -s "$SRC" ]; then
  cat > "$SRC" <<EOSRC
Types: deb
URIs: https://apt.bashclub.org/release/
Suites: bookworm
Components: main
Signed-By: $KEY
Enabled: true
EOSRC
  chmod 0644 "$SRC"
fi

DEBIAN_FRONTEND=noninteractive apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y bashclub-zsync
command -v bashclub-zsync
""".strip()
    # Use `bash -s` over SSH so we don't have to quote multi-line
    cmd = f"bash -s <<'EOF'\n{script}\nEOF"
    r = run_command(host, cmd, timeout=180)
    return {
        "success": r["success"],
        "stdout": r["stdout"],
        "stderr": r["stderr"],
    }


# ---------------------------------------------------------------------------
# Config write
# ---------------------------------------------------------------------------

def read_config(host: Dict[str, Any], source: Optional[str] = None) -> Dict[str, Any]:
    cfg_path = config_path_for(source)
    r = run_command(host, f"cat {shlex.quote(cfg_path)} 2>/dev/null", timeout=10)
    if not r["success"] or not r["stdout"]:
        return {"exists": False, "values": {}, "raw": "", "config_path": cfg_path}
    parsed = _parse_config(r["stdout"])
    return {"exists": True, "values": parsed["values"], "raw": r["stdout"],
            "_lines": parsed["lines"], "config_path": cfg_path}


def write_config(host: Dict[str, Any], values: Dict[str, str],
                 source: Optional[str] = None) -> Dict[str, Any]:
    """Write config preserving comments/order from existing file.

    Strategy: read current file, parse, replace values in place, write back.
    A timestamped backup is created alongside. The path is derived from the
    ``source`` argument (per-source config) or the source value embedded in
    ``values["source"]``; otherwise the legacy default is used.
    """
    cfg_path = config_path_for(source or values.get("source"))

    # Read existing to preserve layout
    r_read = run_command(host, f"cat {shlex.quote(cfg_path)} 2>/dev/null", timeout=10)
    existing_lines = None
    if r_read["success"] and r_read["stdout"]:
        existing_lines = _parse_config(r_read["stdout"])["lines"]

    # Filter: only non-empty values get written (empty string = "clear")
    cleaned = {k: v for k, v in values.items() if v is not None and str(v) != ""}
    new_text = _serialize_config(cleaned, existing_lines)

    # Encode as base64 to transport safely
    import base64
    b64 = base64.b64encode(new_text.encode("utf-8")).decode("ascii")
    script = (
        f"mkdir -p {shlex.quote(CONFIG_DIR)} && "
        f"if [ -f {shlex.quote(cfg_path)} ]; then "
        f"cp -a {shlex.quote(cfg_path)} {shlex.quote(cfg_path)}.bak.$(date +%Y%m%d%H%M%S); "
        f"fi && "
        f"echo {shlex.quote(b64)} | base64 -d > {shlex.quote(cfg_path)} && "
        f"chmod 0640 {shlex.quote(cfg_path)}"
    )
    r = run_command(host, script, timeout=15)
    return {"success": r["success"], "stderr": r["stderr"], "config_path": cfg_path}


def delete_config(host: Dict[str, Any], source: Optional[str] = None,
                  purge_snapshots: bool = False) -> Dict[str, Any]:
    """Delete a per-source replication config and (optionally) its replica
    target dataset.

    Steps:
      1. Read the config to discover ``target=...``.
      2. Remove the cron entry for this config (if any).
      3. Backup + delete the config file itself.
      4. If ``purge_snapshots`` is True: ``zfs destroy -r <target>``
         (after refusing if the target looks like a system root such as
         ``rpool`` or ``rpool/ROOT``).

    Returns a structured result so the UI can show what happened.
    """
    cfg_path = config_path_for(source)
    cfg = read_config(host, source=source)
    target_ds = (cfg.get("values") or {}).get("target", "").strip()

    out: Dict[str, Any] = {
        "success": False,
        "config_path": cfg_path,
        "config_existed": cfg.get("exists", False),
        "target_dataset": target_ds,
        "cron_removed": False,
        "config_removed": False,
        "snapshots_purged": False,
        "destroy_output": "",
        "error": "",
    }

    # 1. cron entry
    try:
        cron_res = remove_cron(host, source=source)
        out["cron_removed"] = bool(cron_res.get("success"))
    except Exception as e:
        out["error"] = f"cron remove failed: {e}"

    # 2. config file (with backup)
    if cfg.get("exists"):
        rm = run_command(
            host,
            f"if [ -f {shlex.quote(cfg_path)} ]; then "
            f"cp -a {shlex.quote(cfg_path)} {shlex.quote(cfg_path)}.bak.$(date +%Y%m%d%H%M%S) && "
            f"rm -f {shlex.quote(cfg_path)} && echo __CFG_OK__; "
            f"fi",
            timeout=15,
        )
        out["config_removed"] = "__CFG_OK__" in (rm.get("stdout") or "")
        if not out["config_removed"]:
            out["error"] = (out["error"] + " | " if out["error"] else "") + \
                           "config remove failed: " + (rm.get("stderr") or "").strip()

    # 3. snapshot/replica purge — DESTRUCTIVE, so guard against system roots.
    if purge_snapshots and target_ds:
        forbidden = {"", "rpool", "rpool/ROOT", "rpool/data", "tank", "tankhdd"}
        # Allow nested paths like "rpool/repl" but refuse top-level pools.
        if target_ds in forbidden or "/" not in target_ds:
            out["error"] = (out["error"] + " | " if out["error"] else "") + \
                           f"refusing to destroy system-root dataset: {target_ds}"
        elif not re.match(r"^[A-Za-z0-9._:/-]+$", target_ds):
            out["error"] = (out["error"] + " | " if out["error"] else "") + \
                           "target dataset has invalid characters"
        else:
            r = run_command(host, f"zfs destroy -r {shlex.quote(target_ds)} 2>&1", timeout=120)
            out["snapshots_purged"] = bool(r.get("success"))
            out["destroy_output"] = (r.get("stdout") or r.get("stderr") or "")[-2000:]

    out["success"] = out["config_removed"] or not out["config_existed"]
    return out


def list_configs(host: Dict[str, Any]) -> Dict[str, Any]:
    """Enumerate per-source config files in /etc/bashclub.

    Returns ``{configs: [{path, source, target, exists}], default_exists: bool}``.
    """
    cmd = (
        f"for f in {shlex.quote(CONFIG_DIR)}/*.conf; do "
        f"  [ -f \"$f\" ] || continue; "
        f"  echo __FILE__ $f; cat $f; echo __END__; "
        f"done 2>/dev/null"
    )
    r = run_command(host, cmd, timeout=15)
    out: List[Dict[str, Any]] = []
    if r["success"] and r["stdout"]:
        cur = None
        buf: List[str] = []
        for line in r["stdout"].splitlines():
            if line.startswith("__FILE__ "):
                cur = line[len("__FILE__ "):].strip()
                buf = []
            elif line == "__END__":
                if cur:
                    parsed = _parse_config("\n".join(buf))
                    out.append({
                        "path": cur,
                        "source": parsed["values"].get("source", ""),
                        "target": parsed["values"].get("target", ""),
                    })
                cur = None
            elif cur is not None:
                buf.append(line)
    return {"configs": out}


# ---------------------------------------------------------------------------
# Run + log
# ---------------------------------------------------------------------------

def run_now(host: Dict[str, Any], source: Optional[str] = None) -> Dict[str, Any]:
    """Trigger bashclub-zsync manually for the given source's config.

    Output is captured into the standard log so the UI can read it back.
    A short timeout is used for the SSH channel; the log is authoritative.
    """
    cfg_path = config_path_for(source)
    cmd = (
        f"mkdir -p /var/log/bashclub-zsync && "
        f"{BINARY_NAME} -c {shlex.quote(cfg_path)} "
        f">> {shlex.quote(LOG_PATH)} 2>&1; echo __exit=$?"
    )
    r = run_command(host, cmd, timeout=600)
    # Extract trailing __exit=N
    exit_code = None
    if r["stdout"]:
        m = re.search(r"__exit=(\d+)\s*$", r["stdout"].strip())
        if m:
            exit_code = int(m.group(1))
    return {
        "success": r["success"] and (exit_code == 0),
        "exit_code": exit_code,
        "stderr": r["stderr"],
    }


def bootstrap_ssh(target_host: Dict[str, Any], source_host: Dict[str, Any]) -> Dict[str, Any]:
    """Enable passwordless SSH from target -> source.

    Steps (all idempotent):
      1. Ensure /root/.ssh/id_ed25519 exists on the target; generate if missing.
      2. Read the target's public key.
      3. Populate target's known_hosts with the source's host key (ssh-keyscan).
      4. Append the target's public key to source's authorized_keys.
      5. Probe the SSH connection from target to source in BatchMode.

    Returns a dict describing each step so the UI can surface partial failures.
    """
    src_addr = source_host["address"]
    src_port = int(source_host.get("port") or 22)
    src_user = source_host.get("user") or "root"

    out: Dict[str, Any] = {
        "success": False,
        "key_generated": False,
        "target_pubkey": "",
        "known_hosts_updated": False,
        "authorized_keys_updated": False,
        "probe_ok": False,
        "probe_output": "",
        "error": "",
    }

    # Step 1+2: ensure key + fetch public
    gen_script = (
        "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
        "if [ ! -f /root/.ssh/id_ed25519 ]; then "
        "  ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N '' -C bashclub-zsync-$(hostname) >/dev/null && "
        "  echo __GENERATED__; "
        "fi && "
        "cat /root/.ssh/id_ed25519.pub"
    )
    r = run_command(target_host, gen_script, timeout=20)
    if not r["success"]:
        out["error"] = "Key generation/read failed on target: " + (r["stderr"] or r["stdout"])
        return out
    stdout = r["stdout"]
    if "__GENERATED__" in stdout:
        out["key_generated"] = True
        stdout = stdout.replace("__GENERATED__", "").strip()
    pubkey = stdout.strip().splitlines()[-1] if stdout.strip() else ""
    if not pubkey.startswith("ssh-"):
        out["error"] = "Unexpected public key format on target"
        return out
    out["target_pubkey"] = pubkey

    # Step 3: known_hosts on target
    kh_script = (
        f"touch /root/.ssh/known_hosts && chmod 600 /root/.ssh/known_hosts && "
        f"tmp=$(mktemp) && "
        f"ssh-keyscan -T 10 -p {src_port} {shlex.quote(src_addr)} >\"$tmp\" 2>/dev/null && "
        f"if [ -s \"$tmp\" ]; then "
        f"  while IFS= read -r line; do "
        f"    grep -qxF \"$line\" /root/.ssh/known_hosts || echo \"$line\" >> /root/.ssh/known_hosts; "
        f"  done < \"$tmp\"; "
        f"  rm -f \"$tmp\"; echo __KH_OK__; "
        f"else rm -f \"$tmp\"; fi"
    )
    r = run_command(target_host, kh_script, timeout=20)
    out["known_hosts_updated"] = "__KH_OK__" in (r.get("stdout") or "")

    # Step 4: append target pubkey to source authorized_keys
    from app.ssh_manager import _append_authorized_key
    r = _append_authorized_key(source_host, pubkey)
    out["authorized_keys_updated"] = bool(r.get("success"))
    if not out["authorized_keys_updated"]:
        out["error"] = "Failed to append authorized_keys on source: " + (r.get("stderr") or r.get("stdout") or "unknown")
        return out

    # Step 5: probe from target
    probe = (
        f"ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 "
        f"-p {src_port} {shlex.quote(src_user + '@' + src_addr)} 'echo __PROBE_OK__'"
    )
    r = run_command(target_host, probe, timeout=20)
    combined = (r.get("stdout") or "") + (r.get("stderr") or "")
    out["probe_output"] = combined.strip()[-500:]
    out["probe_ok"] = r.get("success", False) and "__PROBE_OK__" in (r.get("stdout") or "")
    out["success"] = out["probe_ok"]
    if not out["probe_ok"] and not out["error"]:
        out["error"] = "SSH probe failed"
    return out


def create_target_dataset(host: Dict[str, Any], dataset: str) -> Dict[str, Any]:
    """Create a ZFS filesystem intended as a replication target.

    Uses ``-p`` to create parents and disables auto-snapshots via the
    ``com.sun:auto-snapshot=false`` property (same property ``zfs-auto-snapshot``
    and Proxmox honor). Idempotent: succeeds silently if the dataset already
    exists and enforces the property either way.
    """
    name = (dataset or "").strip()
    if not name or "/" not in name or name.startswith("/") or name.endswith("/"):
        return {"success": False, "error": "Invalid dataset name (expected pool/path)"}
    if not re.match(r"^[A-Za-z0-9._:/-]+$", name):
        return {"success": False, "error": "Dataset name contains invalid characters"}
    q = shlex.quote(name)
    script = (
        f"if zfs list -H -o name {q} >/dev/null 2>&1; then "
        f"  zfs set com.sun:auto-snapshot=false {q}; echo __EXISTS__; "
        f"else "
        f"  zfs create -p -o com.sun:auto-snapshot=false {q} && echo __CREATED__; "
        f"fi"
    )
    r = run_command(host, script, timeout=30)
    msg = r.get("stdout", "")
    return {
        "success": r["success"],
        "existed": "__EXISTS__" in msg,
        "created": "__CREATED__" in msg,
        "stderr": r.get("stderr", ""),
    }


def list_tagged_datasets(host: Dict[str, Any], tag: str = "bashclub:zsync") -> Dict[str, Any]:
    """List all filesystems + volumes on a host with their tag value.

    Returns ``{datasets: [{name, type, tagged, value}], tag: tag}``.
    ``value`` is ``""`` for unset (ZFS output ``-``).
    """
    # Only allow a safe tag format to avoid shell-escape surprises
    if not re.match(r"^[A-Za-z0-9_.:-]+$", tag):
        return {"datasets": [], "tag": tag, "error": "invalid tag format"}
    cmd = f"zfs list -H -o name,type,{shlex.quote(tag)} -t filesystem,volume"
    r = run_command(host, cmd, timeout=20)
    if not r["success"]:
        return {"datasets": [], "tag": tag, "error": (r["stderr"] or r["stdout"] or "").strip()}
    datasets = []
    for line in r["stdout"].splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, dtype, value = parts[0], parts[1], parts[2]
        tagged = value not in ("", "-")
        datasets.append({
            "name": name,
            "type": dtype,
            "tagged": tagged,
            "value": "" if value == "-" else value,
        })
    return {"datasets": datasets, "tag": tag}


def set_dataset_tags(host: Dict[str, Any], tag: str,
                     enable: List[str], disable: List[str],
                     value: str = "all") -> Dict[str, Any]:
    """Apply ``zfs set <tag>=<value>`` / ``zfs inherit <tag>`` to a list of datasets.

    The call is idempotent — running it twice is safe. Operates on filesystems
    and volumes only; invalid names are rejected client-side by the caller.
    """
    if not re.match(r"^[A-Za-z0-9_.:-]+$", tag):
        return {"success": False, "error": "invalid tag format"}
    if not re.match(r"^[A-Za-z0-9_./:@-]*$", value or ""):
        return {"success": False, "error": "invalid value format"}

    # Validate dataset names
    name_re = re.compile(r"^[A-Za-z0-9._:/-]+$")
    for lst in (enable, disable):
        for n in lst:
            if not name_re.match(n or ""):
                return {"success": False, "error": f"invalid dataset name: {n}"}

    results: List[Dict[str, Any]] = []
    all_ok = True
    for n in enable:
        cmd = f"zfs set {shlex.quote(tag)}={shlex.quote(value)} {shlex.quote(n)}"
        r = run_command(host, cmd, timeout=15)
        ok = r.get("success", False)
        if not ok:
            all_ok = False
        results.append({"dataset": n, "op": "set", "success": ok,
                        "stderr": r.get("stderr", "").strip()})
    for n in disable:
        cmd = f"zfs inherit {shlex.quote(tag)} {shlex.quote(n)}"
        r = run_command(host, cmd, timeout=15)
        ok = r.get("success", False)
        if not ok:
            all_ok = False
        results.append({"dataset": n, "op": "inherit", "success": ok,
                        "stderr": r.get("stderr", "").strip()})
    return {"success": all_ok, "results": results}


def _cron_marker(cfg_path: str) -> str:
    """Stable, grep-able marker that uniquely identifies a zsync cron line for
    a specific config file. Used to find/replace/remove the entry without
    touching unrelated user cron jobs."""
    return f"{BINARY_NAME} -c {cfg_path}"


# Common cron presets surfaced to the UI. ``label`` is i18n-key; ``schedule``
# is a literal cron expression. The bashclub default is "20 0-22 * * *"
# (twenty past every hour, leaving the 23:00 slot free for housekeeping).
CRON_PRESETS = [
    {"id": "bashclub_default", "label": "repl_cron_preset_default", "schedule": "20 0-22 * * *"},
    {"id": "every_15min",      "label": "repl_cron_preset_15min",   "schedule": "*/15 * * * *"},
    {"id": "every_30min",      "label": "repl_cron_preset_30min",   "schedule": "*/30 * * * *"},
    {"id": "hourly",           "label": "repl_cron_preset_hourly",  "schedule": "0 * * * *"},
    {"id": "every_2h",         "label": "repl_cron_preset_2h",      "schedule": "0 */2 * * *"},
    {"id": "every_6h",         "label": "repl_cron_preset_6h",      "schedule": "0 */6 * * *"},
    {"id": "daily_0300",       "label": "repl_cron_preset_daily",   "schedule": "0 3 * * *"},
]

# Cron expression: 5 fields of *, digits, /, -, ,. Reject anything else to
# avoid command injection through the schedule.
_CRON_RE = re.compile(r"^[\s\d\*/,\-]+$")


def _validate_cron(schedule: str) -> bool:
    schedule = (schedule or "").strip()
    if not _CRON_RE.match(schedule):
        return False
    fields = schedule.split()
    return len(fields) == 5


def get_cron(host: Dict[str, Any], source: Optional[str] = None) -> Dict[str, Any]:
    """Read root's crontab and return the current zsync entry (if any) for
    the per-source config file."""
    cfg_path = config_path_for(source)
    marker = _cron_marker(cfg_path)
    r = run_command(host, "crontab -l 2>/dev/null", timeout=10)
    raw = r.get("stdout", "") or ""
    entry = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if marker in stripped:
            # Split off the schedule (first 5 whitespace-separated fields)
            parts = stripped.split(None, 5)
            if len(parts) >= 6:
                entry = {
                    "schedule": " ".join(parts[:5]),
                    "command": parts[5],
                    "raw": stripped,
                }
            break
    return {"installed": entry is not None, "entry": entry,
            "config_path": cfg_path, "presets": CRON_PRESETS}


def set_cron(host: Dict[str, Any], schedule: str,
             source: Optional[str] = None,
             log_path: str = "/var/log/bashclub-zsync.log") -> Dict[str, Any]:
    """Install or replace the zsync cron entry for the per-source config.

    Idempotent: existing zsync lines for this config are removed first, then
    the new one is appended. All other cron lines are preserved verbatim.
    """
    cfg_path = config_path_for(source)
    if not _validate_cron(schedule):
        return {"success": False, "error": "invalid cron schedule (expected 5 fields, digits / * - , /)"}
    if not re.match(r"^[A-Za-z0-9._/-]+$", log_path or ""):
        return {"success": False, "error": "invalid log path"}

    marker = _cron_marker(cfg_path)
    new_line = f"{schedule.strip()} {BINARY_NAME} -c {cfg_path} >> {log_path} 2>&1"
    # Build a small awk filter that drops existing zsync lines for THIS config
    # but keeps everything else. Then append the new line.
    import base64
    appended = base64.b64encode(new_line.encode("utf-8")).decode("ascii")
    marker_b64 = base64.b64encode(marker.encode("utf-8")).decode("ascii")
    # IMPORTANT: cron requires a trailing newline on the last line, otherwise
    # "crontab" rejects the file with "missing newline before EOF". base64 -d
    # writes raw bytes without a trailing newline, so we explicitly append one.
    # IMPORTANT: cron requires a trailing newline on the last line, otherwise
    # "crontab" rejects the file with "missing newline before EOF". base64 -d
    # writes raw bytes without a trailing newline, so we explicitly append one.
    # The "crontab" utility itself signals cron via the spool file's mtime,
    # but we additionally trigger an explicit reload so the new schedule is
    # picked up deterministically (covers both Debian's cron and any setup
    # that uses cronie / systemd-cron).
    script = (
        "set -e; "
        f"M=$(echo {shlex.quote(marker_b64)} | base64 -d); "
        "TMP=$(mktemp); "
        "(crontab -l 2>/dev/null || true) | grep -vF \"$M\" > \"$TMP\" || true; "
        f"echo {shlex.quote(appended)} | base64 -d >> \"$TMP\"; "
        "printf '\\n' >> \"$TMP\"; "
        "crontab \"$TMP\"; "
        "rm -f \"$TMP\"; "
        + _cron_reload_snippet() +
        "echo __OK__"
    )
    r = run_command(host, script, timeout=15)
    ok = r.get("success", False) and "__OK__" in (r.get("stdout") or "")
    return {"success": ok, "schedule": schedule.strip(), "command": new_line,
            "config_path": cfg_path,
            "stderr": r.get("stderr", "")}


def remove_cron(host: Dict[str, Any], source: Optional[str] = None) -> Dict[str, Any]:
    """Remove the zsync cron entry for the given config file."""
    cfg_path = config_path_for(source)
    marker = _cron_marker(cfg_path)
    import base64
    marker_b64 = base64.b64encode(marker.encode("utf-8")).decode("ascii")
    script = (
        f"M=$(echo {shlex.quote(marker_b64)} | base64 -d); "
        "TMP=$(mktemp); "
        "(crontab -l 2>/dev/null || true) | grep -vF \"$M\" > \"$TMP\" || true; "
        "crontab \"$TMP\"; "
        "rm -f \"$TMP\"; "
        + _cron_reload_snippet() +
        "echo __OK__"
    )
    r = run_command(host, script, timeout=15)
    ok = r.get("success", False) and "__OK__" in (r.get("stdout") or "")
    return {"success": ok, "stderr": r.get("stderr", "")}


def _cron_reload_snippet() -> str:
    """Return a shell snippet that reloads whichever cron implementation is
    active. All branches are best-effort — never fail the outer command."""
    return (
        "if command -v systemctl >/dev/null 2>&1; then "
        "  systemctl reload cron 2>/dev/null || "
        "  systemctl reload crond 2>/dev/null || "
        "  systemctl reload-or-restart cron 2>/dev/null || true; "
        "elif command -v service >/dev/null 2>&1; then "
        "  service cron reload 2>/dev/null || service cron restart 2>/dev/null || true; "
        "fi; "
    )


def run_checkzfs(host: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Run ``checkzfs --source <ip> --columns +message`` on the given (target)
    host and parse the box-drawing table output into structured rows.

    Returns ``{success, raw, rows: [{status, source, replica, snapshot, age,
    count, message}], summary: {ok, warn, crit}}``.
    """
    ip = _extract_ip(source)
    if not ip or not re.match(r"^[A-Za-z0-9._:-]+$", ip):
        return {"success": False, "error": "invalid source", "raw": "", "rows": []}
    # ``--no-color`` would be ideal but isn't supported by every checkzfs
    # version. We force ``TERM=dumb`` and additionally strip ANSI escapes
    # below, which covers both cases.
    cmd = f"TERM=dumb {CHECKZFS_BINARY} --source {shlex.quote(ip)} --columns +message 2>&1"
    r = run_command(host, cmd, timeout=120)
    raw = r.get("stdout", "") or ""
    # Strip ANSI escape sequences (CSI ... letter) and lone ESC chars.
    ansi_re = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b")
    raw_clean = ansi_re.sub("", raw)
    rows: List[Dict[str, Any]] = []
    summary = {"ok": 0, "warn": 0, "crit": 0, "other": 0}
    sep = "║"  # box-drawing column separator
    for line in raw_clean.splitlines():
        if sep not in line:
            continue
        parts = [p.strip() for p in line.split(sep)]
        # Header row: starts with "status"
        if parts and parts[0].lower().startswith("status"):
            continue
        # Need at least: status, source, replica, snapshot, age, count, message
        if len(parts) < 7:
            continue
        status = parts[0].lower()
        if status in summary:
            summary[status] += 1
        else:
            summary["other"] += 1
        replica = parts[2]
        rows.append({
            "status": status,
            "source": parts[1],
            "replica": "" if replica in ("", "—", "-") else replica,
            "snapshot": parts[3],
            "age": parts[4],
            "count": parts[5],
            "message": parts[6],
        })
    return {
        "success": r.get("success", False),
        "raw": raw[-8000:],  # cap
        "rows": rows,
        "summary": summary,
    }


def tail_log(host: Dict[str, Any], lines: int = 200) -> Dict[str, Any]:
    lines = max(1, min(int(lines), 5000))
    r = run_command(host, f"tail -n {lines} {shlex.quote(LOG_PATH)} 2>/dev/null", timeout=15)
    return {
        "success": r["success"],
        "content": r["stdout"] if r["success"] else "",
        "present": bool(r["success"] and r["stdout"]),
    }
