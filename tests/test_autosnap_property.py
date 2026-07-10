"""com.sun:auto-snapshot per-dataset control: set true/false, reset to
inherit (zfs inherit), and the bulk value+source map that drives the
Datasets-view control (local vs inherited)."""

import pytest
from app import zfs_commands as z


@pytest.fixture
def cap(monkeypatch):
    calls = []

    def fake_run(host, cmd, timeout=30, cache_ttl=0, **kw):
        calls.append(cmd)
        return {"success": True, "stdout": "", "stderr": ""}

    monkeypatch.setattr(z, "run_command", fake_run)
    monkeypatch.setattr(z, "_invalidate", lambda h: None)
    return calls


HOST = {"address": "10.0.0.1"}


# --- set_auto_snapshot -----------------------------------------------------

def test_set_enable_disable(cap):
    z.set_auto_snapshot(HOST, "rpool/data", enabled=True)
    assert cap[-1] == "zfs set com.sun:auto-snapshot=true rpool/data"
    z.set_auto_snapshot(HOST, "rpool/data", enabled=False)
    assert cap[-1] == "zfs set com.sun:auto-snapshot=false rpool/data"


def test_set_per_label(cap):
    z.set_auto_snapshot(HOST, "rpool/data", enabled=False, label="daily")
    assert cap[-1] == "zfs set com.sun:auto-snapshot:daily=false rpool/data"


# --- inherit_auto_snapshot -------------------------------------------------

def test_inherit_resets_property(cap):
    z.inherit_auto_snapshot(HOST, "rpool/data")
    assert cap[-1] == "zfs inherit com.sun:auto-snapshot rpool/data"


def test_inherit_per_label(cap):
    z.inherit_auto_snapshot(HOST, "rpool/data", label="daily")
    assert cap[-1] == "zfs inherit com.sun:auto-snapshot:daily rpool/data"


def test_inherit_rejects_bad_dataset(cap):
    r = z.inherit_auto_snapshot(HOST, "rpool/data; rm -rf /")
    assert r["success"] is False
    assert cap == []                       # nothing executed


# --- get_autosnap_map ------------------------------------------------------

def test_map_parses_value_and_source(monkeypatch):
    out = "\n".join([
        "rpool\ttrue\tlocal",
        "rpool/data\ttrue\tinherited from rpool",
        "rpool/repl/x\tfalse\tlocal",
        "rpool/scratch\t-\t-",
    ])
    monkeypatch.setattr(z, "run_command",
                        lambda h, c, timeout=30, cache_ttl=0, **k: {"success": True, "stdout": out, "stderr": ""})
    m = z.get_autosnap_map(HOST)
    assert m["rpool"] == {"value": "true", "source": "local"}
    assert m["rpool/data"] == {"value": "true", "source": "inherited from rpool"}
    assert m["rpool/repl/x"] == {"value": "false", "source": "local"}
    assert m["rpool/scratch"] == {"value": "-", "source": "-"}


def test_map_empty_on_failure(monkeypatch):
    monkeypatch.setattr(z, "run_command",
                        lambda h, c, timeout=30, cache_ttl=0, **k: {"success": False, "stdout": "x"})
    assert z.get_autosnap_map(HOST) == {}


# --- parse_default_exclude (cron opt-out vs opt-in mode) -------------------

def test_default_exclude_absent_is_false():
    cron = "*/15 * * * * root zfs-auto-snapshot --quiet --syslog --label=frequent --keep=12 //"
    assert z.parse_default_exclude(cron) is False


def test_default_exclude_long_flag():
    cron = "0 * * * * root zfs-auto-snapshot -q -g --default-exclude --label=hourly --keep=96 //"
    assert z.parse_default_exclude(cron) is True


@pytest.mark.parametrize("flags", ["-e", "-qe", "-qge"])
def test_default_exclude_short_flag(flags):
    cron = f"0 3 * * * root zfs-auto-snapshot {flags} --label=daily --keep=14 //"
    assert z.parse_default_exclude(cron) is True


def test_default_exclude_ignores_comments_and_labels():
    # a --label without 'e' short flag, and a commented-out exclude line
    cron = "\n".join([
        "# 0 0 * * * root zfs-auto-snapshot --default-exclude --label=monthly //",
        "0 0 * * * root zfs-auto-snapshot -q --label=frequent --keep=12 //",
    ])
    assert z.parse_default_exclude(cron) is False


def test_status_reports_mode(monkeypatch):
    def fake(host, cmd, timeout=30, cache_ttl=0, **k):
        if "which zfs-auto-snapshot" in cmd:
            return {"success": True, "stdout": "/usr/sbin/zfs-auto-snapshot\nINSTALLED"}
        return {"success": True, "stdout": "0 3 * * * root zfs-auto-snapshot -e --label=daily --keep=14 //"}
    monkeypatch.setattr(z, "run_command", fake)
    st = z.get_auto_snapshot_status(HOST)
    assert st["default_exclude"] is True
