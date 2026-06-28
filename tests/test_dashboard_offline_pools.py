"""An offline host's last pool sample must not keep counting as ONLINE in the
dashboard tiles -- it gets flagged stale and excluded from every counter."""

from app.analytics import summarize_host_pools


def _pool(health="ONLINE", cap=10, days=None):
    return {"pool": "rpool", "health": health, "cap_pct": cap,
            "forecast_days_until_full": days}


def test_reachable_host_counts_online_pools():
    counts, pools = summarize_host_pools(True, [_pool(), _pool()])
    assert counts["pools_total"] == 2
    assert counts["pools_ok"] == 2
    assert counts["pools_degraded"] == 0
    assert all(p["stale"] is False for p in pools)


def test_offline_host_pools_are_stale_and_uncounted():
    counts, pools = summarize_host_pools(False, [_pool()])
    assert counts == {"pools_total": 0, "pools_ok": 0, "pools_degraded": 0,
                      "pools_capacity_warn": 0, "forecast_pools_critical": 0}
    assert pools[0]["stale"] is True
    # last-known health is preserved for display
    assert pools[0]["health"] == "ONLINE"


def test_unknown_reachability_still_counts():
    # None = not yet classified; we have a recent sample, so keep counting.
    counts, pools = summarize_host_pools(None, [_pool()])
    assert counts["pools_total"] == 1
    assert counts["pools_ok"] == 1
    assert pools[0]["stale"] is False


def test_degraded_pool_counted_when_online_host():
    counts, _ = summarize_host_pools(True, [_pool(health="DEGRADED")])
    assert counts["pools_degraded"] == 1
    assert counts["pools_ok"] == 0


def test_offline_degraded_pool_not_counted_as_degraded():
    counts, pools = summarize_host_pools(False, [_pool(health="DEGRADED")])
    assert counts["pools_degraded"] == 0
    assert pools[0]["stale"] is True


def test_capacity_and_forecast_only_for_live_hosts():
    live, _ = summarize_host_pools(True, [_pool(cap=95, days=10)])
    assert live["pools_capacity_warn"] == 1
    assert live["forecast_pools_critical"] == 1

    dead, _ = summarize_host_pools(False, [_pool(cap=95, days=10)])
    assert dead["pools_capacity_warn"] == 0
    assert dead["forecast_pools_critical"] == 0


def test_empty_pool_list():
    counts, pools = summarize_host_pools(True, [])
    assert counts["pools_total"] == 0
    assert pools == []


def test_does_not_mutate_input_dicts():
    src = _pool()
    summarize_host_pools(False, [src])
    assert "stale" not in src  # annotated copy, original untouched
