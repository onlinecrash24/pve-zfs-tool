"""AI-report scheduler seeding: a restart BEFORE a schedule's hour must not
mark today as done and skip the run. Regression for schedules that showed in
'scheduled tasks' but never fired."""

import datetime
import pytest

from app import ai_reports as ar


@pytest.fixture(autouse=True)
def _clean_last_run():
    saved = dict(ar._last_run_keys)
    ar._last_run_keys.clear()
    yield
    ar._last_run_keys.clear()
    ar._last_run_keys.update(saved)


def _daily(hour, enabled=True, key="host:10.0.0.1"):
    return {"key": key, "host": "10.0.0.1", "enabled": enabled,
            "interval": "daily", "hour": hour, "weekday": 0}


def _weekly(hour, weekday, enabled=True, key="__all__"):
    return {"key": key, "host": None, "enabled": enabled,
            "interval": "weekly", "hour": hour, "weekday": weekday}


# --- _period_target_passed -------------------------------------------------

def test_daily_target_passed():
    now = datetime.datetime(2026, 7, 24, 8, 0)   # 08:00
    assert ar._period_target_passed(now, _daily(7)) is True
    assert ar._period_target_passed(now, _daily(8)) is True    # at the hour
    assert ar._period_target_passed(now, _daily(9)) is False   # upcoming


def test_weekly_target_passed():
    wed = datetime.datetime(2026, 7, 22, 8, 0)   # 2026-07-22 is a Wednesday (wd=2)
    assert ar._period_target_passed(wed, _weekly(7, 0)) is True   # Mon already gone
    assert ar._period_target_passed(wed, _weekly(7, 2)) is True   # today, hour passed
    assert ar._period_target_passed(wed, _weekly(9, 2)) is False  # today, hour upcoming
    assert ar._period_target_passed(wed, _weekly(7, 4)) is False  # Fri still ahead


# --- the regression: restart before the hour must NOT suppress today -------

def test_restart_before_hour_leaves_schedule_unseeded(monkeypatch):
    monkeypatch.setattr(ar, "get_active_schedules", lambda: [_daily(7)])
    now = datetime.datetime(2026, 7, 24, 6, 0)   # app (re)starts at 06:00
    ar._seed_elapsed_schedules(now)
    # 7:00 hasn't happened yet -> not seeded -> the loop will fire it at 7:00
    assert "host:10.0.0.1" not in ar._last_run_keys


def test_restart_after_hour_seeds_to_avoid_refire(monkeypatch):
    monkeypatch.setattr(ar, "get_active_schedules", lambda: [_daily(7)])
    now = datetime.datetime(2026, 7, 24, 8, 0)   # restart at 08:00, past 07:00
    ar._seed_elapsed_schedules(now)
    # already elapsed today -> seeded with today's key -> won't re-fire today
    assert ar._last_run_keys["host:10.0.0.1"] == "2026-07-24"


def test_seed_never_clobbers_an_already_run_schedule(monkeypatch):
    monkeypatch.setattr(ar, "get_active_schedules", lambda: [_daily(7)])
    ar._last_run_keys["host:10.0.0.1"] = "2026-07-23"   # ran yesterday
    ar._seed_elapsed_schedules(datetime.datetime(2026, 7, 24, 8, 0))
    # setdefault must not overwrite an existing entry
    assert ar._last_run_keys["host:10.0.0.1"] == "2026-07-23"


def test_disabled_schedule_is_not_seeded(monkeypatch):
    monkeypatch.setattr(ar, "get_active_schedules", lambda: [_daily(7, enabled=False)])
    ar._seed_elapsed_schedules(datetime.datetime(2026, 7, 24, 8, 0))
    assert "host:10.0.0.1" not in ar._last_run_keys


def test_run_key_matches_between_loop_and_seed():
    now = datetime.datetime(2026, 7, 24, 8, 0)
    assert ar._period_run_key(now, "daily") == "2026-07-24"
    iso = now.isocalendar()
    assert ar._period_run_key(now, "weekly") == f"{iso[0]}-W{iso[1]}"
