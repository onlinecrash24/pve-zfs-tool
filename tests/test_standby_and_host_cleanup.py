"""Host removal cleans its monitor state; standby hosts never fire offline
notifications and are counted separately on the dashboard."""

import pytest

from app import database, monitor, analytics
from app import ssh_manager as sm


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(database, "_initialized", False)
    database.init_db()
    yield


def _put_state(scope, key, value="x"):
    conn = database.get_conn()
    try:
        conn.execute(
            "INSERT INTO monitor_state (scope, key, value, updated_ts) VALUES (?,?,?,0)",
            (scope, key, value),
        )
        conn.commit()
    finally:
        conn.close()


def _all_keys():
    conn = database.get_conn()
    try:
        rows = conn.execute("SELECT scope, key FROM monitor_state").fetchall()
        return {(r["scope"], r["key"]) for r in rows}
    finally:
        conn.close()


# --- clear_host_state (the host-delete bug) --------------------------------

def test_clear_host_state_removes_all_scopes_for_host(temp_db):
    addr, other = "192.168.1.80", "192.168.66.70"
    _put_state("host", addr, "down")
    _put_state("pool_health", f"{addr}:rpool", "ONLINE")
    _put_state("capacity", f"{addr}:rpool", "below")
    _put_state("stale_snap", f"{addr}:daily", '{"count": 2}')
    _put_state("repl", f"{addr}::/etc/bashclub/x.conf", '{"status": "ok"}')
    _put_state("host", other, "up")
    _put_state("pool_health", f"{other}:rpool", "ONLINE")

    removed = monitor.clear_host_state(addr)
    assert removed == 5
    assert _all_keys() == {("host", other), ("pool_health", f"{other}:rpool")}


def test_clear_host_state_escapes_like_wildcards(temp_db):
    # '_' is a LIKE wildcard AND legal in hostnames: deleting 'my_host' must
    # not sweep 'myxhost' rows along.
    _put_state("pool_health", "my_host:rpool", "ONLINE")
    _put_state("pool_health", "myxhost:rpool", "ONLINE")
    removed = monitor.clear_host_state("my_host")
    assert removed == 1
    assert ("pool_health", "myxhost:rpool") in _all_keys()


# --- standby: no offline notifications --------------------------------------

def test_standby_host_never_notifies_on_transitions(temp_db, monkeypatch):
    sent = []
    monkeypatch.setattr(monitor, "send_notification",
                        lambda *a, **k: sent.append(a))
    host = {"address": "10.0.0.9", "name": "miyagi", "standby": True}
    monitor.check_host_reachability(host, True)    # first sample: up
    monitor.check_host_reachability(host, False)   # goes down (normal for standby)
    monitor.check_host_reachability(host, True)    # wakes up again
    monitor.check_host_reachability(host, False)   # sleeps again
    assert sent == []
    # ... but the state is still tracked for the dashboard
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT value FROM monitor_state WHERE scope='host' AND key=?",
            ("10.0.0.9",)).fetchone()
        assert row["value"] == "down"
    finally:
        conn.close()


def test_normal_host_still_notifies(temp_db, monkeypatch):
    sent = []
    monkeypatch.setattr(monitor, "send_notification",
                        lambda *a, **k: sent.append(a[0]))
    host = {"address": "10.0.0.1", "name": "pve1"}
    monitor.check_host_reachability(host, True)    # baseline: up (no alert)
    monitor.check_host_reachability(host, False)   # transition -> alert
    assert sent == ["host_offline"]


def test_disabling_standby_causes_no_spurious_alert(temp_db, monkeypatch):
    # Host went down while in standby (state recorded, no alert). Turning
    # standby OFF while it's still down must not fire a late alert -- only a
    # future transition should.
    sent = []
    monkeypatch.setattr(monitor, "send_notification",
                        lambda *a, **k: sent.append(a[0]))
    host = {"address": "10.0.0.9", "standby": True}
    monitor.check_host_reachability(host, False)
    host_no_sb = {"address": "10.0.0.9"}
    monitor.check_host_reachability(host_no_sb, False)   # prev==new -> quiet
    assert sent == []
    monitor.check_host_reachability(host_no_sb, True)    # recovery -> alert
    assert sent == ["host_offline"]


# --- set_host_standby -------------------------------------------------------

def test_set_host_standby_updates_entry(monkeypatch):
    hosts = [{"address": "10.0.0.9", "name": "miyagi", "port": 22, "user": "root"}]
    saved = {}
    monkeypatch.setattr(sm, "load_hosts", lambda: hosts)
    monkeypatch.setattr(sm, "save_hosts", lambda h: saved.update(done=h))
    ok, _ = sm.set_host_standby("10.0.0.9", True)
    assert ok is True
    assert saved["done"][0]["standby"] is True
    ok, _ = sm.set_host_standby("1.2.3.4", True)
    assert ok is False


# --- dashboard counts standby separately ------------------------------------

def test_dashboard_standby_not_counted_offline(temp_db, monkeypatch):
    hosts = [
        {"address": "10.0.0.9", "name": "miyagi", "standby": True},
        {"address": "10.0.0.1", "name": "pve1"},
    ]
    import app.ssh_manager
    monkeypatch.setattr(app.ssh_manager, "load_hosts", lambda: hosts)
    _put_state("host", "10.0.0.9", "down")
    _put_state("host", "10.0.0.1", "up")

    d = analytics.dashboard()
    agg = d["aggregate"]
    assert agg["hosts_online"] == 1
    assert agg["hosts_offline"] == 0
    assert agg["hosts_standby"] == 1
    by_addr = {h["address"]: h for h in d["hosts"]}
    assert by_addr["10.0.0.9"]["standby"] is True
    assert by_addr["10.0.0.9"]["reachable"] is False
    assert by_addr["10.0.0.1"]["standby"] is False
