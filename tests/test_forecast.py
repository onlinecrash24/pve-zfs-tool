"""Capacity forecast.

The old version fit least squares over a 30-day window. At 15-minute sampling
that is ~2880 points, so a new sample moved the slope by ~0.03 % and the
projection never visibly reacted -- while still being skewed by the sawtooth
that auto-snapshots create (allocation jumps up, pruning drops it again).
Now: hourly medians (removes the sawtooth), a median-of-slopes fit (ignores the
remaining outliers), and a short recent window that widens only when data is
thin.
"""

import time

from app import analytics as an


# --- hourly medians --------------------------------------------------------

def test_bucket_medians_collapse_an_hour():
    base = 3600 * 100
    pts = [(base + 0, 10), (base + 900, 90), (base + 1800, 20), (base + 2700, 30)]
    out = an.bucket_medians(pts)
    assert len(out) == 1
    assert out[0][0] == base
    assert out[0][1] == 25.0          # median of 10,20,30,90 -- not the 90 spike


def test_bucket_medians_keep_hours_apart_and_sorted():
    out = an.bucket_medians([(7200, 5), (3600, 1), (7200, 7)])
    assert [t for t, _ in out] == [3600, 7200]
    assert out[1][1] == 6.0


def test_bucket_medians_empty():
    assert an.bucket_medians([]) == []
    assert an.bucket_medians(None) == []


# --- robust slope ----------------------------------------------------------

def test_theil_sen_matches_a_clean_line():
    pts = [(0, 0), (1, 100), (2, 200), (3, 300)]
    assert an.theil_sen_slope(pts) == 100.0


def test_theil_sen_ignores_an_outlier():
    # one snapshot burst must not bend the trend
    pts = [(0, 0), (1, 100), (2, 200), (3, 99999), (4, 400)]
    assert an.theil_sen_slope(pts) == 100.0


def test_theil_sen_needs_two_points():
    assert an.theil_sen_slope([(0, 1)]) is None
    assert an.theil_sen_slope([]) is None


def test_theil_sen_detects_shrinking():
    assert an.theil_sen_slope([(0, 500), (1, 400), (2, 300)]) == -100.0


# --- end to end against a fake series --------------------------------------

def _series(monkeypatch, hours, per_hour, size, start=0):
    """A pool growing by per_hour bytes, sampled every 15 minutes."""
    now = int(time.time())
    rows = []
    for i in range(hours * 4):
        ts = now - (hours * 4 - i) * 900
        rows.append({"timestamp": ts,
                     "alloc_bytes": start + per_hour * (i / 4.0),
                     "size_bytes": size})
    monkeypatch.setattr(an, "_fetch_pool_series", lambda h, p, w: rows)
    return rows


def test_forecast_projects_a_growing_pool(monkeypatch):
    # 1 GB/h; after 48 h of samples ~808 GB of 1000 are used, so ~192 GB free
    # at 24 GB/day -> ~8 days
    gb = 1024 ** 3
    _series(monkeypatch, hours=48, per_hour=gb, size=1000 * gb, start=760 * gb)
    res = an.forecast_detail("h", "tank")
    assert res["reason"] == "ok"
    assert 7.5 < res["days"] < 8.5
    assert abs(res["bytes_per_day"] - 24 * gb) < gb    # ~24 GB/day


def test_forecast_reports_no_growth(monkeypatch):
    gb = 1024 ** 3
    _series(monkeypatch, hours=48, per_hour=0, size=1000 * gb, start=100 * gb)
    res = an.forecast_detail("h", "tank")
    assert res["days"] is None
    assert res["reason"] == "no_growth"


def test_forecast_reports_a_full_pool(monkeypatch):
    gb = 1024 ** 3
    _series(monkeypatch, hours=48, per_hour=gb, size=100 * gb, start=200 * gb)
    res = an.forecast_detail("h", "tank")
    assert res["days"] == 0
    assert res["reason"] == "full"


def test_forecast_without_data(monkeypatch):
    monkeypatch.setattr(an, "_fetch_pool_series", lambda h, p, w: [])
    res = an.forecast_detail("h", "tank")
    assert res["days"] is None and res["reason"] == "no_data"


def test_forecast_widens_the_window_when_recent_data_is_thin(monkeypatch):
    gb = 1024 ** 3
    now = int(time.time())
    windows = []

    def fake_fetch(host, pool, window_days):
        windows.append(window_days)
        if window_days == an.FORECAST_PRIMARY_DAYS:
            return []                      # nothing recent
        return [{"timestamp": now - (20 - i) * 86400,
                 "alloc_bytes": (100 + i) * gb, "size_bytes": 1000 * gb}
                for i in range(20)]

    monkeypatch.setattr(an, "_fetch_pool_series", fake_fetch)
    res = an.forecast_detail("h", "tank")
    assert windows == [an.FORECAST_PRIMARY_DAYS, an.FORECAST_WINDOW_DAYS]
    assert res["window_days"] == an.FORECAST_WINDOW_DAYS


def test_legacy_wrapper_still_returns_days(monkeypatch):
    gb = 1024 ** 3
    _series(monkeypatch, hours=48, per_hour=gb, size=1000 * gb, start=760 * gb)
    assert an.forecast_days_until_full("h", "tank") == an.forecast_detail("h", "tank")["days"]
