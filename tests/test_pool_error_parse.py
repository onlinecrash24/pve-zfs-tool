"""zpool status abbreviates large error counters with SI suffixes (1.2K, 15M).
parse_pool_errors must decode them -- matching only \\d+ silently dropped the
whole pool line, so a pool with high errors was reported as having none, exactly
when the alert matters most."""

from app.metrics import parse_pool_errors, _parse_count


def test_parse_count_plain():
    assert _parse_count("0") == 0
    assert _parse_count("1234") == 1234


def test_parse_count_suffixes():
    assert _parse_count("1.2K") == 1200
    assert _parse_count("15M") == 15_000_000
    assert _parse_count("2G") == 2_000_000_000
    assert _parse_count("1T") == 1_000_000_000_000


def test_parse_count_empty_or_dash():
    assert _parse_count("-") == 0
    assert _parse_count("") == 0
    assert _parse_count(None) == 0


def test_pool_errors_with_suffix():
    status = (
        "  pool: tank\n"
        " state: ONLINE\n"
        "config:\n"
        "\n"
        "\tNAME        STATE     READ WRITE CKSUM\n"
        "\ttank        ONLINE       0     0  1.2K\n"
        "\t  sda       ONLINE       0     0   600\n"
        "\nerrors: No known data errors\n"
    )
    assert parse_pool_errors(status, "tank") == {"read": 0, "write": 0, "cksum": 1200}


def test_pool_errors_plain_still_works():
    assert parse_pool_errors("\ttank        ONLINE       3     0     0\n", "tank") == {
        "read": 3, "write": 0, "cksum": 0}


def test_pool_errors_missing_pool_returns_none():
    assert parse_pool_errors("\ttank        ONLINE   0 0 0\n", "rpool") is None
