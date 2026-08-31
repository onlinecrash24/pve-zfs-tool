<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="app/static/img/logo.svg">
    <img src="app/static/img/logo-transparent.svg" alt="PVE ZFS Tool" width="500">
  </picture>
</p>

<p align="center">A Docker-based web application for managing ZFS pools, datasets, snapshots, and auto-snapshots across one or more Proxmox VE hosts via SSH.</p>

<p align="center">
  <b>English</b> &middot; <a href="README_DE.md">Deutsch</a>
</p>

## What it is

Manage ZFS across several Proxmox VE hosts from one place: pull a single file
out of a VM or LXC snapshot, set up `bashclub-zsync` replication with a wizard,
and see per guest whether it actually has a backup **and** a replica — not just
whether a job exists.

It talks to your hosts over SSH and runs the same commands you would (`zfs`,
`zpool`, `qm`, `pct`, `pvesh`). Nothing is installed on the hosts.

## Who it is for

Anyone running **ZFS on one or more Proxmox VE nodes** who wants a shared view
of snapshots, replication and backups without opening five SSH sessions.

**Not for you if:**

- you don't use ZFS on your Proxmox hosts — almost nothing here applies
- you want a **PVE cluster manager**. This is not one; it complements the
  Proxmox UI rather than replacing it
- you want to **replace Proxmox Backup Server**. It *reads* your backups to tell
  you what is protected; it never creates or deletes one
- you want an agent on each host. There is none, by design

## What it does that other tools don't

- **File-level restore from a snapshot.** Browse a VM's zvol snapshot down to
  the individual file — partitions are detected via `kpartx`, NTFS included
  (`ntfs-3g`) — and restore just that file. No full rollback, no cloning the
  guest first.
- **Replicas matched by snapshot GUID.** A ZFS snapshot's GUID survives
  `send`/`recv`, so a replica is matched to its source by *proof* rather than by
  guessing from names or paths. That also makes the reverse case visible: a host
  configured as a replication target that holds the newest snapshots means
  replication reversed or stopped — and you only find that out during a restore.
- **Backup and replication judged together.** A guest with three replicas and no
  backup survives a dead disk but not ransomware. A guest with a backup and no
  replica is covered. The overview says which, per guest, instead of counting
  copies.
- **A replication wizard for `bashclub-zsync`** that installs the package on
  both sides, bootstraps passwordless SSH from target to source, and writes the
  per-source config.

**[→ Full feature list](FEATURES.md)**

## What this tool can reach

Worth reading before you install it — it holds a lot of access on purpose.

- **Root SSH on every host you register.** A key pair is generated on first
  start; the private half stays in the `ssh-keys` volume
  (`/root/.ssh/id_ed25519`), and you paste the public half into each host's
  `authorized_keys` yourself. Everything — listing pools, creating and
  destroying snapshots, migrating guests, restoring configurations — runs as
  root over that connection. Treat access to this web UI as equivalent to an
  admin SSH session on all of your nodes.
- **The container runs as root**, but needs neither `privileged` nor any host
  mount.
- **The web login is the door in front of it.** Session-based, credentials from
  environment variables, with a rate limit against brute force. Set them (see
  [Configuration](#environment-variables)) and put it behind HTTPS.
- **By default nothing leaves your network.** The AI reports are the single
  exception and they are opt-in: without an API key nothing is sent at all, with
  Ollama everything stays local, and "Export raw data" shows you the exact JSON
  that would be transmitted.
- **`/metrics` returns 404** unless you set `PROMETHEUS_TOKEN`.
- **Every state change is recorded** in the audit log — who, when, what, from
  which IP.
- **Ad-hoc passwords** (used to reach a host that has lost its key during a
  disaster recovery) live only for that one operation. They are never written to
  disk and never logged.
- **What it never does:** no telemetry, no phone-home, no auto-update.

Hardening in place: CSRF tokens on every state-changing request, whitelist
validation before shell execution, SSH host-key verification (TOFU), one-click
key rotation that deploys the new key everywhere before removing the old one,
and path-traversal protection on the file browser. The full list is in
[FEATURES.md](FEATURES.md#security).

[SECURITY.md](SECURITY.md) says where to report a vulnerability, what counts as
one, and which weaknesses are known and accepted today.
[CHANGELOG.md](CHANGELOG.md) has every release.

## Quick Start

### Option 1: Docker Compose with GHCR Image (recommended)

Create a `docker-compose.yml`:

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
      - SECRET_KEY=your-secret-key-here       # CHANGE THIS!
      - ADMIN_USER=admin                      # CHANGE THIS!
      - ADMIN_PASSWORD=your-strong-password    # CHANGE THIS!
      - FORCE_HTTPS=true                      # Set to false if not behind HTTPS proxy
      - TZ=UTC                                # Timezone (e.g. Europe/Berlin)
      - DEFAULT_LANG=en                       # Default UI language: de or en

volumes:
  ssh-keys:
  zfs-data:
```

```bash
docker compose up -d
```

### Option 2: Manual Build from Source

```bash
git clone https://github.com/onlinecrash24/pve-zfs-tool.git
cd pve-zfs-tool
docker compose up -d --build
```

### Health check

The image ships a health check, so `docker ps` reports `healthy` or `unhealthy`
instead of only `Up`. Compose inherits it from the image — there is nothing to
add to your `docker-compose.yml`.

It requests `/login` every 30 s. That path renders a real page, so a pass means
the application is serving rather than merely that the port is open. The first
check is deferred 20 s to cover start-up, and three consecutive failures mark
the container unhealthy.

Worth knowing: Docker does not restart an unhealthy container by itself.
`restart: unless-stopped` covers a crash; acting on unhealthy-but-running needs
an orchestrator or a watchdog. What the status does give you for free is a
dependency gate:

```yaml
depends_on:
  zfs-tool:
    condition: service_healthy
```

---

Open the web UI at `http://DOCKER-HOST-IP:5000`

> **Important:** Change `SECRET_KEY`, `ADMIN_USER`, and `ADMIN_PASSWORD` before deploying to production. The application will log warnings at startup if default values are detected.

## Setup

1. **Start the container** -- The SSH key pair is generated automatically on first start.
2. **Login** -- Open the web UI and log in with the credentials configured in `docker-compose.yml`.
3. **Copy the public key** -- The public key is displayed on the home page. Copy it.
4. **Add to Proxmox hosts** -- Paste the key into `~/.ssh/authorized_keys` on each Proxmox host:
   ```bash
   echo "ssh-ed25519 AAAA... zfs-tool@docker" >> /root/.ssh/authorized_keys
   ```
5. **Add hosts in the UI** -- Go to "Hosts", add name, IP, port, and user. The host is probed on add and must be a Proxmox VE node (a standalone Proxmox Backup Server is refused -- its backups are read through the PVE nodes instead); step 4 has to be done first, otherwise the check cannot run and you are asked whether to add the host unverified.
6. **Test connection** -- Click "Test" to verify SSH connectivity.
7. **Manage ZFS** -- Select a host from the dropdown and explore pools, snapshots, etc.

## Prerequisites on Proxmox Host (optional)

For **VM file-level restore**, the following packages must be installed on the Proxmox host(s):

```bash
apt install kpartx          # Required — partition detection for zvol snapshots
apt install ntfs-3g         # Optional — only needed for Windows VM NTFS partitions
```

> `kpartx` may already be installed as part of `multipath-tools`. Check with `which kpartx`.

## HTTPS with Reverse Proxy (recommended)

For production deployments, place the container behind an HTTPS reverse proxy. The application includes `ProxyFix` middleware and automatically trusts `X-Forwarded-*` headers from your proxy.

Set `FORCE_HTTPS=true` in your `docker-compose.yml` to enable secure session cookies.

### Nginx Proxy Manager (NPM)

1. Add a new Proxy Host in NPM
2. Set the forward hostname to the Docker host IP (or container name if in the same Docker network)
3. Set the forward port to `5000`
4. Enable SSL via Let's Encrypt on the SSL tab
5. Under **Advanced** tab, no extra config needed -- NPM sets all required headers automatically

> **Tip:** If NPM and zfs-tool run on the same Docker host, add them to the same Docker network for reliable connectivity.

### Caddy (automatic TLS)

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

## Notifications Setup

### Telegram
1. Create a bot via [@BotFather](https://t.me/BotFather) on Telegram
2. Get your Chat ID via [@userinfobot](https://t.me/userinfobot) or [@getidsbot](https://t.me/getidsbot)
3. For group notifications, add the bot to the group and use the group Chat ID (starts with `-100`)
4. Enter Bot Token and Chat ID in the Notifications settings
5. Click "Send Test" to verify

### Gotify
1. Set up a [Gotify](https://gotify.net/) server
2. Create an application in Gotify and copy the app token
3. Enter the server URL and token in the Notifications settings
4. Click "Send Test" to verify

### Matrix
1. Get your homeserver URL (e.g. `https://matrix.org`)
2. Get an access token from Element: Settings → Help & About → Access Token
3. Get the room ID (e.g. `!abc123:matrix.org`) from room settings in Element
4. Enter homeserver URL, access token, and room ID in the Notifications settings
5. Click "Send Test" to verify

## Prometheus Integration (optional)

Set the `PROMETHEUS_TOKEN` environment variable to enable the `/metrics` endpoint (it stays `404` otherwise). Example Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: pvezfs
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials: your-long-random-token
    static_configs:
      - targets: ['zfs-tool.example.com']
```

Exposed metrics include: `pvezfs_host_reachable`, `pvezfs_pool_capacity_percent`, `pvezfs_pool_size_bytes`, `pvezfs_pool_alloc_bytes`, `pvezfs_pool_free_bytes`, `pvezfs_pool_fragmentation_percent`, `pvezfs_pool_health{state="…"}`, `pvezfs_pool_error_total_sum`, `pvezfs_pool_forecast_days_until_full`, and a scrape timestamp.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `dev-key-change-me` | Flask session secret key -- **must be changed!** |
| `ADMIN_USER` | `admin` | Login username -- **should be changed** |
| `ADMIN_PASSWORD` | `password` | Login password -- **must be changed!** |
| `FORCE_HTTPS` | `true` | Secure session cookies -- set to `false` if not behind HTTPS proxy |
| `TZ` | `UTC` | Timezone for reports and scheduler (e.g. `Europe/Berlin`, `America/New_York`) |
| `DEFAULT_LANG` | `en` | Default UI language for new visitors (`de` or `en`); users can still switch |
| `METRICS_RETENTION_DAYS` | `90` | How long pool + disk (SMART) samples are kept before auto-cleanup; `<=0` keeps forever |
| `AUDIT_RETENTION_DAYS` | `365` | How long audit-log entries are kept; `<=0` keeps forever |
| `PROMETHEUS_TOKEN` | _(unset)_ | Opt-in bearer token for `/metrics` endpoint. If unset, the Prometheus exporter is disabled |

### Persistent Volumes

| Volume | Path | Description |
|--------|------|-------------|
| `ssh-keys` | `/root/.ssh` | SSH key pair (persisted across restarts) |
| `zfs-data` | `/app/data` | Host config, notification settings, AI reports, SSH known hosts |

## Tech Stack

- **Backend** -- Python 3.12, Flask, Paramiko (SSH), Gunicorn, fpdf2
- **Frontend** -- Vanilla JavaScript SPA, CSS dark theme
- **Deployment** -- Docker, Docker Compose, GitHub Container Registry

## Project Structure

```
pve-zfs-tool/
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── requirements.txt
└── app/
    ├── main.py              # Flask API routes & authentication
    ├── ssh_manager.py       # SSH connection, host management, key rotation
    ├── zfs_commands.py      # ZFS command wrappers via SSH (cached reads)
    ├── validators.py        # Input validation (whitelist-based)
    ├── cache.py             # TTL in-memory cache for SSH results
    ├── database.py          # Shared SQLite (metrics / audit / monitor state)
    ├── metrics.py           # Background sampler + pool timeseries queries
    ├── monitor.py           # Proactive state-change notifications
    ├── analytics.py         # Dashboard aggregation, forecast, Prometheus
    ├── audit.py             # Audit-log writer and query API
    ├── ai_reports.py        # AI-powered ZFS analysis & reports
    ├── ai_pdf.py            # PDF report generation
    ├── snapshot_analysis.py # Shared snapshot health analysis (UI + AI)
    ├── autosnap.py          # zfs-auto-snapshot retention editor (cron files)
    ├── hostbackup.py        # Proxmox host config backups (create/schedule/prune)
    ├── timezone.py          # Timezone helper (TZ environment variable)
    ├── notifications.py     # Telegram, Gotify, Matrix & Email notifications
    ├── replication.py       # bashclub-zsync integration (install, config, cron, checkzfs)
    ├── replication_monitor.py # Replication lag detection + status (sampler hook)
    ├── dr.py                # Disaster recovery (replica discovery, reverse sync, config restore + package reinstall)
    ├── tasks.py             # In-memory async task registry (long-running ops)
    ├── templates/
    │   ├── index.html       # Single-page application
    │   └── login.html       # Login page
    └── static/
        ├── css/style.css    # Dark theme UI
        ├── js/app.js        # Frontend logic
        └── js/i18n.js       # EN/DE translations
```

## License

MIT
