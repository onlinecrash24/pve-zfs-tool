<p align="center">
  <img src="app/static/img/logo.png" alt="PVE ZFS Tool" width="500">
</p>

<p align="center">A Docker-based web application for managing ZFS pools, datasets, snapshots, and auto-snapshots across one or more Proxmox VE hosts via SSH.</p>

## Features

### ZFS Pool Management
- **Pool Overview** -- Status, IO statistics, health, fragmentation, dedup ratio
- **Pool Scrub** -- Start scrubs directly from the UI with automatic completion notification
- **Pool Upgrade** -- Automatically detects if a feature upgrade is available (green button), with confirmation before upgrading
- **Pool History** -- View recent pool activity

### Dataset Management
- **List & Filter** -- View all datasets with type, compression, used/available space
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
- **File-Level Restore** -- Browse and restore individual files from LXC container snapshots:
  - Mounts snapshot as readonly clone
  - Navigate files via breadcrumb file browser
  - Preview text files directly in the UI
  - Restore individual files or entire directories back to the live container
  - Automatic cleanup: restore clone is unmounted when closing the browser (via X, backdrop click, or close button)

### Health & Monitoring
- **ARC Statistics** -- Adaptive Replacement Cache hit/miss rates and memory usage
- **ZFS Events** -- Recent ZFS kernel events
- **SMART Status** -- Disk health for all drives in each pool (resolved via `/dev/disk/by-id/`)
- **Restore Clone Cleanup** -- View and destroy leftover restore-mount datasets with one-click cleanup

### Notifications
- **Telegram** -- Receive notifications via Telegram bot
- **Gotify** -- Receive notifications via self-hosted Gotify server
- **Matrix** -- Receive notifications via Matrix room (Client-Server API)
- **Scrub Monitor** -- Background thread detects scrub completion and sends notification automatically
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

### AI Reports & Analysis
- **Multi-Provider Support** -- OpenAI (GPT), Anthropic (Claude), Ollama (local), or any OpenAI-compatible API
- **Ollama Model Discovery** -- Automatically query and select available models from your Ollama instance
- **Daily/Weekly Reports** -- Automated ZFS infrastructure analysis on schedule
- **Comprehensive Analysis** -- Pool health, storage capacity, scrub status, snapshot coverage, SMART health, anomalies
- **Actionable Recommendations** -- Prioritized suggestions for scrubs, cleanup, capacity planning
- **Interactive Chat** -- Ask follow-up questions about your ZFS data
- **Notification Integration** -- Optionally send reports via Telegram, Gotify, or Matrix
- **Bilingual Reports** -- Reports follow the global UI language (English/German)
- **Customizable System Prompt** -- Edit the AI prompt to reduce false positives for your environment
- **PDF Export** -- Download reports as PDF

### Security
- **CSRF Protection** -- Token-based protection for all state-changing requests
- **Input Validation** -- Whitelist-based validation on all parameters before shell execution
- **Rate Limiting** -- Login brute-force protection (5 attempts, 5-minute lockout)
- **Secure Sessions** -- HttpOnly cookies, SameSite=Lax, configurable Secure flag, 8-hour timeout
- **Security Headers** -- CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy
- **Path Traversal Prevention** -- Realpath validation with symlink attack protection
- **SSH Host Key Verification** -- Trust On First Use (TOFU), known hosts saved and verified on subsequent connections
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

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `dev-key-change-me` | Flask session secret key -- **must be changed!** |
| `ADMIN_USER` | `admin` | Login username -- **should be changed** |
| `ADMIN_PASSWORD` | `password` | Login password -- **must be changed!** |
| `FORCE_HTTPS` | `true` | Secure session cookies -- set to `false` if not behind HTTPS proxy |
| `TZ` | `UTC` | Timezone for reports and scheduler (e.g. `Europe/Berlin`, `America/New_York`) |

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
    ├── ssh_manager.py       # SSH connection & host management
    ├── zfs_commands.py      # ZFS command wrappers via SSH
    ├── validators.py        # Input validation (whitelist-based)
    ├── ai_reports.py        # AI-powered ZFS analysis & reports
    ├── ai_pdf.py            # PDF report generation
    ├── notifications.py     # Telegram, Gotify & Matrix notifications
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
