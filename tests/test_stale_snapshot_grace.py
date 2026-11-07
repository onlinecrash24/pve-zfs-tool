"""The stale-snapshot check waits until the host has been up long enough.

Field report: a host marked Expected Offline was off for two days, booted
at 17:42, and at 17:51 -- the first sample -- "Stale hourly" and "Stale
daily" arrived, eight datasets each. Nothing was wrong: a host takes no
snapshots while it is off. A label is now judged only once the uptime
exceeds that label's threshold; a host up for a day without a daily
snapshot is reported as before.
"""

import pytest

from app import database, monitor


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(database, "_initialized", False)
    database.init_db()
    sent = []
    monkeypatch.setattr(monitor, "send_notification", lambda *a, **k: sent.append(a[1]))
    import app.zfs_commands as zc
    import app.snapshot_analysis as sa
    monkeypatch.setattr(zc, "get_snapshot_ages", lambda h: {"datasets": {"rpool/data": {}}})
    monkeypatch.setattr(zc, "get_auto_snapshot_status", lambda h: {"retention_policy": {}})
    monkeypatch.setattr(zc, "get_autosnap_disabled_datasets", lambda h: set())
    stale = {"per_label": {
        "hourly": {"stale_datasets": [{"dataset": "rpool/data"}, {"dataset": "rpool/ROOT"}]},
        "daily": {"stale_datasets": [{"dataset": "rpool/data"}]},
    }}
    monkeypatch.setattr(sa, "analyze_snapshots", lambda *a, **k: stale)
    return sent


HOST = {"name": "pve-test", "address": "10.0.0.7", "standby": True}


def test_first_sample_after_boot_is_silent(world, monkeypatch):
    monkeypatch.setattr(monitor, "host_uptime_seconds", lambda h: 9 * 60)     # booted 9 minutes ago
    monitor.check_auto_snapshots(HOST)
    assert world == []


def test_a_label_is_judged_once_the_host_outlived_its_threshold(world, monkeypatch):
    monkeypatch.setattr(monitor, "host_uptime_seconds", lambda h: 5 * 3600)   # up 5 h: hourly yes, daily not yet
    monitor.check_auto_snapshots(HOST)
    assert world == ["Stale hourly snapshots on pve-test"]


def test_a_long_running_host_is_judged_on_every_label(world, monkeypatch):
    monkeypatch.setattr(monitor, "host_uptime_seconds", lambda h: 3 * 86400)
    monitor.check_auto_snapshots(dict(HOST, standby=False))
    assert sorted(world) == ["Stale daily snapshots on pve-test", "Stale hourly snapshots on pve-test"]


def test_unknown_uptime_keeps_the_old_behaviour(world, monkeypatch):
    monkeypatch.setattr(monitor, "host_uptime_seconds", lambda h: None)
    monitor.check_auto_snapshots(HOST)
    assert len(world) == 2


def test_uptime_is_read_from_proc(monkeypatch):
    import app.ssh_manager as sm
    monkeypatch.setattr(sm, "run_command", lambda h, c, **k: {"success": True, "stdout": "18244.52 71234.10\n"})
    assert monitor.host_uptime_seconds({"address": "h"}) == pytest.approx(18244.52)
    monkeypatch.setattr(sm, "run_command", lambda h, c, **k: {"success": False, "stdout": ""})
    assert monitor.host_uptime_seconds({"address": "h"}) is None
