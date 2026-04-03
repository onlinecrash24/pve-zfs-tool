# ZFS Tool for Proxmox VE

A Docker-based web application for managing ZFS pools, datasets, snapshots, and auto-snapshots across one or more Proxmox VE hosts via SSH.

## Features

### ZFS Management
- **Pool Overview** -- Status, IO statistics, health, fragmentation, dedup ratio
- **Pool Scrub** -- Start scrubs directly from the UI
- **Pool History** -- View recent pool activity
- **Dataset Management** -- List, create, destroy datasets; view and set properties
- **Compression** -- View compression ratios and configure compression per dataset

### Snapshot Management
- **List Snapshots** -- View all snapshots, filter by dataset
- **Create Snapshots** -- Manual snapshots with custom names, recursive support
- **Rollback** -- Rollback to any snapshot (with option to destroy newer snapshots)
- **Clone** -- Clone snapshots into new datasets
- **Diff** -- View changes between snapshots
- **Delete** -- Remove individual snapshots

### Proxmox VM/CT Integration
- **Guest Overview** -- List all VMs and LXC containers with status
- **Per-Guest Snapshots** -- View ZFS snapshots specific to a VM or container
- **Guest Snapshot Rollback** -- Rollback individual guest disks to a previous state

### ZFS Auto-Snapshot
- **Status** -- Check if zfs-auto-snapshot is installed, view cron/timer config
- **Per-Dataset Config** -- Enable/disable auto-snapshot per dataset and label (frequent, hourly, daily, weekly, monthly)

### Health & Monitoring
- **ARC Statistics** -- Adaptive Replacement Cache hit/miss rates and memory usage
- **ZFS Events** -- Recent ZFS kernel events
- **SMART Status** -- Disk health for all drives in each pool

### Notifications
- **Telegram** -- Receive notifications via Telegram bot
- **Gotify** -- Receive notifications via self-hosted Gotify server
- **Configurable Events** -- Enable/disable notifications per event type:
  - Scrub started/finished
  - Snapshot created/deleted
  - Rollback performed
  - Pool errors/degraded state
  - Health warnings
  - Host offline
  - Auto-snapshot events

### Multi-Host SSH
- **SSH Key Auto-Generation** -- Ed25519 key pair generated on first start
- **Public Key Display** -- Shown on the home page for easy copy to Proxmox hosts
- **Multiple Hosts** -- Add and manage multiple Proxmox VE nodes
- **Connection Test** -- Verify SSH connectivity per host

## Quick Start

```bash
# Clone the repository
git clone https://git.myantispam.de/onlinecrash/zfz-tool.git
cd zfz-tool

# Start the container
docker compose up -d --build

# Open the web UI
# http://localhost:5000
```

## Setup

1. **Start the container** -- The SSH key pair is generated automatically on first start.
2. **Copy the public key** -- The public key is displayed on the home page. Copy it.
3. **Add to Proxmox hosts** -- Paste the key into `~/.ssh/authorized_keys` on each Proxmox host:
   ```bash
   echo "ssh-ed25519 AAAA... zfs-tool@docker" >> /root/.ssh/authorized_keys
   ```
4. **Add hosts in the UI** -- Go to "Hosts", add name, IP, port, and user.
5. **Test connection** -- Click "Test" to verify SSH connectivity.
6. **Manage ZFS** -- Select a host from the dropdown and explore pools, snapshots, etc.

## Notifications Setup

### Telegram
1. Create a bot via [@BotFather](https://t.me/BotFather) on Telegram
2. Get your Chat ID via [@userinfobot](https://t.me/userinfobot) or [@getidsbot](https://t.me/getidsbot)
3. For group notifications, add the bot to the group and use the group Chat ID (starts with `-100`)
4. Enter Bot Token and Chat ID in the Notifications settings

### Gotify
1. Set up a [Gotify](https://gotify.net/) server
2. Create an application in Gotify and copy the app token
3. Enter the server URL and token in the Notifications settings

## Configuration

Environment variables in `docker-compose.yml`:

| Variable | Default | Description |
|----------|---------|-------------|
| `SECRET_KEY` | `change-me-in-production` | Flask session secret key |

Persistent volumes:

| Volume | Path | Description |
|--------|------|-------------|
| `ssh-keys` | `/root/.ssh` | SSH key pair (persisted across restarts) |
| `zfs-data` | `/app/data` | Host config, notification settings |

## Tech Stack

- **Backend** -- Python 3.12, Flask, Paramiko (SSH), Gunicorn
- **Frontend** -- Vanilla JavaScript SPA, CSS dark theme
- **Deployment** -- Docker, Docker Compose

## Project Structure

```
zfs-tool/
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── requirements.txt
└── app/
    ├── main.py              # Flask API routes
    ├── ssh_manager.py       # SSH connection & host management
    ├── zfs_commands.py      # ZFS command wrappers via SSH
    ├── notifications.py     # Telegram & Gotify notifications
    ├── templates/
    │   └── index.html       # Single-page application
    └── static/
        ├── css/style.css    # Dark theme UI
        └── js/app.js        # Frontend logic
```

## License

MIT
