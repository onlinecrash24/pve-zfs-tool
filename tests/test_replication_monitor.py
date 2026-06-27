"""Cron-interval estimation and lag classification for the replication monitor."""

import pytest
from app import replication_monitor as rm


# --- cron interval (smallest gap between firings within 24h) --------------

def test_interval_every_15_min():
    assert rm.cron_interval_seconds("*/15 * * * *") == 15 * 60


def test_interval_hourly():
    assert rm.cron_interval_seconds("0 * * * *") == 3600


def test_interval_every_2h():
    assert rm.cron_interval_seconds("0 */2 * * *") == 2 * 3600


def test_interval_bashclub_default_20_past_0_to_22():
    # fires at :20 of hours 0..22 -> smallest gap is 1h between consecutive
    # firings; the wrap from 22:20 to 00:20 next day is 2h, not the minimum.
    assert rm.cron_interval_seconds("20 0-22 * * *") == 3600


def test_interval_once_per_day():
    assert rm.cron_interval_seconds("0 3 * * *") == 86400


@pytest.mark.parametrize("bad", ["", "0 3 * *", "not a cron", None, "0 3 * * * *"])
def test_interval_unparseable_returns_none(bad):
    assert rm.cron_interval_seconds(bad) is None


# --- field expansion ------------------------------------------------------

def test_expand_star():
    assert rm._expand_field("*", 0, 5) == [0, 1, 2, 3, 4, 5]


def test_expand_step_and_range_and_list():
    assert rm._expand_field("*/2", 0, 6) == [0, 2, 4, 6]
    assert rm._expand_field("1-3", 0, 59) == [1, 2, 3]
    assert rm._expand_field("0,30", 0, 59) == [0, 30]
    assert rm._expand_field("0-10/5", 0, 59) == [0, 5, 10]


# --- lag classification ---------------------------------------------------

def test_classify_pending_when_no_lag():
    assert rm._classify(None, 3600) == "pending"


def test_classify_ok_within_2x_interval():
    assert rm._classify(3600, 3600) == "ok"
    assert rm._classify(int(3600 * 1.9), 3600) == "ok"


def test_classify_warn_between_2x_and_4x():
    assert rm._classify(int(3600 * 3), 3600) == "warn"


def test_classify_crit_beyond_4x():
    assert rm._classify(int(3600 * 5), 3600) == "crit"


def test_classify_uses_default_interval_when_zero():
    # interval 0 falls back to DEFAULT_INTERVAL_SECONDS internally
    assert rm._classify(10, 0) == "ok"
