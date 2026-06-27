"""Prometheus text-exposition formatting helpers."""

from app import analytics as a


def test_prom_escape():
    assert a._prom_escape('a"b') == 'a\\"b'
    assert a._prom_escape("a\\b") == "a\\\\b"
    assert a._prom_escape("a\nb") == "a\\nb"
    assert a._prom_escape(None) == ""


def test_prom_line_no_labels():
    assert a._prom_line("pvezfs_pools_total", {}, 3) == "pvezfs_pools_total 3"


def test_prom_line_with_labels():
    line = a._prom_line("pvezfs_pool_capacity_percent",
                        {"host": "pve1", "pool": "rpool"}, 49)
    assert line.startswith("pvezfs_pool_capacity_percent{")
    assert 'host="pve1"' in line
    assert 'pool="rpool"' in line
    assert line.endswith("} 49")


def test_prom_line_escapes_label_values():
    line = a._prom_line("m", {"k": 'a"b'}, 1)
    assert 'k="a\\"b"' in line
