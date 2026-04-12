"""AI-powered ZFS report generation supporting OpenAI, Anthropic, Ollama, and custom providers."""

import json
import os
import re
import threading
import time
import uuid
import logging
import urllib.request
import urllib.error
from app.timezone import now as tz_now

DATA_DIR = "/app/data"
AI_CONFIG_FILE = os.path.join(DATA_DIR, "ai_config.json")
AI_REPORTS_FILE = os.path.join(DATA_DIR, "ai_reports.json")

log = logging.getLogger(__name__)
_lock = threading.Lock()
_scheduler_thread = None
_scheduler_stop = threading.Event()
_last_run_key = None  # Persists across scheduler restarts

# Cache for latest collected data (used by chat)
_latest_data = None

DEFAULT_CONFIG = {
    "provider": "openai",
    "openai": {
        "api_key": "",
        "model": "gpt-4o-mini",
        "base_url": "https://api.openai.com/v1",
    },
    "anthropic": {
        "api_key": "",
        "model": "claude-sonnet-4-20250514",
    },
    "ollama": {
        "base_url": "http://localhost:11434",
        "model": "llama3",
    },
    "custom": {
        "base_url": "",
        "api_key": "",
        "model": "",
    },
    "schedule": {
        "enabled": False,
        "interval": "daily",
        "hour": 6,
        "weekday": 0,
    },
    "report_language": "en",
    "notify_on_report": False,
    "system_prompt": "",
    "max_reports": 50,
}

DEFAULT_SYSTEM_PROMPT_EN = """You are a ZFS storage expert analyzing a Proxmox VE environment.
Analyze the provided ZFS data and generate a comprehensive status report.

Structure your report with these sections:
1. **Overall Health Summary** - Quick status of all pools across all hosts
2. **Storage Capacity** - Usage per pool with warnings if above 80%
3. **Scrub Status** - Last scrub results, overdue scrubs (recommend monthly)
4. **Snapshot Analysis** - Total counts, auto-snapshot coverage, datasets without snapshots, retention concerns
5. **SMART Disk Health** - Status of all drives
6. **Anomalies & Warnings** - Anything unusual or concerning
7. **Recommendations** - Actionable items sorted by priority

IMPORTANT — Proxmox-specific rules you MUST follow:
- **Snapshot freshness**: The data includes per-dataset snapshot details (per_dataset). To determine the most recent snapshot, you MUST check the "newest" date across ALL child datasets (e.g. rpool/data/subvol-*, rpool/ROOT/pve-1, tank/data/vm-*), not just the root pool dataset. Parent dataset snapshots often show 0B used — this is normal because actual data lives in child datasets.
- **zfs-auto-snapshot on Proxmox**: On Proxmox VE, zfs-auto-snapshot is managed via CRON JOBS (found in /etc/cron.{frequent,hourly,daily,weekly,monthly}/zfs-auto-snapshot), NOT via systemd. There is no systemd service for zfs-auto-snapshot. NEVER recommend "systemctl status zfs-auto-snapshot" or similar systemd commands. If snapshot scheduling needs to be checked, recommend: "ls /etc/cron.*/zfs-auto-snapshot" or "cat /etc/cron.daily/zfs-auto-snapshot".
- **Snapshot levels**: zfs-auto-snapshot typically creates 5 levels: frequent (every 15 min), hourly, daily, weekly, monthly. Each level has its own retention policy.
- **0B used snapshots**: Snapshots on parent datasets (e.g. rpool@zfs-auto-snap_daily-...) often show 0B used. This is completely normal and does NOT indicate a problem — the actual snapshot data is in the child datasets.
- **Retention analysis**: The data includes a pre-computed "retention_analysis" section per host with:
  - "per_label": For each label (frequent/hourly/daily/weekly/monthly): total_snapshots, dataset_count, configured_keep (from cron --keep=N), per_dataset_avg, newest_age_human, count_mismatches (datasets where actual != configured), stale_datasets (datasets where newest snapshot exceeds max age threshold).
  - "missing_labels": Datasets that SHOULD have a label (because it's configured in cron) but DON'T have any snapshots for it.
  Use this data directly — do NOT guess or invent retention values. Report count_mismatches and stale_datasets as findings. If stale_datasets is empty for a label, that label is healthy.
  Age thresholds: frequent > 30min, hourly > 2h, daily > 48h, weekly > 14d, monthly > 62d.
- **Snapshot integrity**: ZFS scrub already validates ALL data including snapshots via checksums. A successful scrub with 0 errors confirms snapshot chain integrity. Do NOT recommend separate checksum verification — scrub covers this.

Use emoji indicators: ✅ OK, ⚠️ Warning, ❌ Critical
Be concise but thorough. Focus on actionable insights. Avoid false positives.
Write the entire report in English."""

DEFAULT_SYSTEM_PROMPT_DE = """Du bist ein ZFS-Speicherexperte und analysierst eine Proxmox VE Umgebung.
Analysiere die bereitgestellten ZFS-Daten und erstelle einen umfassenden Statusbericht.

Strukturiere den Bericht mit diesen Abschnitten:
1. **Gesamtstatus** - Schnellübersicht aller Pools auf allen Hosts
2. **Speicherkapazität** - Belegung pro Pool mit Warnung ab 80%
3. **Scrub-Status** - Letzte Scrub-Ergebnisse, überfällige Scrubs (monatlich empfohlen)
4. **Snapshot-Analyse** - Gesamtanzahl, Auto-Snapshot-Abdeckung, Datasets ohne Snapshots, Aufbewahrungshinweise
5. **SMART-Festplattenzustand** - Status aller Laufwerke
6. **Anomalien & Warnungen** - Auffälligkeiten oder Bedenken
7. **Empfehlungen** - Handlungsempfehlungen nach Priorität sortiert

WICHTIG — Proxmox-spezifische Regeln, die du UNBEDINGT beachten musst:
- **Snapshot-Aktualität**: Die Daten enthalten Snapshot-Details pro Dataset (per_dataset). Um den neuesten Snapshot zu bestimmen, MUSST du das "newest"-Datum über ALLE Child-Datasets prüfen (z.B. rpool/data/subvol-*, rpool/ROOT/pve-1, tank/data/vm-*), nicht nur das Root-Pool-Dataset. Snapshots auf Parent-Datasets zeigen oft 0B used — das ist normal, da die eigentlichen Daten in Child-Datasets liegen.
- **zfs-auto-snapshot auf Proxmox**: Auf Proxmox VE wird zfs-auto-snapshot über CRON-JOBS verwaltet (in /etc/cron.{frequent,hourly,daily,weekly,monthly}/zfs-auto-snapshot), NICHT über systemd. Es gibt keinen systemd-Service für zfs-auto-snapshot. Empfehle NIEMALS "systemctl status zfs-auto-snapshot" oder ähnliche systemd-Befehle. Zum Prüfen der Snapshot-Planung empfehle: "ls /etc/cron.*/zfs-auto-snapshot" oder "cat /etc/cron.daily/zfs-auto-snapshot".
- **Snapshot-Ebenen**: zfs-auto-snapshot erstellt typischerweise 5 Ebenen: frequent (alle 15 Min), hourly, daily, weekly, monthly. Jede Ebene hat ihre eigene Aufbewahrungsrichtlinie.
- **0B-Snapshots**: Snapshots auf Parent-Datasets (z.B. rpool@zfs-auto-snap_daily-...) zeigen oft 0B used. Das ist völlig normal und weist NICHT auf ein Problem hin — die tatsächlichen Snapshot-Daten befinden sich in den Child-Datasets.
- **Aufbewahrungsanalyse (Retention)**: Die Daten enthalten eine vorberechnete "retention_analysis" pro Host mit:
  - "per_label": Für jedes Label (frequent/hourly/daily/weekly/monthly): total_snapshots, dataset_count, configured_keep (aus Cron --keep=N), per_dataset_avg, newest_age_human, count_mismatches (Datasets wo IST != SOLL), stale_datasets (Datasets wo neuester Snapshot das Max-Alter überschreitet).
  - "missing_labels": Datasets die ein Label haben SOLLTEN (weil in Cron konfiguriert) aber KEINE Snapshots dafür haben.
  Verwende diese Daten direkt — erfinde oder rate KEINE Retention-Werte. Melde count_mismatches und stale_datasets als Befunde. Wenn stale_datasets für ein Label leer ist, ist dieses Label gesund.
  Alters-Schwellwerte: frequent > 30min, hourly > 2h, daily > 48h, weekly > 14d, monthly > 62d.
- **Snapshot-Integrität**: ZFS Scrub validiert bereits ALLE Daten inklusive Snapshots per Checksumme. Ein erfolgreicher Scrub mit 0 Fehlern bestätigt die Integrität der Snapshot-Kette. Empfehle KEINE separate Checksummen-Prüfung — Scrub deckt dies ab.

Verwende Emoji-Indikatoren: ✅ OK, ⚠️ Warnung, ❌ Kritisch
Sei prägnant aber gründlich. Fokus auf umsetzbare Erkenntnisse. Vermeide Fehlalarme.
Schreibe den gesamten Bericht auf Deutsch."""


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


# ---------------------------------------------------------------------------
# Config management
# ---------------------------------------------------------------------------

def load_config():
    _ensure_data_dir()
    if not os.path.exists(AI_CONFIG_FILE):
        return dict(DEFAULT_CONFIG)
    try:
        with open(AI_CONFIG_FILE, "r") as f:
            cfg = json.load(f)
        merged = dict(DEFAULT_CONFIG)
        for key in DEFAULT_CONFIG:
            if key in cfg:
                if isinstance(DEFAULT_CONFIG[key], dict):
                    merged[key] = dict(DEFAULT_CONFIG[key])
                    merged[key].update(cfg[key])
                else:
                    merged[key] = cfg[key]
        return merged
    except Exception:
        return dict(DEFAULT_CONFIG)


def save_config(config):
    _ensure_data_dir()
    with _lock:
        with open(AI_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)


def load_config_masked():
    """Load config with API keys masked for frontend display."""
    cfg = load_config()
    for provider in ("openai", "anthropic", "custom"):
        key = cfg.get(provider, {}).get("api_key", "")
        if key and len(key) > 8:
            cfg[provider]["api_key"] = key[:4] + "..." + key[-4:]
    # Include both default prompts so the UI can display the right one per language
    cfg["default_system_prompt_en"] = DEFAULT_SYSTEM_PROMPT_EN
    cfg["default_system_prompt_de"] = DEFAULT_SYSTEM_PROMPT_DE
    return cfg


def save_config_unmasked(new_config):
    """Save config, preserving existing API keys if masked values are sent back."""
    existing = load_config()
    for provider in ("openai", "anthropic", "custom"):
        new_key = new_config.get(provider, {}).get("api_key", "")
        if new_key and "..." in new_key:
            # Masked key sent back – preserve existing
            new_config[provider]["api_key"] = existing.get(provider, {}).get("api_key", "")
    # Clear system_prompt if it matches a default (so language switch works)
    sp = new_config.get("system_prompt", "").strip()
    if sp == DEFAULT_SYSTEM_PROMPT_EN.strip() or sp == DEFAULT_SYSTEM_PROMPT_DE.strip():
        new_config["system_prompt"] = ""
    save_config(new_config)


def list_ollama_models(base_url=None):
    """Query available models from an Ollama instance."""
    config = load_config()
    url = (base_url or config.get("ollama", {}).get("base_url", "http://localhost:11434")).rstrip("/")
    try:
        req = urllib.request.Request(f"{url}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            models = [m.get("name", "") for m in data.get("models", [])]
            return {"success": True, "models": sorted(models)}
    except Exception as e:
        return {"success": False, "error": str(e), "models": []}


# ---------------------------------------------------------------------------
# Report history
# ---------------------------------------------------------------------------

def load_reports():
    _ensure_data_dir()
    if not os.path.exists(AI_REPORTS_FILE):
        return []
    try:
        with open(AI_REPORTS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []


def _save_reports(reports):
    _ensure_data_dir()
    with _lock:
        with open(AI_REPORTS_FILE, "w") as f:
            json.dump(reports, f, indent=2, ensure_ascii=False)


def _add_report(report):
    config = load_config()
    max_reports = config.get("max_reports", 50)
    reports = load_reports()
    reports.insert(0, report)
    if len(reports) > max_reports:
        reports = reports[:max_reports]
    _save_reports(reports)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_age(seconds):
    """Format seconds into a human-readable age string."""
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days}d {hours}h"


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def collect_host_data(host_address=None):
    """Collect ZFS data from a specific host or all hosts."""
    global _latest_data

    from app.ssh_manager import load_hosts
    from app.zfs_commands import (
        get_pools, get_pool_status, get_datasets, get_snapshots,
        get_arc_stats, get_zfs_events, get_smart_status,
        get_auto_snapshot_status, get_snapshot_ages,
    )

    all_hosts = load_hosts()

    # Filter to specific host if requested
    if host_address:
        hosts = [h for h in all_hosts if h.get("address") == host_address]
        if not hosts:
            return None
    else:
        hosts = all_hosts

    data = {
        "collected_at": tz_now().strftime("%Y-%m-%d %H:%M:%S"),
        "host_count": len(hosts),
        "hosts": [],
    }

    for host in hosts:
        host_data = {
            "name": host.get("name", ""),
            "address": host.get("address", ""),
            "pools": [],
            "datasets_summary": [],
            "snapshot_summary": {},
            "arc_stats": None,
            "events": [],
            "smart": {},
            "auto_snapshot": None,
            "errors": [],
        }

        # Pools
        try:
            pools = get_pools(host)
            host_data["pools"] = pools
            # Get scrub info from pool status
            for pool in pools:
                try:
                    status = get_pool_status(host, pool["name"])
                    if status.get("success"):
                        stdout = status.get("stdout", "")
                        for line in stdout.splitlines():
                            line = line.strip()
                            if line.startswith("scan:") or line.startswith("scrub"):
                                pool["last_scan"] = line
                                break
                except Exception:
                    pass
        except Exception as e:
            host_data["errors"].append(f"Pools: {e}")

        # Datasets (summary only)
        try:
            datasets = get_datasets(host)
            for ds in datasets:
                host_data["datasets_summary"].append({
                    "name": ds.get("name", ""),
                    "type": ds.get("type", ""),
                    "used": ds.get("used", ""),
                    "avail": ds.get("avail", ""),
                    "compress": ds.get("compress", ""),
                    "ratio": ds.get("ratio", ""),
                })
        except Exception as e:
            host_data["errors"].append(f"Datasets: {e}")

        # Snapshots — two-pass analysis:
        # Pass 1: get_snapshots() for basic counts (sorted newest-first)
        # Pass 2: get_snapshot_ages() with epoch timestamps for precise age analysis
        try:
            snapshots = get_snapshots(host)
            snap_by_dataset = {}
            auto_count = 0
            manual_count = 0
            newest = None
            oldest = None
            total_count = len(snapshots)

            for snap in snapshots:
                ds = snap.get("dataset", "unknown")
                sname = snap.get("snapshot", "")
                creation = snap.get("creation", "")

                if ds not in snap_by_dataset:
                    snap_by_dataset[ds] = {"count": 0, "auto": 0, "manual": 0,
                                           "oldest": creation, "newest": creation,
                                           "used_total": snap.get("used", "")}
                snap_by_dataset[ds]["count"] += 1
                if creation:
                    snap_by_dataset[ds]["oldest"] = creation

                is_auto = sname.startswith("zfs-auto-snap") or sname.startswith("autosnap")
                if is_auto:
                    auto_count += 1
                    snap_by_dataset[ds]["auto"] += 1
                else:
                    manual_count += 1
                    snap_by_dataset[ds]["manual"] += 1

                if creation:
                    if newest is None:
                        newest = creation
                    oldest = creation

            host_data["snapshot_summary"] = {
                "total": total_count,
                "auto": auto_count,
                "manual": manual_count,
                "oldest": oldest,
                "newest": newest,
                "per_dataset": snap_by_dataset,
            }
        except Exception as e:
            host_data["errors"].append(f"Snapshots: {e}")

        # Snapshot retention analysis with epoch timestamps
        # (per dataset, per label — like check-snapshot-age.txt)
        try:
            import time as _time
            now_epoch = int(_time.time())
            snap_ages = get_snapshot_ages(host)
            retention_cfg = {}
            if host_data.get("auto_snapshot"):
                retention_cfg = host_data["auto_snapshot"].get("retention_policy", {})

            # Max allowed age per label (seconds) before warning
            max_age = {
                "frequent": 1800,    # 30 min
                "hourly":   7200,    # 2 hours
                "daily":    172800,  # 48 hours
                "weekly":   1209600, # 14 days
                "monthly":  5400000, # ~62 days
            }

            # Analyze per dataset per label
            retention_warnings = []
            label_global = {}  # Global per-label stats
            datasets_without_labels = {}  # Datasets missing expected labels

            all_expected_labels = set(retention_cfg.keys())

            for ds, labels_data in snap_ages.items():
                present_labels = set(labels_data.keys()) - {"other"}

                # Check for missing labels on this dataset
                missing = all_expected_labels - present_labels - {"other"}
                if missing and ds in snap_by_dataset:  # Only for datasets that have any snapshots
                    for ml in missing:
                        if ml not in datasets_without_labels:
                            datasets_without_labels[ml] = []
                        datasets_without_labels[ml].append(ds)

                for label, info in labels_data.items():
                    if label == "other":
                        continue
                    count = info["count"]
                    newest_epoch = info["newest"]
                    age_sec = now_epoch - newest_epoch

                    # Global label stats
                    if label not in label_global:
                        label_global[label] = {
                            "total_snapshots": 0,
                            "dataset_count": 0,
                            "configured_keep": retention_cfg.get(label, "?"),
                            "oldest_age_sec": 0,
                            "newest_age_sec": age_sec,
                            "count_mismatches": [],
                            "stale_datasets": [],
                        }
                    lg = label_global[label]
                    lg["total_snapshots"] += count
                    lg["dataset_count"] += 1

                    if age_sec < lg["newest_age_sec"]:
                        lg["newest_age_sec"] = age_sec
                    if age_sec > lg["oldest_age_sec"]:
                        lg["oldest_age_sec"] = age_sec

                    # Check: count vs configured --keep
                    configured = retention_cfg.get(label)
                    if configured and count != configured:
                        lg["count_mismatches"].append({
                            "dataset": ds,
                            "actual": count,
                            "configured": configured,
                        })

                    # Check: newest snapshot too old?
                    threshold = max_age.get(label, 5400000)
                    if age_sec > threshold:
                        age_human = _format_age(age_sec)
                        max_human = _format_age(threshold)
                        lg["stale_datasets"].append({
                            "dataset": ds,
                            "age": age_human,
                            "threshold": max_human,
                        })

            # Build compact summary for AI
            for label, lg in label_global.items():
                lg["per_dataset_avg"] = round(lg["total_snapshots"] / max(lg["dataset_count"], 1))
                lg["newest_age_human"] = _format_age(lg["newest_age_sec"])
                # Limit stale/mismatch lists to avoid token bloat
                if len(lg["stale_datasets"]) > 5:
                    total_stale = len(lg["stale_datasets"])
                    lg["stale_datasets"] = lg["stale_datasets"][:5]
                    lg["stale_datasets"].append({"note": f"... and {total_stale - 5} more"})
                if len(lg["count_mismatches"]) > 5:
                    total_mm = len(lg["count_mismatches"])
                    lg["count_mismatches"] = lg["count_mismatches"][:5]
                    lg["count_mismatches"].append({"note": f"... and {total_mm - 5} more"})

            # Missing labels summary (truncated)
            missing_labels_summary = {}
            for label, ds_list in datasets_without_labels.items():
                missing_labels_summary[label] = {
                    "count": len(ds_list),
                    "examples": ds_list[:5],
                }

            host_data["retention_analysis"] = {
                "per_label": label_global,
                "missing_labels": missing_labels_summary,
                "datasets_analyzed": len(snap_ages),
            }
        except Exception as e:
            host_data["errors"].append(f"Retention analysis: {e}")

        # ARC Stats
        try:
            host_data["arc_stats"] = get_arc_stats(host)
        except Exception as e:
            host_data["errors"].append(f"ARC: {e}")

        # ZFS Events (last 10 only)
        try:
            events = get_zfs_events(host)
            if isinstance(events, dict):
                raw = events.get("stdout", "")
                host_data["events"] = raw.strip().splitlines()[-10:] if raw else []
            elif isinstance(events, list):
                host_data["events"] = events[-10:]
        except Exception as e:
            host_data["errors"].append(f"Events: {e}")

        # SMART
        try:
            host_data["smart"] = get_smart_status(host)
        except Exception as e:
            host_data["errors"].append(f"SMART: {e}")

        # Auto-snapshot status
        try:
            host_data["auto_snapshot"] = get_auto_snapshot_status(host)
        except Exception as e:
            host_data["errors"].append(f"Auto-snapshot: {e}")

        data["hosts"].append(host_data)

    _latest_data = data
    return data


# ---------------------------------------------------------------------------
# LLM API callers
# ---------------------------------------------------------------------------

def _call_openai(provider_cfg, messages, timeout=120):
    """Call OpenAI-compatible API (also used for custom providers)."""
    base_url = provider_cfg.get("base_url", "https://api.openai.com/v1").rstrip("/")
    url = f"{base_url}/chat/completions"
    body = {
        "model": provider_cfg.get("model", "gpt-4o-mini"),
        "messages": messages,
        "max_tokens": 4096,
        "temperature": 0.3,
    }
    headers = {"Content-Type": "application/json"}
    api_key = provider_cfg.get("api_key", "")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"success": True, "content": content, "usage": result.get("usage", {})}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        return {"success": False, "error": f"HTTP {e.code}: {body_text[:500]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _call_anthropic(provider_cfg, messages, timeout=120):
    """Call Anthropic Messages API."""
    url = "https://api.anthropic.com/v1/messages"
    api_key = provider_cfg.get("api_key", "")

    # Convert from OpenAI message format: extract system message
    system_text = ""
    user_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_text = msg["content"]
        else:
            user_messages.append(msg)

    body = {
        "model": provider_cfg.get("model", "claude-sonnet-4-20250514"),
        "max_tokens": 4096,
        "messages": user_messages,
    }
    if system_text:
        body["system"] = system_text

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            content = ""
            for block in result.get("content", []):
                if block.get("type") == "text":
                    content += block.get("text", "")
            return {"success": True, "content": content, "usage": result.get("usage", {})}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        return {"success": False, "error": f"HTTP {e.code}: {body_text[:500]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def _call_ollama(provider_cfg, messages, timeout=300):
    """Call Ollama chat API."""
    base_url = provider_cfg.get("base_url", "http://localhost:11434").rstrip("/")
    url = f"{base_url}/api/chat"
    body = {
        "model": provider_cfg.get("model", "llama3"),
        "messages": messages,
        "stream": False,
    }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read())
            content = result.get("message", {}).get("content", "")
            return {"success": True, "content": content, "usage": {}}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        return {"success": False, "error": f"HTTP {e.code}: {body_text[:500]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def call_llm(messages):
    """Dispatch to the configured LLM provider."""
    config = load_config()
    provider = config.get("provider", "openai")

    if provider == "anthropic":
        return _call_anthropic(config.get("anthropic", {}), messages)
    elif provider == "ollama":
        return _call_ollama(config.get("ollama", {}), messages)
    elif provider == "custom":
        return _call_openai(config.get("custom", {}), messages)
    else:  # openai
        return _call_openai(config.get("openai", {}), messages)


# ---------------------------------------------------------------------------
# Test connection
# ---------------------------------------------------------------------------

def test_connection():
    """Send a minimal test message to verify LLM connectivity."""
    config = load_config()
    provider = config.get("provider", "openai")

    messages = [
        {"role": "system", "content": "Respond with exactly: OK"},
        {"role": "user", "content": "Test connection. Reply with OK."},
    ]

    # Use a shorter timeout for test (30s instead of 300s)
    if provider == "anthropic":
        result = _call_anthropic(config.get("anthropic", {}), messages, timeout=30)
    elif provider == "ollama":
        result = _call_ollama(config.get("ollama", {}), messages, timeout=30)
    elif provider == "custom":
        result = _call_openai(config.get("custom", {}), messages, timeout=30)
    else:
        result = _call_openai(config.get("openai", {}), messages, timeout=30)

    if result.get("success"):
        return {"success": True, "message": result.get("content", "OK")[:200]}
    return {"success": False, "message": result.get("error", "Unknown error")}


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_report(host_address=None, lang_override=None):
    """Collect ZFS data and generate an AI analysis report."""
    config = load_config()
    provider = config.get("provider", "openai")
    provider_cfg = config.get(provider, {})
    model = provider_cfg.get("model", "unknown")

    try:
        data = collect_host_data(host_address)
    except Exception as e:
        return {"success": False, "error": f"Data collection failed: {e}"}

    if data is None or not data.get("hosts"):
        return {"success": False, "error": "Host not found or no hosts configured"}

    # Build system prompt based on language (direct parameter overrides config)
    lang = lang_override or config.get("report_language", "en")
    custom_prompt = config.get("system_prompt", "").strip()

    if custom_prompt:
        system_prompt = custom_prompt
        if lang == "de":
            system_prompt += "\n\nWICHTIG: Schreibe den gesamten Bericht auf Deutsch."
    else:
        system_prompt = DEFAULT_SYSTEM_PROMPT_DE if lang == "de" else DEFAULT_SYSTEM_PROMPT_EN

    # Build user message with collected data
    data_json = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    # Truncate if too large (keep under ~30k chars for token limits)
    if len(data_json) > 30000:
        data_json = data_json[:30000] + "\n... (truncated)"

    if lang == "de":
        user_msg = f"Hier sind die aktuellen ZFS-Infrastrukturdaten. Bitte analysiere sie und erstelle einen Statusbericht auf Deutsch.\n\n```json\n{data_json}\n```"
    else:
        user_msg = f"Here is the current ZFS infrastructure data. Please analyze it and generate a status report.\n\n```json\n{data_json}\n```"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    result = call_llm(messages)
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "LLM call failed")}

    host_names = [h.get("name", h.get("address", "?")) for h in data.get("hosts", [])]
    host_addresses = [h.get("address", "") for h in data.get("hosts", [])]
    report = {
        "id": str(uuid.uuid4())[:8],
        "timestamp": tz_now().strftime("%Y-%m-%d %H:%M:%S"),
        "provider": provider,
        "model": model,
        "content": result.get("content", ""),
        "host_count": len(data.get("hosts", [])),
        "host_names": host_names,
        "host_addresses": host_addresses,
        "usage": result.get("usage", {}),
    }

    _add_report(report)

    # Send via notification channels if enabled
    if config.get("notify_on_report"):
        try:
            from app.notifications import send_notification
            content_text = result.get("content", "")
            # Send the full report (truncated to 4000 chars for message limits)
            report_text = content_text[:4000]
            if len(content_text) > 4000:
                report_text += "\n\n... (truncated)"
            title = "KI-Bericht" if lang == "de" else "AI Report"
            send_notification(
                "ai_report",
                title,
                f"Provider: {provider} ({model})\n\n{report_text}",
            )
        except Exception as e:
            log.warning("Failed to send report notification: %s", e)

    return {"success": True, "report": report}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def chat(question, host_address=None, lang_override=None):
    """Ask a follow-up question using the latest collected data."""
    global _latest_data

    config = load_config()
    lang = lang_override or config.get("report_language", "en")
    custom_prompt = config.get("system_prompt", "").strip()

    if custom_prompt:
        system_prompt = custom_prompt
    else:
        system_prompt = DEFAULT_SYSTEM_PROMPT_DE if lang == "de" else DEFAULT_SYSTEM_PROMPT_EN

    if lang == "de":
        system_prompt += "\n\nDer Benutzer stellt eine Nachfrage zu seiner ZFS-Infrastruktur. Antworte basierend auf den bereitgestellten Daten. Antworte auf Deutsch."
    else:
        system_prompt += "\n\nThe user is asking a follow-up question about their ZFS infrastructure. Answer based on the data provided."

    # Use cached data or collect fresh for the specified host
    if _latest_data is None:
        try:
            collect_host_data(host_address)
        except Exception as e:
            return {"success": False, "error": f"Data collection failed: {e}"}

    data_json = json.dumps(_latest_data, indent=2, ensure_ascii=False, default=str)
    if len(data_json) > 25000:
        data_json = data_json[:25000] + "\n... (truncated)"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"ZFS infrastructure data:\n```json\n{data_json}\n```\n\nQuestion: {question}"},
    ]

    result = call_llm(messages)
    if not result.get("success"):
        return {"success": False, "error": result.get("error", "LLM call failed")}

    return {"success": True, "answer": result.get("content", "")}


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def _scheduler_loop():
    """Background thread for scheduled report generation.

    Always runs regardless of whether schedule is enabled, so that enabling
    the schedule at runtime takes effect without restarting.  Uses a
    last-run date string to avoid the fragile 2-minute window approach.
    """
    global _last_run_key
    log.info("AI report scheduler started")

    while not _scheduler_stop.is_set():
        try:
            config = load_config()
            schedule = config.get("schedule", {})
            if not schedule.get("enabled"):
                _scheduler_stop.wait(30)
                continue

            now = tz_now()
            target_hour = schedule.get("hour", 6)
            interval = schedule.get("interval", "daily")

            # Build a run-key that is unique per scheduled period
            if interval == "weekly":
                run_key = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]}"
            else:
                run_key = now.strftime("%Y-%m-%d")

            should_run = False
            if now.hour >= target_hour and _last_run_key != run_key:
                if interval == "daily":
                    should_run = True
                elif interval == "weekly" and now.weekday() == schedule.get("weekday", 0):
                    should_run = True

            if should_run:
                log.info("Scheduled AI report generation triggered (key=%s, hour=%s)", run_key, target_hour)
                _last_run_key = run_key
                try:
                    generate_report()
                except Exception as e:
                    log.error("Scheduled report generation failed: %s", e)

            _scheduler_stop.wait(30)
        except Exception as e:
            log.error("Scheduler error: %s", e)
            _scheduler_stop.wait(30)

    log.info("AI report scheduler stopped")


def start_scheduler():
    """Start the scheduler thread (always runs, checks config each cycle)."""
    global _scheduler_thread, _last_run_key
    # Only start a new thread if one isn't already running
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    # Set last_run_key to today so we don't immediately trigger on startup
    if _last_run_key is None:
        _last_run_key = tz_now().strftime("%Y-%m-%d")
    _scheduler_stop.clear()
    _scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True)
    _scheduler_thread.start()


def stop_scheduler():
    """Stop the scheduler thread."""
    global _scheduler_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        _scheduler_stop.set()
        _scheduler_thread.join(timeout=5)
    _scheduler_thread = None
