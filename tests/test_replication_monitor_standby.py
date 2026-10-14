"""A pair whose source is marked expected-offline is a standby pair, not a
critical one.

Follow-up to the host-backup mail storm: the replication monitor did not
look at the standby flag either. A deliberately powered-off source cannot
sync, its lag grows, and the pair read CRIT once -- a false alarm with a
true cause. "standby" is its own status now: shown, never alerted.
"""

import pytest

from app import database
from app import replication_monitor as rm


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(database, "_initialized", False)
    database.init_db()
    yield


def _snap(status, **over):
    s = {"host_address": "10.0.0.9", "host_name": "pve2", "config_path": "/etc/bashclub/10.0.0.5.conf",
         "source": "root@10.0.0.5", "target": "tank/repl", "status": status,
         "last_sync_ts": 1_700_000_000, "lag_seconds": 90_000, "schedule": "0 * * * *"}
    s.update(over)
    return s


# --- classification ------------------------------------------------------------

@pytest.fixture
def one_pair(monkeypatch):
    alerted = []
    monkeypatch.setattr(rm, "list_configs", lambda h: {"configs": [
        {"path": "/etc/bashclub/10.0.0.5.conf", "source": "root@10.0.0.5", "target": "tank/repl"}]})
    monkeypatch.setattr(rm, "_check_pair", lambda h, c: _snap("crit"))
    monkeypatch.setattr(rm, "_maybe_alert", lambda snap: alerted.append(snap))
    return alerted


def test_a_pair_whose_source_is_standby_reads_standby_not_crit(one_pair, monkeypatch):
    monkeypatch.setattr(rm, "load_hosts", lambda: [{"address": "10.0.0.5", "standby": True},
                                                   {"address": "10.0.0.9"}])
    out = rm.run_checks_for_host({"address": "10.0.0.9"})
    assert [p["status"] for p in out] == ["standby"]
    assert out[0]["standby"] is True
    assert one_pair[0]["status"] == "standby"        # the alert path sees standby too


def test_the_same_pair_reads_crit_when_the_source_is_not_marked(one_pair, monkeypatch):
    monkeypatch.setattr(rm, "load_hosts", lambda: [{"address": "10.0.0.5"}, {"address": "10.0.0.9"}])
    out = rm.run_checks_for_host({"address": "10.0.0.9"})
    assert [p["status"] for p in out] == ["crit"]


def test_a_bare_address_in_source_matches_too():
    assert rm._source_ip("root@10.0.0.5") == "10.0.0.5"
    assert rm._source_ip("10.0.0.5") == "10.0.0.5"
    assert rm._source_ip("") == ""


# --- transitions: standby never fires, and coming back is not a recovery -------

@pytest.fixture
def sent(temp_db, monkeypatch):
    calls = []
    monkeypatch.setattr(rm, "send_notification", lambda *a, **k: calls.append(a))
    return calls


def test_crit_then_standby_is_silent(sent):
    rm._maybe_alert_locked(_snap("crit"))
    assert len(sent) == 1                                  # the real CRIT alerted
    rm._maybe_alert_locked(_snap("standby"))
    rm._maybe_alert_locked(_snap("standby"))
    assert len(sent) == 1                                  # nothing for standby, no cooldown re-fire


def test_standby_then_ok_is_not_announced_as_a_recovery(sent):
    rm._maybe_alert_locked(_snap("standby"))
    rm._maybe_alert_locked(_snap("ok", lag_seconds=60))
    assert sent == []


def test_standby_then_crit_alerts_like_any_new_fault(sent):
    rm._maybe_alert_locked(_snap("standby"))
    rm._maybe_alert_locked(_snap("crit"))
    assert len(sent) == 1 and "CRIT" in sent[0][1]


def test_the_summary_and_the_rank_know_standby():
    assert rm._STATUS_RANK["standby"] == rm._STATUS_RANK["pending"]
    assert rm._STATUS_RANK["standby"] < rm._STATUS_RANK["warn"]
