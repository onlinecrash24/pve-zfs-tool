"""A failed probe is not an outage until a second one fails.

Field report, 15 hosts, 03.11.2026: the nightly dist-upgrade restarted sshd
for seconds, the sampler round ran in that minute, three hosts were
reported offline and "back online" came a round -- 15 minutes -- later.
The hosts were never down. Now: re-probe after HOST_OFFLINE_CONFIRM_SEC,
alert only on the second failure, and announce a recovery only if the
outage was announced.
"""

import pytest

from app import database, monitor


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(database, "_initialized", False)
    database.init_db()
    yield


@pytest.fixture
def sent(temp_db, monkeypatch):
    calls = []
    monkeypatch.setattr(monitor, "send_notification", lambda *a, **k: calls.append((a[1], k.get("state"))))
    return calls


@pytest.fixture
def slept(monkeypatch):
    waits = []
    monkeypatch.setattr(monitor, "_sleep", lambda s: waits.append(s))
    monkeypatch.setattr(monitor, "HOST_OFFLINE_CONFIRM_SEC", 45)
    return waits


HOST = {"name": "PVE-24-RZ", "address": "10.0.0.24"}


# --- the re-probe ---------------------------------------------------------------

def test_a_blip_answers_the_reprobe_and_nothing_fires(sent, slept):
    monitor.check_host_reachability(HOST, True)                     # baseline: up
    assert monitor.confirm_offline(HOST, lambda: True) is True      # the second probe answers
    assert slept == [45]
    assert sent == []
    assert monitor._state_get("host", HOST["address"])[0] == "up"


def test_a_real_outage_fails_the_reprobe_and_fires_once(sent, slept):
    monitor.check_host_reachability(HOST, True)
    assert monitor.confirm_offline(HOST, lambda: False) is False
    monitor.check_host_reachability(HOST, False)
    assert sent == [("Host Offline", "new")]
    assert monitor._state_get("host", HOST["address"])[0] == "down"


def test_a_host_already_known_down_is_not_waited_for(sent, slept):
    monitor.check_host_reachability(HOST, True)
    monitor.check_host_reachability(HOST, False)                    # alerted, state down
    assert monitor.confirm_offline(HOST, lambda: pytest.fail("no re-probe for a known-down host")) is False
    assert slept == []


def test_zero_switches_the_reprobe_off(sent, slept, monkeypatch):
    monkeypatch.setattr(monitor, "HOST_OFFLINE_CONFIRM_SEC", 0)
    monitor.check_host_reachability(HOST, True)
    assert monitor.confirm_offline(HOST, lambda: pytest.fail("must not probe")) is False
    assert slept == []


def test_a_reprobe_that_raises_counts_as_failed(sent, slept):
    monitor.check_host_reachability(HOST, True)

    def boom():
        raise RuntimeError("ssh")
    assert monitor.confirm_offline(HOST, boom) is False


# --- recovery only after an announced outage ---------------------------------------

def test_recovery_follows_an_announced_outage(sent, temp_db):
    monitor.check_host_reachability(HOST, True)
    monitor.check_host_reachability(HOST, False)
    monitor.check_host_reachability(HOST, True)
    assert sent == [("Host Offline", "new"), ("Host Back Online", "resolved")]


def test_a_host_first_seen_down_comes_up_without_a_recovery(sent, temp_db):
    monitor.check_host_reachability(HOST, False)                    # first observation
    monitor.check_host_reachability(HOST, True)
    assert sent == []


def test_a_standby_outage_that_was_never_announced_has_no_recovery(sent, temp_db):
    monitor.check_host_reachability(HOST, True)
    monitor.check_host_reachability(dict(HOST, standby=True), False)   # went down while standby: silent
    monitor.check_host_reachability(HOST, True)                        # standby cleared, host back
    assert sent == []


def test_the_marker_is_cleared_so_the_next_outage_alerts_again(sent, temp_db):
    monitor.check_host_reachability(HOST, True)
    monitor.check_host_reachability(HOST, False)
    monitor.check_host_reachability(HOST, True)
    monitor.check_host_reachability(HOST, False)
    monitor.check_host_reachability(HOST, True)
    assert [s for s, _ in sent] == ["Host Offline", "Host Back Online", "Host Offline", "Host Back Online"]


# --- the sampler asks before it tells ----------------------------------------------

def test_the_sampler_reprobes_before_running_the_checks(monkeypatch, temp_db):
    from app import metrics
    import app.ssh_manager as sm
    answers = iter([False, True])                                   # first probe fails, re-probe answers
    monkeypatch.setattr(sm, "test_connection", lambda h: next(answers))
    monkeypatch.setattr(monitor, "_sleep", lambda s: None)
    seen = {}
    monkeypatch.setattr(monitor, "run_checks", lambda host, pools, reachable, **k: seen.setdefault("reachable", reachable))
    monkeypatch.setattr(metrics, "_record_wol_mac", lambda h: None, raising=False)
    import app.zfs_commands as zc
    monkeypatch.setattr(zc, "get_pools_result", lambda h: {"success": True, "pools": []}, raising=False)
    monitor.check_host_reachability(HOST, True)
    try:
        metrics._sample_and_monitor(dict(HOST))
    except Exception:
        pass                                                        # pool sampling may need more stubs
    assert seen.get("reachable") is True
