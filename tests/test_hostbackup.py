"""Host config backup: filename validation, prune selection, backup script."""

import pytest
from app import hostbackup as hb


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


# --- run-key (schedule period) -------------------------------------------

def test_run_key_periods():
    import datetime
    now = datetime.datetime(2026, 6, 27, 3, 0, 0)
    assert hb._run_key(now, "daily") == "2026-06-27"
    assert hb._run_key(now, "monthly") == "2026-06"
    wk = hb._run_key(now, "weekly")
    assert wk.startswith("2026-W")
