"""AI-powered ZFS report generation supporting OpenAI, Anthropic, Ollama, and custom providers."""

import datetime
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
_scheduler_start_lock = threading.Lock()
_scheduler_thread = None
_scheduler_stop = threading.Event()
_last_run_key = None  # Legacy: single-schedule last-run (kept for backwards compat)
_last_run_keys = {}   # Per-schedule last-run: {schedule_key: run_key}

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
    "schedules": {},
    "report_language": "en",
    "notify_on_report": False,
    "attach_pdf": True,
    "system_prompt": "",
    "max_reports": 50,
}

DEFAULT_SYSTEM_PROMPT_EN = """You are a ZFS storage expert analyzing a Proxmox VE environment.
Analyze the provided ZFS data and generate a comprehensive status report.

Structure your report with EXACTLY these seven sections, in this order, with
these exact titles and NO additional sections (fold fragmentation, ARC, events
etc. into the relevant section below — do not invent an 8th section). Each
section heading MUST be a level-2 markdown heading that BEGINS with a status
tag in square brackets, like this:

## [OK] 1. Overall Health Summary
## [OK] 2. Storage Capacity
## [OK] 3. Scrub Status
## [OK] 4. Snapshot Analysis
## [OK] 5. SMART Disk Health
## [OK] 6. Anomalies & Warnings
## [OK] 7. Recommendations

The status tag is exactly one of [OK], [WARN], [CRIT] and reflects THIS section
only:
  [OK]   = nothing actionable in this area.
  [WARN] = something to address soon (capacity > 80 %, overdue scrub < 60 days,
           stale auto-snapshots, only low-priority recommendations exist).
  [CRIT] = immediate attention (pool DEGRADED/FAULTED, capacity > 95 %, read/
           write/checksum errors, SMART pre-fail, scrub overdue > 60 days,
           retention gaps that break the policy).
Be conservative: a healthy area is [OK]. Tag the Recommendations section [OK]
when its only items are optional/low-priority; use [WARN]/[CRIT] there only if
you are genuinely recommending an urgent action. The overall verdict is the
WORST section tag, so do not tag a section [CRIT]/[WARN] unless that section
really contains a critical/warning finding.

IMPORTANT — Proxmox-specific rules you MUST follow:
- **Snapshot freshness**: The data includes per-dataset snapshot details (per_dataset). To determine the most recent snapshot, you MUST check the "newest" date across ALL child datasets (e.g. rpool/data/subvol-*, rpool/ROOT/pve-1, tank/data/vm-*), not just the root pool dataset. Parent dataset snapshots often show 0B used — this is normal because actual data lives in child datasets.
- **zfs-auto-snapshot on Proxmox**: On Proxmox VE, zfs-auto-snapshot is managed via CRON JOBS (found in /etc/cron.{frequent,hourly,daily,weekly,monthly}/zfs-auto-snapshot), NOT via systemd. There is no systemd service for zfs-auto-snapshot. NEVER recommend "systemctl status zfs-auto-snapshot" or similar systemd commands. If snapshot scheduling needs to be checked, recommend: "ls /etc/cron.*/zfs-auto-snapshot" or "cat /etc/cron.daily/zfs-auto-snapshot".
- **Snapshot levels**: zfs-auto-snapshot typically creates 5 levels: frequent (every 15 min), hourly, daily, weekly, monthly. Each level has its own retention policy.
- **0B used snapshots**: Snapshots on parent datasets (e.g. rpool@zfs-auto-snap_daily-...) often show 0B used. This is completely normal and does NOT indicate a problem — the actual snapshot data is in the child datasets.
- **Retention analysis**: The data includes a pre-computed "retention_analysis" section per host with:
  - "per_label": For each label (frequent/hourly/daily/weekly/monthly/backup-zfs/bashclub-zfs): total_snapshots, dataset_count, configured_keep (from cron --keep=N), per_dataset_avg, newest_age_human, count_mismatches (datasets where actual != configured), stale_datasets (datasets where newest snapshot exceeds max age), gaps (holes in the snapshot chain where time between consecutive snapshots exceeds 1.5x the expected interval).
  - "missing_labels": Datasets that SHOULD have a label (because it's configured in cron) but DON'T have any snapshots for it.
  - "manual_snapshots": Non-auto snapshots (irregular/manual) with name, dataset, and age. These may indicate forgotten test snapshots that waste space.
  Use this data directly — do NOT guess or invent retention values. Report count_mismatches, stale_datasets, and gaps as findings. Gaps are especially critical: they represent time periods where no rollback is possible.
  Age thresholds: frequent > 1h, hourly > 2h, daily > 25h, weekly > 8d, monthly > 32d.
- **Snapshot integrity**: ZFS scrub already validates ALL data including snapshots via checksums. A successful scrub with 0 errors confirms snapshot chain integrity. Do NOT recommend separate checksum verification — scrub covers this.
- **ZDB deep diagnostics**: If a pool is DEGRADED, FAULTED, or has data errors, the data may include a "zdb_diagnostics" section with low-level pool internals (block stats, vdev tree, disk labels). Use this data to provide detailed root-cause analysis: which vdev failed, txg state, block allocation issues. If zdb_diagnostics is absent, the pools are healthy — do NOT recommend running zdb manually.
- **Fragmentation on SSD/NVMe is NORMAL**: ZFS fragmentation percentage reflects free-space fragmentation, not file fragmentation. On SSD/NVMe pools (which is the vast majority of Proxmox installations), high fragmentation (even 50–90%) has NO measurable performance impact because SSDs have no seek time. Do NOT flag fragmentation as an issue unless the pool is demonstrably on spinning rust (HDDs) AND fragmentation exceeds 50%. Never recommend "defragmentation" — ZFS cannot defragment in-place; the only remedy is send/recv to a fresh pool, which is almost always unnecessary. When in doubt, assume SSD/NVMe and ignore fragmentation entirely.
- **SMART status "N/A", "Unknown" or "smartctl fehlt"**: Only "FAILED" is a fault. "PASSED" is healthy. "N/A"/"Unknown" mean SMART data is unavailable for that drive (virtual disk, controller/HBA that needs a device type smartctl couldn't reach, or a passthrough layer) and "smartctl fehlt" means smartmontools is not installed on the host. Treat all of these as INFORMATIONAL, not a defect — do NOT tag section 5 [WARN] just because some drives show no data. Mention them briefly as "no SMART data available" and, for "smartctl fehlt", suggest installing smartmontools.

Be concise but thorough. Focus on actionable insights. Avoid false positives.
Write the entire report in English.

MANDATORY — After the seven sections, end the report with EXACTLY this
machine-readable verdict block (own paragraph, no markdown formatting, no
extra text after it). It MUST equal the worst section tag above:

[VERDICT: ok|warn|crit]
[CRITICAL_FINDINGS: <integer>]
[WARNINGS: <integer>]

CRITICAL_FINDINGS = number of sections you tagged [CRIT].
WARNINGS         = number of sections you tagged [WARN].

An overdue scrub of ~1 week is NOT critical. SSD fragmentation is NEVER an
issue. If every section is [OK], the verdict is ok with both counts 0. Be
conservative: choose the LOWEST verdict that honestly fits the section tags."""

DEFAULT_SYSTEM_PROMPT_DE = """Du bist ein ZFS-Speicherexperte und analysierst eine Proxmox VE Umgebung.
Analysiere die bereitgestellten ZFS-Daten und erstelle einen umfassenden Statusbericht.

Strukturiere den Bericht mit GENAU diesen sieben Abschnitten, in dieser
Reihenfolge, mit genau diesen Titeln und KEINEN zusätzlichen Abschnitten
(Fragmentierung, ARC, Events usw. in den passenden Abschnitt unten einbauen —
erfinde keinen 8. Abschnitt). Jede Abschnitts-Überschrift MUSS eine Level-2-
Markdown-Überschrift sein, die mit einem Status-Tag in eckigen Klammern
BEGINNT, so:

## [OK] 1. Gesamtstatus
## [OK] 2. Speicherkapazität
## [OK] 3. Scrub-Status
## [OK] 4. Snapshot-Analyse
## [OK] 5. SMART-Festplattenzustand
## [OK] 6. Anomalien & Warnungen
## [OK] 7. Empfehlungen

Das Status-Tag ist genau eines von [OK], [WARN], [CRIT] und bezieht sich NUR
auf diesen Abschnitt:
  [OK]   = in diesem Bereich kein Handlungsbedarf.
  [WARN] = demnächst angehen (Belegung > 80 %, überfälliger Scrub < 60 Tage,
           veraltete Auto-Snapshots, nur niedrigpriorisierte Empfehlungen).
  [CRIT] = sofortige Aufmerksamkeit (Pool DEGRADED/FAULTED, Belegung > 95 %,
           Read-/Write-/Checksum-Fehler, SMART Pre-Fail, Scrub überfällig
           > 60 Tage, Retention-Lücken, die die Policy brechen).
Sei konservativ: ein gesunder Bereich ist [OK]. Den Empfehlungs-Abschnitt nur
dann [WARN]/[CRIT] taggen, wenn du wirklich eine dringende Maßnahme empfiehlst;
sonst [OK]. Das Gesamt-Verdict ist das SCHLECHTESTE Abschnitts-Tag — tagge
einen Abschnitt also nur [CRIT]/[WARN], wenn er wirklich einen kritischen/
Warn-Befund enthält.

WICHTIG — Proxmox-spezifische Regeln, die du UNBEDINGT beachten musst:
- **Snapshot-Aktualität**: Die Daten enthalten Snapshot-Details pro Dataset (per_dataset). Um den neuesten Snapshot zu bestimmen, MUSST du das "newest"-Datum über ALLE Child-Datasets prüfen (z.B. rpool/data/subvol-*, rpool/ROOT/pve-1, tank/data/vm-*), nicht nur das Root-Pool-Dataset. Snapshots auf Parent-Datasets zeigen oft 0B used — das ist normal, da die eigentlichen Daten in Child-Datasets liegen.
- **zfs-auto-snapshot auf Proxmox**: Auf Proxmox VE wird zfs-auto-snapshot über CRON-JOBS verwaltet (in /etc/cron.{frequent,hourly,daily,weekly,monthly}/zfs-auto-snapshot), NICHT über systemd. Es gibt keinen systemd-Service für zfs-auto-snapshot. Empfehle NIEMALS "systemctl status zfs-auto-snapshot" oder ähnliche systemd-Befehle. Zum Prüfen der Snapshot-Planung empfehle: "ls /etc/cron.*/zfs-auto-snapshot" oder "cat /etc/cron.daily/zfs-auto-snapshot".
- **Snapshot-Ebenen**: zfs-auto-snapshot erstellt typischerweise 5 Ebenen: frequent (alle 15 Min), hourly, daily, weekly, monthly. Jede Ebene hat ihre eigene Aufbewahrungsrichtlinie.
- **0B-Snapshots**: Snapshots auf Parent-Datasets (z.B. rpool@zfs-auto-snap_daily-...) zeigen oft 0B used. Das ist völlig normal und weist NICHT auf ein Problem hin — die tatsächlichen Snapshot-Daten befinden sich in den Child-Datasets.
- **Aufbewahrungsanalyse (Retention)**: Die Daten enthalten eine vorberechnete "retention_analysis" pro Host mit:
  - "per_label": Für jedes Label (frequent/hourly/daily/weekly/monthly/backup-zfs/bashclub-zfs): total_snapshots, dataset_count, configured_keep (aus Cron --keep=N), per_dataset_avg, newest_age_human, count_mismatches (Datasets wo IST != SOLL), stale_datasets (Datasets wo neuester Snapshot das Max-Alter überschreitet), gaps (Lücken in der Snapshot-Kette wo der Abstand zwischen aufeinanderfolgenden Snapshots 1.5x das erwartete Intervall überschreitet).
  - "missing_labels": Datasets die ein Label haben SOLLTEN (weil in Cron konfiguriert) aber KEINE Snapshots dafür haben.
  - "manual_snapshots": Nicht-automatische Snapshots (manuell/irregulär) mit Name, Dataset und Alter. Diese können vergessene Test-Snapshots sein die Speicher verschwenden.
  Verwende diese Daten direkt — erfinde oder rate KEINE Retention-Werte. Melde count_mismatches, stale_datasets und gaps als Befunde. Gaps sind besonders kritisch: sie stellen Zeiträume dar in denen kein Rollback möglich ist.
  Alters-Schwellwerte: frequent > 1h, hourly > 2h, daily > 25h, weekly > 8d, monthly > 32d.
- **Snapshot-Integrität**: ZFS Scrub validiert bereits ALLE Daten inklusive Snapshots per Checksumme. Ein erfolgreicher Scrub mit 0 Fehlern bestätigt die Integrität der Snapshot-Kette. Empfehle KEINE separate Checksummen-Prüfung — Scrub deckt dies ab.
- **ZDB-Tiefendiagnose**: Falls ein Pool DEGRADED, FAULTED oder Datenfehler hat, können die Daten eine "zdb_diagnostics"-Sektion enthalten mit Low-Level Pool-Internals (Block-Statistiken, vdev-Baum, Disk-Labels). Nutze diese Daten für eine detaillierte Ursachenanalyse: welches vdev ausgefallen ist, txg-Status, Block-Allokationsprobleme. Falls zdb_diagnostics fehlt, sind die Pools gesund — empfehle NICHT, zdb manuell auszuführen.
- **Fragmentierung auf SSD/NVMe ist NORMAL**: Der ZFS-Fragmentierungs-Prozentsatz bezieht sich auf die Fragmentierung des freien Speichers, nicht auf Datei-Fragmentierung. Auf SSD/NVMe-Pools (der absolute Großteil aller Proxmox-Installationen) hat hohe Fragmentierung (auch 50–90 %) KEINEN messbaren Performance-Einfluss, da SSDs keine Suchzeit haben. Melde Fragmentierung NICHT als Problem, außer der Pool läuft nachweislich auf rotierenden HDDs UND die Fragmentierung übersteigt 50 %. Empfehle NIEMALS "Defragmentierung" — ZFS kann nicht in-place defragmentieren; die einzige Abhilfe wäre send/recv auf einen neuen Pool, was fast nie nötig ist. Im Zweifel: SSD/NVMe annehmen und Fragmentierung ignorieren.
- **SMART-Status "N/A", "Unknown" oder "smartctl fehlt"**: Nur "FAILED" ist ein Defekt. "PASSED" ist gesund. "N/A"/"Unknown" bedeuten, dass für dieses Laufwerk keine SMART-Daten verfügbar sind (virtuelle Disk, Controller/HBA, den smartctl ohne passenden Gerätetyp nicht erreicht, oder eine Passthrough-Schicht); "smartctl fehlt" heißt, smartmontools ist auf dem Host nicht installiert. Behandle all das als INFORMATIV, nicht als Mangel — tagge Abschnitt 5 NICHT [WARN], nur weil einzelne Laufwerke keine Daten liefern. Erwähne sie kurz als "keine SMART-Daten verfügbar" und empfiehl bei "smartctl fehlt" die Installation von smartmontools.

Sei prägnant aber gründlich. Fokus auf umsetzbare Erkenntnisse. Vermeide Fehlalarme.
Schreibe den gesamten Bericht auf Deutsch.

PFLICHT — Beende den Bericht NACH den sieben Abschnitten mit GENAU diesem
maschinenlesbaren Verdict-Block (eigener Absatz, kein Markdown, kein Text
danach). Er MUSS dem schlechtesten Abschnitts-Tag oben entsprechen:

[VERDICT: ok|warn|crit]
[CRITICAL_FINDINGS: <ganze Zahl>]
[WARNINGS: <ganze Zahl>]

CRITICAL_FINDINGS = Anzahl der mit [CRIT] getaggten Abschnitte.
WARNINGS         = Anzahl der mit [WARN] getaggten Abschnitte.

Ein vor ~1 Woche fälliger Scrub ist NICHT kritisch. SSD-Fragmentierung ist NIE
ein Problem. Wenn jeder Abschnitt [OK] ist, ist das Verdict ok mit beiden
Zählern 0. Sei konservativ: wähle das NIEDRIGSTE Verdict, das ehrlich zu den
Abschnitts-Tags passt."""


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


# format_age is now in snapshot_analysis.py but keep alias for backwards compat
from app.snapshot_analysis import format_age as _format_age, analyze_snapshots, truncate_for_ai


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
        get_auto_snapshot_status, get_snapshot_ages, get_zdb_analysis,
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
            # Get scrub info from pool status + detect problems for zdb trigger
            degraded_pools = []
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
                        # Detect pool problems: DEGRADED, FAULTED, errors
                        health = pool.get("health", "").upper()
                        has_errors = "errors:" in stdout and "No known data errors" not in stdout
                        if health in ("DEGRADED", "FAULTED", "UNAVAIL", "SUSPENDED") or has_errors:
                            degraded_pools.append(pool["name"])
                except Exception:
                    pass

            # ZDB deep analysis — only for pools with problems
            if degraded_pools:
                zdb_results = {}
                for pool_name in degraded_pools:
                    try:
                        zdb_results[pool_name] = get_zdb_analysis(host, pool_name)
                    except Exception as e:
                        zdb_results[pool_name] = {"error": str(e)}
                host_data["zdb_diagnostics"] = zdb_results
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
        # (per dataset, per label — inspired by check-snapshot-age.txt)
        try:
            snap_age_data = get_snapshot_ages(host)
            retention_cfg = {}
            if host_data.get("auto_snapshot"):
                retention_cfg = host_data["auto_snapshot"].get("retention_policy", {})

            from app.zfs_commands import (get_autosnap_disabled_datasets,
                                          get_dataset_creations)
            analysis = analyze_snapshots(
                snap_age_data, retention_cfg,
                autosnap_disabled=get_autosnap_disabled_datasets(host),
                dataset_creation=get_dataset_creations(host))
            host_data["retention_analysis"] = truncate_for_ai(analysis)
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

# Regex set that picks out the trailing verdict block our system prompts
# require the LLM to emit. We tolerate ``**[VERDICT: ...]**``, code-fenced
# variants and stray whitespace so a non-pedantic model still gets parsed.
_VERDICT_BLOCK_RE = re.compile(
    r"""(?ix)               # case-insensitive, verbose
    (?:\*{0,2}|`{0,3})      # optional bold/code wrapper
    \[\s*VERDICT\s*:\s*(?P<verdict>ok|warn|crit)\s*\]
    (?:\*{0,2}|`{0,3})
    \s*
    (?:\*{0,2}|`{0,3})
    \[\s*CRITICAL_FINDINGS\s*:\s*(?P<crit>\d+)\s*\]
    (?:\*{0,2}|`{0,3})
    \s*
    (?:\*{0,2}|`{0,3})
    \[\s*WARNINGS\s*:\s*(?P<warn>\d+)\s*\]
    (?:\*{0,2}|`{0,3})
    """,
)
# Fallback: standalone verdict line(s) when the LLM split the three keys.
_VERDICT_LINE_RE = re.compile(
    r"(?im)^\s*(?:\*{0,2}|`{0,3})\[\s*(?:VERDICT|CRITICAL_FINDINGS|WARNINGS)\s*:[^\]]*\]\s*(?:\*{0,2}|`{0,3})\s*$"
)


def _extract_and_strip_verdict_block(content: str):
    """Return ``(cleaned_content, {verdict, critical_findings, warnings})``.

    Pulls the structured verdict the system prompt asks the LLM to emit at
    the end of the report, strips it (and the surrounding heading like
    "## Verdict-Block:" the LLM sometimes writes), and returns the rest as
    the visible report text. If the block is missing or malformed, the
    metadata dict is empty and the original content is returned unchanged
    -- the notification side then falls back to its heuristic verdict.
    """
    if not content:
        return content or "", {}
    meta = {}
    # 1) Try the compact "all three keys near each other" pattern first.
    m = _VERDICT_BLOCK_RE.search(content)
    if m:
        meta = {
            "verdict": m.group("verdict").lower(),
            "critical_findings": int(m.group("crit")),
            "warnings": int(m.group("warn")),
        }
    # 2) Strip every standalone verdict / critical_findings / warnings
    #    line individually so leftover stragglers don't show up.
    cleaned = _VERDICT_LINE_RE.sub("", content)
    # 3) Remove a now-empty trailing heading like "## Verdict" / "**Status**"
    #    that introduced the block.
    heading_patterns = [
        r"(?im)^\s*#{1,6}\s*(verdict|status|machine[- ]readable[- ]?status)[:\s]*$",
        r"(?im)^\s*\*{1,3}\s*(verdict|status|machine[- ]readable[- ]?status)\s*\*{1,3}\s*:?\s*$",
    ]
    for pat in heading_patterns:
        cleaned = re.sub(pat, "", cleaned)
    # 4) Collapse runs of >2 blank lines into exactly one blank line so the
    #    PDF doesn't end on three empty paragraphs.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).rstrip() + "\n"
    return cleaned, meta


# Maps the fixed 7-section layout to a status computed from FACTS (not LLM
# prose), keyed by the section's leading number. Smaller LLMs (e.g.
# glm-5.2:cloud via Ollama) routinely ignore the "## [OK] N. Title" tag
# instruction, so we can't rely on the model emitting the tags. Instead we
# derive each section's status from the collected data and inject the tag
# ourselves -- model-independent, and it can't contradict the facts.
_STATUS_ORDER = {"ok": 0, "warn": 1, "crit": 2}


def _pct(s):
    try:
        return float(str(s).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def _compute_section_statuses(data):
    """Return ``(status_by_section_number, overall)`` from collected facts.

    Sections: 1 Overall, 2 Capacity, 3 Scrub, 4 Snapshots, 5 SMART,
    6 Anomalies, 7 Recommendations. Conservative: a section is ``ok`` unless
    a fact clearly pushes it to warn/crit, so a healthy host stays green.
    """
    cap = scrub = snap = smart = anom = "ok"

    def worsen(cur, new):
        return new if _STATUS_ORDER[new] > _STATUS_ORDER[cur] else cur

    for h in (data.get("hosts") or []):
        for p in (h.get("pools") or []):
            c = _pct(p.get("cap"))
            if c is not None:
                if c >= 95:
                    cap = worsen(cap, "crit")
                elif c >= 80:
                    cap = worsen(cap, "warn")
            health = str(p.get("health", "")).upper()
            if health in ("DEGRADED", "FAULTED", "UNAVAIL", "SUSPENDED", "REMOVED"):
                anom = worsen(anom, "crit")
            ls = str(p.get("last_scan", "")).lower()
            if "none requested" in ls:
                scrub = worsen(scrub, "warn")

        smart_data = h.get("smart") or {}
        for pool_disks in (smart_data.get("pools") or {}).values():
            for d in (pool_disks or []):
                st = str(d.get("status", "")).upper()
                if "FAIL" in st:
                    smart = worsen(smart, "crit")
                # PASSED -> ok; N/A / Unknown / "smartctl fehlt" are informational
                # (missing SMART data, not a fault) and must not raise a warning.

        ra = h.get("retention_analysis") or {}
        snap_issue = False
        for lg in (ra.get("per_label") or {}).values():
            if lg.get("stale_datasets") or lg.get("count_mismatches") or lg.get("gaps"):
                snap_issue = True
        if ra.get("missing_labels"):
            snap_issue = True
        if snap_issue:
            snap = worsen(snap, "warn")

        if h.get("errors"):
            anom = worsen(anom, "warn")

    statuses = {2: cap, 3: scrub, 4: snap, 5: smart, 6: anom, 7: "ok"}
    overall = "ok"
    for k in (2, 3, 4, 5, 6):
        overall = worsen(overall, statuses[k])
    statuses[1] = overall
    return statuses, overall


# Heading line with a leading section number, optionally already carrying a
# status tag (which we override with the fact-based one).
_HEADING_NUM_RE = re.compile(
    r"^(#{1,4})\s+(?:\*{0,2}\s*)?(?:\[\s*(?:OK|WARN|WARNING|CRIT|CRITICAL)\s*\]\s*)?(\d+)\.\s+(.*)$",
    re.IGNORECASE,
)
_TAG_FOR_STATUS = {"ok": "OK", "warn": "WARN", "crit": "CRIT"}


def _inject_section_tags(content, statuses):
    """Rewrite numbered section headings to carry the fact-based status tag,
    e.g. ``## 1. Gesamtstatus`` -> ``## [OK] 1. Gesamtstatus``. Any existing
    tag the LLM emitted is replaced so the marker always matches the facts."""
    if not content:
        return content or ""
    out = []
    for line in content.split("\n"):
        m = _HEADING_NUM_RE.match(line.strip())
        if m:
            hashes, num, title = m.group(1), int(m.group(2)), m.group(3)
            st = statuses.get(num)
            if st:
                out.append(f"{hashes} [{_TAG_FOR_STATUS[st]}] {num}. {title}")
                continue
        out.append(line)
    return "\n".join(out)


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

    # Pull the structured verdict block out of the raw LLM response, store
    # it as first-class fields on the report, and strip it from the visible
    # content so the PDF / UI doesn't show "[VERDICT: warn] [CRITICAL_…]" as
    # raw text at the bottom. The notification path then receives the
    # already-parsed verdict instead of re-parsing it from a stripped body.
    raw_content = result.get("content", "")
    cleaned_content, verdict_meta = _extract_and_strip_verdict_block(raw_content)

    # Section status + overall verdict are computed from FACTS (collected
    # data), not the LLM prose, because smaller models routinely ignore the
    # "## [OK] N. Title" tag instruction -> no colored markers and an
    # inconsistent verdict (a green report shipping a "warning" email). We
    # derive each section's status from the data and inject the tag into the
    # headings ourselves, so the markers always render and always match the
    # facts. The LLM's own tags / [VERDICT] block are ignored for the
    # verdict but kept as a fallback if fact computation yields nothing.
    section_status_map, fact_overall = _compute_section_statuses(data)
    cleaned_content = _inject_section_tags(cleaned_content, section_status_map)

    crit_count = sum(1 for k in (2, 3, 4, 5, 6) if section_status_map.get(k) == "crit")
    warn_count = sum(1 for k in (2, 3, 4, 5, 6) if section_status_map.get(k) == "warn")
    verdict = fact_overall
    verdict_source = "facts"
    # Defensive fallback: if for some reason facts produced nothing useful
    # AND the LLM emitted a verdict block, honor that instead.
    if verdict == "ok" and crit_count == 0 and warn_count == 0 and not (data.get("hosts")):
        if verdict_meta.get("verdict"):
            verdict = verdict_meta["verdict"]
            crit_count = verdict_meta.get("critical_findings") or 0
            warn_count = verdict_meta.get("warnings") or 0
            verdict_source = "block"

    report = {
        "id": str(uuid.uuid4())[:8],
        "timestamp": tz_now().strftime("%Y-%m-%d %H:%M:%S"),
        "provider": provider,
        "model": model,
        "content": cleaned_content,
        "host_count": len(data.get("hosts", [])),
        "host_names": host_names,
        "host_addresses": host_addresses,
        "usage": result.get("usage", {}),
        # Fact-derived verdict + per-section status map (1..7 -> ok/warn/crit)
        # so the PDF/UI can render markers even on a model that ignored the
        # heading-tag instruction.
        "verdict": verdict,
        "critical_findings": crit_count,
        "warnings_count": warn_count,
        "verdict_source": verdict_source,
        "section_statuses": section_status_map,
    }

    _add_report(report)

    # Send via notification channels if enabled. The result is bubbled up
    # in the response so the UI / scheduler log can show "ok / skipped /
    # failed" per channel -- previously every dispatch outcome was hidden.
    notify_summary: Dict[str, Any] = {"enabled": False, "results": {}, "skipped_reason": None}
    if config.get("notify_on_report"):
        notify_summary["enabled"] = True
        try:
            from app.notifications import send_notification
            content_text = cleaned_content
            # Send the full report (truncated to 4000 chars for message limits)
            report_text = content_text[:4000]
            if len(content_text) > 4000:
                report_text += "\n\n... (truncated)"
            title = "KI-Bericht" if lang == "de" else "AI Report"
            # For combined reports the host list can balloon to dozens of
            # entries. Keep it compact in the subject line: "all hosts (N)".
            host_tag = ""
            if host_names:
                if len(host_names) <= 2:
                    host_tag = f" ({', '.join(host_names)})"
                else:
                    label = "alle Hosts" if lang == "de" else "all hosts"
                    host_tag = f" ({label}, {len(host_names)})"

            # Build PDF attachment if enabled
            pdf_attachment = None
            if config.get("attach_pdf", True):
                try:
                    from app.ai_pdf import generate_pdf
                    pdf_bytes = generate_pdf(report)
                    safe_ts = report["timestamp"].replace(" ", "_").replace(":", "-")
                    host_slug = "all-hosts"
                    if host_addresses and len(host_addresses) == 1:
                        host_slug = host_addresses[0].replace(":", "_").replace("/", "_")
                    pdf_filename = f"ZFS_Report_{host_slug}_{safe_ts}.pdf"
                    pdf_attachment = (pdf_filename, pdf_bytes)
                except Exception as e:
                    log.warning("PDF generation for notification failed: %s", e)

            # Build the short email body from the verdict we already derived
            # (section tags first, then the [VERDICT] block). This bypasses
            # the heuristic in _summarize_ai_report. Falls back to None when
            # neither source produced a verdict -- the notification side then
            # runs its heuristic on the cleaned content.
            email_short = None
            v = verdict
            if v:
                de = (lang or "").lower().startswith("de")
                cf = crit_count or 0
                wn = warn_count or 0
                if v == "crit":
                    email_short = (
                        f"🚨 Handlung zwingend nötig — {max(cf,1)} kritische(r) Bereich(e) im Bericht."
                        if de else
                        f"🚨 Action required — {max(cf,1)} critical section(s) in the report."
                    )
                elif v == "warn":
                    email_short = (
                        f"⚠️ Aufmerksamkeit empfohlen — {max(wn,1)} Bereich(e) mit Warnung."
                        if de else
                        f"⚠️ Attention recommended — {max(wn,1)} section(s) with warnings."
                    )
                else:
                    email_short = (
                        "✅ Alles im grünen Bereich — keine kritischen Hinweise gefunden."
                        if de else
                        "✅ All clear — no critical findings."
                    )

            log.info(
                "ai_report: dispatching notification (hosts=%d, lang=%s, pdf=%s, verdict=%s, source=%s)",
                len(host_names or []), lang, bool(pdf_attachment),
                v or "(heuristic)", report.get("verdict_source"),
            )
            results = send_notification(
                "ai_report",
                f"{title}{host_tag}",
                f"Provider: {provider} ({model})\n\n{report_text}",
                pdf_attachment=pdf_attachment,
                lang=lang,
                email_short=email_short,
            )
            # send_notification returns either {"skipped": True, ...} when
            # the event is disabled, or {channel: result_dict} per active
            # channel. Normalise that into a flat summary.
            if isinstance(results, dict) and results.get("skipped"):
                notify_summary["skipped_reason"] = results.get("reason")
            else:
                for ch, res in (results or {}).items():
                    ok = bool(res and res.get("success"))
                    notify_summary["results"][ch] = {
                        "success": ok,
                        "detail": (res or {}).get("detail")
                                 if not ok else None,
                    }
            log.info("ai_report: notification summary: %s", notify_summary)
        except Exception as e:
            log.warning("Failed to send report notification: %s", e)
            notify_summary["error"] = str(e)
    else:
        notify_summary["skipped_reason"] = "notify_on_report disabled"

    return {"success": True, "report": report, "notify": notify_summary}


def generate_report_async(host_address=None, lang_override=None):
    """Run generate_report() in a background thread and return a task id.

    The synchronous path still works (used by the scheduler), but UI-driven
    runs go through here so the user can keep navigating while the LLM call
    is in flight. The task result has the same shape as generate_report()'s
    return value.
    """
    from app.tasks import start_task

    def _job(progress, _host_address, _lang):
        progress("Collecting host data …")
        # generate_report() is the slow part: data collection + LLM round-trip.
        # We can't stream sub-step progress from inside it without restructuring
        # but the "running" status alone unblocks the UI.
        result = generate_report(host_address=_host_address, lang_override=_lang)
        if result.get("success"):
            progress("Report generated", report_id=result.get("report", {}).get("id"))
        else:
            progress("Report generation failed", error=result.get("error", ""))
        return result

    return start_task("ai_report", _job, host_address, lang_override, prefix="ai")


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

def get_active_schedules():
    """Return a list of active schedule entries with computed next-run time.

    Each entry: {key, host, label, enabled, interval, hour, weekday, last_run, next_run}
    `host` is None for the 'all hosts' schedule, or a host address string.
    """
    config = load_config()
    entries = []

    # Legacy / "all hosts" schedule
    legacy = config.get("schedule") or {}
    if legacy:
        entries.append({
            "key": "__all__",
            "host": None,
            "label": "all_hosts",
            "enabled": bool(legacy.get("enabled")),
            "interval": legacy.get("interval", "daily"),
            "hour": int(legacy.get("hour", 6)),
            "weekday": int(legacy.get("weekday", 0)),
        })

    # Per-host schedules
    schedules = config.get("schedules") or {}
    if isinstance(schedules, dict):
        for host_addr, cfg in schedules.items():
            if not isinstance(cfg, dict):
                continue
            entries.append({
                "key": f"host:{host_addr}",
                "host": host_addr,
                "label": host_addr,
                "enabled": bool(cfg.get("enabled")),
                "interval": cfg.get("interval", "daily"),
                "hour": int(cfg.get("hour", 6)),
                "weekday": int(cfg.get("weekday", 0)),
            })

    # Compute next_run for each
    now = tz_now()
    for e in entries:
        e["last_run"] = _last_run_keys.get(e["key"])
        e["next_run"] = _compute_next_run(now, e) if e["enabled"] else None
    return entries


def _compute_next_run(now, entry):
    """Return a human-readable next-run time for a schedule entry."""
    hour = entry.get("hour", 6)
    interval = entry.get("interval", "daily")
    weekday = entry.get("weekday", 0)

    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if interval == "weekly":
        days_ahead = (weekday - now.weekday()) % 7
        candidate = candidate + datetime.timedelta(days=days_ahead)
        if candidate <= now:
            candidate = candidate + datetime.timedelta(days=7)
    else:  # daily
        if candidate <= now:
            candidate = candidate + datetime.timedelta(days=1)
    return candidate.strftime("%Y-%m-%d %H:%M")


def _period_run_key(now, interval):
    """The last-run key for the current period (must match the loop's key):
    ISO week for weekly, calendar date for daily."""
    if interval == "weekly":
        iso = now.isocalendar()
        return f"{iso[0]}-W{iso[1]}"
    return now.strftime("%Y-%m-%d")


def _period_target_passed(now, entry):
    """Has this schedule's target moment for the current period already passed
    at ``now``? Used to seed ONLY already-elapsed schedules at (re)start, so a
    restart before the scheduled hour doesn't wrongly mark today as done and
    skip an upcoming run."""
    hour = int(entry.get("hour", 6))
    if entry.get("interval") == "weekly":
        wd = int(entry.get("weekday", 0))
        if now.weekday() > wd:
            return True                       # the weekday already went by this week
        if now.weekday() == wd and now.hour >= hour:
            return True                       # today is the day and the hour passed
        return False
    return now.hour >= hour                   # daily: just the hour


def _seed_elapsed_schedules(now):
    """Seed ``_last_run_keys`` for enabled schedules whose current-period
    target has already passed, so they don't fire immediately on (re)start.
    Upcoming schedules are deliberately left unseeded so a restart before the
    scheduled hour still fires them at their time. setdefault keeps this
    idempotent -- it never clobbers a schedule that already ran this period."""
    for entry in get_active_schedules():
        if entry["enabled"] and _period_target_passed(now, entry):
            _last_run_keys.setdefault(entry["key"],
                                      _period_run_key(now, entry["interval"]))


def _scheduler_loop():
    """Background thread for scheduled report generation.

    Evaluates all active schedules (legacy "all hosts" + per-host) each tick.
    """
    global _last_run_key
    log.info("AI report scheduler started")

    while not _scheduler_stop.is_set():
        try:
            now = tz_now()
            # First pass: which entries want to fire in this tick?
            # We collect the full set up-front so the second pass can
            # de-duplicate overlapping schedules -- specifically, when the
            # all-hosts entry fires it already covers every host, so any
            # per-host entry that would fire in the same tick is redundant
            # (user reported receiving two near-identical emails 60 s apart
            # with conflicting LLM verdicts because both daily-at-06:00
            # schedules were active and we ran the LLM twice on the same
            # data).
            due_entries = []
            for entry in get_active_schedules():
                if not entry["enabled"]:
                    continue

                interval = entry["interval"]
                target_hour = entry["hour"]
                run_key = _period_run_key(now, interval)

                should_run = False
                if now.hour >= target_hour and _last_run_keys.get(entry["key"]) != run_key:
                    if interval == "daily":
                        should_run = True
                    elif interval == "weekly" and now.weekday() == entry["weekday"]:
                        should_run = True

                if should_run:
                    due_entries.append((entry, run_key))

            # Dedup: if the all-hosts entry fires in this tick, suppress
            # every per-host entry that also fires in the same tick. The
            # all-hosts run covers their hosts already; firing per-host on
            # top would send a second email with the same data. We still
            # mark the per-host entries' last_run_keys so they don't
            # re-trigger seconds later in the next 30 s tick.
            all_hosts_due = any(e["key"] == "__all__" for e, _ in due_entries)
            suppressed_keys = set()
            if all_hosts_due:
                for entry, run_key in due_entries:
                    if entry["key"] == "__all__":
                        continue
                    suppressed_keys.add(entry["key"])
                    _last_run_keys[entry["key"]] = run_key
                    log.info(
                        "Scheduled AI report: skipping per-host entry %s "
                        "(host=%s) -- already covered by today's all-hosts "
                        "run (key=%s)",
                        entry["label"], entry["host"], run_key,
                    )

            # Second pass: actually fire the survivors. We re-derive
            # should_run / run_key from the captured tuple so the rest of
            # the original body keeps working unchanged.
            for entry, run_key in due_entries:
                if entry["key"] in suppressed_keys:
                    continue
                should_run = True
                target_hour = entry.get("hour", 0)
                if should_run:
                    log.info(
                        "Scheduled AI report triggered for %s (key=%s, hour=%s, host=%s)",
                        entry["label"], run_key, target_hour, entry["host"] or "<all>",
                    )
                    _last_run_keys[entry["key"]] = run_key
                    # Mirror into legacy var for the "all hosts" entry
                    if entry["key"] == "__all__":
                        _last_run_key = run_key
                    try:
                        result = generate_report(host_address=entry["host"])
                        if result.get("success"):
                            n = result.get("notify", {})
                            if n.get("enabled"):
                                channels = list((n.get("results") or {}).keys())
                                ok_channels = [c for c, r in (n.get("results") or {}).items()
                                               if r.get("success")]
                                log.info(
                                    "Scheduled AI report for %s: report saved, "
                                    "notification dispatched to %d/%d channel(s) (%s)",
                                    entry["label"], len(ok_channels), len(channels),
                                    ", ".join(channels) or "none",
                                )
                            else:
                                log.info(
                                    "Scheduled AI report for %s: report saved, "
                                    "notifications skipped (%s)",
                                    entry["label"], n.get("skipped_reason") or "disabled",
                                )
                        else:
                            log.error(
                                "Scheduled AI report for %s failed: %s",
                                entry["label"], result.get("error", "unknown"),
                            )
                    except Exception as e:
                        log.error("Scheduled report generation failed for %s: %s", entry["label"], e)

            _scheduler_stop.wait(30)
        except Exception as e:
            log.error("Scheduler error: %s", e)
            _scheduler_stop.wait(30)

    log.info("AI report scheduler stopped")


def start_scheduler():
    """Start the scheduler thread (idempotent, thread-safe).

    The lock guards against two gthread request-threads racing into the
    starter concurrently (import-time start vs. the defensive re-arm in
    POST /api/ai/config) and spawning two scheduler threads.
    """
    global _scheduler_thread, _last_run_key
    with _scheduler_start_lock:
        # Seed last-run for schedules whose target already passed -- runs on
        # every call (startup AND the POST /api/ai/config re-arm) so a
        # just-saved past-time schedule doesn't fire immediately either.
        # Crucially, schedules whose hour is still UPCOMING today are left
        # unseeded, so a restart before that hour still fires them (the bug:
        # the old blanket seed marked every schedule "done today" and skipped
        # any run scheduled after the restart time).
        now = tz_now()
        if _last_run_key is None:
            _last_run_key = _period_run_key(now, "daily")
        _seed_elapsed_schedules(now)

        # Only start a new thread if one isn't already running
        if _scheduler_thread and _scheduler_thread.is_alive():
            return
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
