"""The host-backup scheduler on a host that is down.

Field report, verbatim: a host failed, and the tool mailed "Host-Backup
fehlgeschlagen" every 30 minutes. Marking the host "Erwartet offline" changed
nothing. Two defects: the scheduler never looked at that flag, and every
retry (one per _RETRY_THROTTLE_SEC) sent its own notification.
"""

from datetime import datetime

import pytest

from app import hostbackup as hb

NOW = datetime(2026, 10, 10, 10, 0)


@pytest.fixture
def world(monkeypatch):
    """One scheduled host; everything that touches SSH or disk is stubbed.
    Returns the mutable knobs the tests turn."""
    w = {"hosts": [{"address": "10.0.0.5", "name": "PVE-Malta"}],
         "result": {"success": False, "error": "archive build failed on host: timed out"},
         "attempts": 0, "sent": [], "clock": 1_700_000_000.0}
    monkeypatch.setattr(hb, "load_config", lambda: {"schedules": {
        "10.0.0.5": {"enabled": True, "interval": "daily", "hour": 3, "keep": 8}}})
    monkeypatch.setattr(hb, "load_hosts", lambda: w["hosts"])
    monkeypatch.setattr(hb, "_newest_backup_dt", lambda h: None)       # always due
    monkeypatch.setattr(hb, "prune_backups", lambda h, k: 0)

    def create(host, include_priv=False):
        w["attempts"] += 1
        return dict(w["result"])
    monkeypatch.setattr(hb, "create_backup", create)
    monkeypatch.setattr(hb.time, "time", lambda: w["clock"])
    monkeypatch.setattr("app.notifications.send_notification",
                        lambda ev, title, msg, **k: w["sent"].append((title, k.get("priority"))))
    hb._last_attempt.clear(); hb._failing.clear(); hb._standby_logged.clear()
    yield w
    hb._last_attempt.clear(); hb._failing.clear(); hb._standby_logged.clear()


def _tick(w, advance=hb._RETRY_THROTTLE_SEC + 1):
    w["clock"] += advance
    hb._scheduler_tick(now=NOW)


def test_a_host_marked_expected_offline_is_not_even_tried(world):
    world["hosts"][0]["standby"] = True
    for _ in range(4):
        _tick(world)
    assert world["attempts"] == 0
    assert world["sent"] == []


def test_a_failing_host_is_retried_but_notified_once(world):
    for _ in range(5):
        _tick(world)
    assert world["attempts"] == 5                     # retries keep going
    assert len(world["sent"]) == 1                    # one mail, not five
    assert world["sent"][0][0].startswith("Host-Backup fehlgeschlagen: PVE-Malta")


def test_retries_respect_the_throttle(world):
    _tick(world)
    _tick(world, advance=60)                          # a minute later: too soon
    assert world["attempts"] == 1


def test_recovery_is_announced_once_and_a_later_outage_notifies_again(world):
    _tick(world); _tick(world)                        # failing, one mail
    world["result"] = {"success": True, "duration_sec": 1, "bytes": 10}
    _tick(world); _tick(world)                        # back: one recovery mail
    titles = [t for t, _ in world["sent"]]
    assert titles == ["Host-Backup fehlgeschlagen: PVE-Malta",
                      "Host-Backup wieder erfolgreich: PVE-Malta"]
    assert world["sent"][1][1] == 3                   # recovery is low priority
    world["result"] = {"success": False, "error": "timed out"}
    _tick(world)
    assert len(world["sent"]) == 3                    # a new outage is a new mail


def test_a_healthy_host_sends_nothing(world):
    world["result"] = {"success": True, "duration_sec": 1, "bytes": 10}
    for _ in range(3):
        _tick(world)
    assert world["sent"] == []


def test_a_crash_inside_create_backup_counts_as_a_failure_not_a_scheduler_death(world, monkeypatch):
    def boom(host, include_priv=False):
        raise RuntimeError("paramiko exploded")
    monkeypatch.setattr(hb, "create_backup", boom)
    _tick(world); _tick(world)
    assert len(world["sent"]) == 1
    assert "crashed" in hb._failing and False or "10.0.0.5" in hb._failing


def test_clearing_standby_resumes_attempts(world):
    world["hosts"][0]["standby"] = True
    _tick(world)
    world["hosts"][0]["standby"] = False
    _tick(world)
    assert world["attempts"] == 1
