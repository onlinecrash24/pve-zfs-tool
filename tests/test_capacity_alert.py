"""Capacity notifications.

A user reported never getting a warning about a pool filling up (snapshots
eating the space). The alert existed and was enabled by default, but only fired
on an UPWARD crossing: the very first observation of a pool was recorded
silently, so a pool that was already above the threshold when the tool was
installed never notified at all. Thresholds were also hardcoded at 90%, which is
late for ZFS.
"""

from app import monitor as m
from app.notifications import DEFAULT_CONFIG


# --- banding ---------------------------------------------------------------

def test_capacity_level_bands():
    assert m.capacity_level(50, 70, 80) == "below"
    assert m.capacity_level(70, 70, 80) == "warn"      # inclusive
    assert m.capacity_level(79.9, 70, 80) == "warn"
    assert m.capacity_level(80, 70, 80) == "crit"      # inclusive
    assert m.capacity_level(97, 70, 80) == "crit"


# --- the reported bug ------------------------------------------------------

def test_already_full_pool_alerts_on_first_sight():
    # THE regression: unseen pool already above the threshold used to stay
    # silent forever because only crossings alerted
    assert m.should_alert_capacity(None, "warn") is True
    assert m.should_alert_capacity(None, "crit") is True


def test_first_sight_below_threshold_is_quiet():
    assert m.should_alert_capacity(None, "below") is False


def test_alerts_only_on_the_way_up():
    assert m.should_alert_capacity("below", "warn") is True
    assert m.should_alert_capacity("warn", "crit") is True
    assert m.should_alert_capacity("below", "crit") is True
    # no repeat while it stays there, and no alert while it drains
    assert m.should_alert_capacity("warn", "warn") is False
    assert m.should_alert_capacity("crit", "crit") is False
    assert m.should_alert_capacity("crit", "warn") is False
    assert m.should_alert_capacity("crit", "below") is False


def test_unknown_previous_state_is_treated_as_unseen():
    # old rows stored "above"/"below"; an unknown value must not swallow the alert
    assert m.should_alert_capacity("above", "crit") is True


# --- configurable thresholds ----------------------------------------------

def test_defaults_warn_before_it_is_too_late():
    th = DEFAULT_CONFIG["thresholds"]
    assert th["capacity_warn_pct"] == 70
    assert th["capacity_crit_pct"] == 80


def test_thresholds_come_from_the_config(monkeypatch):
    monkeypatch.setattr("app.notifications.load_config",
                        lambda: {"thresholds": {"capacity_warn_pct": 60,
                                                "capacity_crit_pct": 85}})
    assert m.capacity_thresholds() == (60.0, 85.0)


def test_warn_above_crit_is_pulled_back(monkeypatch):
    # otherwise the warning band would be empty and never reachable
    monkeypatch.setattr("app.notifications.load_config",
                        lambda: {"thresholds": {"capacity_warn_pct": 95,
                                                "capacity_crit_pct": 80}})
    warn, crit = m.capacity_thresholds()
    assert warn <= crit


def test_broken_config_falls_back(monkeypatch):
    def boom():
        raise RuntimeError("no config")
    monkeypatch.setattr("app.notifications.load_config", boom)
    warn, crit = m.capacity_thresholds()
    assert warn == m.CAPACITY_WARN_PCT and crit == m.CAPACITY_CRIT_PCT
