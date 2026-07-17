<p align="center">
  <img src="app/static/img/logo.png" alt="PVE ZFS Tool" width="500">
</p>

<p align="center">A Docker-based web application for managing ZFS pools, datasets, snapshots, and auto-snapshots across one or more Proxmox VE hosts via SSH.</p>

<p align="center">
  <b>English</b> &middot; <a href="README_DE.md">Deutsch</a>
</p>

## Features

### ZFS Pool Management
- **Pool Overview** -- Status, IO statistics, health, fragmentation, dedup ratio; capacity and fragmentation carry a traffic light (green/orange/red)
- **Pool Scrub** -- Start scrubs directly from the UI with automatic completion notification
- **Pool Upgrade** -- Automatically detects if a feature upgrade is available (green button), with confirmation before upgrading
- **Pool History** -- View recent pool activity
- **autotrim / autoexpand** -- Toggle both pool properties directly from the pool detail dialog (continuous TRIM on SSDs; automatic growth after replacing a device with a larger one)

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
- **Lifecycle Control** -- Start, graceful shutdown, reboot and hard stop per guest (status-dependent buttons, confirmation for disruptive actions, audit-logged)
- **Per-Guest Snapshots** -- View ZFS snapshots specific to a VM or container
- **Smart Rollback** -- Automatically stops VM/LXC before rollback and restarts afterwards
- **Replication Status** -- Per-guest indicator in the guest list: green (all disks tagged & not lagging), yellow (only some disks tagged, or the source is lagging), red (not replicated); derived from the `bashclub:zsync` tags plus the replication monitor
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
- **Setup Wizard** -- Source/target host pair → setup → datasets → config → log, with progressive disclosure
- **Green/Red Pre-Flight** -- Before setup, checks what already exists (PVE, bashclub repo, `bashclub-zsync` installed, SSH trust) and only performs the missing steps
- **One-Click Setup** -- Installs `bashclub-zsync` on **both** hosts via the official deb822 APT repo (`apt.bashclub.org/release/`, suite derived from the host: bookworm/trixie) and bootstraps passwordless SSH from target to source (key generation, `ssh-keyscan` for `known_hosts`, `authorized_keys` append, BatchMode probe)
- **PVE Detection** -- Per-host PVE-version badge (warns if a host is not Proxmox VE)
- **Per-Source Config Files** -- Each replication pair lives in its own `/etc/bashclub/<source-ip>.conf` so multiple pairs coexist on a single target host (matches the upstream bashclub convention)
- **Dataset Tagging** -- Checkbox list of all source datasets/zvols; sets/clears the `bashclub:zsync` user property (value `all`) so the upstream filter actually picks them up
- **Target Dataset Helper** -- Dropdown of existing datasets on the target plus a "+ create new" option (`zfs create -p -o com.sun:auto-snapshot=false`, defaults to `rpool/repl`)
- **Complete Config Form** -- 16 fields mirroring the upstream `/etc/bashclub/zsync.conf` (sshport, tag, snapshot_filter, min_keep, zfs_auto_snapshot_*, checkzfs_*); empty fields fall back to upstream defaults so the saved file is always production-ready
- **Cron Schedule Manager** -- Preset dropdown (bashclub default `20 0-22 * * *`, every 15/30 min, hourly, 2h, 6h, daily 03:00, custom) with live preview, idempotent install/replace/remove, explicit reload across cron / cronie / systemd-cron
- **checkzfs Health Panel** -- Runs `checkzfs --source <ip>` on the target and renders an OK/WARN/CRIT summary plus a grouped table; ANSI-stripped, replicated-only filter on by default
- **Multi-Pair Overview** -- Lists every configured pair across the fleet (scans `/etc/bashclub/*.conf` on every registered host); per-row "Open" loads it into the wizard
- **Replication Health Monitor** -- Per-pair status (OK / WARN / CRIT / pending / no-cron), last-sync timestamp and lag are shown right in the overview, derived from the newest replica snapshot vs. the cron interval. Runs every 15 min on the existing sampler and fires a `replication_lag` notification on status transitions
- **Same-Host Replication** -- Source and target may be the same machine for cross-pool backups (e.g. `rpool` → `sata-pool/repl`); a same-pool target is refused (a replica on the same vdevs is not a backup)
- **Safe Delete** -- Removes cron entry + config (with timestamped backup); optional checkbox additionally destroys all `zfs-auto-snap_*` snapshots under the replica target -- datasets and zsync baseline snapshots are kept, top-level pools refused

### Disaster Recovery
- **Reverse Sync** -- Send a replica back to a rebuilt source host with `zfs send -R | ssh <source> zfs recv`, reusing the SSH trust established during replication setup
- **Reinstalled-Host Host Key** -- A rebuilt destination has a new SSH host key, which would abort the transfer under strict checking ("REMOTE HOST IDENTIFICATION HAS CHANGED"); an on-by-default option drops the stale `known_hosts` entry and re-scans the current key (fingerprint logged). A host-key failure gets a targeted hint pointing at that option
- **Guest Config Restore** -- Reverse sync restores only the disk; this step puts the VM/CT config (`/etc/pve/{qemu-server,lxc}/<vmid>.conf`) back from a host-config backup so Proxmox shows the guest again -- derives the VMID/type from the replica dataset, previews the config, and won't overwrite an existing one unless confirmed
- **Replica Discovery** -- Scans every registered host for replica roots and lists the replicated datasets and their snapshots
- **Flexible Target** -- Pick a registered host or a free-form address/port/user (a rebuilt host may have a new IP); the destination dataset defaults to the original source path
- **Snapshot Choice** -- Send the newest replica snapshot (default) or any older one; `zfs send -R` carries all descendants and properties
- **Guarded Force** -- Optional `zfs recv -F` (rollback to match the stream) is off by default and gated behind a typed confirmation
- **Background Task** -- The (potentially multi-hour) resend runs as a background job with live progress, so the UI stays responsive
- **File-Level Recovery** -- Individual files are restored through the existing Snapshots view (mount any replica snapshot read-only, browse, preview, restore)

### Snapshot Check
- **Install zfs-auto-snapshot** -- One-click install of the package (stock Debian, no extra repo) directly from the retention card when it's missing on a host
- **Retention Policy Editor** -- View and **edit** the configured `--keep=N` value per level (frequent/hourly/daily/weekly/monthly) and enable/disable individual levels, written straight back to the zfs-auto-snapshot cron files with a timestamped backup
- **Per-Label Analysis** -- Total snapshots, dataset count, per-dataset average, newest age
- **Gap Detection** -- Identifies gaps in snapshot chains exceeding `MAX_AGE * 1.5`
- **Stale Datasets** -- Warns when snapshots exceed age thresholds (frequent >1h, hourly >2h, daily >25h, weekly >8d, monthly >32d)
- **Count Mismatches** -- Compares actual snapshot count vs. configured retention (SOLL/IST); replica datasets (`com.sun:auto-snapshot=false`, e.g. zsync targets) are excluded from this comparison since their snapshot count follows the *source* host's retention -- stale/gap detection still applies to them
- **Missing Labels** -- Detects labels configured in cron but absent from datasets
- **Manual Snapshots** -- Lists non-standard snapshots (not matching known auto-snapshot labels)

### Health & Monitoring
- **ARC Statistics** -- Cache effectiveness led by the hit ratio with a traffic light (>=90 % green, >=80 % orange, below red); raw hits/misses as context
- **ARC Limit Editor** -- View runtime + persistent (`/etc/modprobe.d/zfs.conf`) ARC limits, current size and fill %; set a new limit with Min / Recommended / Max reference buttons (Proxmox floor `2 GiB + 1 GiB/TiB pool`, ~25 % RAM, 50 % RAM). Writes the modprobe config with a timestamped backup, rebuilds the initramfs for **all** kernels (`update-initramfs -u -k all`) and applies the runtime value immediately; `arc_max=0` resets to the ZFS default
- **Monitoring State Hygiene** -- A pool destroyed/exported while DEGRADED is announced once and its monitoring state cleared (no eternal ghosts); the cleanup only runs on a verified pool listing, never on a failed `zpool list`. Dashboard tiles likewise exclude pools of offline hosts (rendered as "stale" instead of a confident green ONLINE)
- **ZFS Events** -- Recent ZFS kernel events
- **SMART Status** -- Disk health for all drives in each pool (resolved via `/dev/disk/by-id/`); one-click `smartmontools` install when missing (per-disk temperature/wear trends live under Metrics)
- **LXC Restore Clone Cleanup** -- View and destroy leftover restore-mount datasets with one-click cleanup
- **VM Zvol Restore Sessions** -- Overview of active mounts, kpartx mappings, and snapdev status with per-item unmount and bulk cleanup
- **Scheduled Tasks Overview** -- Shows active AI report schedules (per-host and combined) with next-run and last-run times
- **ZDB Deep Diagnostics** -- Automatic `zdb` analysis for DEGRADED/FAULTED pools (block stats, disk labels)

### Historical Metrics (Trends)
- **Background Sampler** -- Captures pool capacity, fragmentation, allocation, health and dedup ratio every 15 minutes per host
- **Per-Disk SMART** -- Temperature (traffic-light by media type -- HDD 45/55 °C, SSD/NVMe 60/70 °C), SMART health, wear/percentage-used, reallocated/pending sectors and power-on hours for every physical disk, with a temperature trend chart; `smartmontools` installable inline when missing
- **SQLite Storage** -- Configurable retention (default 90 days for metrics, 365 for the audit log -- `METRICS_RETENTION_DAYS` / `AUDIT_RETENTION_DAYS`) in `/app/data/pvezfs.db`; auto-trimmed each cycle with a WAL checkpoint-truncate so the volume stays bounded
- **Inline Trend Charts** -- Theme-aware inline SVG (gradient area fill, no external JS): pool capacity%, fragmentation%, allocated GB, and per-disk temperature
- **Configurable Range** -- 6 h / 24 h / 7 d / 30 d / 90 d views
- **Sample Now** -- Trigger an immediate sample after adding a new host

### Audit Log
- **Destructive Actions Logged** -- Login success/failure, host add/remove, pool scrub/upgrade, dataset create/destroy/set, snapshot create/destroy/rollback/clone, auto-snapshot toggle, file & zvol restores, cache invalidation, notifications/AI config saves
- **Fields Recorded** -- Timestamp, user, IP, host, action code, target, JSON details, success flag
- **Filterable UI** -- Filter by action, user, current host, or failures only
- **SQLite Backed** -- Indexed for fast queries, persisted across restarts

### Performance
- **SSH Connection Pool** -- Connections are reused per (thread, host) instead of a fresh handshake per command: health-checked before reuse (120 s idle TTL), one transparent reconnect when a reused connection died, command timeouts never re-run the command. SFTP downloads (host backups) use the same pool. Opt-out with `SSH_POOL=0`
- **SSH Command Cache** -- TTL-based in-memory cache (15-300s) for read-only ZFS queries drastically reduces SSH round-trips on active views; writes automatically invalidate the cache for the affected host
- **Batched Reads** -- Multi-value reads (e.g. the ARC editor's six values) are collected in a single SSH round-trip
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
  - `replication_lag` -- a replication pair's last sync exceeds its expected interval (WARN/CRIT), and on recovery
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
  - Host config backup failed (scheduled)

### AI Reports & Analysis
- **Multi-Provider Support** -- OpenAI (GPT), Anthropic (Claude), Ollama (local), or any OpenAI-compatible API
- **Ollama Model Discovery** -- Automatically query and select available models from your Ollama instance
- **Per-Host & Combined Schedules** -- Independent daily/weekly plans per host, plus an optional combined "all hosts" report
- **Comprehensive Analysis** -- Pool health, storage capacity, scrub status, snapshot coverage, SMART health, anomalies
- **Fixed Seven-Section Layout** -- Every report has the same structure (Overall, Capacity, Scrub, Snapshots, SMART, Anomalies, Recommendations) so reports are comparable run to run
- **Colored Status Markers** -- Each section heading carries a green / amber / red marker in both the PDF and the web viewer, with a status banner at the top of the PDF
- **Fact-Based Verdict** -- The per-section status and the overall verdict are computed from the collected facts (pool health, capacity %, scrub age, SMART, retention), not the LLM prose, so the e-mail verdict can never contradict a green report. The notification e-mail carries a one-line verdict (✅ / ⚠️ / 🚨) and the full report as a PDF attachment
- **Snapshot Retention Analysis** -- Per-dataset per-label retention check, gap detection, stale snapshot warnings
- **ZDB Diagnostics** -- Automatic deep analysis triggered for degraded/faulted pools
- **Actionable Recommendations** -- Prioritized suggestions for scrubs, cleanup, capacity planning
- **Interactive Chat** -- Ask follow-up questions about your ZFS data
- **Scope Toggle & Dispatch Feedback** -- Generate a single-host or combined "all hosts" report on demand; per-card "Test now" buttons report which channels actually received the message
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
- **Wake-on-LAN** -- Wake an offline host from the Hosts view: the management NIC's MAC is captured automatically while the host is online; magic packets are sent from the container **and** relayed via every other reachable host (a bridged Docker network usually can't broadcast into the LAN, a sibling PVE node can)
- **Expected Offline** -- Mark a host as expected-offline (e.g. a backup server that is powered off most of the time and woken via WOL): no offline notifications for its up/down cycles, a neutral gray "Expected Offline" badge instead of red, and the HOSTS dashboard tile stays green. While awake it is monitored normally
- **Clean Removal** -- Deleting a host also clears all of its monitoring state (offline flag, pool health, stale-snapshot counts, replication-lag rows) so no ghost entries linger on the dashboard

### Host Config Backup
- **Config-Level Snapshot** -- One-click backup of a Proxmox host's configuration (NOT VM disks): the `/etc/pve` cluster filesystem, network config (`interfaces`, `hosts`, `resolv.conf`), **APT repos + signing keys** (`/etc/apt` minus `auth.conf`, **plus `/usr/share/keyrings/*.gpg`** -- the deb822 keyring location outside `/etc` that e.g. bashclub uses), **`/root/.ssh/authorized_keys`** (public keys), **`/etc/fstab`**, **`/etc/vzdump.conf`**, the **zfs-auto-snapshot retention cron**, **bashclub-zsync replication config** (`/etc/bashclub`) and the **ARC limit** (`/etc/modprobe.d/zfs.conf`), plus command captures (`pveversion -v`, `dpkg --get-selections`, `ip`/`route`, `zpool`/`zfs` state) -- everything needed to bring a rebuilt host back to full working order
- **NIC Naming Artifacts** -- Persistent-name rules (`udev *net*.rules`, systemd `.link` files) and a per-NIC identity capture (MAC, driver via `ethtool -i`, `udevadm` path) — a PVE major upgrade can rename interfaces, and this is exactly what you need to reconstruct the mapping
- **Pulled Into the Tool** -- The archive is fetched into the data volume via SFTP and can be downloaded any time for a worst-case recovery
- **Scheduled** -- Per-host daily/weekly/monthly schedule with a keep-N retention; a failed scheduled backup raises a `host_backup_failed` notification
- **Secrets Opt-In** -- `/etc/pve/priv` (cluster CA private key etc.) is **excluded by default**; an explicit toggle includes it, with an in-UI warning that those archives are highly sensitive. All downloads are login-gated
- **Under Hosts** -- A per-host "Backup" action opens create-now, schedule, and the stored-backup list (download / delete)

### PVE Config Restore
Rebuild a **freshly-installed PVE** to a previous host's configuration from a host-config backup — no bare-metal/OS restore needed (found under **Proxmox → PVE Config Restore**).
- **Backup Browser + Selective Restore** -- Browse a backup's files, categorized (Guests, Network, Storage, Package sources (APT), Users, SSH access, Firewall, Jobs & cron, other `/etc/pve`, read-only system info); preview any file and restore individual ones. `/etc/pve/nodes/<oldnode>/…` is remapped to the local node, and the executable bit is preserved (cron run-parts scripts stay runnable)
- **Four Primary Actions, in order** -- (1) **Reinstall packages**, (2) **Restore all configs**, (3) **Reboot**, (4) **Restore all guest configs** — the recommended recovery sequence, right at the top
- **Reboot + Hand-Off** -- The restored config only takes effect after a restart. The reboot is fired backgrounded (so the call returns cleanly), and since `authorized_keys` + network came back with the configs, the target picker then switches itself from the ad-hoc IP/password entry to the matching **registered host** and waits until it's back online — the guest configs then go over the tool's SSH key
- **Reinstall Packages (self-contained)** -- **First** restores the APT sources + signing keys from the backup (so third-party packages are resolvable), **then** installs the captured package set with `apt-get install` (the manually-installed set from `apt-mark showmanual` when present, else the full install-marked selection; unknown names filtered against `apt-cache pkgnames` so one stale name can't abort the run), as a background task with live progress. Reports **honestly** which requested packages are still not installed afterwards — the still-missing list and apt log are shown
- **Restore All Configs** -- One click restores every config file except guest configs (own button) and info-only captures: network, storage/fstab, APT sources, firewall, jobs/cron, users, SSH access, misc `/etc/pve`
- **Bulk Guest Configs** -- Restore every VM/CT `<vmid>.conf` at once
- **Per-Category Bulk Restore** -- Each category header (collapsible, collapsed by default) also has its own "Restore all" for finer control
- **Bulk Restore Overwrites** -- The bulk actions replace existing files by design (a full restore brings the old config back); when "Overwrite" is unchecked the confirm dialog says so, so pre-existing stock files on a fresh host aren't silently skipped. Single-file restore keeps the skip-unless-overwrite safety
- **Ad-Hoc Target** -- Point at a **not-yet-registered** host by IP + user + password (transient, never stored, never logged); a reinstalled host's new SSH host key is accepted automatically. Avoids the register-first chicken-and-egg
- **Bring the Host Back Online** -- Restore `authorized_keys` (or one-click "install tool key") so the original registered host (same address) becomes key-reachable again after the restore
- **Safety Rails** -- Preview + per-item confirm, no blind pmxcfs overwrite, single-file restore keeps existing files unless "Overwrite", a connectivity warning on network config

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
