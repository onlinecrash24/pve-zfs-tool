<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="app/static/img/logo.svg">
    <img src="app/static/img/logo-transparent.svg" alt="PVE ZFS Tool" width="500">
  </picture>
</p>

<p align="center">Eine Docker-basierte Web-Anwendung zur Verwaltung von ZFS-Pools, Datasets, Snapshots und Auto-Snapshots auf einem oder mehreren Proxmox-VE-Hosts via SSH.</p>

<p align="center">
  <a href="README.md">English</a> &middot; <b>Deutsch</b>
</p>

## Worum es geht

ZFS über mehrere Proxmox-VE-Hosts von einer Stelle verwalten: eine einzelne
Datei aus einem VM- oder LXC-Snapshot zurückholen, `bashclub-zsync`-Replikation
per Assistent einrichten, und je Gast sehen, ob er tatsächlich ein Backup **und**
ein Replikat hat — nicht nur, ob ein Job existiert.

Die Verbindung läuft über SSH, ausgeführt werden dieselben Befehle, die du auch
tippen würdest (`zfs`, `zpool`, `qm`, `pct`, `pvesh`). Auf den Hosts wird nichts
installiert.

## Für wen

Für alle, die **ZFS auf einem oder mehreren Proxmox-VE-Knoten** betreiben und
Snapshots, Replikation und Backups an einer Stelle sehen wollen, statt fünf
SSH-Sitzungen zu öffnen.

**Nicht das Richtige, wenn:**

- du auf deinen Proxmox-Hosts kein ZFS verwendest — dann trifft hier fast nichts zu
- du einen **PVE-Cluster-Manager** suchst. Das hier ist keiner; es ergänzt die
  Proxmox-Oberfläche, statt sie zu ersetzen
- du den **Proxmox Backup Server ersetzen** willst. Dieses Werkzeug *liest* deine
  Backups, um zu sagen, was geschützt ist — es erzeugt und löscht keine
- du einen Agenten auf jedem Host willst. Den gibt es bewusst nicht

## Was es kann, das andere nicht können

- **Datei-Level-Restore aus einem Snapshot.** Den zvol-Snapshot einer VM bis zur
  einzelnen Datei durchsuchen — Partitionen werden über `kpartx` erkannt, NTFS
  eingeschlossen (`ntfs-3g`) — und genau diese Datei zurückholen. Kein
  vollständiger Rollback, kein vorheriges Klonen des Gastes.
- **Replikate über die Snapshot-GUID zugeordnet.** Die GUID eines ZFS-Snapshots
  übersteht `send`/`recv`. Ein Replikat wird seiner Quelle also *beweisbar*
  zugeordnet statt über Namen oder Pfade geraten. Das macht auch den umgekehrten
  Fall sichtbar: Hält ein als Replikationsziel konfigurierter Host die neuesten
  Snapshots, ist die Replikation umgekehrt oder gestoppt — was man sonst erst
  beim Restore merkt.
- **Backup und Replikation zusammen bewertet.** Ein Gast mit drei Replikaten und
  ohne Backup übersteht eine kaputte Platte, aber keine Verschlüsselung. Ein Gast
  mit Backup und ohne Replikat ist abgedeckt. Die Übersicht sagt, was von beidem
  zutrifft — je Gast, statt Kopien zu zählen.
- **Ein Replikations-Assistent für `bashclub-zsync`**, der das Paket auf beiden
  Seiten installiert, passwortloses SSH vom Ziel zur Quelle einrichtet und die
  Konfiguration je Quelle schreibt.

**[→ Vollständige Funktionsliste](FEATURES_DE.md)**

## Worauf dieses Werkzeug zugreifen kann

Lohnt sich vor der Installation zu lesen — es hält bewusst weitreichenden Zugriff.

- **root-SSH auf jeden Host, den du einträgst.** Beim ersten Start wird ein
  Schlüsselpaar erzeugt; der private Teil bleibt im Volume `ssh-keys`
  (`/root/.ssh/id_ed25519`), den öffentlichen trägst du selbst in
  `authorized_keys` jedes Hosts ein. Alles — Pools auflisten, Snapshots anlegen
  und löschen, Gäste migrieren, Konfigurationen zurückspielen — läuft als root
  über diese Verbindung. Zugriff auf diese Weboberfläche ist gleichbedeutend mit
  einer Admin-SSH-Sitzung auf allen deinen Knoten.
- **Der Container läuft als root**, braucht aber weder `privileged` noch
  irgendeinen Host-Mount.
- **Der Web-Login ist die Tür davor.** Sitzungsbasiert, Zugangsdaten aus
  Umgebungsvariablen, mit Rate-Limit gegen Brute Force. Setze sie (siehe
  [Konfiguration](#umgebungsvariablen)) und stelle einen HTTPS-Proxy davor.
- **Standardmäßig verlässt nichts dein Netz.** Einzige Ausnahme sind die
  KI-Berichte, und die sind opt-in: ohne API-Schlüssel wird überhaupt nichts
  gesendet, mit Ollama bleibt alles lokal, und „Rohdaten exportieren" zeigt exakt
  das JSON, das übertragen würde.
- **`/metrics` antwortet mit 404**, solange `PROMETHEUS_TOKEN` nicht gesetzt ist.
- **Jede Zustandsänderung steht im Audit-Log** — wer, wann, was, von welcher IP.
- **Ad-hoc-Passwörter** (um bei einer Disaster Recovery einen Host zu erreichen,
  der seinen Schlüssel verloren hat) gelten nur für diesen einen Vorgang. Sie
  werden nie auf Platte geschrieben und nie geloggt.
- **Was es nie tut:** keine Telemetrie, kein Phone-Home, kein Auto-Update.

Vorhandene Härtung: CSRF-Token bei jeder zustandsändernden Anfrage,
Whitelist-Validierung vor jeder Shell-Ausführung, SSH-Host-Key-Prüfung (TOFU),
Schlüsselrotation per Klick, die den neuen Schlüssel überall ausrollt, bevor der
alte entfernt wird, und Schutz gegen Path Traversal im Datei-Browser. Die
vollständige Liste steht in [FEATURES_DE.md](FEATURES_DE.md#sicherheit).

[SECURITY.md](SECURITY.md) nennt die Meldestelle für Schwachstellen, was als
solche gilt und welche Schwächen heute bekannt und in Kauf genommen sind
(englisch). [CHANGELOG.md](CHANGELOG.md) enthält alle Releases.

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
      - DEFAULT_LANG=de                       # Standard-UI-Sprache: de oder en

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

### Health-Check

Das Image bringt einen Health-Check mit, `docker ps` meldet also `healthy` bzw.
`unhealthy` statt nur `Up`. Compose übernimmt ihn aus dem Image — in der eigenen
`docker-compose.yml` ist nichts zu ergänzen.

Abgefragt wird alle 30 s `/login`. Dieser Pfad rendert eine echte Seite, ein
Treffer heißt also, dass die Anwendung bedient, und nicht bloß, dass der Port
offen ist. Der erste Check wartet 20 s auf den Start, drei Fehlschläge in Folge
markieren den Container als `unhealthy`.

Wichtig zu wissen: Docker startet einen ungesunden Container nicht von selbst
neu. `restart: unless-stopped` deckt einen Absturz ab; auf „läuft, antwortet
aber nicht" zu reagieren, braucht einen Orchestrator oder einen Watchdog. Gratis
bekommt man dagegen eine Abhängigkeitsschranke:

```yaml
depends_on:
  zfs-tool:
    condition: service_healthy
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

In `docker-compose.yml` `FORCE_HTTPS=true` setzen, um sichere Session-Cookies zu aktivieren,
und `TRUST_PROXY=true`, damit die Anwendung die Client-Adresse aus `X-Forwarded-For`
übernimmt, statt in jedem Nutzer den Proxy zu sehen. Ohne diese Variable zählt die
Login-Sperre alle Nutzer als einen, und im Audit-Log steht die Adresse des Proxys.
**Nicht** auf einem direkt erreichbaren Port setzen: Den Header kann jeder Client
schreiben, und ihm dort zu vertrauen ließe einen Aufrufer seine Adresse selbst wählen
und die Sperre umgehen.

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

## Konfiguration

### Umgebungsvariablen

| Variable | Default | Beschreibung |
|----------|---------|--------------|
| `SECRET_KEY` | `dev-key-change-me` | Flask-Session-Secret -- **muss geändert werden!** |
| `ADMIN_USER` | `admin` | Login-Benutzername -- **sollte geändert werden** |
| `ADMIN_PASSWORD` | `password` | Login-Passwort -- **muss geändert werden!** |
| `FORCE_HTTPS` | `true` | Sichere Session-Cookies -- auf `false` setzen, wenn nicht hinter HTTPS-Proxy |
| `TRUST_PROXY` | nicht gesetzt | Client-Adresse aus `X-Forwarded-For` übernehmen. **Nur** hinter einem Reverse-Proxy -- auf einem direkt erreichbaren Port könnte jeder Client seine Adresse selbst wählen und die Login-Sperre umgehen |
| `TZ` | `UTC` | Zeitzone für Reports und Scheduler (z. B. `Europe/Berlin`, `America/New_York`) |
| `DEFAULT_LANG` | `en` | Standard-UI-Sprache für neue Besucher (`de` oder `en`); Nutzer können weiterhin umschalten |
| `METRICS_RETENTION_DAYS` | `90` | Wie lange Pool- + Disk-(SMART-)Messwerte aufbewahrt werden, bevor aufgeräumt wird; `<=0` behält für immer |
| `AUDIT_RETENTION_DAYS` | `365` | Wie lange Audit-Log-Einträge aufbewahrt werden; `<=0` behält für immer |
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
    ├── autosnap.py          # zfs-auto-snapshot Retention-Editor (Cron-Dateien)
    ├── hostbackup.py        # Proxmox-Host-Config-Backups (erstellen/planen/prunen)
    ├── timezone.py          # Zeitzonen-Helper (TZ-Umgebungsvariable)
    ├── notifications.py     # Telegram, Gotify, Matrix & Email Notifications
    ├── replication.py       # bashclub-zsync-Integration (Install, Config, Cron, checkzfs)
    ├── replication_monitor.py # Replikations-Lag-Erkennung + Status (Sampler-Hook)
    ├── dr.py                # Disaster Recovery (Replikat-Erkennung, Reverse-Sync, Config-Restore + Paket-Reinstall)
    ├── tasks.py             # In-Memory-Async-Task-Registry (lang laufende Ops)
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
