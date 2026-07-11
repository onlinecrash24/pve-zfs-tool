"""Per-guest replication state: tag map, state combination, source health."""

import json
import pytest

from app import replication as r
from app import replication_monitor as rm
from app import database


def _ds(name, tagged):
    return {"name": name, "type": "volume" if "disk" in name else "filesystem",
            "tagged": tagged, "value": "all" if tagged else ""}


# --- guest_replication_map ------------------------------------------------

def test_map_full_partial_none_and_ignores_non_guest():
    datasets = [
        _ds("rpool", False),                       # pool root — ignored
        _ds("rpool/data", False),                  # container — ignored
        _ds("rpool/ROOT/pve-1", False),            # system — ignored
        _ds("rpool/data/vm-100-disk-0", True),     # 100: both tagged -> full
        _ds("rpool/data/vm-100-disk-1", True),
        _ds("rpool/data/vm-101-disk-0", True),     # 101: one of two -> partial
        _ds("rpool/data/vm-101-disk-1", False),
        _ds("rpool/data/subvol-111-disk-0", False),  # 111: none tagged
        _ds("rpool/data/vm-100-state-suspend", True),  # state — not a disk
        _ds("rpool/data/vm-100-cloudinit", True),      # cloudinit — not a disk
        _ds("rpool/data/base-9000-disk-0", True),      # template disk
    ]
    m = r.guest_replication_map(datasets)
    assert m["100"] == {"total": 2, "tagged": 2, "state": "full"}
    assert m["101"] == {"total": 2, "tagged": 1, "state": "partial"}
    assert m["111"] == {"total": 1, "tagged": 0, "state": "none"}
    assert m["9000"]["state"] == "full"
    # state/cloudinit did not inflate the disk count for 100
    assert m["100"]["total"] == 2


def test_map_empty():
    assert r.guest_replication_map([]) == {}
    assert r.guest_replication_map(None) == {}


# --- guest_replication_states ---------------------------------------------

def _states(datasets, src=None):
    return r.guest_replication_states(datasets, src)


def test_states_colors_from_tagging():
    ds = [_ds("rpool/data/vm-100-disk-0", True),   # full
          _ds("rpool/data/vm-101-disk-0", True),   # partial
          _ds("rpool/data/vm-101-disk-1", False),
          _ds("rpool/data/vm-102-disk-0", False)]  # none
    s = _states(ds)
    assert s["100"]["state"] == "green" and s["100"]["reason"] == "ok"
    assert s["101"]["state"] == "yellow" and s["101"]["reason"] == "partial"
    assert s["102"]["state"] == "red" and s["102"]["reason"] == "none"


def test_states_lagging_source_downgrades_full_to_yellow():
    ds = [_ds("rpool/data/vm-100-disk-0", True)]
    assert _states(ds, "ok")["100"]["state"] == "green"
    assert _states(ds, "pending")["100"]["state"] == "green"   # not warn/crit
    assert _states(ds, "warn")["100"] == {"state": "yellow", "reason": "lag",
                                          "tagged": 1, "total": 1}
    assert _states(ds, "crit")["100"]["reason"] == "lag"


def test_states_lag_does_not_rescue_untagged():
    # a non-replicated guest stays red regardless of source status
    ds = [_ds("rpool/data/vm-100-disk-0", False)]
    assert _states(ds, "crit")["100"]["state"] == "red"


# --- source_health_map (persisted monitor state) --------------------------

@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(database, "_initialized", False)
    database.init_db()
    yield


def _put(key, value):
    conn = database.get_conn()
    try:
        conn.execute(
            "INSERT INTO monitor_state (scope, key, value, updated_ts) VALUES (?,?,?,?)",
            ("repl", key, json.dumps(value), 0),
        )
        conn.commit()
    finally:
        conn.close()


def test_source_health_map_worst_status_per_source(temp_db):
    # same source feeds two targets: worst status wins
    _put("t1::/etc/bashclub/a.conf", {"source": "root@192.168.1.251", "status": "ok"})
    _put("t2::/etc/bashclub/b.conf", {"source": "root@192.168.1.251", "status": "warn"})
    _put("t3::/etc/bashclub/c.conf", {"source": "root@192.168.66.70", "status": "ok"})
    m = rm.source_health_map()
    assert m["192.168.1.251"] == "warn"    # warn beats ok
    assert m["192.168.66.70"] == "ok"


def test_source_health_map_ignores_incomplete_rows(temp_db):
    _put("t1::x", {"status": "crit"})                 # no source
    _put("t2::y", {"source": "root@10.0.0.1"})        # no status
    assert rm.source_health_map() == {}
