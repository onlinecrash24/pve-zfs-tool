<p align="center">
  <img src="app/static/img/logo.png" alt="PVE ZFS Tool" width="500">
</p>

<p align="center">Eine Docker-basierte Web-Anwendung zur Verwaltung von ZFS-Pools, Datasets, Snapshots und Auto-Snapshots auf einem oder mehreren Proxmox-VE-Hosts via SSH.</p>

<p align="center">
  <a href="README.md">English</a> &middot; <b>Deutsch</b>
</p>

## Funktionen

### ZFS-Pool-Verwaltung
- **Pool-Übersicht** -- Status, IO-Statistiken, Health, Fragmentierung, Dedup-Ratio
- **Pool-Scrub** -- Scrubs direkt aus der UI starten mit automatischer Abschluss-Benachrichtigung
- **Pool-Upgrade** -- Erkennt automatisch, ob ein Feature-Upgrade verfügbar ist (grüner Button) inkl. Bestätigung vor dem Upgrade
- **Pool-History** -- Jüngste Pool-Aktivitäten einsehen

### Dataset-Verwaltung
- **Getrennte Ansichten** -- Filesysteme (LXC, Daten) und VM-Volumes in eigenen Bereichen mit typ-spezifischen Aktionen
- **Datasets anlegen** -- Neue Datasets mit optionalen Kompressions-Einstellungen
- **Properties** -- Alle ZFS-Dataset-Eigenschaften anzeigen und ändern

### Snapshot-Verwaltung
- **Interaktive Timeline** -- Visuelle Timeline nach Dataset gruppiert, neueste zuerst, farbkodierte Punkte (blau = neuester, auto vs. manuell unterschieden)
- **Tabellenansicht** -- Klassische Tabelle (Standard) mit Typ-Badges (zvol/filesystem), per Dropdown umschaltbar
- **Suche** -- Snapshots nach Dataset-Name filtern, in Timeline- und Tabellenansicht
- **Snapshots erstellen** -- Manuelle Snapshots mit eigenem Namen, rekursiv möglich
- **Rollback** -- Smart-Rollback erkennt VMs/LXC-Container automatisch, stoppt sie vor dem Rollback und startet sie danach neu
- **Klonen** -- Snapshots per Modal-Dialog klonen, mit Ziel-Datastore/Pool-Auswahl und editierbarem Klonnamen (Standard: `{name}_CLONE`); unterstützt Pool-übergreifendes Klonen via `zfs send | zfs recv`
- **Diff** -- Änderungen für Filesystem-Datasets (`zfs diff`) und zvol/VM-Snapshots (Inkrement-Send-Größen, Snapshot-Properties, Größenübersicht)
- **Löschen** -- Nur manuell angelegte Snapshots löschbar (Auto-Snapshots sind geschützt)

### Proxmox-VM/CT-Integration
- **Guest-Übersicht** -- Alle VMs und LXC-Container mit Status
- **Per-Guest-Snapshots** -- ZFS-Snapshots zu einer bestimmten VM bzw. Container anzeigen
- **Smart-Rollback** -- Stoppt VM/LXC automatisch vor dem Rollback und startet sie danach neu
- **LXC-Datei-Restore** -- Einzelne Dateien aus LXC-Container-Snapshots durchsuchen und wiederherstellen:
  - Mountet den Snapshot als readonly-Clone
  - Datei-Browser mit Breadcrumbs
  - Textdateien direkt im UI vorschauen
  - Einzelne Dateien oder ganze Verzeichnisse zurück in den laufenden Container restaurieren
  - Automatisches Cleanup: Restore-Clone wird beim Schließen des Browsers unmounted
- **VM-Datei-Restore** -- Dateien aus VM-Disk-Snapshots durchsuchen und herunterladen (Linux & Windows):
  - Automatisches `snapdev=visible`-Handling für zvol-Snapshot-Zugriff
  - Partitions-Erkennung via `kpartx` mit Dateisystem-Identifikation
  - Unterstützt ext4, xfs, btrfs (Linux), NTFS via ntfs-3g (Windows), vfat (EFI)
  - BitLocker/LUKS-verschlüsselte Partitionen werden erkannt und als nicht mountbar angezeigt
  - Automatisches Filtern nicht-mountbarer Typen (Swap, LVM, ZFS member, RAID, bcache, Ceph usw.)
  - Datei-Browser mit Vorschau und Download
  - Robustes Cleanup: kpartx-Mappings, dmsetup-Fallback, snapdev zurücksetzen
  - Cleanup beim Schließen des Modals, Tab-Schließen (sendBeacon) und über die Health-Seite

### Replikation (bashclub-zsync)
- **5-Schritt-Wizard** -- Quell-/Ziel-Host-Paar → Setup → Datasets → Konfiguration → Log, mit progressiver Freischaltung
- **Ein-Klick-Einrichtung** -- Installiert `bashclub-zsync` auf **beiden** Hosts über das offizielle deb822-APT-Repo (`apt.bashclub.org/release/`) und richtet passwortlosen SSH-Zugang vom Ziel zur Quelle ein (Key-Generierung, `ssh-keyscan` für `known_hosts`, `authorized_keys` ergänzen, BatchMode-Probe)
- **PVE-Erkennung** -- Pro Host PVE-Versions-Badge (warnt, wenn ein Host kein Proxmox VE ist)
- **Per-Source-Config-Dateien** -- Jedes Replikations-Paar lebt in einer eigenen `/etc/bashclub/<source-ip>.conf`, sodass mehrere Paare auf einem Ziel-Host nebeneinander existieren können (entspricht der Upstream-bashclub-Konvention)
- **Dataset-Tagging** -- Checkbox-Liste aller Quell-Datasets/Zvols; setzt bzw. entfernt die `bashclub:zsync`-User-Property (Wert `all`), damit der Upstream-Filter sie auch tatsächlich aufgreift
- **Ziel-Dataset-Helfer** -- Dropdown der vorhandenen Datasets auf dem Ziel plus „+ neu anlegen" (`zfs create -p -o com.sun:auto-snapshot=false`, Vorschlag: `rpool/repl`)
- **Vollständiges Konfigurations-Formular** -- 16 Felder analog zur Upstream-`/etc/bashclub/zsync.conf` (sshport, tag, snapshot_filter, min_keep, zfs_auto_snapshot_*, checkzfs_*); leere Felder werden beim Speichern automatisch durch Upstream-Defaults ersetzt, sodass die geschriebene Datei stets produktionsreif ist
- **Cron-Zeitplan-Verwaltung** -- Vorlagen-Dropdown (bashclub-Standard `20 0-22 * * *`, alle 15/30 Min, stündlich, 2h, 6h, täglich 03:00, frei konfigurierbar) mit Live-Preview, idempotentem Anlegen/Ersetzen/Entfernen, expliziter Reload für cron / cronie / systemd-cron
- **checkzfs-Statuspanel** -- Führt `checkzfs --source <ip>` auf dem Ziel aus und zeigt eine OK/WARN/CRIT-Übersicht plus gruppierte Tabelle; ANSI bereinigt, Filter „nur replizierte" standardmäßig aktiv
- **Multi-Paar-Übersicht** -- Listet alle konfigurierten Paare des Bestands (scannt auf jedem registrierten Host `/etc/bashclub/*.conf`); pro Zeile lädt „Öffnen" das Paar in den Wizard
- **Sicheres Löschen** -- Entfernt Cron-Eintrag + Config (mit Zeitstempel-Backup); optionale Checkbox löscht zusätzlich alle `zfs-auto-snap_*`-Snapshots unterhalb des Replikat-Ziels -- Datasets und zsync-Basis-Snapshots bleiben erhalten, Top-Level-Pools werden abgelehnt

### Snapshot-Check
- **Retention-Policy-Übersicht** -- Zeigt konfigurierte `--keep=N`-Werte pro Label aus Cron
- **Analyse pro Label** -- Snapshot-Gesamtzahl, Dataset-Anzahl, Durchschnitt pro Dataset, Alter des neuesten Snapshots
- **Gap-Erkennung** -- Identifiziert Lücken in Snapshot-Ketten, wenn diese `MAX_AGE * 1.5` übersteigen
- **Veraltete Datasets** -- Warnt, wenn Snapshots die Altersgrenzen überschreiten (frequent > 1 h, hourly > 2 h, daily > 25 h, weekly > 8 T, monthly > 32 T)
- **Count-Mismatches** -- Vergleicht tatsächliche Snapshot-Anzahl mit konfigurierter Retention (SOLL/IST)
- **Fehlende Labels** -- Erkennt Labels, die in Cron konfiguriert sind, aber im Dataset fehlen
- **Manuelle Snapshots** -- Listet Nicht-Standard-Snapshots (die keinem bekannten Auto-Snapshot-Label entsprechen)

### Health & Monitoring
- **ARC-Statistiken** -- Adaptive-Replacement-Cache Hit/Miss-Rate und Speichernutzung
- **ZFS-Events** -- Aktuelle ZFS-Kernel-Events
- **SMART-Status** -- Festplattenzustand aller Laufwerke pro Pool (aufgelöst via `/dev/disk/by-id/`)
- **LXC-Restore-Clone-Cleanup** -- Übrig gebliebene Restore-Mount-Datasets mit einem Klick entfernen
- **VM-Zvol-Restore-Sessions** -- Übersicht aktiver Mounts, kpartx-Mappings und snapdev-Status mit Einzel-Unmount und Sammel-Cleanup
- **Scheduled-Tasks-Übersicht** -- Zeigt aktive AI-Report-Schedules (per Host und kombiniert) mit nächster/letzter Ausführung
- **ZDB-Tiefendiagnose** -- Automatische `zdb`-Analyse für DEGRADED/FAULTED-Pools (Block-Statistiken, Disk-Labels)

### Historische Metriken (Trends)
- **Background-Sampler** -- Erfasst alle 15 Minuten pro Host Pool-Kapazität, Fragmentierung, Allokation, Health und Dedup-Ratio
- **SQLite-Speicherung** -- 90 Tage Retention in `/app/data/pvezfs.db` (WAL-Journaling)
- **Inline-Trend-Charts** -- Kapazität %, Fragmentierung %, Allokiert GB pro Pool als leichtgewichtiges Inline-SVG (kein externes JS)
- **Einstellbarer Zeitbereich** -- 6 h / 24 h / 7 T / 30 T / 90 T
- **Sample Now** -- Sofortige Messung auslösen, z. B. nach Hinzufügen eines neuen Hosts

### Audit-Log
- **Zustandsändernde Aktionen protokolliert** -- Login Erfolg/Fehler, Host hinzufügen/entfernen, Pool Scrub/Upgrade, Dataset erstellen/löschen/setzen, Snapshot erstellen/löschen/rollback/klonen, Auto-Snapshot-Toggle, Datei- & Zvol-Restores, Cache-Invalidation, Speichern von Notification-/AI-Einstellungen
- **Erfasste Felder** -- Timestamp, User, IP, Host, Action-Code, Ziel, JSON-Details, Erfolgs-Flag
- **Filterbare UI** -- Nach Action, User, aktuellem Host oder nur Fehlern filtern
- **SQLite-basiert** -- Indiziert für schnelle Queries, persistent über Neustarts hinweg

### Performance
- **SSH-Command-Cache** -- TTL-basierter In-Memory-Cache (15–300 s) für lesende ZFS-Abfragen reduziert SSH-Round-Trips auf aktiven Seiten drastisch; Schreibvorgänge invalidieren den Cache für den betroffenen Host automatisch
- **Cache-Stats-API** -- `/api/cache/stats` zeigt Hit-Rate und Anzahl Einträge für Ops-Transparenz

### Benachrichtigungen
- **Telegram** -- Benachrichtigungen via Telegram-Bot, PDF-Reports als Dokumente
- **Gotify** -- Benachrichtigungen via selbst gehostetem Gotify-Server (nur Text)
- **Matrix** -- Benachrichtigungen via Matrix-Raum (Client-Server-API v3), PDF-Reports als `m.file`-Anhänge
- **Email (SMTP)** -- Benachrichtigungen und PDF-Reports an einen oder mehrere Empfänger; STARTTLS, SSL/TLS oder Klartext
- **Scrub-Monitor** -- Background-Thread erkennt Scrub-Abschluss und sendet automatisch eine Benachrichtigung
- **Live-Dashboard** -- Die Startseite zeigt Host-Status, Pool-Health, Kapazität, freien Platz und eine lineare-Regressions-Prognose „voll in X Tagen" auf Basis der 30-Tage-Messdaten
- **Kapazitäts-Prognose** -- `/api/forecast?host=...&pool=...` liefert die geschätzten Tage, bis der Pool zu 100 % belegt ist (Least-Squares auf `alloc_bytes`)
- **Prometheus-Exporter** -- `/metrics` im Prometheus-Text-Format (opt-in: Env-Variable `PROMETHEUS_TOKEN` setzen; Zugriff via `Authorization: Bearer <token>` oder `?token=`). Exponiert Host-Erreichbarkeit, Pool size/alloc/free/capacity/fragmentation, Pool-Health (one-hot), I/O-Fehlersummen, Kapazitäts-Prognose und Scrape-Timestamp
- **Proaktives Monitoring** -- Der Sampler prüft alle 15 Min pro Host auf Zustandsänderungen und feuert Benachrichtigungen (keine manuelle Aktion nötig):
  - `pool_error` -- Pool-Health wechselt ONLINE → DEGRADED/FAULTED/UNAVAIL und zurück
  - `health_warning` -- Kapazität überschreitet 90 % bzw. Read/Write/Checksum-Errors treten auf
  - `host_offline` -- SSH-Probe schlägt fehl, wo sie zuvor erfolgreich war (inkl. Recovery)
  - `auto_snapshot` -- Neuester Auto-Snap pro Label (frequent/hourly/daily/weekly/monthly) älter als erwartet (pro Host/Label auf einmal täglich begrenzt)
- **Test-Benachrichtigungen** -- Testnachricht pro Kanal senden, um die Konfiguration zu prüfen
- **Konfigurierbare Events** -- Benachrichtigungen pro Event-Typ aktivieren/deaktivieren:
  - Scrub gestartet/abgeschlossen
  - Snapshot erstellt/gelöscht
  - Rollback durchgeführt
  - Pool-Fehler / Degraded-Status
  - Pool-Upgrade
  - Zustandswarnungen
  - Host offline
  - Datei-Restore-Aktionen
  - AI-Report generiert (mit PDF-Anhang)

### AI-Reports & Analyse
- **Multi-Provider** -- OpenAI (GPT), Anthropic (Claude), Ollama (lokal) oder jede OpenAI-kompatible API
- **Ollama-Modell-Erkennung** -- Verfügbare Modelle automatisch aus der Ollama-Instanz abfragen und auswählen
- **Per-Host- und kombinierte Schedules** -- Unabhängige tägliche/wöchentliche Pläne pro Host plus ein optionaler „All-Hosts"-Report
- **Umfassende Analyse** -- Pool-Health, Speicherkapazität, Scrub-Status, Snapshot-Abdeckung, SMART-Health, Anomalien
- **Snapshot-Retention-Analyse** -- Per-Dataset-per-Label Retention-Check, Gap-Erkennung, Warnungen für veraltete Snapshots
- **ZDB-Diagnose** -- Automatische Tiefenanalyse bei degraded/faulted Pools
- **Umsetzbare Empfehlungen** -- Priorisierte Vorschläge zu Scrubs, Cleanup, Kapazitätsplanung
- **Interaktiver Chat** -- Rückfragen zu deinen ZFS-Daten stellen
- **Notification-Integration** -- Reports via Telegram, Gotify, Matrix oder Email; PDF-Anhang auf Email, Telegram und Matrix unterstützt
- **Zweisprachige Reports** -- Reports folgen der globalen UI-Sprache (Englisch/Deutsch)
- **Anpassbarer System-Prompt** -- AI-Prompt bearbeiten, um Fehlalarme in deiner Umgebung zu reduzieren
- **PDF-Export** -- Reports als PDF herunterladen oder automatisch zustellen lassen
- **Raw-Data-Export** -- JSON-Payload exportieren, das an das LLM gesendet würde

### Sicherheit
- **CSRF-Schutz** -- Token-basierter Schutz für alle zustandsändernden Requests
- **Input-Validation** -- Whitelist-basierte Prüfung aller Parameter vor Shell-Ausführung
- **Rate-Limiting** -- Brute-Force-Schutz am Login (5 Versuche, 5 Min Sperre)
- **Sichere Sessions** -- HttpOnly-Cookies, SameSite=Lax, konfigurierbares Secure-Flag, 8 h Timeout
- **Security-Header** -- CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy
- **Path-Traversal-Schutz** -- Realpath-Validation inkl. Symlink-Attack-Absicherung
- **SSH-Host-Key-Verification** -- Trust On First Use (TOFU), Known-Hosts gespeichert und bei Folgeverbindungen verifiziert
- **SSH-Key-Rotation** -- Ein-Klick-Rotation von der Startseite; erzeugt neues Ed25519-Keypair, rollt es auf alle Hosts aus und entfernt den alten erst nach Verifikation (Aussperren ausgeschlossen); per-Host-Status für Deploy/Verify/Cleanup
- **Reverse-Proxy-ready** -- ProxyFix-Middleware für korrekte HTTPS-Erkennung hinter NPM, nginx, Caddy

### Authentifizierung & i18n
- **Login** -- Session-basierte Authentifizierung, Zugangsdaten über Umgebungsvariablen konfigurierbar
- **Sprachumschaltung** -- Englisch und Deutsch mit sofortigem UI-Re-Render, persistiert in localStorage
- **Session-Management** -- Automatische Weiterleitung zum Login bei Session-Ablauf (401-Handling)

### Multi-Host-SSH
- **Automatische SSH-Key-Erzeugung** -- Ed25519-Keypair beim ersten Start erzeugt
- **Public-Key-Anzeige** -- Auf der Startseite mit Copy-Button (funktioniert unter HTTP und HTTPS)
- **Mehrere Hosts** -- Mehrere Proxmox-VE-Nodes hinzufügen und verwalten
- **Connection-Test** -- SSH-Konnektivität pro Host prüfen

## Quick Start

### Option 1: Docker Compose mit GHCR-Image (empfohlen)

`docker-compose.yml` anlegen:

```yaml
services:
  zfs-tool:
    image: ghcr.io/onlinecrash24/pve-zfs-tool:latest
    container_name: zfs-tool
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - ssh-keys:/root/.ssh
      - zfs-data:/app/data
    environment:
      - SECRET_KEY=your-secret-key-here       # UNBEDINGT ÄNDERN!
      - ADMIN_USER=admin                      # UNBEDINGT ÄNDERN!
      - ADMIN_PASSWORD=your-strong-password    # UNBEDINGT ÄNDERN!
      - FORCE_HTTPS=true                      # false, wenn nicht hinter HTTPS-Proxy
      - TZ=UTC                                # Zeitzone (z. B. Europe/Berlin)

volumes:
  ssh-keys:
  zfs-data:
```

```bash
docker compose up -d
```

### Option 2: Manueller Build aus den Sourcen

```bash
git clone https://github.com/onlinecrash24/pve-zfs-tool.git
cd pve-zfs-tool
docker compose up -d --build
```

---

Web-UI öffnen unter `http://DOCKER-HOST-IP:5000`

> **Wichtig:** `SECRET_KEY`, `ADMIN_USER` und `ADMIN_PASSWORD` vor dem Produktiv-Einsatz ändern. Beim Start werden Warnungen geloggt, wenn noch Standardwerte aktiv sind.

## Einrichtung

1. **Container starten** -- Das SSH-Keypair wird beim ersten Start automatisch erzeugt.
2. **Login** -- Web-UI öffnen und mit den in `docker-compose.yml` hinterlegten Zugangsdaten anmelden.
3. **Public Key kopieren** -- Der Public Key wird auf der Startseite angezeigt. Kopieren.
4. **Zu Proxmox-Hosts hinzufügen** -- Key in `~/.ssh/authorized_keys` auf jedem Proxmox-Host einfügen:
   ```bash
   echo "ssh-ed25519 AAAA... zfs-tool@docker" >> /root/.ssh/authorized_keys
   ```
5. **Hosts in der UI hinzufügen** -- Unter „Hosts" Name, IP, Port und User eintragen.
6. **Verbindung testen** -- „Test" klicken, um die SSH-Konnektivität zu prüfen.
7. **ZFS verwalten** -- Host oben im Dropdown wählen und Pools, Snapshots usw. erkunden.

## Voraussetzungen auf dem Proxmox-Host (optional)

Für **VM-Datei-Restore** müssen folgende Pakete auf dem/den Proxmox-Host(s) installiert sein:

```bash
apt install kpartx          # Erforderlich — Partitions-Erkennung für zvol-Snapshots
apt install ntfs-3g         # Optional — nur für Windows-VM-NTFS-Partitionen
```

> `kpartx` ist oft bereits als Teil von `multipath-tools` installiert. Prüfen mit `which kpartx`.

## HTTPS mit Reverse Proxy (empfohlen)

Für Produktiv-Deployments den Container hinter einen HTTPS-Reverse-Proxy stellen. Die Anwendung enthält `ProxyFix`-Middleware und vertraut automatisch den `X-Forwarded-*`-Headern des Proxys.

In `docker-compose.yml` `FORCE_HTTPS=true` setzen, um sichere Session-Cookies zu aktivieren.

### Nginx Proxy Manager (NPM)

1. Neuen Proxy-Host in NPM anlegen
2. Forward-Hostname auf die Docker-Host-IP (bzw. Container-Name, wenn im selben Docker-Netzwerk) setzen
3. Forward-Port auf `5000`
4. SSL im SSL-Tab via Let's Encrypt aktivieren
5. Unter **Advanced** keine zusätzliche Konfiguration nötig -- NPM setzt alle erforderlichen Header automatisch

> **Tipp:** Wenn NPM und zfs-tool auf demselben Docker-Host laufen, beide ins selbe Docker-Netzwerk legen für zuverlässige Erreichbarkeit.

### Caddy (automatisches TLS)

```
zfs.example.com {
    reverse_proxy localhost:5000
}
```

### nginx

```nginx
server {
    listen 443 ssl;
    server_name zfs.example.com;
    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## Benachrichtigungen einrichten

### Telegram
1. Über [@BotFather](https://t.me/BotFather) einen Bot anlegen
2. Chat-ID via [@userinfobot](https://t.me/userinfobot) oder [@getidsbot](https://t.me/getidsbot) abrufen
3. Für Gruppen-Benachrichtigungen den Bot der Gruppe hinzufügen und die Gruppen-Chat-ID verwenden (beginnt mit `-100`)
4. Bot-Token und Chat-ID in den Notification-Einstellungen hinterlegen
5. „Send Test" klicken, um die Einrichtung zu prüfen

### Gotify
1. [Gotify](https://gotify.net/)-Server einrichten
2. In Gotify eine Application anlegen und das App-Token kopieren
3. Server-URL und Token in den Notification-Einstellungen eintragen
4. „Send Test" klicken

### Matrix
1. Homeserver-URL ermitteln (z. B. `https://matrix.org`)
2. Access-Token aus Element holen: Settings → Help & About → Access Token
3. Raum-ID (z. B. `!abc123:matrix.org`) in den Raum-Einstellungen in Element ablesen
4. Homeserver-URL, Access-Token und Raum-ID in den Notification-Einstellungen eintragen
5. „Send Test" klicken

## Prometheus-Integration (optional)

Die Umgebungsvariable `PROMETHEUS_TOKEN` setzen, um den `/metrics`-Endpoint zu aktivieren (ansonsten `404`). Beispiel Prometheus-Scrape-Config:

```yaml
scrape_configs:
  - job_name: pvezfs
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials: dein-langes-zufaelliges-token
    static_configs:
      - targets: ['zfs-tool.example.com']
```

Exportierte Metriken u. a.: `pvezfs_host_reachable`, `pvezfs_pool_capacity_percent`, `pvezfs_pool_size_bytes`, `pvezfs_pool_alloc_bytes`, `pvezfs_pool_free_bytes`, `pvezfs_pool_fragmentation_percent`, `pvezfs_pool_health{state="…"}`, `pvezfs_pool_error_total_sum`, `pvezfs_pool_forecast_days_until_full` und ein Scrape-Timestamp.

### Fertiger Monitoring-Stack (Prometheus + Grafana, optional)

Das Repo enthält ein einsatzfertiges Prometheus + Grafana-Setup als
opt-in-Profile in derselben `docker-compose.yml`. Grafana lädt ein
„PVE ZFS Tool"-Dashboard mit allen Metriken vorverkabelt.

```bash
# Token erzeugen und in .env ablegen (wird von beiden Containern gelesen)
echo "PROMETHEUS_TOKEN=$(openssl rand -hex 32)" > .env

# Optional Grafana-Admin-Passwort überschreiben
echo "GRAFANA_ADMIN_PASSWORD=bitte-aendern" >> .env

# zfs-tool + prometheus + grafana starten
docker compose --profile monitoring up -d
```

Anschließend:

- Grafana: <http://DOCKER-HOST:3000> (Login `admin` / euer Passwort) —
  das Dashboard liegt unter **Dashboards → ZFS → PVE ZFS Tool**
- Prometheus: <http://DOCKER-HOST:9090> (nur falls man Roh-Serien
  inspizieren möchte; `--web.enable-lifecycle` ist aktiv, also kann
  die Config per `curl -XPOST http://DOCKER-HOST:9090/-/reload` neu
  geladen werden).

Die Provisioning-Dateien liegen unter `docker/prometheus/` und
`docker/grafana/` — gerne kopieren und das Dashboard erweitern.

## Konfiguration

### Umgebungsvariablen

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `SECRET_KEY` | `dev-key-change-me` | Flask-Session-Secret -- **muss geändert werden!** |
| `ADMIN_USER` | `admin` | Login-Benutzername -- **sollte geändert werden** |
| `ADMIN_PASSWORD` | `password` | Login-Passwort -- **muss geändert werden!** |
| `FORCE_HTTPS` | `true` | Sichere Session-Cookies -- auf `false` setzen, wenn nicht hinter HTTPS-Proxy |
| `TZ` | `UTC` | Zeitzone für Reports und Scheduler (z. B. `Europe/Berlin`, `America/New_York`) |
| `PROMETHEUS_TOKEN` | _(nicht gesetzt)_ | Opt-in Bearer-Token für `/metrics`. Wenn nicht gesetzt, ist der Prometheus-Exporter deaktiviert |

### Persistente Volumes

| Volume | Pfad | Beschreibung |
|--------|------|--------------|
| `ssh-keys` | `/root/.ssh` | SSH-Keypair (persistent über Neustarts) |
| `zfs-data` | `/app/data` | Host-Config, Notification-Einstellungen, AI-Reports, SSH-Known-Hosts |

## Tech-Stack

- **Backend** -- Python 3.12, Flask, Paramiko (SSH), Gunicorn, fpdf2
- **Frontend** -- Vanilla-JavaScript-SPA, CSS-Dark-Theme
- **Deployment** -- Docker, Docker Compose, GitHub Container Registry

## Projekt-Struktur

```
pve-zfs-tool/
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── requirements.txt
└── app/
    ├── main.py              # Flask-API-Routen & Authentifizierung
    ├── ssh_manager.py       # SSH-Verbindung, Host-Verwaltung, Key-Rotation
    ├── zfs_commands.py      # ZFS-Command-Wrapper via SSH (gecachte Reads)
    ├── validators.py        # Input-Validation (whitelist-basiert)
    ├── cache.py             # TTL-In-Memory-Cache für SSH-Ergebnisse
    ├── database.py          # Gemeinsame SQLite (Metriken / Audit / Monitor-State)
    ├── metrics.py           # Background-Sampler + Pool-Zeitreihen-Queries
    ├── monitor.py           # Proaktive Zustandswechsel-Benachrichtigungen
    ├── analytics.py         # Dashboard-Aggregation, Forecast, Prometheus
    ├── audit.py             # Audit-Log Writer und Query-API
    ├── ai_reports.py        # AI-gestützte ZFS-Analyse & Reports
    ├── ai_pdf.py            # PDF-Report-Erzeugung
    ├── snapshot_analysis.py # Gemeinsame Snapshot-Health-Analyse (UI + AI)
    ├── timezone.py          # Zeitzonen-Helper (TZ-Umgebungsvariable)
    ├── notifications.py     # Telegram, Gotify, Matrix & Email Notifications
    ├── replication.py       # bashclub-zsync-Integration (Install, Config, Cron, checkzfs)
    ├── templates/
    │   ├── index.html       # Single-Page-Application
    │   └── login.html       # Login-Seite
    └── static/
        ├── css/style.css    # Dark-Theme-UI
        ├── js/app.js        # Frontend-Logik
        └── js/i18n.js       # EN/DE-Übersetzungen
```

## Lizenz

MIT
