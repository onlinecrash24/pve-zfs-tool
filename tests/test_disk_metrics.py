"""disk_metrics storage: latest-per-disk selection and time-series query."""

import time as _time
import pytest

from app import database, metrics


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(database, "_initialized", False)
    database.init_db()
    yield


def _disk(dev, temp, **kw):
    d = {"device": dev, "type": "hdd", "model": "M", "serial": "S", "temp_c": temp,
         "power_on_hours": 100, "health_passed": True, "realloc_sectors": 0,
         "pending_sectors": 0, "wear_pct": None}
    d.update(kw)
    return d


def test_latest_disk_wins_and_series(temp_db, monkeypatch):
    now = int(_time.time())
    # older sample
    monkeypatch.setattr(metrics.time, "time", lambda: now - 900)
    metrics._store_disk_metrics("10.0.0.1", [_disk("sda", 40),
                                             _disk("sdb", 33, type="ssd")])
    # newer sample for sda only
    monkeypatch.setattr(metrics.time, "time", lambda: now)
    metrics._store_disk_metrics("10.0.0.1", [_disk("sda", 42)])

    latest = {d["device"]: d for d in metrics.latest_disks("10.0.0.1")}
    assert set(latest) == {"sda", "sdb"}
    assert latest["sda"]["temp_c"] == 42        # newest sample wins
    assert latest["sdb"]["temp_c"] == 33
    assert latest["sdb"]["type"] == "ssd"

    # series for sda has both samples in ascending time order
    series = metrics.query_disk_series("10.0.0.1", device="sda", hours=24)
    assert [r["temp_c"] for r in series] == [40, 42]


def test_health_passed_roundtrips_as_int(temp_db, monkeypatch):
    now = int(_time.time())
    monkeypatch.setattr(metrics.time, "time", lambda: now)
    metrics._store_disk_metrics("h", [
        _disk("sda", 40, health_passed=True),
        _disk("sdb", 40, health_passed=False),
        _disk("sdc", 40, health_passed=None),
    ])
    latest = {d["device"]: d for d in metrics.latest_disks("h")}
    assert latest["sda"]["health_passed"] == 1
    assert latest["sdb"]["health_passed"] == 0
    assert latest["sdc"]["health_passed"] is None


def test_store_empty_is_noop(temp_db):
    metrics._store_disk_metrics("h", [])
    assert metrics.latest_disks("h") == []
