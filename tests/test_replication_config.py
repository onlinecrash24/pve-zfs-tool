"""bashclub-zsync config parsing/serialisation and per-source path mapping."""

import pytest
from app import replication as r


# --- _extract_ip / _safe_filename / config_path_for ----------------------

@pytest.mark.parametrize("source,ip", [
    ("root@192.168.1.80", "192.168.1.80"),
    ("192.168.1.80", "192.168.1.80"),
    ("user@host.example", "host.example"),
    ("", ""),
])
def test_extract_ip(source, ip):
    assert r._extract_ip(source) == ip


def test_safe_filename_sanitises():
    assert r._safe_filename("192.168.1.80") == "192.168.1.80"
    assert r._safe_filename("a/b c;d") == "a_b_c_d"


def test_config_path_for_per_source_and_default():
    assert r.config_path_for("root@192.168.1.80") == "/etc/bashclub/192.168.1.80.conf"
    assert r.config_path_for(None) == r.CONFIG_PATH
    assert r.config_path_for("") == r.CONFIG_PATH


# --- parse / serialize ----------------------------------------------------

def test_parse_preserves_values_and_strips_quotes():
    text = '\n'.join([
        "# a comment",
        "target=rpool/repl",
        'source="root@192.168.1.80"',
        "sshport=22",
        "snapshot_filter='hourly|daily'",
        "",
    ])
    parsed = r._parse_config(text)
    vals = parsed["values"]
    assert vals["target"] == "rpool/repl"
    assert vals["source"] == "root@192.168.1.80"
    assert vals["sshport"] == "22"
    assert vals["snapshot_filter"] == "hourly|daily"


def test_serialize_roundtrip_preserves_comments_and_order():
    text = "# keep me\ntarget=old\nsource=root@1.2.3.4\n"
    lines = r._parse_config(text)["lines"]
    out = r._serialize_config({"target": "rpool/repl", "source": "root@1.2.3.4"}, lines)
    assert "# keep me" in out
    # target updated in place, source preserved, comment kept, order intact
    assert 'target="rpool/repl"' in out
    assert out.index("# keep me") < out.index("target=")


def test_serialize_appends_unknown_keys():
    out = r._serialize_config({"target": "rpool/repl", "min_keep": "3"}, existing_lines=None)
    assert 'target="rpool/repl"' in out
    assert 'min_keep="3"' in out


def test_escape_neutralises_shell_specials():
    esc = r._escape('a"b$c`d\\e')
    # each special is backslash-escaped for a double-quoted shell string
    assert '\\"' in esc and "\\$" in esc and "\\`" in esc and "\\\\" in esc


# --- cron validation + marker --------------------------------------------

@pytest.mark.parametrize("expr", [
    "20 0-22 * * *",
    "*/15 * * * *",
    "0 3 * * 1",
    "0 */6 * * *",
])
def test_validate_cron_accepts_valid(expr):
    assert r._validate_cron(expr) is True


@pytest.mark.parametrize("expr", [
    "20 0-22 * *",          # only 4 fields
    "20 0-22 * * * extra",  # 6 fields
    "20 0-22 * * mon",      # letters
    "rm -rf /",
    "",
])
def test_validate_cron_rejects_invalid(expr):
    assert r._validate_cron(expr) is False


def test_cron_marker_is_config_specific():
    m = r._cron_marker("/etc/bashclub/192.168.1.80.conf")
    assert "bashclub-zsync -c /etc/bashclub/192.168.1.80.conf" == m


# --- install script: APT suite must be derived, not hardcoded -------------

def test_install_script_derives_suite_from_os_release():
    s = r._build_install_script()
    # suite comes from the host, validated against bashclub's published dists
    assert "VERSION_CODENAME" in s
    assert "dists/${SUITE}/Release" in s
    assert "Suites: $SUITE" in s
    # no lone hardcoded suite line (bookworm only appears as the fallback value)
    assert "Suites: bookworm" not in s
    assert 'SUITE="${VERSION_CODENAME:-bookworm}"' in s


def test_install_script_still_installs_zsync_from_bashclub_repo():
    s = r._build_install_script()
    assert "https://apt.bashclub.org/release/" in s
    assert "apt-get install -y bashclub-zsync" in s
    assert "bashclub-archive-keyring.gpg" in s


def test_install_script_apt_update_is_non_fatal():
    # a broken foreign repo must not abort before zsync is installed
    s = r._build_install_script()
    assert "apt-get update -qq || true" in s
