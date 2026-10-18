"""Mute per host, with an expiry.

Field report: a host went dark for a company move, the host-backup retry
mailed every 30 minutes, and the only way out was to delete the
replication cron and switch mail off for the whole estate. One switch on
the host, checked centrally in send_notification, with an expiry so a
host muted for a move does not stay silent for a year.
"""

import io
import os
import re
import time

import pytest

from app import notifications as nt
from app import ssh_manager as sm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = 1_800_000_000


# --- the flag -------------------------------------------------------------------

def test_mute_states():
    assert not sm.host_is_muted({"address": "h"})
    assert not sm.host_is_muted({"address": "h", "muted": False, "muted_until": None})
    assert sm.host_is_muted({"address": "h", "muted": True, "muted_until": None})            # until switched off
    assert sm.host_is_muted({"address": "h", "muted": True, "muted_until": NOW + 60}, now=NOW)
    assert not sm.host_is_muted({"address": "h", "muted": True, "muted_until": NOW - 1}, now=NOW)  # expired: off by itself
    assert not sm.host_is_muted({"address": "h", "muted": True, "muted_until": "garbage"})


def test_set_host_mute_stores_an_expiry_or_none(monkeypatch):
    hosts = [{"name": "pve1", "address": "10.0.0.5"}]
    monkeypatch.setattr(sm, "load_hosts", lambda: hosts)
    monkeypatch.setattr(sm, "save_hosts", lambda h: None)
    ok, _ = sm.set_host_mute("10.0.0.5", True, hours=24)
    assert ok and hosts[0]["muted"] and abs(hosts[0]["muted_until"] - (time.time() + 86400)) < 5
    sm.set_host_mute("10.0.0.5", True, hours=None)
    assert hosts[0]["muted"] and hosts[0]["muted_until"] is None
    sm.set_host_mute("10.0.0.5", False)
    assert not hosts[0]["muted"] and hosts[0]["muted_until"] is None
    assert sm.set_host_mute("10.0.0.9", True)[0] is False


# --- the gate -------------------------------------------------------------------

@pytest.fixture
def quiet_channels(monkeypatch):
    monkeypatch.setattr(nt, "load_config", lambda: {"events": {"host_offline": True},
                                                     "telegram": {}, "gotify": {}, "matrix": {},
                                                     "email": {}, "webhook": {}})
    monkeypatch.setattr(sm, "load_hosts", lambda: [
        {"name": "pve1", "address": "10.0.0.5", "muted": True, "muted_until": None},
        {"name": "pve2", "address": "10.0.0.6"},
    ])


def test_a_muted_host_sends_nothing_by_name_or_address(quiet_channels):
    for ref in ("pve1", "10.0.0.5"):
        res = nt.send_notification("host_offline", "t", "m", host=ref)
        assert res.get("skipped") and res.get("muted"), ref


def test_an_unmuted_host_and_no_host_go_through(quiet_channels):
    assert not nt.send_notification("host_offline", "t", "m", host="pve2").get("muted")
    assert not nt.send_notification("host_offline", "t", "m").get("muted")


def test_a_broken_host_list_never_blocks_a_notification(quiet_channels, monkeypatch):
    monkeypatch.setattr(sm, "load_hosts", lambda: (_ for _ in ()).throw(RuntimeError("disk")))
    assert not nt.send_notification("host_offline", "t", "m", host="pve1").get("muted")


# --- every caller names its host ---------------------------------------------------

def test_every_notification_call_passes_its_host():
    missing = []
    for fn in ("hostbackup.py", "main.py", "monitor.py", "zfs_commands.py", "replication_monitor.py"):
        src = io.open(os.path.join(ROOT, "app", fn), encoding="utf-8").read()
        for m in re.finditer(r"\bsend_notification\(", src):
            if src[src.rfind("\n", 0, m.start()):m.start()].lstrip().startswith("def "):
                continue
            depth, j = 1, m.end()
            while depth and j < len(src):
                depth += {"(": 1, ")": -1}.get(src[j], 0)
                j += 1
            call = src[m.start():j]
            if "host=" not in call and "request.json" not in call and "data.get" not in call:
                missing.append(f"{fn}:{src[:m.start()].count(chr(10)) + 1}")
    assert missing == [], missing


# --- the replication pair of a muted source -----------------------------------------

@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    from app import database
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(database, "_initialized", False)
    database.init_db()


def test_a_crit_pair_of_a_muted_source_is_silent(temp_db, monkeypatch):
    from app import replication_monitor as rm
    sent = []
    monkeypatch.setattr(rm, "send_notification", lambda *a, **k: sent.append(k))
    monkeypatch.setattr(sm, "load_hosts", lambda: [
        {"name": "pve1", "address": "10.0.0.5", "muted": True, "muted_until": None},
        {"name": "pve2", "address": "10.0.0.9"}])
    snap = {"host_address": "10.0.0.9", "host_name": "pve2", "config_path": "/etc/bashclub/10.0.0.5.conf",
            "source": "root@10.0.0.5", "target": "tank/repl", "status": "crit",
            "last_sync_ts": 1_700_000_000, "lag_seconds": 90_000, "schedule": "0 * * * *"}
    rm._maybe_alert_locked(snap)
    assert sent == []
    monkeypatch.setattr(sm, "load_hosts", lambda: [{"name": "pve1", "address": "10.0.0.5"},
                                                   {"name": "pve2", "address": "10.0.0.9"}])
    rm._maybe_alert_locked(dict(snap, config_path="/etc/bashclub/other.conf"))
    assert len(sent) == 1 and sent[0].get("host") == "10.0.0.9"       # the target host is named
