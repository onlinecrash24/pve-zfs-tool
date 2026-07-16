"""Host config backup: filename validation, prune selection, backup script,
and the scheduler due-logic (robust against restarts + missed windows)."""

from datetime import datetime
import pytest
from app import hostbackup as hb


# --- backup_filename_dt ---------------------------------------------------

def test_backup_filename_dt_parses():
    assert hb.backup_filename_dt("pve-backup-20260708-120232.tar.gz") == datetime(2026, 7, 8, 12, 2, 32)
    assert hb.backup_filename_dt("pve-backup-20260708-120232-withpriv.tar.gz") == datetime(2026, 7, 8, 12, 2, 32)


def test_backup_filename_dt_invalid():
    assert hb.backup_filename_dt("garbage.tar.gz") is None
    assert hb.backup_filename_dt("") is None


# --- backup_due (the reliability fix) -------------------------------------

DAILY = {"interval": "daily", "hour": 23, "keep": 8}
NOW = datetime(2026, 7, 10, 6, 19)      # 06:19, before the 23:00 target hour


def test_not_due_if_backup_exists_today():
    newest = datetime(2026, 7, 10, 0, 5)   # already have one today
    assert hb.backup_due(newest, NOW, DAILY) is False


def test_daily_waits_for_target_hour_when_recent():
    # last backup was yesterday; it's morning (before 23:00) and not overdue
    newest = datetime(2026, 7, 9, 23, 0)
    assert hb.backup_due(newest, NOW, DAILY) is False


def test_daily_fires_at_or_after_target_hour():
    now_evening = datetime(2026, 7, 10, 23, 5)
    newest = datetime(2026, 7, 9, 23, 0)
    assert hb.backup_due(newest, now_evening, DAILY) is True


def test_daily_catch_up_when_overdue_regardless_of_hour():
    # the 23:00 window on 7/9 was missed (container down); newest is 2 days old
    newest = datetime(2026, 7, 8, 12, 2)   # ~2 days before NOW (06:19 on 7/10)
    assert hb.backup_due(newest, NOW, DAILY) is True   # catches up at 06:19


def test_due_when_no_backup_at_all():
    assert hb.backup_due(None, NOW, DAILY) is True


def test_weekly_only_on_or_after_target_weekday():
    sched = {"interval": "weekly", "hour": 3, "weekday": 2}   # Wed
    # Tuesday morning, no backup this week, not overdue -> not yet
    tue = datetime(2026, 7, 7, 10, 0)      # 2026-07-07 is a Tuesday
    assert hb.backup_due(None, tue, sched) in (True,)  # None newest => overdue => catch up
    # with a recent backup last week, on Tuesday before the Wed target -> wait
    last_week = datetime(2026, 7, 1, 3, 0)
    assert hb.backup_due(last_week, tue, sched) is False
    wed = datetime(2026, 7, 8, 3, 5)       # Wednesday, at hour
    assert hb.backup_due(last_week, wed, sched) is True


def test_weekly_not_due_if_backup_this_week():
    sched = {"interval": "weekly", "hour": 3, "weekday": 2}
    this_week = datetime(2026, 7, 6, 3, 0)   # same ISO week as the 8th
    assert hb.backup_due(this_week, datetime(2026, 7, 8, 5, 0), sched) is False


# --- filename safety ------------------------------------------------------

@pytest.mark.parametrize("name", [
    "pve-backup-20260627-030000.tar.gz",
    "pve-backup-20260627-030000-withpriv.tar.gz",
])
def test_valid_backup_names(name):
    assert hb.is_valid_backup_name(name) is True


@pytest.mark.parametrize("bad", [
    "../etc/passwd",
    "pve-backup-20260627-030000.tar.gz/../x",
    "evil.sh",
    "pve-backup-2026.tar.gz",       # wrong ts shape
    "pve-backup-20260627-030000.zip",
    "",
    "pve-backup-20260627-030000-withpriv.tar.gz.bak",
])
def test_invalid_backup_names(bad):
    assert hb.is_valid_backup_name(bad) is False


def test_safe_addr():
    assert hb._safe_addr("192.168.1.80") == "192.168.1.80"
    assert hb._safe_addr("root@host/../x") == "root_host_.._x"


# --- prune selection ------------------------------------------------------

def _names(*tss):
    return [f"pve-backup-{ts}.tar.gz" for ts in tss]


def test_prune_keeps_n_newest():
    names = _names("20260101-000000", "20260102-000000", "20260103-000000",
                   "20260104-000000")
    # keep 2 -> delete the two oldest
    to_delete = hb.select_prunable(names, keep=2)
    assert to_delete == _names("20260101-000000", "20260102-000000")


def test_prune_nothing_when_under_keep():
    names = _names("20260101-000000", "20260102-000000")
    assert hb.select_prunable(names, keep=5) == []


def test_prune_keep_zero_deletes_all():
    names = _names("20260101-000000", "20260102-000000")
    assert sorted(hb.select_prunable(names, keep=0)) == sorted(names)


def test_prune_ignores_foreign_files():
    names = _names("20260101-000000") + ["random.txt", "notes.md"]
    # only 1 valid backup, keep 5 -> nothing to delete
    assert hb.select_prunable(names, keep=5) == []


# --- backup script --------------------------------------------------------

def test_script_excludes_priv_by_default():
    s = hb._build_backup_script(include_priv=False, dest="/tmp/x.tar.gz")
    assert "INCLUDE_PRIV=0" in s
    assert "--exclude=priv" in s
    assert "/tmp/x.tar.gz" in s
    # the brace-group meta block must render as a real shell brace group
    assert "{ echo" in s and "; }" in s
    assert "{{" not in s  # f-string braces fully resolved


def test_script_includes_priv_when_requested():
    s = hb._build_backup_script(include_priv=True, dest="/tmp/y.tar.gz")
    assert "INCLUDE_PRIV=1" in s


def test_script_captures_expected_commands():
    s = hb._build_backup_script(include_priv=False, dest="/tmp/x.tar.gz")
    for cmd in ("pveversion -v", "dpkg --get-selections", "ip route show",
                "zpool status", "zfs list", "pvecm status"):
        assert cmd in s


def test_script_captures_authorized_keys_not_private():
    # authorized_keys (public) is captured so a restore re-establishes SSH
    # access; private keys must never be swept in.
    s = hb._build_backup_script(include_priv=False, dest="/tmp/x.tar.gz")
    assert "/root/.ssh/authorized_keys" in s
    assert "id_rsa" not in s and "id_ed25519" not in s


def test_script_captures_apt_repos_excludes_auth():
    # APT repo config (+ public signing keys) is captured so a restore brings
    # the package sources back; auth.conf (repo passwords) is excluded.
    s = hb._build_backup_script(include_priv=False, dest="/tmp/x.tar.gz")
    assert "/etc/apt" in s
    assert "--exclude=auth.conf" in s
    # keyrings OUTSIDE /etc/apt (deb822 convention, e.g. bashclub) -- without
    # them the restored .sources fail signature verification (NO_PUBKEY)
    assert "/usr/share/keyrings/" in s


def test_script_captures_fstab_and_vzdump_conf():
    s = hb._build_backup_script(include_priv=False, dest="/tmp/x.tar.gz")
    assert "/etc/fstab" in s
    assert "/etc/vzdump.conf" in s


def test_script_captures_zfs_tool_ancillary_configs():
    # so all ZFS-tool features survive a restore: snapshot retention (cron),
    # replication config, ARC limit.
    s = hb._build_backup_script(include_priv=False, dest="/tmp/x.tar.gz")
    assert "/etc/cron.d" in s
    assert "/etc/cron.hourly/zfs-auto-snapshot" in s
    assert "/etc/bashclub" in s
    assert "/etc/modprobe.d/zfs.conf" in s


def test_script_captures_nic_naming_artifacts():
    # a PVE major upgrade can rename NICs; the backup must carry everything
    # needed to reconstruct the mapping (rules/.link files + MAC/driver/path)
    s = hb._build_backup_script(include_priv=False, dest="/tmp/x.tar.gz")
    for token in ("/etc/udev/rules.d/*net*.rules",
                  "/etc/systemd/network/*.link",
                  "/lib/systemd/network/*.link",
                  "/sys/class/net",
                  "ethtool -i",
                  "udevadm info",
                  "nic-identity.txt"):
        assert token in s


# --- list_all_backups aggregation ----------------------------------------

def test_list_all_backups_aggregates_and_sorts(tmp_path, monkeypatch):
    monkeypatch.setattr(hb, "BACKUP_DIR", str(tmp_path))
    # two hosts, each with backups at different times
    h1 = {"address": "10.0.0.1", "name": "pve1"}
    h2 = {"address": "10.0.0.2", "name": "pve2"}
    import os
    for host, names in [
        (h1, ["pve-backup-20260101-000000.tar.gz"]),
        (h2, ["pve-backup-20260201-000000.tar.gz", "pve-backup-20260115-000000.tar.gz"]),
    ]:
        d = hb.host_backup_dir(host["address"])
        os.makedirs(d, exist_ok=True)
        for n in names:
            with open(os.path.join(d, n), "wb") as f:
                f.write(b"x")
    out = hb.list_all_backups([h1, h2])["backups"]
    # every backup carries its host, newest first across hosts
    assert [b["filename"] for b in out] == [
        "pve-backup-20260201-000000.tar.gz",
        "pve-backup-20260115-000000.tar.gz",
        "pve-backup-20260101-000000.tar.gz",
    ]
    assert out[0]["host_name"] == "pve2"
    assert out[-1]["host_address"] == "10.0.0.1"


# --- run-key (schedule period) -------------------------------------------

def test_same_period():
    now = datetime(2026, 7, 8, 3, 0, 0)
    assert hb._same_period(datetime(2026, 7, 8, 23, 0), now, "daily") is True
    assert hb._same_period(datetime(2026, 7, 7, 23, 0), now, "daily") is False
    assert hb._same_period(datetime(2026, 7, 1, 3, 0), now, "monthly") is True
    assert hb._same_period(datetime(2026, 6, 30, 3, 0), now, "monthly") is False
    assert hb._same_period(datetime(2026, 7, 6, 3, 0), now, "weekly") is True   # same ISO week


# --- scheduler startup ----------------------------------------------------

def test_start_scheduler_does_not_crash_with_schedules(monkeypatch):
    # Regression: start_scheduler() must spawn the loop without referencing any
    # removed globals (it once left a stale `_last_run_keys` seed loop, which
    # raised NameError on boot AND on every POST /host-backup/schedule -> 500).
    monkeypatch.setattr(hb, "load_config", lambda: {"schedules": {
        "10.0.0.1": {"enabled": True, "interval": "daily", "hour": 3, "keep": 8},
    }})
    monkeypatch.setattr(hb, "load_hosts", lambda: [])   # no host -> no real SSH
    # ensure a clean slate (another test may have started it)
    hb._sched_stop.set()
    if hb._sched_thread:
        hb._sched_thread.join(timeout=2)
    hb._sched_thread = None
    try:
        hb.start_scheduler()                       # must not raise
        assert hb._sched_thread is not None and hb._sched_thread.is_alive()
    finally:
        hb._sched_stop.set()
        if hb._sched_thread:
            hb._sched_thread.join(timeout=2)
        hb._sched_thread = None
