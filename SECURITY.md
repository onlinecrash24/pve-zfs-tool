# Security Policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:
**[Report a vulnerability](https://github.com/onlinecrash24/pve-zfs-tool/security/advisories/new)**
(Security tab → Report a vulnerability). It is private to you and the
maintainer, and it keeps the report, the discussion, and the eventual advisory
in one place.

If you would rather not use GitHub, email **webmaster@onlinecrash24.de** with
`pve-zfs-tool` in the subject. Either way, please do not open a public issue
for anything exploitable.

Useful to include, as far as you have it: the version (shown on the login page
and the home page), what an attacker would need to already have, and the
smallest reproduction you can manage.

This is a single-maintainer project, so the honest expectation is a reply
within a few days rather than a same-day acknowledgement, and a fix in the next
release rather than an out-of-band one. You will be credited in the release
notes unless you would rather not be.

## Supported versions

Only the latest release. There are no backports and no security-only branches —
if a fix ships, it ships in the next tag. Container images are published to
`ghcr.io/onlinecrash24/pve-zfs-tool`; `:latest` follows the most recent release.

## What this tool holds, on purpose

Read this before deciding whether something is a vulnerability, because most of
it looks alarming and is the design.

- **Root SSH on every host you register.** A key pair is generated on first
  start, the private half stays in the `ssh-keys` volume, and you install the
  public half yourself. Everything the tool does — listing pools, destroying
  snapshots, migrating guests, restoring configurations — runs as root over
  that connection. **Access to this web UI is equivalent to an admin SSH
  session on all of your nodes**, and it is meant to be.
- **The container runs as root**, though it needs neither `privileged` nor any
  host mount.
- **The web login is the only door in front of that.** Session-based,
  credentials from environment variables, rate-limited against brute force.
  It belongs behind HTTPS and not on an untrusted network.
- **By default nothing leaves your network.** AI reports are the single
  exception and are opt-in: without an API key nothing is sent, with Ollama
  everything stays local, and "Export raw data" shows the exact JSON that would
  be transmitted.
- **`/metrics` returns 404** unless `PROMETHEUS_TOKEN` is set.
- **Every state change is recorded** in the audit log — who, when, what, from
  which IP.
- **Ad-hoc passwords** (used to reach a host that has lost its key during a
  disaster recovery) live only for that one operation and are never written to
  disk or to the log.
- **No telemetry, no phone-home, no auto-update.**

## In scope

- Authentication or session handling that lets an unauthenticated request reach
  an authenticated route
- CSRF, XSS, or anything that makes a logged-in browser act without intent
- Command injection through a value that reaches a shell — every user-supplied
  value that ends up in an SSH command is supposed to pass an allowlist
  validator in `app/validators.py` first, so a value that gets past one is a
  finding
- Path traversal in the file-restore and host-backup paths
- Leaking credentials, API keys, or ad-hoc passwords into logs, the audit
  trail, saved configuration, or an AI report payload
- A destructive operation that runs without the server-side guard it claims

## Not in scope

- **That the tool runs privileged commands on your hosts.** That is the whole
  feature. A logged-in user destroying a dataset is the product working.
- **The default credentials** (`admin` / `password`) when they are left unset.
  They are a documented fallback and the application warns loudly about them at
  startup. Note that the repository's own `docker-compose.yml` — the one the
  build-from-source path uses — still carries those literal values, while the
  compose block in the README uses placeholders marked `CHANGE THIS!`. Setting
  them is your job either way.
- Anything that requires you to already hold the tool's private SSH key, the
  contents of the data volume, or a valid session.
- Running the UI on a public network without HTTPS.
- Denial of service by asking the tool to do expensive work you are already
  authorised to ask for.

## Known and accepted, as of v0.9.921

Stated here rather than discovered later:

- **The replication key grants a full root shell.** Replication is pull-based,
  so the key pair is generated on the *target* and its public half is installed
  on the *source*: a compromised source has no credential for the target, which
  is the direction that matters most. But that key is installed without
  `restrict`, `from=`, or a forced command, so the exposure runs the other way
  — **a compromised replication target has full root on its source.**
  Narrowing it is planned; a forced command alone cannot work, because the
  disaster-recovery reverse sync legitimately runs `zfs recv` on the source.
- **Replicas are not marked `canmount=noauto`.** In a pull model the target
  connects into the source, so a compromised source controls the stream that
  the target receives and may mount. Planned.
- **The HTTP API is internal.** It follows the UI and is neither documented nor
  stable; treat it as an implementation detail rather than an interface.
