<p align="center">
  <img src="app/static/img/logo.png" alt="PVE ZFS Tool" width="500">
</p>

<p align="center">A Docker-based web application for managing ZFS pools, datasets, snapshots, and auto-snapshots across one or more Proxmox VE hosts via SSH.</p>

<p align="center">
  <b>English</b> &middot; <a href="README_DE.md">Deutsch</a>
</p>

## Features

### ZFS Pool Management
- **Pool Overview** -- Status, IO statistics, health, fragmentation, dedup ratio
- **Pool Scrub** -- Start scrubs directly from the UI with automatic completion notification
- **Pool Upgrade** -- Automatically detects if a feature upgrade is available (green button), with confirmation before upgrading
- **Pool History** -- View recent pool activity

### Dataset Management
- **Separated Views** -- Filesystems (LXC, data) and VM Volumes shown in distinct sections with type-specific actions
- **Create Datasets** -- Create new datasets with optional compression settings
- **Properties** -- View and modify all ZFS dataset properties

### Snapshot Management
- **Interactive Timeline** -- Visual timeline grouped by dataset, newest first, with color-coded dots (blue = newest, auto vs. manual distinction)
- **Table View** -- Classic table view (default) with type badges (zvol/filesystem), switchable via dropdown
- **Search** -- Filter snapshots by dataset name in both timeline and table view
- **Create Snapshots** -- Manual snapshots with custom names, recursive support
- **Rollback** -- Smart rollback that auto-detects VMs/LXC containers, stops them before rollback and restarts them afterwards
- **Clone** -- Clone snapshots via modal dialog with target datastore/pool selector, editable clone name (default: `{name}_CLONE`), supports cross-pool cloning via `zfs send | zfs recv`
- **Diff** -- View changes for filesystem datasets (`zfs diff`) and zvol/VM snapshots (incremental send estimates, snapshot properties, size overview)
- **Delete** -- Remove manually created snapshots only (auto-snapshots are protected from deletion)

### Proxmox VM/CT Integration
- **Guest Overview** -- List all VMs and LXC containers with status
- **Per-Guest Snapshots** -- View ZFS snapshots specific to a VM or container
- **Smart Rollback** -- Automatically stops VM/LXC before rollback and restarts afterwards
- **LXC File-Level Restore** -- Browse and restore individual files from LXC container snapshots:
  - Mounts snapshot as readonly clone
  - Navigate files via breadcrumb file browser
  - Preview text files directly in the UI
  - Restore individual files or entire directories back to the live container
  - Automatic cleanup: restore clone is unmounted when closing the browser
- **VM File-Level Restore** -- Browse and download files from VM disk snapshots (Linux & Windows):
  - Automatic `snapdev=visible` handling for zvol snapshot access
  - Partition detection via `kpartx` with filesystem identification
  - Supports ext4, xfs, btrfs (Linux), NTFS via ntfs-3g (Windows), vfat (EFI)
  - BitLocker/LUKS-encrypted partitions detected and shown as non-mountable
  - Automatic filtering of non-mountable types (swap, LVM, ZFS member, RAID, bcache, ceph, etc.)
  - File browser with preview and download functionality
  - Robust cleanup: kpartx mappings, dmsetup fallback, snapdev reset
  - Cleanup on modal close, tab close (sendBeacon), and via Health page

### Replication (bashclub-zsync)
- **5-Step Wizard** -- Source/target host pair → setup → datasets → config → log, with progressive disclosure
- **One-Click Setup** -- Installs `bashclub-zsync` on **both** hosts via the official deb822 APT repo (`apt.bashclub.org/release/`) and bootstraps passwordless SSH from target to source (key generation, `ssh-keyscan` for `known_hosts`, `authorized_keys` append, BatchMode probe)
- **PVE Detection** -- Per-host PVE-version badge (warns if a host is not Proxmox VE)
- **Per-Source Config Files** -- Each replication pair lives in its own `/etc/bashclub/<source-ip>.conf` so multiple pairs coexist on a single target host (matches the upstream bashclub convention)
- **Dataset Tagging** -- Checkbox list of all source datasets/zvols; sets/clears the `bashclub:zsync` user property (value `all`) so the upstream filter actually picks them up
- **Target Dataset Helper** -- Dropdown of existing datasets on the target plus a "+ create new" option (`zfs create -p -o com.sun:auto-snapshot=false`, defaults to `rpool/repl`)
- **Complete Config Form** -- 16 fields mirroring the upstream `/etc/bashclub/zsync.conf` (sshport, tag, snapshot_filter, min_keep, zfs_auto_snapshot_*, checkzfs_*); empty fields fall back to upstream defaults so the saved file is always production-ready
- **Cron Schedule Manager** -- Preset dropdown (bashclub default `20 0-22 * * *`, every 15/30 min, hourly, 2h, 6h, daily 03:00, custom) with live preview, idempotent install/replace/remove, explicit reload across cron / cronie / systemd-cron
- **checkzfs Health Panel** -- Runs `checkzfs --source <ip>` on the target and renders an OK/WARN/CRIT summary plus a grouped table; ANSI-stripped, replicated-only filter on by default
- **Multi-Pair Overview** -- Lists every configured pair across the fleet (scans `/etc/bashclub/*.conf` on every registered host); per-row "Open" loads it into the wizard
- **Safe Delete** -- Removes cron entry + config (with timestamped backup); optional checkbox additionally destroys all `zfs-auto-snap_*` snapshots under the replica target -- datasets and zsync baseline snapshots are kept, top-level pools refused

### Snapshot Check
- **Retention Policy Overview** -- Displays configured `--keep=N` values per label from cron
- **Per-Label Analysis** -- Total snapshots, dataset count, per-dataset average, newest age
- **Gap Detection** -- Identifies gaps in snapshot chains exceeding `MAX_AGE * 1.5`
- **Stale Datasets** -- Warns when snapshots exceed age thresholds (frequent >1h, hourly >2h, daily >25h, weekly >8d, monthly >32d)
- **Count Mismatches** -- Compares actual snapshot count vs. configured retention (SOLL/IST)
- **Missing Labels** -- Detects labels configured in cron but absent from datasets
- **Manual Snapshots** -- Lists non-standard snapshots (not matching known auto-snapshot labels)

### Health & Monitoring
- **ARC Statistics** -- Adaptive Replacement Cache hit/miss rates and memory usage
- **ZFS Events** -- Recent ZFS kernel events
- **SMART Status** -- Disk health for all drives in each pool (resolved via `/dev/disk/by-id/`)
- **LXC Restore Clone Cleanup** -- View and destroy leftover restore-mount datasets with one-click cleanup
- **VM Zvol Restore Sessions** -- Overview of active mounts, kpartx mappings, and snapdev status with per-item unmount and bulk cleanup
- **Scheduled Tasks Overview** -- Shows active AI report schedules (per-host and combined) with next-run and last-run times
- **ZDB Deep Diagnostics** -- Automatic `zdb` analysis for DEGRADED/FAULTED pools (block stats, disk labels)

### Historical Metrics (Trends)
- **Background Sampler** -- Captures pool capacity, fragmentation, allocation, health and dedup ratio every 15 minutes per host
- **SQLite Storage** -- 90-day retention in `/app/data/pvezfs.db` (WAL journaling)
- **Inline Trend Charts** -- Capacity%, fragmentation%, allocated GB per pool rendered as lightweight inline SVG (no external JS)
- **Configurable Range** -- 6 h / 24 h / 7 d / 30 d / 90 d views
- **Sample Now** -- Trigger an immediate sample after adding a new host

### Audit Log
- **Destructive Actions Logged** -- Login success/failure, host add/remove, pool scrub/upgrade, dataset create/destroy/set, snapshot create/destroy/rollback/clone, auto-snapshot toggle, file & zvol restores, cache invalidation, notifications/AI config saves
- **Fields Recorded** -- Timestamp, user, IP, host, action code, target, JSON details, success flag
- **Filterable UI** -- Filter by action, user, current host, or failures only
- **SQLite Backed** -- Indexed for fast queries, persisted across restarts

### Performance
- **SSH Command Cache** -- TTL-based in-memory cache (15-300s) for read-only ZFS queries drastically reduces SSH round-trips on active views; writes automatically invalidate the cache for the affected host
- **Cache Stats API** -- `/api/cache/stats` exposes hit rate and entry count for ops visibility

### Notifications
- **Telegram** -- Receive notifications via Telegram bot, PDF reports delivered as documents
- **Gotify** -- Receive notifications via self-hosted Gotify server (text only)
- **Matrix** -- Receive notifications via Matrix room (Client-Server API v3), PDF reports as `m.file` attachments
- **Email (SMTP)** -- Send notifications and PDF reports to one or more recipients; STARTTLS, SSL/TLS or plaintext
- **Scrub Monitor** -- Background thread detects scrub completion and sends notification automatically
- **Live Dashboard** -- Home page shows per-host status, pool health, capacity, free space, and a linear-regression forecast "full in X days" computed from 30 d of sample data
- **Capacity Forecast** -- `/api/forecast?host=...&pool=...` returns projected days until the pool reaches 100 % allocated (least-squares on `alloc_bytes`)
- **Prometheus Exporter** -- `/metrics` in Prometheus text exposition format (opt-in: set `PROMETHEUS_TOKEN` env var; access with `Authorization: Bearer <token>` or `?token=`). Exposes host reachability, pool size/alloc/free/capacity/fragmentation, pool health (one-hot), I/O error totals, capacity forecast, and scrape timestamp
- **Proactive Monitoring** -- Every 15 min the sampler checks each host for state changes and fires notifications (no manual action required):
  - `pool_error` -- pool health transitions ONLINE → DEGRADED/FAULTED/UNAVAIL and back
  - `health_warning` -- capacity crosses 90 %, or read/write/checksum errors appear
  - `host_offline` -- SSH probe fails where it previously succeeded (and recovery)
  - `auto_snapshot` -- newest auto-snap per label (frequent/hourly/daily/weekly/monthly) older than expected (throttled to once per day per host/label)
- **Test Notifications** -- Send test messages per channel to verify configuration
- **Configurable Events** -- Enable/disable notifications per event type:
  - Scrub started/finished
  - Snapshot created/deleted
  - Rollback performed
  - Pool errors/degraded state
  - Pool upgraded
  - Health warnings
  - Host offline
  - File restore actions
  - AI report generated (delivered with PDF attachment)

### AI Reports & Analysis
- **Multi-Provider Support** -- OpenAI (GPT), Anthropic (Claude), Ollama (local), or any OpenAI-compatible API
- **Ollama Model Discovery** -- Automatically query and select available models from your Ollama instance
- **Per-Host & Combined Schedules** -- Independent daily/weekly plans per host, plus an optional combined "all hosts" report
- **Comprehensive Analysis** -- Pool health, storage capacity, scrub status, snapshot coverage, SMART health, anomalies
- **Snapshot Retention Analysis** -- Per-dataset per-label retention check, gap detection, stale snapshot warnings
- **ZDB Diagnostics** -- Automatic deep analysis triggered for degraded/faulted pools
- **Actionable Recommendations** -- Prioritized suggestions for scrubs, cleanup, capacity planning
- **Interactive Chat** -- Ask follow-up questions about your ZFS data
- **Notification Integration** -- Send reports via Telegram, Gotify, Matrix or Email -- PDF attachment supported on Email, Telegram and Matrix
- **Bilingual Reports** -- Reports follow the global UI language (English/German)
- **Customizable System Prompt** -- Edit the AI prompt to reduce false positives for your environment
- **PDF Export** -- Download reports as PDF or have them delivered automatically
- **Raw Data Export** -- Export the JSON payload that would be sent to the LLM

### Security
- **CSRF Protection** -- Token-based protection for all state-changing requests
- **Input Validation** -- Whitelist-based validation on all parameters before shell execution
- **Rate Limiting** -- Login brute-force protection (5 attempts, 5-minute lockout)
- **Secure Sessions** -- HttpOnly cookies, SameSite=Lax, configurable Secure flag, 8-hour timeout
- **Security Headers** -- CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy
- **Path Traversal Prevention** -- Realpath validation with symlink attack protection
- **SSH Host Key Verification** -- Trust On First Use (TOFU), known hosts saved and verified on subsequent connections
- **SSH Key Rotation** -- One-click rotation from the Home page; generates a new Ed25519 keypair, deploys it to all hosts before removing the old key (never locks you out), with per-host deploy/verify/cleanup status
- **Reverse Proxy Ready** -- ProxyFix middleware for correct HTTPS detection behind NPM, nginx, Caddy

### Authentication & i18n
- **Login** -- Session-based authentication, credentials configurable via environment variables
- **Language Switch** -- English and German with instant UI re-render, persisted in localStorage
- **Session Management** -- Automatic redirect to login on session expiry (401 handling)

### Multi-Host SSH
- **SSH Key Auto-Generation** -- Ed25519 key pair generated on first start
- **Public Key Display** -- Shown on the home page with copy button (works on HTTP and HTTPS)
- **Multiple Hosts** -- Add and manage multiple Proxmox VE nodes
- **Connection Test** -- Verify SSH connectivity per host

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
5. **Add hosts in the UI** -- Go to "Hosts", add name, IP, port, and user.
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

### One-shot monitoring stack (Prometheus + Grafana, optional)

The repo ships a ready-made Prometheus + Grafana setup as opt-in
profiles in the same `docker-compose.yml`. Grafana auto-loads a
"PVE ZFS Tool" dashboard with all of the above metrics pre-wired.

```bash
# Generate a token and write it to .env (read by both containers)
echo "PROMETHEUS_TOKEN=$(openssl rand -hex 32)" > .env

# Optionally override the Grafana admin password
echo "GRAFANA_ADMIN_PASSWORD=please-change-me" >> .env

# Start zfs-tool + prometheus + grafana
docker compose --profile monitoring up -d
```

Then open:

- Grafana: <http://DOCKER-HOST:3000> (login `admin` / your password) — the
  dashboard lives under **Dashboards → ZFS → PVE ZFS Tool**
- Prometheus: <http://DOCKER-HOST:9090> (only if you want to inspect raw
  series; the `--web.enable-lifecycle` flag also lets you reload config
  via `curl -XPOST http://DOCKER-HOST:9090/-/reload`).

Provisioning files live under `docker/prometheus/` and
`docker/grafana/` — copy and edit if you want to extend the dashboard.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `dev-key-change-me` | Flask session secret key -- **must be changed!** |
| `ADMIN_USER` | `admin` | Login username -- **should be changed** |
| `ADMIN_PASSWORD` | `password` | Login password -- **must be changed!** |
| `FORCE_HTTPS` | `true` | Secure session cookies -- set to `false` if not behind HTTPS proxy |
| `TZ` | `UTC` | Timezone for reports and scheduler (e.g. `Europe/Berlin`, `America/New_York`) |
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
    ├── timezone.py          # Timezone helper (TZ environment variable)
    ├── notifications.py     # Telegram, Gotify, Matrix & Email notifications
    ├── replication.py       # bashclub-zsync integration (install, config, cron, checkzfs)
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
