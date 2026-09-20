"""A pulled disk leaves a tile behind under Metrics. Field report, verbatim:
"ich hab gerade eine hdd getauscht und habe jetzt unter metriken natürlich
eine leiche" -- and the replacement usually inherits the kernel name.
"""

import time as _time

import pytest

import app.main as m
from app import database, metrics


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(database, "_initialized", False)
    database.init_db()
    yield


def _disk(dev, serial, model="M", temp=30):
    return {"device": dev, "type": "hdd", "model": model, "serial": serial, "temp_c": temp,
            "power_on_hours": 100, "health_passed": True, "realloc_sectors": 0,
            "pending_sectors": 0, "wear_pct": None}


def _at(monkeypatch, ts):
    monkeypatch.setattr(metrics.time, "time", lambda: ts)


H = "10.0.0.1"


def test_a_disk_missing_from_the_latest_cycle_is_stale(temp_db, monkeypatch):
    now = int(_time.time())
    _at(monkeypatch, now - 900)
    metrics._store_disk_metrics(H, [_disk("sda", "WD-1", "WD Red"), _disk("nvme0n1", "KX-1")])
    _at(monkeypatch, now)
    metrics._store_disk_metrics(H, [_disk("nvme0n1", "KX-1")])       # sda was pulled
    tiles = {d["device"]: d for d in metrics.latest_disks(H)}
    assert tiles["sda"]["stale"] is True
    assert tiles["nvme0n1"]["stale"] is False
    assert tiles["sda"]["timestamp"] == now - 900                     # its last sighting


def test_a_replacement_with_the_same_name_is_a_second_tile_not_a_merged_one(temp_db, monkeypatch):
    now = int(_time.time())
    _at(monkeypatch, now - 900)
    metrics._store_disk_metrics(H, [_disk("sda", "WD-1", "WD Red", temp=40)])
    _at(monkeypatch, now)
    metrics._store_disk_metrics(H, [_disk("sda", "ST-2", "Seagate", temp=31)])
    tiles = sorted(metrics.latest_disks(H), key=lambda d: d["serial"])
    assert [(d["serial"], d["model"], d["stale"]) for d in tiles] == [
        ("ST-2", "Seagate", False), ("WD-1", "WD Red", True)]


def test_forgetting_the_old_disk_leaves_the_same_named_replacement_alone(temp_db, monkeypatch):
    now = int(_time.time())
    for i, ts in enumerate((now - 2700, now - 1800, now - 900)):
        _at(monkeypatch, ts)
        metrics._store_disk_metrics(H, [_disk("sda", "WD-1", "WD Red")])
    _at(monkeypatch, now)
    metrics._store_disk_metrics(H, [_disk("sda", "ST-2", "Seagate")])
    old = [d for d in metrics.latest_disks(H) if d["serial"] == "WD-1"][0]
    assert metrics.forget_disk(H, "sda", "WD-1", old["timestamp"]) == 3
    left = metrics.latest_disks(H)
    assert [(d["serial"], d["stale"]) for d in left] == [("ST-2", False)]
    assert len(metrics.query_disk_series(H, device="sda", hours=24)) == 1


def test_without_serials_the_time_bound_still_protects_the_replacement(temp_db, monkeypatch):
    # USB bridges sometimes report no serial. Then both disks are "sda" with
    # serial "", and only the timestamp separates them.
    now = int(_time.time())
    _at(monkeypatch, now - 900)
    metrics._store_disk_metrics(H, [_disk("sda", None, "old")])
    _at(monkeypatch, now)
    metrics._store_disk_metrics(H, [_disk("sda", None, "new")])
    # One tile (same key); forgetting "up to the old sighting" must not touch the new row.
    assert metrics.forget_disk(H, "sda", "", now - 900) == 1
    assert [d["model"] for d in metrics.latest_disks(H)] == ["new"]


def test_forget_is_scoped_to_the_host(temp_db, monkeypatch):
    now = int(_time.time())
    _at(monkeypatch, now)
    metrics._store_disk_metrics(H, [_disk("sda", "WD-1")])
    metrics._store_disk_metrics("10.0.0.2", [_disk("sda", "WD-1")])
    assert metrics.forget_disk(H, "sda", "WD-1", now) == 1
    assert len(metrics.latest_disks("10.0.0.2")) == 1


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["csrf_token"] = "t"
    return c


def test_the_route_refuses_an_unbounded_delete(client, monkeypatch):
    called = []
    monkeypatch.setattr("app.metrics.forget_disk", lambda *a: called.append(a) or 0)
    for body in ({"host": H, "device": "sda"},                     # no bound
                 {"host": H, "device": "sda", "before": "soon"},   # not a timestamp
                 {"host": H, "before": 1}):                        # no device
        r = client.post("/api/metrics/disks/forget", json=body, headers={"X-CSRF-Token": "t"})
        assert r.status_code == 400, body
    assert called == []


def test_the_route_forgets_and_reports_the_count(client, monkeypatch):
    seen = {}
    monkeypatch.setattr("app.metrics.forget_disk",
                        lambda h, d, s, b: seen.update(h=h, d=d, s=s, b=b) or 3)
    r = client.post("/api/metrics/disks/forget",
                    json={"host": H, "device": "sda", "serial": "WD-1", "before": 1700000000},
                    headers={"X-CSRF-Token": "t"})
    assert r.get_json() == {"success": True, "deleted": 3}
    assert seen == {"h": H, "d": "sda", "s": "WD-1", "b": 1700000000}
