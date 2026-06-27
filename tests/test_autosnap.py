"""zfs-auto-snapshot retention parsing + in-place file rewriting."""

import pytest
from app import autosnap as a


# Real-world file shapes -------------------------------------------------

FREQUENT_CRON_D = (
    "# /etc/cron.d/zfs-auto-snapshot\n"
    "PATH=/usr/bin:/bin\n"
    "*/15 * * * * root which zfs-auto-snapshot > /dev/null 2>&1 && "
    "zfs-auto-snapshot --quiet --syslog --label=frequent --keep=4 //\n"
)

HOURLY_SCRIPT = (
    "#!/bin/sh\n"
    "exec zfs-auto-snapshot --quiet --syslog --label=hourly --keep=24 //\n"
)

DAILY_DISABLED = (
    "#!/bin/sh\n"
    "# exec zfs-auto-snapshot --quiet --syslog --label=daily --keep=31 //\n"
)


# --- parse_level ----------------------------------------------------------

def test_parse_enabled_level():
    p = a.parse_level(FREQUENT_CRON_D, "frequent")
    assert p == {"keep": 4, "enabled": True}


def test_parse_script_level():
    p = a.parse_level(HOURLY_SCRIPT, "hourly")
    assert p == {"keep": 24, "enabled": True}


def test_parse_disabled_level():
    p = a.parse_level(DAILY_DISABLED, "daily")
    assert p == {"keep": 31, "enabled": False}


def test_parse_missing_level_returns_none():
    assert a.parse_level("nothing here\n", "weekly") is None


def test_parse_prefers_active_over_commented():
    content = (
        "# exec zfs-auto-snapshot --label=hourly --keep=99 //\n"
        "exec zfs-auto-snapshot --label=hourly --keep=24 //\n"
    )
    assert a.parse_level(content, "hourly") == {"keep": 24, "enabled": True}


# --- parse_retention ------------------------------------------------------

def test_parse_retention_orders_levels_and_marks_missing():
    files = {"frequent": FREQUENT_CRON_D, "hourly": HOURLY_SCRIPT}
    levels = a.parse_retention(files)
    assert [l["label"] for l in levels] == a.LEVELS  # fixed order
    by = {l["label"]: l for l in levels}
    assert by["frequent"]["keep"] == 4 and by["frequent"]["installed"]
    assert by["hourly"]["keep"] == 24
    assert by["daily"]["installed"] is False
    assert by["daily"]["keep"] is None


# --- update_level_content -------------------------------------------------

def test_update_keep_in_cron_d():
    out = a.update_level_content(FREQUENT_CRON_D, "frequent", keep=8)
    assert "--keep=8" in out
    assert "--keep=4" not in out
    # untouched bits preserved
    assert "PATH=/usr/bin:/bin" in out
    assert "*/15 * * * * root" in out


def test_update_keep_in_script():
    out = a.update_level_content(HOURLY_SCRIPT, "hourly", keep=48)
    assert "--keep=48" in out
    assert out.startswith("#!/bin/sh")


def test_disable_then_reenable_roundtrip():
    disabled = a.update_level_content(HOURLY_SCRIPT, "hourly", enabled=False)
    assert a.parse_level(disabled, "hourly")["enabled"] is False
    # keep value survives the comment toggle
    assert a.parse_level(disabled, "hourly")["keep"] == 24
    reenabled = a.update_level_content(disabled, "hourly", enabled=True)
    assert a.parse_level(reenabled, "hourly") == {"keep": 24, "enabled": True}


def test_enable_disabled_daily():
    out = a.update_level_content(DAILY_DISABLED, "daily", enabled=True)
    assert a.parse_level(out, "daily")["enabled"] is True


def test_update_keep_and_enabled_together():
    out = a.update_level_content(DAILY_DISABLED, "daily", keep=14, enabled=True)
    p = a.parse_level(out, "daily")
    assert p == {"keep": 14, "enabled": True}


def test_update_only_touches_matching_label():
    # A file that happens to mention two labels: only the targeted one changes.
    content = (
        "exec zfs-auto-snapshot --label=hourly --keep=24 //\n"
        "exec zfs-auto-snapshot --label=daily --keep=31 //\n"
    )
    out = a.update_level_content(content, "hourly", keep=12)
    assert "--label=hourly --keep=12" in out
    assert "--label=daily --keep=31" in out  # untouched
