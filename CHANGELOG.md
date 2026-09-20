# Changelog

Every release of this project, newest first. Generated from the GitHub
releases by `tools/gen_changelog.py` -- edits here are overwritten, so change
the release notes (or the annotated tag they come from) instead.

Full history and container images: <https://github.com/onlinecrash24/pve-zfs-tool/releases>


## v0.9.924 -- 2026-09-04

v0.9.924 — webhooks, and two things you may need to change

**`X-Forwarded-For` is no longer trusted unless you say so.** ProxyFix used to
be applied unconditionally, so on a directly reachable port — which is what
the shipped compose file does — any client could name its own address. Seven
failed logins with a rotating `X-Forwarded-For` never tripped the rate limit,
and the audit log recorded whatever the caller wrote.

Behind a reverse proxy, set **`TRUST_PROXY=true`**. Until you do, every user
shares one login rate-limit bucket (five failed attempts by anyone lock
everyone out for five minutes) and the audit log records the proxy's address.
The startup log says which mode is active. Do not set it on a port that is
reachable directly.

**`/metrics` takes the token in the `Authorization: Bearer` header only.** The
`?token=` query form is gone: a token in the URL lands in proxy access logs,
browser history and Referer headers, and Prometheus has had bearer auth in
scrape_config for years. Both READMEs already showed only the header.

A fifth channel beside Telegram, Gotify, Matrix and email: one JSON document
per event to any HTTP endpoint — n8n, a Slack incoming webhook, a monitoring
bridge.

The body is an editable JSON template with placeholders, not a fixed format.
Generic and Slack ship as starting templates that fill the textarea; Teams,
Discord, Mattermost or a vendor endpoint need only a different template. The
template is parsed as JSON first and placeholders are filled in inside string
values, so a quote in a message can never break it; a placeholder that is the
whole value keeps its type (`"{{state_code}}"` becomes the number `2`).
Invalid JSON and unknown placeholders are refused when you save, with the
position or the name, rather than discovered when the first alert fires.
**Preview** renders the template against a sample event without sending.

The generic document carries monitoring semantics: `severity`, a Nagios
`state_code` (0/1/2), a stable correlation `key`, and `state` as `new` or
`resolved`. Host offline/online and pool failed/recovered share one key, so a
receiver can close the alert it opened. The URL is the credential — Slack,
n8n and their kind put the token in it — and is treated as one.

A dead receiver cannot take the other channels down: the webhook never raises
and gives up after ten seconds.

The restore browser proved that a path stayed under the mount it was handed —
which, when the mount was `/`, was every path on the host.
`mount_path=/&file=etc/hostname` sent `cat /etc/hostname` to the host without
a single `..`, while the documentation advertised path-traversal protection.
All four browse, preview and restore functions now require the mount to sit
under one of the two bases this tool mounts snapshots at, and the guard
compares on a directory boundary rather than a string prefix.

Names read from the host — vdev names from `zpool status`, kpartx mapper
names, dataset names from `zfs get` — go back to it quoted. The clone-target
dropdown takes one SSH round trip instead of one per pool. The deep
diagnostics, the upgrade check and the SMART topology resolution are cached,
since none of them changes between clicks.

Every one of these was demonstrated against the running application before
it was fixed, and each demonstration is now a test.

## v0.9.923 -- 2026-08-31

v0.9.923 — the German feature list says the same as the English one

A documentation release. No behaviour changes.

"Custom Report Logo" existed only in the English feature list — the last of the
entries that had drifted apart, and, like the four restored before it, a
feature that had been specified in German in the first place. Both files now
list 149 features, with no section differing.

The guard is the point. Two long prose files diverge in silence, and this
drifted twice within a day before anyone counted. A test now compares the
bullet count **per section** rather than as one total, so an entry added to one
section and dropped from another cannot cancel out into a pass. A further test
asserts the files are still shaped the way that comparison reads them —
otherwise a reformat away from the current headings would quietly reduce it to
zero sections equalling zero sections.

Verified by deleting the restored bullet again and watching the test name the
exact section.

CHANGELOG.md regenerated, now covering 32 releases. It can never contain the
release that ships it, so it trails the newest tag by one until regenerated
with `tools/gen_changelog.py`.

## v0.9.922 -- 2026-08-31

v0.9.922 — the placeholders were disarming the warning

**SECURITY.md.** A tool that holds root SSH on every host you register should
say where to report a weakness — and should say what it holds, so a reporter
can tell a finding from the design. "It runs privileged commands on your hosts"
is the product, not a vulnerability. Reports now go through GitHub's private
vulnerability reporting (Security → Report a vulnerability), with email as the
alternative.

It also names what is known and accepted rather than leaving it to be
discovered: the replication key grants a full root shell in the target→source
direction, replicas are not marked `canmount=noauto`, and the HTTP API is
internal and unstable.

**CHANGELOG.md**, generated from the published releases — which are themselves
written into the annotated tag. The history is authored once and readable from
a checkout, without a browser.

**A container health check.** `docker ps` now reports `healthy` or `unhealthy`
instead of only `Up`. It requests `/login`, which renders a real page, so a
pass means the application is serving rather than that the port is open.
Compose inherits it from the image; nothing to add. Note that Docker does not
restart an unhealthy container by itself — `restart: unless-stopped` covers a
crash, and reacting to running-but-not-answering needs an orchestrator.

Putting placeholder credentials into `docker-compose.yml` meant first checking
what the startup warning actually matches. It turned out to be the problem.

The check compared one literal each: `SECRET_KEY == "dev-key-change-me"` and
the pair `admin` / `password`. But the compose file shipped
`SECRET_KEY=change-me-in-production`, and the README block shipped
`your-secret-key-here` and `your-strong-password`. So anyone who ran the
compose file unedited got a **fixed, publicly known session key and was never
told**, and anyone who copied the README block got a publicly known key *and*
password, silently. All six shipped values passed the old check as deliberate
choices.

The checks now match a blocklist of every placeholder this repository ships,
plus the empty string. The password is judged on its own rather than paired
with the username — renaming the user used to excuse a default password. The
tests read the values back out of the compose file and both READMEs, so a
placeholder introduced in documentation without being added to the blocklist
fails the build instead of someone's deployment.

**If you are running with a placeholder `SECRET_KEY`, it is now replaced with a
random one at every restart, so sessions no longer survive a restart until you
set a real one.** That is the treatment the old default already received. A
fixed, publicly known key is worse than being logged out.

Releases now publish themselves from the annotated tag's message — this text.
The first attempt got it wrong: `actions/checkout` materialises a tag as a
lightweight ref, and git's `%(contents)` then resolves to the commit message
without complaining, so v0.9.921 was published carrying its merge commit text.
The notes come from the API now.

## v0.9.921 -- 2026-08-31

v0.9.921 — notifications you can actually switch off

Taking stock of what 1.0 still needs turned up two things that were plainly
wrong rather than merely missing.

**Three notification types could not be switched off.** `send_notification`
gates on `config["events"][key]`, but the settings page builds its checkboxes
from a separate, hand-written map — and nothing tied the two together.
`trim_started`, `trim_finished` and `ai_report` were in the registry and absent
from the map, so they fired with no way to stop them. The translations already
existed in both languages; only the wiring was missing.

The guard matters more than the fix, because this had now happened twice. The
existing translation tests only check that keys the UI *uses* are defined, never
the reverse, which is exactly how the gap survived. A new test walks the event
registry and requires both a translation and a checkbox for every entry — it
fails on the previous commit and names all three.

**The feature list overstated a safeguard.** It claimed the forced `zfs recv -F`
was "gated behind a typed confirmation". It is not; there is no typed
confirmation anywhere in this project. Overstating a safeguard on the most
destructive operation there is, is worse than describing none.

The replacement says what actually protects you, and it is stronger than what
was claimed: the server refuses a forced receive while the destination still
holds snapshots of its own — that means the source looks intact, and forcing
would destroy live data. The refusal stands for a direct API call that never
went through the browser.

v0.9.920 shipped with a tag and an image but no release page, because creating
it was a manual step and it was forgotten. The workflow now does it, after the
image build, so a release never points at a version that failed to build.

The notes come from the annotated tag's own message — this text. They stay
deliberately written at the moment of tagging instead of degrading into a
generated commit list, and `git show <tag>` and the release page say the same
thing. A tag carrying only a subject line still produces a release, from
generated notes, rather than nothing at all.

## v0.9.920 -- 2026-08-31

### Is this pool *built* so that one disk takes everything?

Monitoring so far asked "is something broken right now?" — state changes,
capacity, error counters. It never asked the other question. An unmirrored
`special` or `dedup` vdev holds metadata the rest of the pool cannot be read
without: lose that one device and the pool is gone, and it reports a cheerful
`ONLINE` right up until it does.

**Pool structure check.** The vdev tiers are now parsed out of `zpool status`
and shown as a table in the pool detail view, with the raw text kept below it.
Findings follow what a failure actually costs: `data`, `special` and `dedup`
without redundancy are **critical**, a bare SLOG only **warns** (the pool
survives it), and L2ARC and spares are shown but never flagged.

**Announced once, on transition.** A structural fact is not an event. Repeating
it every cycle would be exactly the permanent false alarm that declared
exceptions were built against — so it is silent on first sight, which also means
upgrading to this does not fire a notification for every pool you already have.
Existing findings appear in the pool view and the AI report instead. The check
reuses the `zpool status` output already fetched for metrics, so it costs no
extra SSH call.

**Replicas that take their own snapshots.** A replica running
zfs-auto-snapshot accumulates snapshots that the source's retention will never
clean up. The property and the replica list were both already being collected
and simply never met. Reported only where zfs-auto-snapshot is genuinely
scheduled on that host — a missing property on a host that never snapshots is
not a finding.

**`zpool trim`**, beside the existing scrub and with the same completion
notification: the once-through companion to the `autotrim` property, which
releases blocks continuously as they are freed. A host that stops answering
mid-trim never produces a completion nobody observed.

#### Verified against four live nodes

Every fixture in the test suite indented with tabs; real `zpool status` on
those nodes indents with spaces. It already worked — the parser reads relative
depth — but nothing held that down, and a parser that has only ever seen tabs
is one assumption away from finding no vdevs at all and declaring every pool
healthy. The four outputs are now pinned as fixtures. Constraining the pool
line to the tab-expanded depth fails exactly those four and leaves all eighteen
tab fixtures green.

What the live output found, all of it correct: two of the four nodes boot from
a single disk (critical), and two mirrored pools carry a bare SLOG alongside an
L2ARC on the same SSD (one warning for the SLOG, nothing for the cache).

#### Also

`FEATURES_DE.md` had drifted four bullets behind the English file — product
detection, backup state, backup overview and declared exceptions. Backfilled.

## v0.9.919 -- 2026-08-23

### The running version is now visible

The application had no idea which version it was — the first question in every
bug report, and the one thing a screenshot could never answer. It now shows
under the login form and beside the title on the home page.

The value comes from the git tag, passed into the image by CI as a build arg
rather than kept in a file. Nothing to remember to bump, and it cannot claim a
release it is not: a tagged build says `v0.9.919`, a dev-branch or source build
says `dev`.

> **This release is the first image built with it.** Earlier `:latest` images
> predate the build arg and show nothing — pull this one to see it.

The login page also gained a footer: **MIT License** linking to the repository
on the left, the version on the right.

### A README that says what this is

The page opened with a logo, one sentence, and then 147 bullet points across 18
sections. The full list moved to [FEATURES.md](FEATURES.md) /
[FEATURES_DE.md](FEATURES_DE.md), unchanged. What replaces it:

- **What it is**, in two sentences — leading with file-level restore from a
  snapshot and GUID-matched replicas rather than with pool listings
- **Who it is for, and who it is not for**: not a cluster manager, not a Proxmox
  Backup Server replacement (it *reads* backups to say what is protected, it
  never writes one), pointless without ZFS
- **What this tool can reach** — a section that was missing entirely. It holds
  root SSH on every registered host, which is the point and also the first
  question anyone sensible asks. Stated first, then the limits that are
  genuinely there: nothing leaves the network by default, `/metrics` is 404
  without a token, every state change is in the audit log, ad-hoc DR passwords
  are never written or logged, no telemetry, no phone-home, no auto-update

The AI reports section now opens with what was already true but buried: opt-in,
nothing sent without an API key, fully local via Ollama, and the raw-data export
shows the exact JSON first.

## v0.9.918 -- 2026-08-09

### Backup overview no longer times out

`Unexpected token '<', "<html> <h"...` on the backup overview is fixed. The
collection could not finish inside the HTTP request: the snapshot inventory
across every host plus one `pvesh` call per backup storage (120s timeout
each, run serially) outlasts gunicorn's 300s ceiling, which then answers with
an HTML error page the client tried to parse as JSON.

It runs as a **background task** now — the same machinery the AI report
button on that page already uses — so there is no deadline at all, and the
page shows where it is (`Collecting… hosts 2/5 192.168.1.251`) instead of
sitting blank. The per-storage backup calls also run four at a time instead
of serially.

### Declared exceptions: guests deliberately unprotected

Some guests have no backup or no replica on purpose. Reporting them as
critical on every run is a permanent false alarm that teaches people to
ignore the whole column — including the rows where the warning is real.

Each row in the backup overview now has an **"Exception…"** button: tick
"needs no backup" and/or "needs no replica", add a reason. Unticking
withdraws it.

The declaration is stored as a **PVE guest tag** (`no-backup` /
`no-replication`, configurable) — visible in the Proxmox UI to anyone
managing the guest, and it travels with the guest on migration. The reason,
who and when are kept in this tool.

- **The tag decides.** Remove it in PVE and the exception ends at once.
- A tag set **without** a recorded reason still counts, but is reported as
  "declared in PVE, no reason recorded" — so a foreign or accidental tag
  surfaces instead of quietly hiding a real gap.
- A declaration only excuses **the gap it names**: a guest with neither
  defence and only one of the two declared stays critical.
- **"By design" is grey, not green** — an unprotected guest is not a
  protected one, even when that is fine.
- A declaration reality has outgrown ("needs no backup" on a guest that has
  been getting backups) is flagged as **stale**.

The AI report treats them as decisions rather than findings, but gives them a
short section of their own: an exception nobody reviews quietly becomes a hole.

### Under the hood

The protection verdict moved to the backend. It was written twice — Python
for the report, JavaScript for the table — and two copies of a growing rule
drift apart. `backups.protection_state` is the single authority now; both
sides render it.

## v0.9.917 -- 2026-08-09

### Backup overview: status pills on replication too

The Backup column carried a green tick or a red cross; the Replikationen
column beside it was plain text. Each replica now leads with the same kind
of pill — in sync, behind, or (for a guest with no replica at all) the cross
its "no copy anywhere" line always deserved.

The verdict is `in_sync`, computed label-aware: a copy configured never to
receive `frequent` snapshots stays green rather than permanently showing a
gap it was never meant to close.

The pill sits on its own line with the detail underneath, matching the
Backup column's layout so both read the same way at a glance.

## v0.9.916 -- 2026-08-08

### Backup overview: the host that backs up but does not replicate

A host whose guests were **all backed up and none replicated** rendered a
blank page — "no replicated guests with this host as their source" — and
generating a report on it failed outright with the same sentence. That host
isn't a gap in coverage; it's a perfectly protected estate, and it looked
like the tool was broken.

The overview now lists **every guest** of the selected host, whether or not
the host replicates anything.

### Reports judge both defences, not just copies

The verdict used to be *critical* whenever any guest lacked a replica — so a
fully backed-up host was reported critical for not replicating. Verdict and
banner counts now combine the two protections, the way the Zustand column has
since v0.9.914:

| Replica | Backup | Verdict |
|---|---|---|
| ✓ | ✓ | ok |
| ✗ | ✓ | ok — a backup *is* a second line of defence |
| ✓ | ✗ | warn — survives a dead disk, nothing else |
| ✗ | ✗ | critical |

A guest whose backups were never examined falls back to the old copy-only
judgement, so a host without backup storage reads exactly as before. An
unreadable backup does not count as protection — "could not ask" is not
"is safe".

The prompt now opens with an assessment of the host instead of a tally, and
states explicitly that a host which backs up every guest and replicates none
is in good shape.

### Renamed and moved

German: "Sicherungs-Übersicht" → **"Backup-Übersicht"**. The menu entry moves
from *ZFS* to *Proxmox VE*, after Migration.

Also: the leading tile counts all guests rather than replicated ones, and
"not replicated" is no longer coloured red — its weight depends on the
backup, and the colour contradicted the Zustand column beside it.

## v0.9.915 -- 2026-08-08

### Standalone Proxmox Backup Server is now refused as a host

Nothing in this tool reads from a backup server — guest backup state comes
from the PVE node's own storage layer. Registering a standalone PBS bought
exactly one thing: root SSH into the machine whose entire value lies in
**not** sharing credentials with the systems it backs up. Adding one is now
refused, and no confirmation overrides it. The message says what the host
actually is (real Proxmox software, just nothing here reads from it) rather
than claiming it isn't Proxmox at all.

A PVE node that also runs PBS stays supported — it's still a PVE node, and
every feature reads through it. Its one-time note changed subject: instead
of promising a future read-only API token for datastore monitoring
(dropped), it now explains what's actually worth knowing — a backup server
sharing a machine with the node it backs up shares that machine's fate.

### Hosts table: back to one product column

Since only PVE nodes can be registered, the separate PVE/PBS columns from
v0.9.911 are gone — one "PVE" column with the detected version. Subtitle:
"Add and manage PVE hosts."

## v0.9.914 -- 2026-08-08

### Backup overview: Zustand column judges replication and backup together

A guest with a copy but no backup used to show green "OK" — one of the two
protections was silently missing. A guest with a backup but no copy showed
red "Gefährdet" even though the backup already covers it. The column now
combines both:

| Copy | Backup | Zustand |
|---|---|---|
| ✓ | ✓ | OK |
| ✓ | ✗ | **⚠ Kein Backup** |
| ✗ | ✓ | **OK** |
| ✗ | ✗ | Gefährdet |

A host where backups were never examined falls back to the old copy-only
judgement unchanged. Also renamed for clarity: "Ohne Kopie" → "Ohne
Replikation", "Kopien" → "Replikationen".

### AI reports: a custom logo in the PDF header

Settings → AI Reports has a new **Report Logo** card: upload PNG, JPEG, GIF,
BMP or WEBP up to 5 MB, and it replaces the tool's own logo in the report
header from the next report on. Stored outside the container image so it
survives an update; oversized images are scaled down automatically. The
footer always credits "Powered by PVE ZFS Tool", regardless of which logo is
shown.

## v0.9.913 -- 2026-08-08

### Backup overview: five tiles in one row

The "Without backup" tile used to trail off on its own line below the other
four. It now sits right next to "Without copy" — the tile it mirrors — so the
two kinds of missing protection read together in a single row.

The grid column count tracks the actual tile count: a host without backup
data shows 4 tiles and a 4-column row, avoiding the visible gap a fixed
5-column grid would otherwise leave.

## v0.9.912 -- 2026-08-08

### Backup state per guest

A "Backup" column next to "Replication" in VMs & CTs, and in the Backup
Overview: is there a backup, how old is the newest one, did it pass PBS
verification, and does an enabled backup job actually cover the guest.

Replication and backup protect against different things — a second ZFS copy
survives a dead disk, a backup survives ransomware, a deleted pool and a wrong
command, all of which replication faithfully copies to every replica. A guest
with three copies and no backup is not safe, and now that shows.

Read **through the PVE node** (`pvesh`), which already holds the PBS
credentials — this tool never touches a PBS credential and opens no connection
to the backup server itself. Covers every storage with `content=backup`: PBS,
NFS, CIFS and plain directories. Defaults: warn at 36 hours, critical at 7
days, both configurable next to the capacity thresholds.

A storage that could **not** be listed yields a grey "unknown", never red — a
PBS that is down must not put a fault on every healthy guest.

Adding a PBS host now shows a one-time note: root SSH is not required for
datastore monitoring; a read-only API token is enough and cannot prune, delete
or restore.

### i18n cleanup

Twenty-one UI labels were showing up as their raw internal key name instead of
translated text — a table header reading "host", a button reading "delete".
All fixed in English and German, with a test now guarding against it
happening again.

## v0.9.911 -- 2026-08-08

### Hosts view: separate PVE and PBS columns

The single combined "Product" column introduced in v0.9.910 could only ever
show one version number, so a host running both products showed the PVE
version with the PBS version nowhere on screen. Two columns now — a green PVE
badge and an orange PBS badge, each with its own version, and an em dash where
the product is absent.

### Hosts page title updated

The page manages PBS hosts too now, so the old "Add and manage Proxmox VE
hosts" title was telling users the opposite of what the new columns show. Now
"Proxmox Host Management — Add and manage PVE and PBS hosts."

## v0.9.910 -- 2026-08-08

### Replication report: scoped to source + copies only

The AI replication report previously received the full registered-host list,
so it could describe machines that had nothing to do with the selected source
— a "silent hosts" section, speculation about offsite backup targets, none of
it supported by data actually collected for that report. The payload now
carries only the source host and the hosts holding copies of its guests, and
the prompt explicitly forbids remarking on anything outside that scope.

### PVE / PBS host detection

First step towards Proxmox Backup Server integration. Every host is now
probed while it is being added and classified as **PVE**, **PBS**, or
**PVE+PBS** — shown in a new "Product" column with the detected version.

- A host that answers and is neither is **refused**; the tool manages Proxmox
  hosts only.
- A host that could not be reached at all (SSH key not installed yet, powered
  off) can still be added on confirmation, and gets identified on the next
  successful connection.
- Detection intentionally ignores `proxmox-backup-client`, which is present
  on ordinary PVE nodes and would otherwise mislabel most of them.

## v0.9.909 -- 2026-08-07

Release v0.9.909 — backup overview: cut the collection cost that made it time out

Reported from a production host: the backup overview took a long time to load,
and once returned "Unexpected token '<'" -- an HTML error page instead of JSON,
which is a gunicorn worker hitting its 300s timeout. The retry succeeded because
the snapshot listing had been cached by then, which is the clearest evidence of
what actually happened.

Filtering now happens on the host rather than after transfer. Only guest-disk
snapshots matter (vm-, subvol-, base-, basevol-N-disk-N); everything under
rpool/ROOT, var-lib-vz and the pool roots was being shipped over SSH and parsed
only to be discarded later for having no VMID. With auto-snapshots every 15
minutes those account for the overwhelming majority of the lines.

Hosts are queried in parallel instead of sequentially, so the wait is the
slowest host rather than the sum of all of them. run_command keeps its SSH
connections thread-local, so the workers never share a connection.

The cache also went from 60 to 300 seconds: the listing is expensive to produce,
the view is opened repeatedly, and the data only changes when a snapshot is
taken.

Found while making that change: grep exits 1 when nothing matches, so a host
that simply has no guest snapshots yet would have been treated as one that
failed to answer. The pipeline ends with "|| true" so an empty result stays an
empty result.

Remaining cost, stated plainly: `zfs list -t snapshot` on the host still walks
every snapshot regardless of what is filtered afterwards, so a first, uncached
load on a very large estate can still take a while. If that turns out to matter,
the collection should move to a background task with progress like migration and
the AI report, after which no request can time out at all.

Adds 4 tests: the filter is present in the remote command and survives an empty
match, the pattern keeps guest disks while dropping pool and system datasets,
hosts really are collected concurrently, and the no-hosts case. 634 tests pass.

## v0.9.908 -- 2026-08-07

Release v0.9.908 — backup overview, and a forecast that actually moves

Backup overview: which host holds the original, and where the copies are

New view under ZFS, plus an AI PDF report. For every guest it names the SOURCE
and every COPY on other hosts, correlated by ZFS snapshot GUID: `zfs send`/`recv`
carries the guid across, so the same snapshot has the same guid on the source and
on every replica -- that is exactly how `zfs send -i` finds its common base. Two
datasets sharing guids are therefore the same lineage no matter what they are
called or which pool they sit in. Names, paths and pool layouts all lie; guids do
not. The host holding the newest snapshot is the source.

The bashclub-zsync configs are read as a second, independent signal. They record
what was intended; the guids show what happened. A host configured as a
replication target that holds the NEWEST snapshots means replication reversed or
stopped -- and the source someone would restore from is not the one they think.
That disagreement is reported per guest.

Shaped by feedback from the first live runs:

- Only VMs and containers. Pool roots, rpool/ROOT/pve-1 and var-lib-vz are
  replicated too but have no VMID; listing them as "? (VM)" buried the entries
  that mattered.
- Scoped to the selected host as source. With hosts replicating to each other,
  an unscoped view describes every guest twice, once per direction. The report
  takes the same host, so a multi-source setup gets one report per source.
- Once a host replicates anything, ALL of its guests are listed -- a guest
  missing from an otherwise working replication set is precisely the omission
  worth catching. A host that replicates nothing yields an empty list rather
  than flagging every guest on a standalone machine.
- Guests with no snapshots at all are listed too. They appear in no snapshot
  listing and were invisible, while being the worst case: no snapshots means no
  local rollback and nothing that could ever have been replicated.
- Labels the copy never receives are not counted as missing. Replication configs
  commonly exclude frequent snapshots on purpose; reporting that choice as a gap
  made a healthy copy look broken, and a number that cries wolf on a correct
  setup is one people learn to ignore. Those labels are named as "not
  replicated" instead. A real gap within a replicated label still counts.

The condensation matters: at 15-minute snapshots across several hosts the raw
listing runs to six figures while the prompt is capped at 30k characters. All
correlation happens in Python; the model receives per-guest summaries only --
counts, timestamps, lag, what is missing -- never individual snapshots.

Capacity forecast: make it react, and say why when it cannot

"Full in" barely moved, by construction: a least-squares fit over 30 days is
~2880 points at 15-minute sampling, so one new sample shifted the slope by
~0.03 %. It was also skewed by the auto-snapshot sawtooth, since least squares
follows every allocation spike and pruning drop.

Samples are now collapsed into hourly medians, the slope is a Theil-Sen median of
pairwise slopes rather than least squares, and the window is 7 days by default,
widening to 30 only when recent data is thin. The dashboard shows the growth rate
("+24 GB/day") next to the projection -- that is the number that visibly moves --
and a dash carries a tooltip explaining why there is no projection.

The dashboard also refreshes itself at the sampler's cadence (15 minutes, read
from the sampler rather than hardcoded) instead of sitting there until someone
reloads. A hidden tab is not refreshed; returning to it reloads immediately.

29 new tests. 630 pass.

Not in this stage: PBS inventory, and hosts the tool cannot reach over SSH
(pasting their snapshot listing in by hand).

## v0.9.907 -- 2026-08-07

Release v0.9.907 — capacity alerts, snapshot space, and A/B diff

Four pieces of user feedback, all traced to code and fixed.

Capacity alerts: notify pools that are already full

A user reported never seeing a warning about ZFS space running out from
snapshots. The alert existed and is enabled by default, but only fired on an
upward crossing -- the first observation of a pool was recorded silently, so a
pool that was already above the threshold when the tool was installed never
notified at all. Same class of bug as the scheduler seeding issue in v0.9.899.

An unseen pool at or above the warning level now alerts straight away; after
that only upward changes do. The threshold was also hardcoded at 90%, which is
late for ZFS -- fragmentation rises and the allocator changes strategy well
before that, and snapshots can take the rest quickly. Two configurable levels
now exist, defaulting to 70% warning / 80% critical, editable under
notification settings. The dashboard tile uses the same threshold via the same
helper, so tile and alert can no longer disagree, and shows the configured
percentage instead of a hardcoded 90.

Show how much space snapshots actually occupy

Asked directly: "can I see anywhere how much space the snapshots use?" The
obvious answer -- sum the per-snapshot "used" column -- is wrong and usually far
too small: that value is what destroying THAT ONE snapshot would free, so
blocks referenced by several snapshots count in none of them. Take snapshots,
then delete the data, and each one reports 0 while the dataset actually holds
everything. With periodic auto-snapshots that is the normal case, not a corner
case.

The Snapshots page now shows the real total from the usedbysnapshots dataset
property, with a per-pool breakdown, and it follows the dataset filter -- picking
a dataset (or its parent) sums that subtree instead of showing the whole host's
number. The Pools page gets a "Snapshots" column per pool. The per-snapshot
column is renamed from "Used" to "Frees on delete" with a tooltip explaining why
it must not be added up -- the old label invited exactly the wrong arithmetic
and is likely why the question came up.

Snapshot diff: compare A against B, not only against live

The diff always compared a snapshot against the current filesystem state; the
backend could already take a second snapshot, the frontend just never offered
one. A "compare against" picker now lists every other snapshot of the same
dataset (plus "live filesystem" as the default). Two `zfs diff` constraints are
handled instead of surfacing as cryptic errors: the pair is ordered by creation
time server-side (zfs diff reads oldest-to-newest and refuses the reverse), and
a cross-dataset pair is refused with a clear reason since zfs diff cannot
compare across datasets.

31 new tests across the four areas. 588 tests pass.

## v0.9.906 -- 2026-08-04

Release v0.9.906 — dialogs open immediately, and never over the wrong page

From a user report: ZFS -> Pools -> "History" appeared to do nothing for a long
time, and the dialog then opened while the user was already on the Snapshots
page. Both halves reproduced in the code, and they were two separate bugs.

No feedback

showPoolHistory awaited its request and only then called openModal, so nothing
happened on screen while the command ran -- and there is no global spinner
either. The same shape existed in pool details (three parallel requests),
dataset properties and guest snapshots, so all four now go through one helper
that opens the frame right away with a loading line and fills it afterwards.
Errors land inside the dialog instead of vanishing: none of these functions had
a try/catch.

The dialog opening over another page

Nothing checked whether a late response still belonged anywhere. A sequence
counter (bumped on every open and close) plus the current view now decide
whether the result is still wanted; if the modal was closed, replaced, or the
user navigated away, the answer is dropped silently.

Why it was slow at all

`zpool history` always emits the pool's complete history and only then gets
tailed to 50 lines, so the cost scales with the pool's lifetime rather than the
output. With zfs-auto-snapshot running every 15 minutes that is quickly hundreds
of thousands of entries, while a freshly created test pool returns instantly --
which is why this never showed up here. It also ran with the 30s default
timeout, so on a large pool it could abort with the user seeing nothing at all,
and without caching, so every click paid the full price. It now has a 120s
timeout and a short cache, and the loading line explains why it may take a
moment.

Also: encodeURIComponent for the host/pool/dataset parameters, which these four
call sites were missing.

Adds 6 tests for the history command (tail limit, raised timeout, caching,
rejected pool name, rejected oversized limit). 565 tests pass.

## v0.9.905 -- 2026-08-04

Release v0.9.905 — guest migration between hosts that are not in a cluster

Moves a VM or container to another Proxmox host even when the two are not
clustered, which Proxmox's own live migration cannot do. Same idea as crossover
(https://github.com/lephisto/crossover), which is Ceph-only: `zfs send -i` takes
the place of `rbd export-diff`.

This is near-live, not zero-downtime. The RAM state is not transferred, so the
guest restarts on the target. The disks are pre-copied while it keeps running
and the pre-copy can be repeated, so the cutover only moves the last delta --
seconds to a couple of minutes. For guests that bashclub-zsync already
replicates, the pre-copy is effectively done and the cutover is just the final
delta plus the config.

The flow, as four numbered steps

1. Preflight. Resolves every attached disk to its ZFS dataset via `pvesm path`
   and checks the target VMID is free, the target dataset exists, a PVE storage
   writes into it, the guest's bridges exist on the target, whether a common
   snapshot allows an incremental send, and which SSH direction actually works.
   Each check reports ok/warn/error with a per-disk plan.
2. Pre-copy, repeatable, while the guest runs.
3. Cutover: graceful shutdown escalating to stop, final delta, config written on
   the target, `lock: migrate` on the source, start on the target.
4. Finish: roll back, remove the guest on the source, or clean up the leftover
   migration snapshots.

Failed checks are fixable from where they appear

- No SSH between the hosts: a button bootstraps the target->source trust (the
  pull direction the transfer prefers, and the one bashclub-zsync uses), reusing
  the replication bootstrap so both features share one mechanism.
- No PVE storage for the chosen dataset: a button registers it, with a
  prefilled sanitised ID and pinned to that node via --nodes, because
  storage.cfg is cluster-wide.

Cross-datastore migration is safe, not merely possible

Different pools, datasets and storage IDs all work. The trap -- sending disks to
tank/data while the config still points at a storage backed by rpool/data, so
PVE cannot find them and the guest fails to start long after the migration
reported success -- is now closed: the storage field only offers storages that
actually write into the selected dataset, and the preflight verifies the pair
server-side. Bridges are picked from the ones present on the target. The config
rewrite is derived from those choices rather than typed as free-form regex.

Safety

Only registered hosts, never ad-hoc password targets. An existing target VMID is
never overwritten (checked in the preflight and again in the cutover). A target
dataset without a common snapshot is refused, because the forced receive would
destroy it. The source is left intact, stopped and locked, so the guest cannot
run on both sides and a rollback stays available until the source is cleaned up
explicitly. Every transfer pipeline runs with `set -o pipefail`, so a failing
send is not masked by a receiver that exits 0.

Also fixed: stale UI after a deploy

The templates loaded app.js/i18n.js with a hardcoded "?v=0.9.168" that had not
been bumped in a long time, so browsers kept serving cached JavaScript against a
new backend -- new UI elements silently missing and new i18n keys rendered as
raw key names. The token now derives from the static files' mtime, so every
deploy invalidates the cache on its own. login.html was not cache-busted at all
and now is.

Migration snapshots are cleaned up

Every transfer round leaves a migrate-* snapshot on both hosts. They are the
incremental base during the migration and clutter afterwards -- the snapshot
check would even report them as forgotten manual snapshots. Step 4 lists and
removes them, keeping the newest on the target by default since that is the base
a later migration back would send incrementally from, and refusing while a
migration is still running.

Not in this stage: zero-downtime/RAM migration, a `qm remote-migrate` mode,
batch migration, Ceph storage, ad-hoc targets. ZFS on both ends is required --
ZFS to LVM-thin/Ceph/directory is not covered.

45 new tests (config parsing, storage.cfg parsing and the match check, the
rewrite, command building, snapshot selection, asset versioning). 559 pass.

## v0.9.904 -- 2026-07-25

Release v0.9.904 — PVE Config Restore: close the gaps a full review turned up

Reviewed the config-restore path end to end: what the backup captures, what the
restore allowlist accepts, and what a rebuilt host actually needs. The mechanics
were sound -- every captured path was restorable, the node-rename remap and the
exec-bit preservation were correct, and APT sources/keyrings were already
restored before the package install. Four real gaps remained.

Cluster config could brick a rebuilt node

corosync.conf was part of the bulk "restore all configs". On a freshly installed
node that makes pve-cluster expect a cluster; without quorum /etc/pve turns
read-only and the host can no longer be configured at all. It now has its own
"cluster" category, is excluded from the bulk restore, and stays available as a
deliberate single-file / per-category restore for an actual cluster rebuild.

sshd_config was not in the backup

A fresh install listens with default settings, so a custom Port /
PermitRootLogin / AllowUsers silently did not come back -- and this tool reaches
the host over SSH. /etc/ssh/sshd_config and sshd_config.d/ are now captured and
restored under the "ssh" category. Host PRIVATE keys remain deliberately
excluded (asserted by a test).

Pool import was missing from the flow

A rebuilt host only has its new rpool, so the data pools sit un-imported on the
disks and a restored storage.cfg points at storage that does not exist. The new
"ZFS pools on the target" card lists imported and importable pools and imports
them individually with -f.

More host state travels with the backup

The whole /etc/modprobe.d (instead of only zfs.conf, so vfio-pci passthrough
options survive too), /etc/systemd/system, /etc/sysctl.d + sysctl.conf,
/etc/timezone, /etc/postfix + /etc/aliases, /etc/exports and /etc/samba. Two new
categories ("system", "mail"); exports/samba join "storage".

Noticed while adding those: postfix and samba carry secrets -- sasl_passwd holds
the SMTP relay password in cleartext and samba's *.tdb hold account secrets.
Both are now excluded, matching the existing rules for apt auth.conf and private
keys.

The deliberate limits are now documented, both in the UI (new "what a config
restore does NOT bring back" card) and in the admin guide: Linux/PAM users and
the root password, SSH host private keys, /etc/pve/priv unless the opt-in was
used, and the enabled state of systemd units (unit files return, re-enable with
systemctl enable).

Note: the added paths only affect NEW backups -- existing archives do not
contain them, so pull a fresh host-config backup before relying on them.

Adds 13 tests (corosync excluded from bulk yet still restorable, categorization
and restorability of every new path, the backup script capturing them, secrets
staying out, zpool-import parsing). 514 tests pass.

## v0.9.903 -- 2026-07-25

Release v0.9.903 — pre-v1.0 code review: data-loss, lockout & correctness fixes

Result of a full code review ahead of v1.0, covering the SSH/ZFS/DR layer,
security, and concurrency/shared state. Every finding below was confirmed
against the code; 501 tests pass (20 new), and the fixes were verified against
live Proxmox hosts (smoke, key rotation, snapshot file restore, and a full DR
run: LXC deleted, restored, snapshot chain resumed).

Data loss / lockout

- Report history could be silently truncated or wiped. _add_report did
  load -> insert -> save without holding the lock across the sequence, and the
  save used a truncating open(). Two concurrent completions (scheduler thread +
  UI async thread) could drop a report, and a reader landing mid-write got a
  partial file, parsed it as empty, and the next save persisted only one entry.
  JSON stores are now written atomically (temp file + os.replace) with the lock
  held across the whole read-modify-write. Same treatment for ai_config.json and
  hosts.json (save_hosts, add_host/remove_host/set_host_standby).

- destroy_snapshot auto-escalated to `zfs destroy -R` whenever a snapshot had
  dependent clones, recursively destroying user clones -- live datasets/VMs
  built from that snapshot -- with no confirmation. It now lists the dependent
  clones and only removes the tool's own restore clones (<pool>/restore-*); a
  foreign clone aborts with the clone named in the error.

- SSH key rotation verified the new key over the OLD connection. invalidate_all()
  clears the result cache but not the thread-local SSH pool, so test_connection
  reused the still-open old-key session, reported success even for a broken new
  key, and then removed the old key -> lockout. The pooled connection is now
  dropped before verification, forcing a fresh connect with the new key.

Correctness

- parse_pool_errors missed SI-suffixed counters. `zpool status` abbreviates large
  counts (1.2K, 15M); the regex matched only \d+, dropping the pool line
  entirely, so a pool with high error counts was reported as having none --
  exactly when the alert matters. Suffixes are now decoded.

- Reverse-sync reported success on a failed send. The `zfs send | ssh zfs recv`
  pipeline ran without pipefail, so only the receiver's exit status counted; a
  send-side failure with a 0-exit receiver looked like a successful DR restore.

- AI chat could answer from another host's data: the collected-data cache was a
  single global, refreshed only when empty, so a question scoped to host X was
  answered from whichever host was collected last. It is now keyed by host.

- reverse_sync_async(force=True) now checks server-side whether the destination
  still holds snapshots before `zfs recv -F`. If it does, the source is intact
  and forcing would destroy live data, so it aborts -- a direct API call can no
  longer bypass the UI's preflight.

- The snapshot file browser/preview/restore path-escape guard fails CLOSED: when
  the remote realpath cannot run, access is denied instead of proceeding to
  ls/cat/cp on an unverified path.

- set_retention no longer reports success for a silent no-op: a keep change on a
  level whose cron command has no --keep= token is now an explicit error.

Concurrency & polish

- The login attempt counter is serialized under a lock, so a concurrent burst
  from one IP can no longer interleave its read-modify-write and slip past the
  5-attempt lockout.
- monitor.run_checks and replication_monitor._maybe_alert run their
  check-then-set-then-notify under a lock, so the metrics sampler and a
  concurrent request (sample-now, replication-health auto-refresh) can't both
  observe the same transition -> no duplicate notifications, no lost cooldown.
- Host-key fingerprints use the standard OpenSSH SHA256 form (base64 of the full
  32-byte digest) instead of a truncated colon-hex string, so they match
  `ssh` / `ssh-keyscan` for out-of-band verification.
- set_arc_limit invalidates the host cache, so the UI shows the new ARC value
  immediately instead of up to 15s later.

Known limitations (unchanged, deliberate): default credentials are warned about
but not enforced; the AI schedule keeps run-keys in memory, so a run missed
while the app was down during its hour is skipped for that period; disks behind
MegaRAID/PERC/cciss controllers still need `-d megaraid,N` for SMART.

## v0.9.902 -- 2026-07-24

Release v0.9.902 — retention: don't report a migrated dataset's cold-start as a gap

The snapshot retention analysis flagged migrated/replicated datasets with a
"massive gap of ~2159 hours (~90 days), no 15-minute rollback possible". The
hole was between a held replication-base snapshot (its source creation travels
with the send stream, dating to the migration moment) and the current frequent
snapshots — i.e. the period before the dataset was regularly snapshotted on this
host, not a zfs-auto-snapshot outage. tank/data/vm-113-disk-0 hit this.

analyze_snapshots now takes an optional dataset_creation map and skips a hole
whose preceding snapshot is at/before the dataset's own creation (a base /
received snapshot). Received snapshots keep their source creation, which is <=
the local dataset creation (the receive time), so this cleanly separates
migration/replication artifacts from genuine outages after the creation date. A
1-hour tolerance absorbs source/destination clock skew. (A plain
max(gap.start, creation) clamp would NOT have helped here: gap.start ~= creation,
so the ~90-day hole would remain — the hole has to be dropped, not clamped.)

New helper get_dataset_creations(host) (one cached `zfs list -Hpo name,creation`)
feeds both the AI report (ai_reports.py) and the web UI Snapshot Check
(main.py). monitor.py is unchanged (it consumes stale_datasets, not gaps).

Adds tests/test_snapshot_gaps.py (5): artifact suppressed, real mid-life outage
kept, backward-compat without creation, unknown dataset not suppressed.
481 tests pass.

## v0.9.901 -- 2026-07-24

Release v0.9.901 — SMART disk card: readable power-on, type-aware indicators

The metrics "Datenträger (SMART)" card showed a fixed 4-column grid (wear,
reallocated, pending, power-on) for every disk, so each drive displayed a "—"
for the indicators that don't apply to its type: SSDs/NVMe have no reallocated/
pending sectors (HDD attributes), HDDs have no wear/percentage-used (an SSD/NVMe
concept). Power-on time was also shown as a coarse "1.3 a".

Now the card renders only the indicators that apply to the disk type — HDD:
reallocated / pending / power-on; SSD/NVMe: wear / power-on — and still shows
any attribute a drive actually reports even if unusual for its type. The grid
sizes its columns to the shown stats, so the "—" placeholders are gone.

Power-on time is now formatted as years + months (e.g. "1 J 4 Mon" instead of
"1.3 a"), with the exact hours/days on hover, and the units are localized
(DE Std/T/J/Mon, EN h/d/y/mo).

Frontend only (app/static/js/app.js, i18n.js); the SMART data model was already
type-correct. node --check clean, i18n keys balanced EN/DE, 476 tests unaffected.

## v0.9.900 -- 2026-07-24

Release v0.9.900 — SMART report: device-type fallbacks, no false warnings

The AI report's SMART section (get_smart_status) queried each disk with a bare
`smartctl -H` and no `-d` device type, then hard-coded anything that wasn't
literally PASSED/FAILED to "Unknown" — which the report scored as a yellow
warning. On Hetzner-style hardware (boot SSD auto-detects, but extra disks sit
behind an HBA/SAT layer or are NVMe) this made sdb/sdc/sdd show "⚠ Unknown"
even though the drives are healthy.

Now get_smart_status tries device-type fallbacks (-d sat / -d auto / -d scsi,
or -d nvme for NVMe) after auto-detect, recognises the SCSI health line
(SMART Health Status: OK), and checks whether smartctl is installed at all.
Genuine no-data cases become "N/A" (or "smartctl fehlt") instead of "Unknown".

Also fixes a base-disk bug: a whole-NVMe vdev (/dev/nvme0n1) was truncated to
the nonexistent /dev/nvme0n by the partition-strip fallback, so it always came
back Unknown. lsblk's PKNAME is now authoritative (empty = already a whole disk).

The report classifier no longer raises a warning for N/A / Unknown /
"smartctl fehlt" — only FAILED is critical, PASSED is ok, the rest is
informational. The report prompt (EN + DE) explains this so the LLM footnote
frames missing SMART data as info, not a fault.

Adds 19 unit tests (tests/test_smart_detect.py) for base-disk resolution
(incl. the whole-NVMe regression), the device-type lists, output
classification, and the softened section-5 scoring. 476 tests pass.

Not covered (documented limitation): disks behind MegaRAID/PERC/cciss
controllers, which need `-d megaraid,N` plus controller enumeration.

## v0.9.899 -- 2026-07-24

Release v0.9.899 — scheduled report skipped after a restart before its hour

Fix: a saved report schedule showed under "scheduled tasks" but never ran at
the set time. On startup, start_scheduler() seeded the last-run marker for
EVERY schedule with today's key so it wouldn't fire immediately on boot -- but
unconditionally. If the app (re)started before a daily schedule's hour (e.g. a
deploy at 06:00 with the report set for 07:00), the schedule got marked
"already ran today" and was skipped for the day. Frequent redeploys made this
reliable. "Scheduled tasks" still listed it because that reads the saved
config, not the last-run state.

Now only schedules whose current-period target has ALREADY passed are seeded
at (re)start; upcoming ones stay unseeded so they still fire at their time.
The seeding also runs on the config-save re-arm, and the loop + seed share one
run-key helper so their period keys can't drift. Adds the scheduler's first
unit tests (7), including the restart-before-hour regression.

## v0.9.898 -- 2026-07-24

Release v0.9.898 — AI schedule save button, masked-secret diagnostics

Fix: scheduled AI reports silently didn't run
- The only "Save configuration" button was at the top of the AI view; the two
  schedule cards + the notify-on-report card are far below it, and toggling a
  schedule there was never persisted (no auto-save). The scheduler reads the
  saved config each tick, so it never saw the new schedules -- while "test now"
  still worked because it fires immediately, independent of saved state. Added
  a second Save button directly under the schedule/notification cards (same
  _saveAIConfig) plus an inline hint that changes only take effect after saving.

Fix: masked-placeholder secrets now report clearly instead of a bogus 401
- If a stored notification secret is itself a mask ("ab...yz", e.g. persisted
  by an older save), the UI can't show the difference and the provider answers
  with a confusing 401 (M_UNKNOWN_TOKEN / invalid token). All four test
  endpoints (telegram/gotify/matrix/email) now detect the effective secret is
  still a placeholder and return an actionable message instead of calling the
  provider. (The Matrix masking/send path itself was verified correct -- a real
  401 then genuinely means the credential was revoked.)

## v0.9.897 -- 2026-07-23

Release v0.9.897 — add-host timeout fix, notification coverage, Proxmox VE label

Fix: adding an unreachable host no longer hangs (or crashes the app)
- The TOFU host-key fetch (get_host_fingerprint) used a timeout-less
  paramiko.Transport((address, port)), so a black-holed host made the TCP
  connect block on the OS-default timeout (minutes) -- stalling the add-host
  request and, on the single-worker gunicorn, tripping the worker timeout and
  restarting the app. Now the socket is created with an explicit connect +
  handshake timeout (default 6s). Verified: returns in ~4s against a
  non-routable IP instead of hanging; the host is still added (TOFU happens on
  the first real connection).

Fix: two notification events were missing from the config UI
- replication_lag and host_backup_failed are fired by the tool but were never
  in the Notifications event-config grid, so they couldn't be toggled (they
  still fired, defaulting to on). Added their checkboxes + EN/DE labels.
  Cross-checked that every fired event now has both a config key and a UI
  control (11 grid checkboxes + the AI view's own report toggle).

UI: sidebar section "Proxmox" renamed to "Proxmox VE".

## v0.9.896 -- 2026-07-22

Release v0.9.896 — blue brand

Visual only — no functional changes.

- Swapped the orange logo, app icon and favicon for the new blue set
  (bars #4a94d6 / #3b82c4 / #2f6ba8) so the brand matches the UI's blue accent.
- Sidebar/login logo (logo.svg): blue bars, light text, transparent.
- README logo (logo-transparent.svg, light-mode fallback): blue bars, dark
  text, transparent.
- AI-report PDF header (logo-small.png): blue, transparent (color-keyed from
  the light-bg PNG since the blue set ships no transparent PNG).
- Favicon / app-icon set (16/32/192/512 + SVG): blue.

## v0.9.895 -- 2026-07-21

Release v0.9.895 — new brand: logo, app icon / favicon, outline menu icons

Visual refresh only — no functional changes.

Logo & app icon
- New logo in the sidebar and on the login page (transparent SVG with light
  text for the dark UI). AI-report PDF header uses the transparent variant.
- Full favicon / app-icon set wired up (16/32/192/512 PNG + SVG) as
  <link rel="icon"> / apple-touch-icon — previously there was none.
- README banner (EN/DE) switches logo variant by color scheme via <picture>.
- Removed the orphaned old logo files.

Sidebar menu icons
- The 15 emoji nav icons replaced with a consistent outline icon set, inlined
  so stroke="currentColor" inherits the nav-item color: active items render in
  the accent blue, inactive in muted grey, hover follows automatically.

## v0.9.894 -- 2026-07-18

Release v0.9.894 — PVE Config Restore hardening: real package reinstall, ZFS property restore, reboot hand-off

Package reinstall now actually installs new packages
- Root cause: dpkg --set-selections only records a selection for packages
  dpkg already knows. On a freshly installed host it has never seen
  bashclub-*, mc, ntfs-3g, parted etc., so set-selections silently dropped
  them and dselect-upgrade reported "0 newly installed" while looking
  successful. Switched to `apt-get install <names>`, which actually resolves
  and installs from the (now restored) repos and pulls dependencies.
- Backup now also captures `apt-mark showmanual`; reinstall prefers that set
  (apt pulls deps, autoremove stays clean) with a fallback to the full
  install-marked dpkg selection for older backups. Names apt doesn't know are
  filtered against `apt-cache pkgnames` first (one stale name can't abort the
  whole install).
- The step is self-contained: it restores the backup's APT sources +
  signing keyrings (including /usr/share/keyrings, outside /etc/apt --
  the deb822 location bashclub uses) BEFORE installing, then honestly
  reports which requested packages are still missing afterward.

Restore ZFS pool/dataset properties
- New: re-apply the backup's locally-set ZFS properties (pool autotrim/
  autoexpand, dataset compression/quota/com.sun:auto-snapshot labels with
  inheritance) via zpool set / zfs set. Closes the gap reverse-sync leaves --
  zfs send -R carries dataset properties for replicated datasets, but never
  pool properties, and never non-replicated datasets. Only applies to
  objects that already exist on the target; create-time-only properties are
  skipped and reported.

Reverse-sync to a reinstalled host
- A rebuilt destination has a new SSH host key, which aborted the transfer
  under strict host-key checking. New on-by-default option refreshes the
  sending host's known_hosts (drops the stale entry, re-scans the current
  key, logs its fingerprint) before sending. A host-key failure now gets a
  targeted hint instead of a generic error.

Config-restore workflow, reordered per testing feedback
- Four numbered primary actions in the recommended order: 1) Reinstall
  packages 2) Restore all configs 3) Reboot 4) Restore all guest configs.
- Reboot fires the restart backgrounded (so the SSH call returns cleanly),
  then the target picker automatically switches from the ad-hoc IP/password
  entry to the matching REGISTERED host (authorized_keys + network came back
  with the configs) and waits until it's reachable again.
- Bulk restores (all configs / per-category / all guests) now overwrite on
  proceed -- on a fresh host, most config files exist as stock defaults, so
  leaving "Overwrite" off was silently skipping exactly the files meant to
  be replaced. When the box is unchecked, the confirm dialog says so
  explicitly. Single-file restore keeps the skip-unless-overwrite safety.
- File categories are now collapsible (collapsed by default) so a 91-file
  backup doesn't dominate the view.
- Backup additionally captures /etc/fstab and /etc/vzdump.conf.

Docs: README (EN/DE) and both PDF guides updated throughout for all of the
above.

## v0.9.893 -- 2026-07-15

Release v0.9.893 — clearer "Expected Offline" label

Renamed "Standby" to "Expected Offline" / "Erwartet offline" throughout the
UI (button, badge, tooltip, toast messages). "Standby" was ambiguous -- it
could read as "the tool puts the host to sleep" or "a failover reserve
system"; neither is what the feature does. The new label states plainly what
it means: this host is expected to be offline most of the time (e.g. a
backup server woken via WOL), so the monitor doesn't alert on it.

The internal field name (hosts.json "standby", POST /api/hosts/standby) is
unchanged for compatibility -- docstrings now note it maps to the "Expected
Offline" UI label. README (EN/DE) and the tools/docgen guides updated to
match.

## v0.9.892 -- 2026-07-15

Release v0.9.892 — standby mode for expected-offline hosts, monitor-state cleanup on host delete

Standby mode (Hosts view)
- Mark a host as expected-offline (e.g. a backup server that's powered off
  most of the time and woken via WOL): no offline/online notifications for
  its up/down cycles, a neutral gray "Standby" badge instead of red, and the
  HOSTS dashboard tile counts it separately (stays green). While the host is
  awake it's monitored normally; disabling standby while it's still down
  causes no late alert -- only future transitions do.
- New POST /api/hosts/standby (audited); flag lives in hosts.json.

Fix: deleting a host left ghost monitor state behind
- The offline flag, per-pool health/capacity/error state, stale-snapshot
  counts and replication-lag rows all lingered in monitor_state forever after
  a host was removed, showing ghost entries on the dashboard. DELETE
  /api/hosts now clears every row for that host (LIKE-escaped so an address
  containing "_" can't sweep another host's rows); the audit entry records
  how many rows were cleared.

Docs
- PDF Admin- and Benutzerhandbuch generators versioned under tools/docgen/
  (reportlab-based, no system deps) with auto-detected version line and a
  documented GitHub-Release-asset process (PDFs stay out of git, attached to
  each release instead).
- README (EN/DE) and the guides updated for standby mode + host cleanup.

## v0.9.891 -- 2026-07-12

Release v0.9.891 — PVE Config Restore, complete host-recovery backup, DR pre-flight

PVE Config Restore (new view, under Proxmox)
- Rebuild a freshly-installed PVE from a host-config backup: browse the backup
  categorized (guests / network / storage / APT repos / users / SSH access /
  firewall / jobs), preview any file and restore selectively; node paths are
  remapped to the local node and the executable bit is preserved.
- Bulk-restore all guest configs; guided package reinstall from the captured
  dpkg selections (install/hold only — additive) as a background task.
- Ad-hoc target by IP + user + password (transient, never stored/logged) for a
  host that isn't registered yet; a reinstalled host's new SSH host key is
  accepted automatically. One-click "install tool key" / authorized_keys
  restore brings the original registered host back online.

Host config backup — now captures everything for a full recovery
- APT repos + signing keys (/etc/apt, minus auth.conf), /root/.ssh/
  authorized_keys (public keys only), zfs-auto-snapshot retention cron files,
  /etc/bashclub replication config, and the ARC limit (modprobe.d/zfs.conf).

Disaster Recovery polish
- Reverse-sync pre-flight: green/orange/red check whether the destination still
  exists on the source (live data) before sending; plain-language explanation
  when ZFS refuses to overwrite an existing snapshot chain.
- Corrected the misleading "force overwrite (recv -F)" hint to the real
  full-stream semantics.

UX
- DEFAULT_LANG (docker-compose) sets the default UI language (de/en).
- Home-page feature cards + README (EN/DE) refreshed.
