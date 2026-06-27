"""Dashboard stale-snapshot-label breakdown (clickable Home tile data)."""

from app.analytics import build_stale_detail


def test_parses_key_and_count_and_name():
    state = {
        "192.168.1.80:hourly": {"value": '{"count": 3}', "updated_ts": 100},
    }
    names = {"192.168.1.80": "pve-nas"}
    out = build_stale_detail(state, names)
    assert out == [{
        "host_address": "192.168.1.80",
        "host_name": "pve-nas",
        "label": "hourly",
        "count": 3,
        "updated_ts": 100,
    }]


def test_sorted_newest_first_then_host_label():
    state = {
        "10.0.0.2:daily":  {"value": '{"count": 1}', "updated_ts": 50},
        "10.0.0.1:hourly": {"value": '{"count": 2}', "updated_ts": 200},
        "10.0.0.1:daily":  {"value": '{"count": 5}', "updated_ts": 200},
    }
    out = build_stale_detail(state, {})
    # updated_ts 200 entries first; within same ts sorted by host then label
    assert [(d["host_address"], d["label"]) for d in out] == [
        ("10.0.0.1", "daily"),
        ("10.0.0.1", "hourly"),
        ("10.0.0.2", "daily"),
    ]


def test_unknown_host_falls_back_to_address():
    state = {"1.2.3.4:weekly": {"value": "{}", "updated_ts": 1}}
    out = build_stale_detail(state, {})
    assert out[0]["host_name"] == "1.2.3.4"
    assert out[0]["count"] is None


def test_malformed_value_does_not_crash():
    state = {"1.2.3.4:monthly": {"value": "not json", "updated_ts": 1}}
    out = build_stale_detail(state, {})
    assert out[0]["count"] is None
    assert out[0]["label"] == "monthly"


def test_empty_state():
    assert build_stale_detail({}, {}) == []
    assert build_stale_detail(None, None) == []
