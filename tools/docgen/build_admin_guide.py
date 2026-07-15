# -*- coding: utf-8 -*-
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from pdf_common import build_guide, repo_version, DEFAULT_OUT_DIR

TITLE = "PVE ZFS Tool"
SUBTITLE = "Administratorhandbuch — Architektur & Befehlsreferenz"
META = [
    f"Version: entspricht Release {repo_version()}",
    "Repository: github.com/onlinecrash24/pve-zfs-tool",
    "Zielgruppe: Administratoren, die das Tool betreiben, erweitern oder auditieren",
]

CONTENT = [

# =====================================================================
("h1", "1. Über dieses Dokument"),
("p", "Dieses Handbuch richtet sich an Administratoren und Entwickler, die das PVE ZFS Tool "
      "betreiben, warten oder erweitern. Es beschreibt den Code-Aufbau, die eingesetzten "
      "Technologien sowie — als Kernstück — für jede Aktion in der Weboberfläche den genauen "
      "Befehl (oder API-Aufruf), der auf dem Zielsystem ausgeführt wird. Für eine "
      "anwenderorientierte Beschreibung der Funktionen siehe das separate Benutzerhandbuch."),
("note", "Das Tool führt selbst keine ZFS-/Proxmox-Kommandos lokal aus. Alle Aktionen laufen "
         "per SSH auf den registrierten Proxmox-Hosts — das Tool selbst benötigt daher keine "
         "ZFS-Werkzeuge im Container und ist kein Agent, der auf den Hosts installiert werden muss."),

# =====================================================================
("h1", "2. Architektur-Überblick"),
("h2", "2.1 Gesamtbild"),
("p", "Das Tool ist eine einzelne Flask-Anwendung (Python), die als Docker-Container läuft. "
      "Sie hält keine Kopie der ZFS-Daten vor — jede Ansicht in der Oberfläche löst bei Bedarf "
      "einen oder mehrere SSH-Befehle auf dem gerade gewählten Host aus, parst die Textausgabe "
      "und liefert sie als JSON an das Frontend. Eine Ausnahme bilden historische Metriken und "
      "das Audit-Log, die periodisch gesammelt und in einer lokalen SQLite-Datenbank abgelegt "
      "werden."),
("bullets", [
    "Backend: Python 3.13, Flask, Gunicorn (1 Worker, 8 Threads, gthread-Klasse)",
    "SSH-Zugriff: Paramiko, mit Verbindungs-Pooling pro Thread + Host",
    "Datenhaltung: SQLite (Metriken, Audit-Log, Monitor-Zustand), JSON-Dateien (Hosts, "
    "Konfigurationen)",
    "Frontend: Vanille-JavaScript SPA (kein Build-Schritt, kein Framework), Dark-Theme-CSS, "
    "Deutsch/Englisch",
    "Hintergrund-Threads: Metrik-Sampler, Replikations-Monitor, KI-Report-Scheduler, "
    "Host-Backup-Scheduler — alle als Daemon-Threads im selben Prozess",
]),
("h2", "2.2 Warum genau ein Gunicorn-Worker?"),
("p", "Der Container startet Gunicorn bewusst mit --workers 1 (siehe entrypoint.sh). Der Grund: "
      "Das Tool hält Zustand im Prozessspeicher — die Async-Task-Registry (app/tasks.py), die "
      "Zeitpläne des KI-Report-Schedulers und den SSH-Verbindungs-Pool. Ein zweiter Worker-Prozess "
      "würde diesen Zustand aufspalten, wodurch z. B. das Abfragen des Fortschritts eines "
      "Hintergrund-Tasks (Reverse-Sync, Paket-Reinstall, KI-Report) fehlschlagen könnte, weil die "
      "Anfrage im falschen Worker landet. Gleichzeitig sorgt --worker-class gthread mit 8 Threads "
      "dafür, dass eine einzelne langsame SSH-Anfrage nicht die gesamte Oberfläche blockiert, da "
      "Paramiko den GIL während des Wartens auf Netzwerk-I/O freigibt."),
("note", "--preload wird bewusst NICHT verwendet: Damit würden die Hintergrund-Threads (Sampler, "
         "Scheduler) beim Import im Gunicorn-Master starten und nach dem Fork ein zweites Mal im "
         "Worker — mit doppelt geplanten Berichten als Folge. Ohne --preload wird die App erst im "
         "Worker-Prozess importiert, die Threads starten also genau einmal."),

("h2", "2.3 Kein Agent auf den Proxmox-Hosts"),
("p", "Auf den verwalteten Proxmox-Hosts wird keine Software installiert außer optional den "
      "Werkzeugen, die einzelne Funktionen benötigen (zfs-auto-snapshot, bashclub-zsync, "
      "smartmontools, kpartx, ntfs-3g) — für diese bietet das Tool selbst Ein-Klick-Installer an. "
      "Der Zugriff erfolgt ausschließlich über einen SSH-Schlüssel, den der Container beim ersten "
      "Start selbst erzeugt (/root/.ssh/id_ed25519, persistiert im Docker-Volume ssh-keys)."),

# =====================================================================
("h1", "3. Verzeichnisstruktur"),
("p", "Alle Backend-Module liegen flach unter app/, jedes mit klar abgegrenzter Verantwortung. "
      "Es gibt keine Klassen-Hierarchie und kein ORM — Funktionen nehmen ein host-Dict "
      "({address, port, user, name}) entgegen und geben ein Ergebnis-Dict zurück."),
("table",
    ["Datei", "Verantwortung"],
    [
        ["main.py", "Flask-Routen, Auth/Session/CSRF, Sicherheits-Header, Startup"],
        ["ssh_manager.py", "SSH-Verbindungs-Pool, Host-Verwaltung, Schlüssel-Rotation, Ad-hoc-Passwort-Verbindungen"],
        ["zfs_commands.py", "Alle ZFS-/Proxmox-Kommandos (Pools, Datasets, Snapshots, Restore, ARC, SMART)"],
        ["validators.py", "Whitelist-Eingabevalidierung für alles, was in einen Shell-Befehl einfließt"],
        ["cache.py", "In-Memory-TTL-Cache für lesende SSH-Ergebnisse"],
        ["tasks.py", "Generische Async-Task-Registry für Hintergrund-Operationen"],
        ["database.py", "Gemeinsame SQLite-Datenbank (Schema, Verbindung)"],
        ["metrics.py", "Hintergrund-Sampler für Pool-/Platten-Metriken, Zeitreihen-Abfragen"],
        ["monitor.py", "Zustandswechsel-Erkennung (Host offline, Pool-Health, Kapazität, veraltete Snapshots)"],
        ["analytics.py", "Kapazitätsprognose, Dashboard-Aggregation, Prometheus-Export"],
        ["audit.py", "Audit-Log schreiben/abfragen, Aufbewahrungs-Bereinigung"],
        ["snapshot_analysis.py", "Snapshot-Gesundheitsprüfung (Lücken, veraltet, Anzahl-Soll/Ist)"],
        ["snaptags.py", "Erkennung/Verwaltung der Snapshot-Namens-Tags je Host"],
        ["autosnap.py", "zfs-auto-snapshot-Retention-Editor (Cron-Dateien lesen/schreiben)"],
        ["tuning.py", "ARC-Limit auslesen und setzen"],
        ["smart.py", "SMART-Erfassung je Platte (Temperatur, Gesundheit, Verschleiß)"],
        ["wol.py", "Wake-on-LAN (lokal + Relais über andere Hosts)"],
        ["hostbackup.py", "Host-Config-Backup erstellen/planen/aufbewahren"],
        ["replication.py", "bashclub-zsync-Integration (Installation, Konfiguration, Cron, checkzfs)"],
        ["replication_monitor.py", "Replikations-Lag-Erkennung je Paar"],
        ["dr.py", "Disaster Recovery: Reverse-Sync, PVE-Config-Restore, Paket-Reinstall"],
        ["notifications.py", "Telegram/Gotify/Matrix/E-Mail-Versand"],
        ["ai_reports.py", "KI-gestützte Analyseberichte (OpenAI/Anthropic/Ollama)"],
        ["ai_pdf.py", "PDF-Rendering der KI-Berichte (fpdf2)"],
        ["timezone.py", "Zeitzonen-Hilfsfunktion (TZ-Umgebungsvariable)"],
        ["templates/index.html / login.html", "SPA-Grundgerüst bzw. Login-Seite"],
        ["static/js/app.js", "Gesamte Frontend-Logik (Routing, Views, API-Client)"],
        ["static/js/i18n.js", "Deutsch/Englisch-Übersetzungen"],
        ["static/css/style.css", "Dark-Theme-Styling"],
    ],
    [0], [55*72/25.4, None],
),

# =====================================================================
("h1", "4. Kernkomponenten"),

("h2", "4.1 SSH-Verbindungsmanagement (ssh_manager.py)"),
("p", "Jeder registrierte Host wird als einfaches Dict in /app/data/hosts.json gehalten "
      "(address, port, user, name). Für die eigentliche Verbindung gibt es zwei Pfade:"),
("bullets", [
    "Registrierte Hosts (Schlüssel-Auth): Ein Thread-lokaler Verbindungs-Pool hält pro "
    "(Thread, Host) eine offene Paramiko-Transport-Verbindung bis zu 120 Sekunden Leerlauf "
    "(SSH_CONN_IDLE_TTL) vor. Ein Health-Check (transport.is_active()) erkennt tote "
    "Verbindungen; bei Bedarf wird genau einmal automatisch neu verbunden. Das Pooling lässt "
    "sich über SSH_POOL=0 abschalten.",
    "Ad-hoc-Ziele (Passwort-Auth, z. B. beim PVE Config Restore auf einen frisch installierten "
    "Host): Diese Verbindungen werden NIE gepoolt oder gecacht — jede Anfrage öffnet und "
    "schließt eine eigene Verbindung. Das Passwort wird nur für die eine Verbindung verwendet, "
    "nirgends persistiert und nicht geloggt.",
]),
("p", "Host-Schlüssel-Prüfung: Bei Schlüssel-Auth wird known_hosts (im Datenvolume) genutzt — "
      "beim ersten Kontakt wird der Schlüssel vertraut (Trust-on-first-use) und gespeichert, ein "
      "späterer abweichender Schlüssel löst eine Warnung/Fehler aus. Bei Ad-hoc-Passwort-Zielen "
      "wird ein evtl. vorhandener alter known_hosts-Eintrag für diese Adresse verworfen und der "
      "aktuelle Schlüssel automatisch akzeptiert — der eingegebene Passwort-Login ist hier der "
      "Vertrauensanker (typischer Fall: ein Host wurde neu installiert und hat einen neuen "
      "SSH-Host-Schlüssel)."),
("cmd", "Schlüssel-Rotation (Hosts → SSH-Key rotieren)",
    "Erzeugt ein neues Schlüsselpaar, verteilt den neuen Public Key an ALLE registrierten Hosts, "
    "erst danach wird der alte Schlüssel lokal ersetzt und von den Hosts entfernt — so kann eine "
    "fehlgeschlagene Verteilung nie zum Aussperren führen.",
    ["ssh-keygen -t ed25519 -f <neuer_key> -N \"\"",
     "# je Host: neuen Public Key an ~/.ssh/authorized_keys anhängen (idempotent, grep -qxF Check)",
     "# nach Erfolg auf allen Hosts: alten Key lokal nach id_ed25519.old verschieben",
     "# je Host: alte Zeile aus ~/.ssh/authorized_keys entfernen"]),

("h2", "4.2 Eingabevalidierung (validators.py)"),
("p", "Jeder Wert, der vom Client kommt und in einen Shell-Befehl einfließt, muss zuerst einen "
      "Validator durchlaufen. Es handelt sich durchgehend um Positivlisten (Whitelists) auf "
      "Basis regulärer Ausdrücke — kein Blacklisting von \"gefährlichen\" Zeichen."),
("table",
    ["Validator", "Erlaubtes Muster", "Verwendung"],
    [
        ["validate_pool_name", "[a-zA-Z0-9][a-zA-Z0-9_.-]*", "Pool-Namen (kein / oder @)"],
        ["validate_zfs_name", "[a-zA-Z0-9][a-zA-Z0-9_./@:-]*", "Dataset-/Snapshot-Namen"],
        ["validate_zfs_property", "[a-z][a-z0-9_:.-]*", "ZFS-Property-Namen"],
        ["validate_zfs_value", "[a-zA-Z0-9][a-zA-Z0-9_./:@=, -]*", "ZFS-Property-Werte"],
        ["validate_vmid", "[0-9]+", "Proxmox VMID"],
        ["validate_vm_type", "qemu | lxc", "Gast-Typ"],
        ["validate_path", "kein .. / kein Nullbyte / Whitelist-Zeichen", "Dateisystempfade beim Datei-Restore"],
        ["validate_limit", "positive Ganzzahl, gedeckelt", "Zeilen-/Ergebnis-Limits"],
    ],
    [1],
),
("note", "Zusätzlich werden alle Werte über shlex.quote() in die Shell-Befehle eingesetzt, wo "
         "sie nicht bereits durch die Whitelist auf ein sicheres Zeichenset beschränkt sind "
         "(z. B. Dateipfade, Ad-hoc-Zugangsdaten)."),

("h2", "4.3 Caching (cache.py)"),
("p", "Ein einfacher In-Memory-TTL-Cache reduziert SSH-Traffic für häufig abgefragte, "
      "lesende Kommandos. Schlüssel ist (Host-Adresse, exakter Befehlsstring). Schreibende "
      "Operationen rufen invalidate_host(adresse) auf, um den gesamten Cache für diesen Host zu "
      "leeren. Aktuelle Trefferquote ist per API abrufbar (GET /api/cache/stats)."),
("table",
    ["TTL-Klasse", "Sekunden", "Beispiele"],
    [
        ["_TTL_SHORT", "15", "zpool list, zfs list, zpool status, ARC-Stats, ZFS-Events"],
        ["_TTL_MED", "30", "Snapshot-Alter-Analyse, Proxmox-Gast-Listen"],
        ["_TTL_LONG", "60", "zfs-auto-snapshot-Cron-Konfiguration"],
        ["_TTL_SMART", "300", "smartctl-Abfragen (langsam, Plattenzustand ändert sich selten)"],
    ],
),

("h2", "4.4 Asynchrone Hintergrund-Tasks (tasks.py)"),
("p", "Lang laufende Operationen (Reverse-Sync, Paket-Reinstall, KI-Report-Generierung) würden "
      "eine normale HTTP-Anfrage über das Timeout eines Proxys hinaus blockieren. Stattdessen "
      "startet start_task() einen Daemon-Thread, der über einen progress_cb Fortschritt in eine "
      "prozessweite Registry schreibt; der Client pollt GET /api/replication/task?id=... in "
      "Abständen. Beendete Tasks bleiben 6 Stunden abrufbar, danach werden sie beim nächsten "
      "Start eines neuen Tasks aus dem Speicher entfernt (Garbage Collection)."),

("h2", "4.5 Datenbank (database.py) — gemeinsame SQLite-Datei"),
("p", "Alle strukturierten historischen Daten liegen in einer einzigen SQLite-Datei unter "
      "/app/data/pvezfs.db (WAL-Journaling, mehrere Threads können parallel lesen)."),
("table",
    ["Tabelle", "Zweck", "Aufbewahrung"],
    [
        ["pool_metrics", "Pool-Zeitreihe: Größe, Belegung, Fragmentierung, Health, Dedup-Ratio (alle 15 Min.)", "METRICS_RETENTION_DAYS (Standard 90 Tage)"],
        ["disk_metrics", "SMART-Zeitreihe je Platte: Temperatur, Health, Wear, Sektoren, Betriebsstunden", "METRICS_RETENTION_DAYS"],
        ["audit_log", "Jede sicherheitsrelevante/schreibende Aktion mit Nutzer, IP, Ziel, Erfolg, Details", "AUDIT_RETENTION_DAYS (Standard 365 Tage)"],
        ["monitor_state", "Letzter bekannter Zustand je (Scope, Schlüssel) für Zustandswechsel-Erkennung (Alarme nur bei Änderung)", "läuft mit dem jeweiligen Datensatz mit"],
    ],
),
("p", "Jeder Sampler-Zyklus prüft die konfigurierte Aufbewahrung, löscht abgelaufene Zeilen aus "
      "pool_metrics, disk_metrics und audit_log und führt anschließend PRAGMA "
      "wal_checkpoint(TRUNCATE) aus, damit die -wal-Nebendatei nicht unbegrenzt wächst. Ein Wert "
      "<= 0 bei den Retention-Variablen deaktiviert das Löschen (unbegrenzte Aufbewahrung)."),

("h2", "4.6 Hintergrund-Threads / Scheduler"),
("table",
    ["Thread", "Intervall", "Aufgabe"],
    [
        ["Metrik-Sampler (metrics.py)", "900 s (METRICS_SAMPLE_INTERVAL)", "Pool- + Disk-SMART-Metriken je Host sammeln, Monitor-Checks auslösen, Retention/Cleanup"],
        ["Replikations-Monitor (replication_monitor.py)", "läuft im Sampler-Zyklus mit", "Lag je Replikations-Paar berechnen, Alarm bei Statuswechsel"],
        ["Host-Backup-Scheduler (hostbackup.py)", "Polling alle 300 s, dateibasierte Fälligkeit", "Geplante Config-Backups je Host erstellen, mit Nachhol-Logik nach Ausfall"],
        ["KI-Report-Scheduler (ai_reports.py)", "Polling alle 30 s", "Automatische KI-Berichte zum konfigurierten Zeitpunkt je Host/gesamt auslösen"],
    ],
),

# =====================================================================
("h1", "5. Authentifizierung & Sicherheit"),
("h2", "5.1 Login & Sitzungen"),
("bullets", [
    "Zugangsdaten kommen aus den Umgebungsvariablen ADMIN_USER / ADMIN_PASSWORD (kein "
    "Mehrbenutzersystem, kein Datenbank-Backend für Accounts).",
    "Passwortvergleich per hmac.compare_digest() (Timing-Angriff-resistent).",
    "Rate-Limiting: 5 Fehlversuche je Client-IP sperren für 300 Sekunden.",
    "Bei Erfolg wird die Session geleert und neu aufgebaut (Schutz gegen Session-Fixation) und "
    "ein CSRF-Token erzeugt; Session-Cookie ist HttpOnly, SameSite=Lax, bei FORCE_HTTPS=true "
    "zusätzlich Secure. Sitzungsdauer: 8 Stunden.",
]),
("h2", "5.2 CSRF-Schutz"),
("p", "Jede zustandsändernde Anfrage (POST/PUT/DELETE/PATCH) muss den Header X-CSRF-Token mit "
      "dem in der Session hinterlegten Wert tragen (per hmac.compare_digest geprüft); fehlt oder "
      "stimmt er nicht, wird die Anfrage mit 403 abgelehnt."),
("h2", "5.3 Sicherheits-Header"),
("bullets", [
    "X-Content-Type-Options: nosniff",
    "X-Frame-Options: DENY",
    "Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' data:",
    "Referrer-Policy: strict-origin-when-cross-origin",
]),
("h2", "5.4 Audit-Log"),
("p", "Jede schreibende/sicherheitsrelevante Aktion (Login, Snapshot löschen, Rollback, "
      "Replikations-Änderung, Restore, Benachrichtigungs-Test usw.) wird mit Zeitstempel, "
      "Nutzer, Quell-IP, betroffenem Host, Ziel-Objekt, Erfolg/Fehlschlag und optionalen Details "
      "in audit_log geschrieben. Einsehbar unter Audit-Log in der Oberfläche, filterbar nach "
      "Aktion, Host, Nutzer, Zeitraum, nur Fehlschläge."),
("h2", "5.5 Umgang mit Secrets"),
("bullets", [
    "/etc/pve/priv (Cluster-CA-Private-Key u. Ä.) wird bei Host-Config-Backups standardmäßig "
    "ausgeschlossen; ein expliziter Opt-in-Schalter inkludiert es, mit Warnhinweis im UI.",
    "/root/.ssh/authorized_keys wird gesichert — es enthält ausschließlich ÖFFENTLICHE "
    "Schlüssel; private Schlüssel werden nie erfasst.",
    "/etc/apt/auth.conf* (kann Repo-Passwörter enthalten) wird beim APT-Backup ausdrücklich "
    "ausgeschlossen.",
    "API-Tokens/Bot-Tokens/Passwörter in den Benachrichtigungs- und KI-Einstellungen werden in "
    "der Oberfläche maskiert dargestellt (mask_secret()); beim Speichern wird ein weiterhin "
    "maskierter Wert als \"unverändert\" erkannt (resolve_masked()) statt versehentlich "
    "überschrieben zu werden.",
    "Ad-hoc-Passwörter (PVE Config Restore) werden nirgends gespeichert und nicht geloggt — "
    "auch nicht im Audit-Log (dort erscheint nur die Zieladresse).",
]),

# =====================================================================
("h1", "6. Befehlsreferenz — Was passiert bei welcher Aktion"),
("p", "Dieser Abschnitt listet für jeden Funktionsbereich die konkreten Befehle auf, die das "
      "Backend per SSH auf dem Zielhost ausführt (bzw. bei Benachrichtigungen/KI-Berichten: die "
      "externen HTTP-Aufrufe). Platzhalter in spitzen Klammern (<pool>, <dataset>, ...) werden "
      "durch die validierten, per shlex.quote() geschützten Werte ersetzt."),

("h2", "6.1 Pools"),
("cmd", "Pool-Liste anzeigen", "Grundlage der Pools-Ansicht und des Dashboards.",
    ["zpool list -H -o name,size,alloc,free,fragmentation,capacity,health,dedupratio"]),
("cmd", "Pool-Status / Details", "",
    ["zpool status <pool>"]),
("cmd", "Pool-I/O-Statistik", "",
    ["zpool iostat -v <pool>"]),
("cmd", "Autotrim / Autoexpand anzeigen bzw. setzen", "",
    ["zpool get -H -o property,value autotrim,autoexpand <pool>",
     "zpool set <autotrim|autoexpand>=<on|off> <pool>"]),
("cmd", "Scrub starten", "Startet zusätzlich einen Hintergrund-Thread, der zpool status alle "
    "60 Sekunden abfragt und bei Abschluss/Abbruch eine Benachrichtigung sendet.",
    ["zpool scrub <pool>", "# Poller: zpool status <pool>  (bis \"scrub in progress\" verschwindet)"]),
("cmd", "Pool-Historie", "",
    ["zpool history <pool> | tail -n <limit>"]),
("cmd", "Feature-Upgrade prüfen / durchführen", "",
    ["zpool status <pool>",
     "zpool upgrade <pool> -n",
     "zpool upgrade <pool>   # tatsächliches Upgrade"]),

("h2", "6.2 Datasets"),
("cmd", "Dataset-Liste", "",
    ["zfs list -H -o name,used,avail,refer,mountpoint,type,compression,compressratio [-r <pool>]"]),
("cmd", "Alle Properties anzeigen", "",
    ["zfs get all <dataset>"]),
("cmd", "Property setzen", "",
    ["zfs set <property>=<wert> <dataset>"]),
("cmd", "Dataset anlegen", "Optionale -o Property=Wert-Paare werden angehängt (z. B. beim "
    "Anlegen mit com.sun:auto-snapshot=false für Replikations-Ziele).",
    ["zfs create [-o <property>=<wert> ...] <name>"]),
("cmd", "Dataset löschen", "",
    ["zfs destroy [-r] <name>"]),

("h2", "6.3 Snapshots"),
("cmd", "Snapshot-Liste", "Zusätzlich wird zfs list -H -o name,type geladen, um jeden Snapshot "
    "als Dateisystem oder Volume zu kennzeichnen.",
    ["zfs list -t snapshot -H -o name,used,refer,creation -S creation [-r <dataset>]"]),
("cmd", "Snapshot erstellen", "",
    ["zfs snapshot [-r] <dataset>@<name>"]),
("cmd", "Snapshot löschen", "Bei Fehler \"dependent clones\" wird automatisch mit -R "
    "wiederholt (z. B. wenn noch ein Restore-Klon vom Snapshot abhängt).",
    ["zfs destroy [-r] <dataset>@<snapshot>",
     "zfs destroy -R <dataset>@<snapshot>   # Fallback bei abhängigen Klonen"]),
("cmd", "Rollback", "Bei aktivierter Option \"Gast stoppen\" wird die VM/CT vor dem Rollback "
    "gestoppt und danach wieder gestartet.",
    ["qm stop <vmid>  ODER  pct stop <vmid>   # optional, vor dem Rollback",
     "zfs rollback [-r] [-f] <dataset>@<snapshot>",
     "qm start <vmid>  ODER  pct start <vmid>   # optional, nach dem Rollback"]),
("cmd", "Klonen (gleicher Pool)", "",
    ["zfs clone <snapshot> <ziel>"]),
("cmd", "Klonen (Pool-übergreifend)", "Läuft als Streaming-Pipe direkt auf dem Host; der "
    "empfangene Klon wird anschließend promoted, damit er unabhängig vom Quell-Snapshot wird.",
    ["zfs send <snapshot> | zfs recv <ziel>", "zfs promote <ziel>"]),
("cmd", "Diff / Änderungsvergleich (Dateisystem)", "",
    ["zfs get -H -o value mounted <dataset>", "zfs diff <snapshot1> [<snapshot2>]"]),
("cmd", "Diff / Änderungsschätzung (Zvol)", "Für Blockgeräte gibt es kein zfs diff; stattdessen "
    "wird die inkrementelle Sendegröße als Näherung für die Datenänderung berechnet.",
    ["zfs get -H -o property,value used,referenced,written,creation <snapshot>",
     "zfs send -nvi <vorheriger_snapshot> <snapshot> 2>&1"]),
("cmd", "Sendegröße schätzen (Replikations-Planung)", "",
    ["zfs send -nv <snapshot> 2>&1", "zfs send -nvi <snap_von> <snap_bis> 2>&1"]),

("h2", "6.4 Auto-Snapshot & Retention (Snapshot-Prüfung)"),
("cmd", "zfs-auto-snapshot-Status abfragen", "",
    ["which zfs-auto-snapshot",
     "cat /etc/cron.d/zfs-auto-snapshot",
     "cat /etc/cron.{frequent,hourly,daily,weekly,monthly}/zfs-auto-snapshot",
     "crontab -l | grep zfs-auto-snapshot"]),
("cmd", "zfs-auto-snapshot installieren", "Standard-Debian-Paket, kein zusätzliches Repository "
    "nötig (verfügbar für bookworm UND trixie); idempotent.",
    ["command -v zfs-auto-snapshot   # Kurzschluss, falls schon vorhanden",
     "apt-get update -qq || true",
     "apt-get install -y zfs-auto-snapshot"]),
("cmd", "com.sun:auto-snapshot lesen/setzen/vererben (pro Dataset)", "",
    ["zfs get com.sun:auto-snapshot <dataset> -H -o value,source",
     "zfs set com.sun:auto-snapshot[:<label>]=<true|false> <dataset>",
     "zfs inherit com.sun:auto-snapshot[:<label>] <dataset>"]),
("cmd", "Retention-Editor: Cron-Dateien lesen/schreiben", "Die zfs-auto-snapshot-Cron-Dateien "
    "SIND die Retention-Policy (--keep=N je Level). Der Editor ändert nur die --keep-Zahl und "
    "die aktiviert/deaktiviert-Markierung (# davor) in der jeweiligen Zeile, alles andere bleibt "
    "unangetastet; vor dem Schreiben wird eine Zeitstempel-Sicherung angelegt.",
    ["cat /etc/cron.d/zfs-auto-snapshot   # bzw. cron.hourly/daily/weekly/monthly",
     "cp -a <datei> <datei>.bak.$(date +%Y%m%d%H%M%S)",
     "# Zeile mit neuem --keep=N und/oder (De-)Kommentierung zurückschreiben (base64-kodiert)"]),

("h2", "6.5 Proxmox-Gäste (VMs & Container)"),
("cmd", "VM-/CT-Liste", "",
    ["qm list", "pct list"]),
("cmd", "Lebenszyklus-Aktionen (whitelisted)", "shutdown wartet bis zu 60 s auf ein sauberes "
    "Herunterfahren; stop ist der harte Kill.",
    ["qm start <vmid>",
     "qm shutdown <vmid> --timeout 60",
     "qm stop <vmid>",
     "qm reboot <vmid>",
     "pct start <vmid>",
     "pct shutdown <vmid>",
     "pct stop <vmid>",
     "pct reboot <vmid>"]),
("cmd", "Guest-Snapshots ermitteln", "Filtert die vollständige Snapshot-Liste lokal nach "
    "Dataset-Präfix vm-<vmid> bzw. subvol-<vmid>.",
    ["zfs list -t snapshot -H -o name,used,refer,creation -s creation"]),

("h2", "6.6 Datei-Wiederherstellung — LXC-Container-Snapshots"),
("cmd", "Snapshot mounten (Klon-basiert)", "Nur für Dateisystem-Datasets. Erstellt einen "
    "schreibgeschützten Klon mit eigenem Mountpoint und deaktiviertem Auto-Snapshot, damit keine "
    "Snapshots des Restore-Klons entstehen.",
    ["zfs get -H -o value type <dataset>",
     "zfs destroy -r <alter_klon>   # Aufräumen eines evtl. Vorlaufs",
     "zfs clone -o mountpoint=<pfad> -o readonly=on -o com.sun:auto-snapshot=false <snapshot> <klon>"]),
("cmd", "Verzeichnis durchsuchen / Datei-Vorschau / Wiederherstellen", "Jeder Pfad wird per "
    "realpath gegen Path-Traversal geprüft (muss unterhalb des Mountpoints bleiben).",
    ["realpath <pfad>",
     "ls -la --time-style=long-iso <pfad>",
     "stat -c%s <datei>   # Größenprüfung vor Vorschau (max. 100 KB)",
     "cat <datei>",
     "cp -a <quelle> <ziel>   # einzelne Datei",
     "cp -a <quelle>/. <ziel>/   # ganzes Verzeichnis"]),
("cmd", "Restore-Klon aufräumen", "",
    ["zfs destroy -r <klon>"]),
("cmd", "Übrig gebliebene Restore-Klone auflisten / bereinigen (Zustand-Seite)", "",
    ["zfs list -H -o name,mountpoint,used,creation -t filesystem | grep '/restore-'"]),

("h2", "6.7 Datei-Wiederherstellung — VM-Disk-Snapshots (Zvol, kpartx)"),
("cmd", "Zvol-Snapshot exponieren", "Voraussetzung: kpartx auf dem Host installiert.",
    ["zfs get -H -o value type <dataset>",
     "which kpartx",
     "zfs set snapdev=visible <dataset>",
     "udevadm settle --timeout=5",
     "kpartx -ars /dev/zvol/<dataset>@<snapshot>",
     "kpartx -l /dev/zvol/<dataset>@<snapshot>",
     "blkid -o export /dev/mapper/<partition>",
     "blockdev --getsize64 /dev/mapper/<partition>   # Fallback für Größe"]),
("cmd", "Partition mounten", "Dateisystem-spezifische Optionen, da ein Snapshot einer LAUFENDEN "
    "VM nur crash-konsistent ist (Journal ungespült). ext4/xfs erhalten norecovery, btrfs "
    "rescue=nologreplay, ntfs läuft über ntfs-3g.",
    ["mount -o ro,norecovery <gerät> <mountpunkt>   # ext2/3/4, xfs",
     "mount -o ro,rescue=nologreplay <gerät> <mountpunkt>   # btrfs",
     "mount -t ntfs-3g -o ro <gerät> <mountpunkt>   # NTFS (Windows)",
     "mount -o ro <gerät> <mountpunkt>   # generischer Fallback"]),
("cmd", "Zvol-Restore aufräumen", "",
    ["umount <mountpunkt>",
     "kpartx -d /dev/zvol/<dataset>@<snapshot>",
     "dmsetup remove <mapper-name>   # Fallback, falls kpartx -d nichts findet",
     "zfs set snapdev=hidden <dataset>"]),
("cmd", "Aktive Mounts/Mappings auflisten bzw. komplett bereinigen (Zustand-Seite)", "",
    ["mount | grep /tmp/zfs-tool-zvol-restore",
     "ls /dev/mapper/ | grep -E '(vm-|zd[0-9])'",
     "zfs get snapdev -t volume -s local -H -o name,value"]),

("h2", "6.8 Zustand & Überwachung"),
("cmd", "ARC-Statistik", "",
    ["cat /proc/spl/kstat/zfs/arcstats | grep -E '^(size|hits|misses|c_max)'"]),
("cmd", "ARC-Limit anzeigen (Min/Empfohlen/Max)", "Ein Aufruf liefert Laufzeit-Limit, "
    "persistentes Limit, aktuelle Größe und die RAM-/Pool-Größe für die Referenzwerte "
    "(Proxmox-Untergrenze 2 GiB + 1 GiB/TiB Pool, ~25 % RAM empfohlen, 50 % RAM Maximum).",
    ["cat /sys/module/zfs/parameters/zfs_arc_max",
     "cat /proc/spl/kstat/zfs/arcstats   # c, size, hits, misses",
     "free -b", "zpool list -H -o size",
     "cat /etc/modprobe.d/zfs.conf"]),
("cmd", "ARC-Limit setzen", "Schreibt die persistente Konfiguration mit Zeitstempel-Sicherung, "
    "baut die initramfs für ALLE installierten Kernel neu (nicht-fatal, falls das fehlschlägt) "
    "und setzt den Laufzeitwert sofort.",
    ["cp -a /etc/modprobe.d/zfs.conf /etc/modprobe.d/zfs.conf.bak.<timestamp>",
     "echo \"options zfs zfs_arc_max=<bytes>\" > /etc/modprobe.d/zfs.conf",
     "update-initramfs -u -k all",
     "echo <bytes> > /sys/module/zfs/parameters/zfs_arc_max"]),
("cmd", "ZFS-Kernel-Ereignisse", "",
    ["zpool events -v | tail -n <limit>"]),
("cmd", "SMART-Status je Pool (klassische Ansicht)", "Löst Disk-IDs über /dev/disk/by-id/ auf "
    "und ermittelt über lsblk die zugrunde liegende Basis-Platte (bei partitionierten Devices).",
    ["zpool list -H -o name", "zpool status [<pool>]",
     "readlink -f /dev/disk/by-id/<id>", "lsblk -no PKNAME <gerät>",
     "smartctl -H <basis-platte>"]),
("cmd", "ZDB-Tiefendiagnose (nur bei DEGRADED/FAULTED)", "Wird automatisch für KI-Berichte "
    "herangezogen, wenn ein Pool nicht ONLINE ist.",
    ["zdb <pool>", "zdb -b <pool>",
     "zpool status <pool> | grep -E '^\\t  ' | awk '{print $1}'",
     "zdb -l /dev/<vdev>   # bzw. /dev/disk/by-id/<vdev>"]),

("h2", "6.9 Historische Metriken & SMART-Zeitreihe"),
("p", "Der Sampler ruft alle 15 Minuten je Host zunächst die Pool-Liste und den Pool-Status "
      "(Fehlerzähler) ab, speichert diese in pool_metrics, und führt anschließend — sofern "
      "smartmontools installiert ist — den SMART-Sammel-Durchlauf aus, gespeichert in "
      "disk_metrics."),
("cmd", "SMART-Sammlung je physischer Platte", "zvols/Device-Mapper/Loop-Geräte werden "
    "übersprungen (zd*, dm-*, loop*, sr*, md*, ram*, fd*). Ohne smartctl wird eine Markierung "
    "zurückgegeben, die die Oberfläche als \"nicht installiert\" mit Installations-Button anzeigt.",
    ["command -v smartctl   # sonst Markierung <<<NOSMARTCTL>>>",
     "lsblk -dn -o NAME,TYPE",
     "smartctl -j -a /dev/<platte>   # JSON-Ausgabe je gefundener Platte, ein SSH-Rundlauf"]),
("cmd", "smartmontools installieren", "Standard-Debian-Paket, idempotent.",
    ["command -v smartctl", "apt-get update -qq || true", "apt-get install -y smartmontools"]),

("h2", "6.10 Replikation (bashclub-zsync)"),
("cmd", "Setup-Vorabprüfung", "Vor jeder Aktion wird geprüft, was bereits vorhanden ist, damit "
    "nur die fehlenden Schritte ausgeführt werden.",
    ["pveversion   # bzw. Prüfung auf /etc/pve  → PVE-Erkennung",
     "[ -s /etc/apt/sources.list.d/bashclub.sources ] || [ -s .../bashclub.list ]   # Repo vorhanden?",
     "command -v bashclub-zsync && bashclub-zsync -v"]),
("cmd", "bashclub-zsync installieren (beide Seiten)", "Die Debian-Suite (bookworm/trixie) wird "
    "aus /etc/os-release abgeleitet und gegen das tatsächlich veröffentlichte Repository "
    "geprüft, mit Rückfall auf bookworm. Der Signing-Key liegt unter "
    "apt.bashclub.org/gpg/bashclub.pub (die frühere URL .../gpg.key existiert nicht mehr). Der "
    "Key wird nicht-interaktiv importiert (--batch --yes --no-tty), da eine SSH-Sitzung kein "
    "TTY hat.",
    [". /etc/os-release; SUITE=${VERSION_CODENAME:-bookworm}",
     "curl -fsSL -o /dev/null https://apt.bashclub.org/release/dists/$SUITE/Release   # Suite gültig?",
     "curl -fsSL https://apt.bashclub.org/gpg/bashclub.pub -o /tmp/bashclub.pub",
     "gpg --batch --yes --no-tty --dearmor -o /usr/share/keyrings/bashclub-archive-keyring.gpg < /tmp/bashclub.pub",
     "# /etc/apt/sources.list.d/bashclub.sources schreiben (deb822, Signed-By auf obigen Keyring)",
     "apt-get update -qq || true",
     "apt-get install -y bashclub-zsync"]),
("cmd", "SSH-Bootstrap (Ziel → Quelle)", "Richtet passwortlosen SSH-Zugang vom Ziel-Host zur "
    "Quelle ein, die bashclub-zsync für den Pull-Betrieb braucht.",
    ["ssh-keygen -t ed25519 -f <schlüssel> -N \"\"   # auf dem Ziel-Host, falls noch nicht vorhanden",
     "ssh-keyscan -p <port> <quelle> >> ~/.ssh/known_hosts",
     "cat <public_key> >> ~/.ssh/authorized_keys   # auf der Quelle",
     "ssh -o BatchMode=yes <quelle> true   # Vertrauens-Probe"]),
("cmd", "Datasets für Replikation taggen", "Setzt/entfernt die vom Nutzer wählbaren "
    "Quell-Datasets/Zvols für die Replikation.",
    ["zfs list -H -o name,type,<tag> -t filesystem,volume",
     "zfs set <tag>=all <dataset>",
     "zfs inherit <tag> <dataset>"]),
("cmd", "Ziel-Dataset anlegen", "Auto-Snapshot wird am Ziel bewusst deaktiviert — die "
    "Snapshot-Kette kommt vollständig von der Replikation.",
    ["zfs create -p -o com.sun:auto-snapshot=false <ziel>"]),
("cmd", "Konfiguration lesen/schreiben", "Jedes Replikations-Paar lebt in einer eigenen Datei "
    "je Quell-IP, damit mehrere Paare auf einem Ziel-Host koexistieren können.",
    ["cat /etc/bashclub/<quell-ip>.conf   # bzw. /etc/bashclub/zsync.conf (Legacy-Default)"]),
("cmd", "Cron-Zeitplan verwalten", "Idempotentes Anlegen/Ersetzen/Entfernen mit explizitem "
    "Reload für die jeweils erkannte Cron-Variante.",
    ["crontab -l   # aktuellen Stand lesen",
     "# Zeile für bashclub-zsync ersetzen/entfernen, dann crontab -",
     "systemctl reload cron   # bzw. cronie / systemd-cron, je nach Distribution"]),
("cmd", "checkzfs-Gesundheitsprüfung", "",
    ["checkzfs --source <quell-ip>"]),
("cmd", "Log-Anzeige", "",
    ["tail -n <N> /var/log/bashclub-zsync/zsync.log"]),
("cmd", "Replikations-Paar löschen", "Optional zusätzlich alle zfs-auto-snap_*-Snapshots "
    "unterhalb des Ziels löschen (Datasets und die zsync-Basis-Snapshots bleiben erhalten; "
    "Top-Level-Pools werden als Ziel abgelehnt).",
    ["cp -a /etc/bashclub/<ip>.conf /etc/bashclub/<ip>.conf.bak.<timestamp>",
     "rm /etc/bashclub/<ip>.conf",
     "# Cron-Zeile entfernen",
     "zfs list -t snapshot -H -o name -r <ziel> | grep zfs-auto-snap_   # optional",
     "zfs destroy <gefundene_snapshots>   # optional"]),
("cmd", "Lag-Erkennung (Replikations-Monitor, im Sampler-Zyklus)", "Bewertet ok/warn/crit "
    "anhand des Verhältnisses von Lag zu erwartetem Cron-Intervall (WARN ab dem 2-fachen, CRIT "
    "ab dem 4-fachen Intervall; ohne erkennbaren Cron wird 1 Stunde angenommen).",
    ["zfs list -H -t snapshot -o name,creation -p -s creation -r <ziel_dataset> | tail -n 1"]),

("h2", "6.11 Disaster Recovery — Reverse-Sync"),
("cmd", "Vorabprüfung (Live-Daten-Erkennung)", "Warnt, bevor ein voller Stream auf ein Ziel "
    "gesendet wird, das noch eine eigene, abweichende Snapshot-Kette besitzt (= Live-Daten der "
    "intakten Quelle) — dort würde ZFS den Empfang ohnehin verweigern.",
    ["zfs list -H -o name <quell_dataset>",
     "zfs list -H -t snapshot -o name -r -d 1 <quell_dataset>"]),
("cmd", "Reverse-Sync ausführen", "Läuft als Hintergrund-Task (bis zu 12 Stunden Timeout), da "
    "ein voller Resend bei großen Datenmengen lange dauern kann. -F ist die abgesicherte "
    "Rollback-Option; sie überschreibt eine eigene, abweichende Snapshot-Kette am Ziel NICHT — "
    "das kann nur ein voraufgehendes manuelles zfs destroy.",
    ["zfs list -H -t snapshot -o name -s creation -r -d 1 <replikat>   # neuesten Snapshot ermitteln, falls nicht gewählt",
     "zfs send -R <replikat>@<snapshot> | ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "
     "-p <port> <user>@<quelle> 'zfs recv [-F] <ziel_dataset>'"]),

("h2", "6.12 PVE Config Restore"),
("p", "Baut einen frisch installierten Proxmox-Host wieder auf den Konfigurationsstand eines "
      "früheren Hosts, ausgehend von einem Host-Config-Backup (siehe 6.13). Wichtig: Dies "
      "stellt NUR Konfigurationsdateien und ggf. Pakete wieder her — die eigentlichen VM-/CT-"
      "Festplatten kommen über den Reverse-Sync (6.11)."),
("cmd", "Backup-Inhalt durchsuchen / Datei-Vorschau", "Läuft rein lokal auf dem "
    "gespeicherten Archiv (kein SSH) — kategorisiert die Dateien (Gäste, Netzwerk, Storage, "
    "APT, User, SSH-Zugang, Firewall, Jobs, Sonstiges, System-Info nur lesend).",
    ["# lokal: tarfile.open(<backup>.tar.gz).getmembers() / extractfile(<pfad>)"]),
("cmd", "Einzelne Datei wiederherstellen", "/etc/pve/nodes/<alter-node>/... wird automatisch "
    "auf den aktuellen lokalen Node-Namen umgemappt (per hostname ermittelt); das "
    "Ausführbar-Bit aus dem Archiv wird auf dem Zielsystem erhalten (wichtig für "
    "cron-run-parts-Skripte).",
    ["hostname   # aktuellen Node-Namen ermitteln, für Pfad-Remapping",
     "[ -e <ziel> ] && echo __EXISTS__ || echo __NO__   # Überschreib-Schutz ohne \"Überschreiben\"",
     "mkdir -p <zielverzeichnis>",
     "echo <base64-inhalt> | base64 -d > <ziel>",
     "chmod +x <ziel>   # nur falls im Archiv als ausführbar markiert"]),
("cmd", "Alle Gast-Configs auf einmal wiederherstellen", "Bulk-Variante der Einzel-Wiederherstellung, überspringt vorhandene Configs ohne Überschreiben-Option.",
    ["# je gefundener .../<qemu-server|lxc>/<vmid>.conf: wie \"Einzelne Datei wiederherstellen\""]),
("cmd", "Pakete nachinstallieren", "Nutzt die im Backup gesicherte dpkg --get-selections-Liste; "
    "gefiltert auf install/hold (additiv — entfernt nie Pakete). Läuft als Hintergrund-Task, da "
    "apt-get lange dauern kann. Voraussetzung: Die Paketquellen (APT) wurden zuvor "
    "wiederhergestellt.",
    ["echo <base64-selektionen> | base64 -d | dpkg --set-selections",
     "apt-get update -qq || true",
     "apt-get -y dselect-upgrade"]),
("cmd", "Ad-hoc-Ziel: Verbindungstest / Tool-Key installieren", "Für einen noch nicht "
    "registrierten, frisch installierten Host per IP + Passwort (nie gespeichert).",
    ["hostname; pveversion   # Verbindungstest",
     "mkdir -p ~/.ssh && chmod 700 ~/.ssh",
     "grep -qxF <public_key> ~/.ssh/authorized_keys || echo <public_key> >> ~/.ssh/authorized_keys"]),

("h2", "6.13 Host-Config-Backup"),
("p", "Erstellt ein Konfigurations-Backup eines Proxmox-Hosts (ausdrücklich KEINE VM-/CT-"
      "Festplatten). Das Skript läuft als einzelnes Bash-Kommando auf dem Host, staged alle "
      "Dateien in ein temporäres Verzeichnis und packt sie am Ende zu einem tar.gz, das per "
      "SFTP in das Datenvolume des Containers geholt wird."),
("cmd", "Erfasste Inhalte im Detail", "",
    ["tar -C /etc/pve [--exclude=priv] -cf - . | tar -C $STAGE/etc/pve -xf -   # Cluster-Konfig",
     "cp -a /etc/network/interfaces /etc/hosts /etc/resolv.conf /etc/hostname $STAGE/  # Netzwerk",
     "cp -a /etc/udev/rules.d/*net*.rules /etc/systemd/network/*.link $STAGE/  # NIC-Namens-Artefakte",
     "ethtool -i <nic>; udevadm info -q property <nic>   # NIC-Identität (MAC/Treiber/Pfad)",
     "tar -C /etc/apt --exclude=auth.conf* -cf - . | tar -C $STAGE/etc/apt -xf -   # APT-Repos + Keys",
     "cat /root/.ssh/authorized_keys > $STAGE/root/.ssh/authorized_keys   # nur Public Keys",
     "cp -a /etc/cron.d /etc/cron.{hourly,daily,weekly,monthly}/zfs-auto-snapshot $STAGE/  # Retention",
     "cp -a /etc/bashclub $STAGE/   # Replikations-Config",
     "cp -a /etc/modprobe.d/zfs.conf $STAGE/   # ARC-Limit",
     "pveversion -v; dpkg --get-selections; ip -d address show; ip route show; "
     "zpool status; zpool list; zfs list; pvecm status   # Befehls-Snapshots",
     "tar -C $STAGE -czf <ziel>.tar.gz ."]),
("cmd", "Backup abrufen / auflisten / löschen", "",
    ["# SFTP-Get der fertigen Datei ins Docker-Volume /app/data/host-backups/<host>/",
     "# rein lokale Datei-Operationen für Liste/Löschen/Bereinigung (keine SSH-Befehle)"]),
("note", "Der Scheduler entscheidet dateibasiert (Zeitstempel im Dateinamen) und nicht über "
         "In-Memory-Zustand, ob ein geplantes Backup fällig ist — das übersteht Container-"
         "Neustarts und holt verpasste Läufe nach, statt sie stillschweigend zu überspringen."),

("h2", "6.14 Wake-on-LAN"),
("cmd", "MAC-Adresse erfassen (während der Host online ist)", "",
    ["ip route show default   # Interface der Default-Route ermitteln",
     "cat /sys/class/net/<interface>/address"]),
("cmd", "Magic Packet senden", "Wird sowohl direkt vom Container als auch als Relais über jeden "
    "anderen erreichbaren Host gesendet — ein gebrücktes Docker-Netzwerk kann meist nicht ins "
    "LAN hinein broadcasten, ein benachbarter PVE-Knoten schon.",
    ["# lokal: UDP-Broadcast des Magic-Packets an Port 9",
     "python3 -c \"...\"   # auf einem anderen Host per SSH: identisches Broadcast-Skript, als Relais"]),
("note", "„Erwartet offline“ (UI-Label; intern weiterhin als Flag standby in hosts.json geführt, "
         "Umschalten via POST /api/hosts/standby). Der Erreichbarkeits-Monitor "
         "erfasst seine Zustandswechsel dann weiterhin, sendet aber keine Offline-/Online-"
         "Benachrichtigungen, und das Dashboard zählt ihn separat (neutral, nicht als offline). "
         "Kein SSH-Befehl beteiligt — reine Konfigurations-/Anzeigelogik."),

("h2", "6.15 Benachrichtigungen (externe HTTP-Aufrufe, kein SSH)"),
("table",
    ["Kanal", "Aufruf"],
    [
        ["Telegram", "POST https://api.telegram.org/bot<token>/sendMessage\nPOST .../sendDocument (PDF-Anhang)"],
        ["Gotify", "POST <server_url>/message?token=<token>"],
        ["Matrix", "PUT <homeserver>/_matrix/client/v3/rooms/<room>/send/m.room.message/<txn>\nPOST .../media/v3/upload (Datei-Anhang)"],
        ["E-Mail", "SMTP an <host>:<port> (STARTTLS/SSL/unverschlüsselt, je nach Konfiguration)"],
    ],
    [], [55*72/25.4, None],
),

("h2", "6.16 KI-Berichte (externe HTTP-Aufrufe, kein SSH)"),
("p", "Für die Datensammlung selbst nutzt ai_reports.py dieselben zfs_commands.py-Funktionen "
      "wie die übrige Oberfläche (Pools, Datasets, Snapshots, ARC, Events, SMART, ggf. ZDB bei "
      "degradierten Pools). Nur der eigentliche KI-Aufruf ist ein externer HTTP-Request:"),
("table",
    ["Provider", "Aufruf"],
    [
        ["OpenAI-kompatibel / Custom", "POST <base_url>/chat/completions"],
        ["Anthropic", "POST https://api.anthropic.com/v1/messages"],
        ["Ollama", "POST <base_url>/api/chat, GET <base_url>/api/tags (Modellliste)"],
    ],
    [], [55*72/25.4, None],
),

# =====================================================================
("h1", "7. Datenfluss-Beispiele"),
("h2", "7.1 Aufruf der Startseite / des Dashboards"),
("numbered", [
    "Frontend ruft GET /api/dashboard auf.",
    "analytics.dashboard() liest ausschließlich aus der SQLite-Datenbank (pool_metrics, "
    "monitor_state, audit_log) sowie load_hosts() — es wird KEIN SSH-Befehl in diesem Aufruf "
    "ausgeführt, die Daten stammen aus dem letzten Sampler-Durchlauf.",
    "Ergebnis: Host-Erreichbarkeit, Pool-Gesundheit je Host, Kapazitätswarnungen, veraltete "
    "Auto-Snapshots, Audit-Fehler der letzten 24 h.",
]),
("h2", "7.2 Ein Sampler-Zyklus (alle 15 Minuten)"),
("numbered", [
    "Für jeden registrierten Host: Erreichbarkeit prüfen (SSH-Verbindungsaufbau).",
    "zpool list und je Pool zpool status (Fehlerzähler) abrufen.",
    "Ergebnis in pool_metrics schreiben.",
    "Falls smartmontools installiert: SMART-Sammlung (ein SSH-Rundlauf für alle Platten), "
    "Ergebnis in disk_metrics schreiben.",
    "monitor.run_checks() aufrufen — vergleicht neue Werte mit monitor_state und feuert bei "
    "Zustandswechseln Benachrichtigungen.",
    "replication_monitor.run_checks_for_host() — Lag je Replikations-Paar neu berechnen.",
    "Aufbewahrungs-Bereinigung: abgelaufene Zeilen in pool_metrics/disk_metrics/audit_log "
    "löschen, WAL-Checkpoint.",
]),

# =====================================================================
("h1", "8. Deployment"),
("h2", "8.1 Docker-Image"),
("p", "Basis: python:3.13-slim. Zusätzlich installiert: openssh-client (für ssh-keygen/"
      "ssh-keyscan, falls lokal statt über Paramiko genutzt), tzdata (Zeitzonen-Datenbank), "
      "fonts-dejavu (für UTF-8-fähige PDF-Berichte). Die Anwendung wird nach /app kopiert, "
      "Python-Abhängigkeiten aus requirements.txt installiert."),
("h2", "8.2 entrypoint.sh"),
("numbered", [
    "Erzeugt bei Erststart ein Ed25519-Schlüsselpaar unter /root/.ssh/id_ed25519 (Volume "
    "ssh-keys), falls noch keins existiert.",
    "Gibt den öffentlichen Schlüssel im Log aus (auch über die Startseite abrufbar).",
    "Startet Gunicorn (siehe 2.2 für die Begründung von --workers 1, gthread, kein --preload).",
]),
("h2", "8.3 docker-compose.yml"),
("cmd", "Minimalbeispiel", "",
    ["services:",
     "  zfs-tool:",
     "    image: ghcr.io/onlinecrash24/pve-zfs-tool:latest",
     "    ports: [\"5000:5000\"]",
     "    volumes:",
     "      - ssh-keys:/root/.ssh",
     "      - zfs-data:/app/data",
     "    environment:",
     "      - SECRET_KEY=... ADMIN_USER=... ADMIN_PASSWORD=...",
     "      - FORCE_HTTPS=true TZ=Europe/Berlin DEFAULT_LANG=de"]),
("h2", "8.4 Umgebungsvariablen"),
("table",
    ["Variable", "Standard", "Bedeutung"],
    [
        ["SECRET_KEY", "dev-key-change-me", "Flask-Session-Secret — MUSS geändert werden (sonst wird beim Start ein zufälliger Schlüssel erzeugt, der bei jedem Neustart wechselt und alle Sitzungen invalidiert)"],
        ["ADMIN_USER / ADMIN_PASSWORD", "admin / password", "Login-Zugangsdaten — MÜSSEN geändert werden"],
        ["FORCE_HTTPS", "true", "Secure-Flag für Session-Cookies; auf false setzen, wenn nicht hinter HTTPS-Proxy"],
        ["TZ", "UTC", "Zeitzone für Zeitpläne und Reports"],
        ["DEFAULT_LANG", "en", "Standard-UI-Sprache für neue Besucher (de/en); pro Browser umschaltbar"],
        ["LOG_LEVEL", "INFO", "Python-Logging-Level (stdout, sichtbar via docker compose logs)"],
        ["METRICS_RETENTION_DAYS", "90", "Aufbewahrung Pool-/Disk-Metriken; <=0 = unbegrenzt"],
        ["AUDIT_RETENTION_DAYS", "365", "Aufbewahrung Audit-Log; <=0 = unbegrenzt"],
        ["METRICS_SAMPLE_INTERVAL", "900", "Sampler-Intervall in Sekunden"],
        ["SSH_POOL", "1", "SSH-Verbindungs-Pooling; 0 deaktiviert es (jede Anfrage neu verbinden)"],
        ["PROMETHEUS_TOKEN", "(nicht gesetzt)", "Bearer-Token für /metrics; ohne Token bleibt der Endpunkt 404"],
    ],
    [1], [45*72/25.4, 32*72/25.4, None],
),
("h2", "8.5 Persistente Volumes"),
("table",
    ["Volume", "Pfad im Container", "Inhalt"],
    [
        ["ssh-keys", "/root/.ssh", "Ed25519-Schlüsselpaar + known_hosts"],
        ["zfs-data", "/app/data", "hosts.json, notifications.json, ai_reports.json, "
         "snapcheck_tags.json, host-backups/, pvezfs.db (SQLite)"],
    ],
),
("h2", "8.6 Reverse Proxy"),
("p", "Die Anwendung enthält ProxyFix-Middleware und vertraut X-Forwarded-*-Headern von einem "
      "vorgeschalteten Proxy (nginx, Caddy, Nginx Proxy Manager, Traefik). Bei FORCE_HTTPS=true "
      "werden Session-Cookies mit dem Secure-Flag versehen — der Proxy muss dann tatsächlich "
      "TLS terminieren."),

# =====================================================================
("h1", "9. API-Endpunkt-Referenz"),
("p", "Alle Endpunkte außer /login, /api/login, /static/* und /metrics erfordern eine "
      "authentifizierte Sitzung; schreibende Endpunkte zusätzlich einen gültigen "
      "X-CSRF-Token-Header."),
("table",
    ["Bereich", "Beispiel-Endpunkte"],
    [
        ["Auth", "/api/login, /api/logout, /api/csrf-token, /api/ssh-key/rotate"],
        ["Hosts", "/api/hosts, /api/hosts/test, /api/hosts/wol, /api/hosts/standby, /api/public-key"],
        ["Pools", "/api/pools, /api/pools/status, /api/pools/scrub, /api/pools/upgrade, /api/pools/history"],
        ["Datasets", "/api/datasets, /api/datasets/create, /api/datasets/destroy, /api/datasets/property"],
        ["Snapshots", "/api/snapshots, /api/snapshots/rollback, /api/snapshots/clone, /api/snapshots/diff"],
        ["Auto-Snapshot", "/api/auto-snapshot/status, /api/auto-snapshot/retention, /api/auto-snapshot/install"],
        ["Host-Backup", "/api/host-backup/create, /api/host-backup/list, /api/host-backup/schedule"],
        ["Proxmox-Gäste", "/api/pve/guests, /api/pve/guest-action, /api/pve/guest-replication"],
        ["Datei-Restore", "/api/restore/mount, /api/restore/browse, /api/restore/file, /api/restore/zvol/*"],
        ["Zustand", "/api/health/arc, /api/health/events, /api/health/smart, /api/health/snapshot-check"],
        ["Metriken", "/api/metrics/pools, /api/metrics/series, /api/metrics/disks, /api/metrics/disk-series"],
        ["Replikation", "/api/replication/install, /api/replication/config, /api/replication/cron, "
         "/api/replication/health, /api/replication/checkzfs"],
        ["Disaster Recovery", "/api/dr/replicas, /api/dr/reverse-sync, /api/dr/reverse-precheck"],
        ["Config Restore", "/api/dr/backup-contents, /api/dr/restore-file, /api/dr/restore-all-guests, "
         "/api/dr/adhoc-test, /api/dr/install-key, /api/dr/reinstall-packages"],
        ["Benachrichtigungen", "/api/notifications/config, /api/notifications/test/*"],
        ["KI-Berichte", "/api/ai/config, /api/ai/report, /api/ai/chat, /api/ai/report/pdf/<id>"],
        ["Audit / Cache", "/api/audit, /api/cache/stats, /api/cache/invalidate"],
        ["Prometheus", "/metrics (Bearer-Token via PROMETHEUS_TOKEN)"],
    ],
    [], [40*72/25.4, None],
),

# =====================================================================
("h1", "10. Wartung & Troubleshooting"),
("h2", "10.1 Logs"),
("cmd", "Live-Logs ansehen", "Root-Logger ist explizit auf stdout konfiguriert (LOG_LEVEL, "
    "Standard INFO) — ohne diese Konfiguration würden log.info()-Aufrufe der Hintergrund-Threads "
    "sonst standardmäßig verworfen.",
    ["docker compose logs -f zfs-tool"]),
("h2", "10.2 Cache-Statistik"),
("p", "GET /api/cache/stats liefert Trefferquote, Anzahl aktiver Einträge und Zähler für "
      "Treffer/Fehltreffer/Invalidierungen des SSH-Ergebnis-Caches."),
("h2", "10.3 Datenaufbewahrung anpassen"),
("p", "METRICS_RETENTION_DAYS und AUDIT_RETENTION_DAYS in der docker-compose.yml absenken, "
      "wenn das Datenvolume zu groß wird. SQLite gibt freigewordene Seiten für neue Einfügungen "
      "wieder frei — die Datei pendelt sich auf dem Arbeitsvolumen der eingestellten Retention "
      "ein, statt endlos zu wachsen; ein VACUUM zum sofortigen Verkleinern der Datei auf der "
      "Festplatte ist derzeit nicht automatisiert."),
("h2", "10.4 Tests"),
("p", "Der Quellcode wird von einer umfangreichen Pytest-Suite begleitet (Stand: über 400 "
      "Tests), die reine Funktionen (Parser, Validatoren, Klassifikations-Logik) ohne echte "
      "SSH-Verbindung prüft. Ausführen im Projektverzeichnis:"),
("cmd", "Test-Suite ausführen", "", ["python -m pytest -q"]),
]

if __name__ == "__main__":
    os.makedirs(DEFAULT_OUT_DIR, exist_ok=True)
    out = os.path.join(DEFAULT_OUT_DIR, "PVE-ZFS-Tool_Administratorhandbuch.pdf")
    build_guide(out, TITLE, SUBTITLE, META, CONTENT)
    print("OK:", out)
