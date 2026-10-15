"""bashclub-zsync 1.0.19 and the refactor that followed it (2026-10-15).

Three things changed upstream that the tool has an opinion on: the installed
version is now readable (and the old probe never read one), an empty
``source`` -- zsync's local mode -- is a hard error after 1.0.19, and the
internal snapshot engine names its snapshots ``<label>-YYYY-MM-DD-HHMM``.
Every one of these must also hold against a host that still runs 1.0.18.
"""

from app import replication as r
from app import zfs_commands as zc


# --- version -------------------------------------------------------------------

def test_the_probe_asks_the_package_not_the_script():
    # ``bashclub-zsync -v`` is the usage-to-stderr path, not a version flag.
    assert "bashclub-zsync -v" not in r.VERSION_PROBE and "dpkg-query" in r.VERSION_PROBE
    assert "__version__=v" in r.VERSION_PROBE            # manual installs of <= 1.0.18


def test_version_strings_from_every_source_normalise():
    assert r.parse_zsync_version("1.0.19") == "1.0.19"
    assert r.parse_zsync_version("v1.0.18") == "1.0.18"
    assert r.parse_zsync_version("1.0.19-1") == "1.0.19"   # a Debian revision
    assert r.parse_zsync_version("dev") is None
    assert r.parse_zsync_version("") is None


def test_local_mode_verdict_per_version():
    assert r.local_mode_removed("1.0.18") is False
    assert r.local_mode_removed("1.0.19") is False       # the last release that runs it
    assert r.local_mode_removed("1.0.20") is True
    assert r.local_mode_removed("1.1.0") is True
    assert r.local_mode_removed(None) is None             # unknown: warn, do not condemn


def test_installed_version_reads_the_second_line(monkeypatch):
    monkeypatch.setattr(r, "run_command", lambda h, c, **k: {"success": True,
                        "stdout": "/usr/bin/bashclub-zsync\n1.0.19\n"})
    assert r.installed_version({"address": "h"}) == "1.0.19"
    monkeypatch.setattr(r, "run_command", lambda h, c, **k: {"success": True, "stdout": ""})
    assert r.installed_version({"address": "h"}) is None


def test_get_status_carries_version_and_verdict(monkeypatch):
    def run(host, cmd, **kw):
        if cmd == r.VERSION_PROBE:
            return {"success": True, "stdout": "/usr/bin/bashclub-zsync\nv1.0.18\n", "stderr": ""}
        return {"success": True, "stdout": "", "stderr": ""}
    monkeypatch.setattr(r, "run_command", run)
    monkeypatch.setattr(r, "is_pve_host", lambda h: {"is_pve": True, "version": "9.0"})
    st = r.get_status({"address": "h"}, source="root@10.0.0.5")
    assert st["installed"] and st["version"] == "1.0.18"
    assert st["local_mode_removed"] is False


# --- local mode in the listing ----------------------------------------------------

LISTING = (
    "__FILE__ /etc/bashclub/10.0.0.7.conf\ntarget=tank/repl\nsource=root@10.0.0.7\n__END__\n"
    "__FILE__ /etc/bashclub/local.conf\ntarget=tankhdd/repl\nsource=\n__END__\n"
)


def test_list_configs_flags_an_empty_source_as_local_mode(monkeypatch):
    monkeypatch.setattr(r, "run_command", lambda h, c, **k: {"success": True, "stdout": LISTING})
    cfgs = {c["path"]: c for c in r.list_configs({"address": "h"})["configs"]}
    assert cfgs["/etc/bashclub/local.conf"]["local_mode"] is True
    assert cfgs["/etc/bashclub/10.0.0.7.conf"]["local_mode"] is False


def test_import_preview_names_the_reason_an_empty_source_is_refused(monkeypatch):
    monkeypatch.setattr(r, "_read_config_file", lambda h, p: {"exists": True, "values": {
        "source": "", "target": "tankhdd/repl"}, "raw": "source=\n", "config_path": p})
    pv = r.import_config_preview({"address": "h"}, "/etc/bashclub/local.conf")
    assert not pv["success"]
    assert "local mode" in pv["error"] and "1.0.19" in pv["error"]


# --- auto-snapshot names ----------------------------------------------------------

def test_internal_engine_snapshots_count_as_automatic():
    assert zc.is_auto_snapshot_name("zfs-auto-snap_daily-2026-10-15-0025")
    assert zc.is_auto_snapshot_name("autosnap_2026-10-15_00:00:00_daily")
    assert zc.is_auto_snapshot_name("backup-2026-10-15-1420")         # remote-snapshot-manager
    assert zc.is_auto_snapshot_name("zsync_backup-2026-10-15-1420")
    assert not zc.is_auto_snapshot_name("backup-2026-10-15")           # no HHMM: a human
    assert not zc.is_auto_snapshot_name("before-upgrade")
    assert not zc.is_auto_snapshot_name("manual-1760000000")
    assert not zc.is_auto_snapshot_name("")


def test_the_javascript_mirror_uses_the_same_pattern():
    import io, os, re
    js = io.open(os.path.join("app", "static", "js", "app.js"), encoding="utf-8").read()
    m = re.search(r"function isAutoSnapshotName\(name\) \{\s*return (/.+?/)\.test", js)
    assert m, "isAutoSnapshotName missing in app.js"
    js_re = m.group(1)[1:-1]
    assert js_re == zc._AUTO_SNAP_RE.pattern.replace("(?:", "(?:")
    for site in ('startsWith("zfs-auto-snap")',):
        assert site not in js, "an old inline heuristic survived"


def test_the_form_default_follows_upstream():
    import io, os
    js = io.open(os.path.join("app", "static", "js", "app.js"), encoding="utf-8").read()
    assert 'key: "zfs_auto_snapshot_engine"' in js
    line = next(l for l in js.splitlines() if 'key: "zfs_auto_snapshot_engine"' in l)
    assert 'def: "internal"' in line
