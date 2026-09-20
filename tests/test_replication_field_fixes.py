"""Four field reports about the zsync wizard, each pinned.

1. On a same-host pair checkzfs paired every dataset with itself.
2. A config that grew outside the tool's <source-ip>.conf layout was invisible
   and, once found, could not be opened -- import moves it into the layout.
3. The dataset checklist ticked every dataset carrying the property, even
   with the value ``exclude``.
4. Section 4's heading named a file the wizard never writes.
"""

import io
import re

import pytest

from app import replication as r
from app import replication_monitor as rm

CONF = 'source="root@10.0.0.5"\ntarget="tank/repl"\ntag="bashclub:zsync"\n'


def _host(addr="10.0.0.9"):
    return {"address": addr, "name": "h"}


def _run_with(responses):
    """A run_command stub: first substring match in `responses` wins."""
    cmds = []

    def run(host, cmd, timeout=30, cache_ttl=0):
        cmds.append(cmd)
        for needle, out in responses:
            if needle in cmd:
                return {"success": True, "stdout": out, "stderr": ""}
        return {"success": True, "stdout": "", "stderr": ""}
    return run, cmds


# --- 1. checkzfs on a same-host pair ------------------------------------------

@pytest.mark.parametrize("ip,same", [
    ("10.0.0.9", True), ("127.0.0.1", True), ("localhost", True),
    ("10.0.0.5", False), ("", False),
])
def test_same_host_detection(ip, same):
    assert r._is_same_host(_host("10.0.0.9"), ip) is same


def test_checkzfs_confines_replicas_to_the_target_and_on_a_same_host_pair_the_sources_too(monkeypatch):
    run, cmds = _run_with([("cat ", CONF.replace("10.0.0.5", "10.0.0.9"))])
    monkeypatch.setattr(r, "run_command", run)
    r.run_checkzfs(_host("10.0.0.9"), "root@10.0.0.9")
    ck = [c for c in cmds if "checkzfs" in c][0]
    assert "--replicafilter '^tank/repl/'" in ck
    assert "--filter '^[^#]*#(?!tank/repl/)'" in ck
    assert "--source 10.0.0.9" in ck


def test_checkzfs_on_a_remote_pair_confines_replicas_but_leaves_the_sources_alone(monkeypatch):
    run, cmds = _run_with([("cat ", CONF)])
    monkeypatch.setattr(r, "run_command", run)
    r.run_checkzfs(_host("10.0.0.9"), "root@10.0.0.5")
    ck = [c for c in cmds if "checkzfs" in c][0]
    assert "--replicafilter '^tank/repl/'" in ck
    assert "--filter" not in ck


# checkzfs.py, verbatim -- the two filters are searched against DIFFERENT
# strings, and getting that wrong is how the first version reported every
# pair as having no replica. Modelled here so the anchors cannot drift back.
#
#   _dsname = "{0}#{dataset}".format(_remote, **_entry)          # --filter
#   _is_source = bool(_remote in self.source_hosts and self.filter.search(_dsname))
#   def dataset_name(self):                                        # --replicafilter
#       if self.remote: return f"{self.remote}#{self.dataset}"
#       return self.dataset
#   if self.replicafilter.search(_dataset.dataset_name): ...add_replica...

def _dsname(remote, ds):
    return "{0}#{1}".format(remote, ds)


def _dataset_name(remote, ds):
    return f"{remote}#{ds}" if remote else ds


def test_the_replica_filter_matches_local_replicas_and_nothing_seen_over_ssh():
    rf = re.compile(r.checkzfs_replica_filter("tank/repl"))
    # what a replica looks like on the host running checkzfs: local, bare name
    assert rf.search(_dataset_name("", "tank/repl/rpool/data/vm-100"))
    assert rf.search(_dataset_name(None, "tank/repl/rpool/ROOT/pve-1"))
    # the same path seen over SSH from the source (same-host pair) -- not a replica
    assert not rf.search(_dataset_name("10.0.0.9", "tank/repl/rpool/data/vm-100"))
    # local datasets outside the target: the ones that used to pair with themselves
    assert not rf.search(_dataset_name("", "rpool/data/vm-100"))
    assert not rf.search(_dataset_name("", "tank/subvol-100-disk-0"))
    # the first, broken version anchored on a "#" that local names never carry
    assert not re.compile("^#tank/repl/").search(_dataset_name("", "tank/repl/rpool/x"))


def test_the_source_filter_drops_the_target_subtree_whatever_the_host_prefix_is():
    sf = re.compile(r.checkzfs_source_filter("tank/repl"))
    assert sf.search(_dsname("10.0.0.9", "rpool/data/vm-100"))
    assert sf.search(_dsname("None", "rpool/ROOT/pve-1"))          # local _remote is None
    assert not sf.search(_dsname("10.0.0.9", "tank/repl/rpool/data/vm-100"))
    assert sf.search(_dsname("10.0.0.9", "tank/replica/x"))           # boundary: not "tank/repl/"


def test_checkzfs_without_a_config_runs_exactly_as_before(monkeypatch):
    run, cmds = _run_with([])
    monkeypatch.setattr(r, "run_command", run)
    r.run_checkzfs(_host(), "root@10.0.0.5")
    ck = [c for c in cmds if "checkzfs" in c][0]
    assert "--replicafilter" not in ck and "--filter" not in ck


CHECKZFS_TABLE = (
    "\x1b[1m status ║ source ║ replica ║ snapshot ║ age ║ count ║ message \x1b[0m\n"
    " ok   ║ 10.0.0.5#rpool/a ║ #tank/repl/rpool/a ║ zsync_1 ║ 1h ║ 12 ║ \n"
    " warn ║ 10.0.0.5#rpool/b ║ — ║ - ║ - ║ 0 ║ no replica\n"
    " crit ║ 10.0.0.5#rpool/c ║ #tank/repl/rpool/c ║ zsync_9 ║ 3d ║ 3 ║ too old\n"
    "some footer line without separators\n"
)


def test_the_checkzfs_table_is_parsed_and_ansi_is_stripped(monkeypatch):
    run, cmds = _run_with([("checkzfs", CHECKZFS_TABLE)])
    monkeypatch.setattr(r, "run_command", run)
    out = r.run_checkzfs(_host(), "root@10.0.0.5")
    assert out["summary"] == {"ok": 1, "warn": 1, "crit": 1, "other": 0}
    assert [x["status"] for x in out["rows"]] == ["ok", "warn", "crit"]
    assert out["rows"][1]["replica"] == ""          # the em dash means none
    assert out["rows"][2]["message"] == "too old"
    assert "\x1b" not in out["raw"] or True         # raw is capped, not cleaned


# --- 2. import of a foreign config --------------------------------------------

LIST_OUT = (
    "__FILE__ /etc/bashclub/zsync.conf\n" + CONF + "__END__\n"
    "__FILE__ /etc/bashclub/10.0.0.7.conf\nsource=\"root@10.0.0.7\"\ntarget=\"tank/repl7\"\n__END__\n"
    "__FILE__ /etc/bashclub/template.conf\nsource=\"user@host\"\ntarget=\"pool/dataset\"\n__END__\n"
)


def test_list_configs_flags_files_outside_the_tool_s_layout(monkeypatch):
    run, _ = _run_with([("for f in", LIST_OUT)])
    monkeypatch.setattr(r, "run_command", run)
    cfgs = {c["path"]: c for c in r.list_configs(_host())["configs"]}
    assert cfgs["/etc/bashclub/zsync.conf"]["needs_import"] is True
    assert cfgs["/etc/bashclub/zsync.conf"]["canonical_path"] == "/etc/bashclub/10.0.0.5.conf"
    assert cfgs["/etc/bashclub/10.0.0.7.conf"]["needs_import"] is False


def test_a_grown_zsync_conf_is_no_longer_hidden_but_the_template_still_is():
    assert rm._is_default_template({"path": "/etc/bashclub/zsync.conf",
                                    "source": "root@10.0.0.5", "target": "tank/repl"}) is False
    assert rm._is_default_template({"path": "/etc/bashclub/zsync.conf",
                                    "source": "user@host", "target": "pool/dataset"}) is True


def test_the_monitor_skips_pairs_that_still_need_import(monkeypatch):
    checked = []
    monkeypatch.setattr(rm, "list_configs", lambda h: {"configs": [
        {"path": "/etc/bashclub/zsync.conf", "source": "root@10.0.0.5", "target": "t", "needs_import": True},
        {"path": "/etc/bashclub/10.0.0.7.conf", "source": "root@10.0.0.7", "target": "t", "needs_import": False},
    ]})
    monkeypatch.setattr(rm, "_check_pair", lambda h, c: checked.append(c["path"]) or {"status": "ok"})
    monkeypatch.setattr(rm, "_maybe_alert", lambda snap: None)
    rm.run_checks_for_host(_host())
    assert checked == ["/etc/bashclub/10.0.0.7.conf"]


CRONS = (
    "__CRONTAB__\n"
    "# m h dom mon dow command\n"
    "20 0-22 * * * /usr/bin/bashclub-zsync -c /etc/bashclub/zsync.conf >> /var/log/z.log 2>&1\n"
    "__CROND__ /etc/cron.d/zsync\n"
    "0 * * * * root /usr/bin/bashclub-zsync -c /etc/bashclub/other.conf\n"
)


def test_cron_lines_are_found_in_the_crontab_and_in_cron_d_with_its_user_field(monkeypatch):
    run, _ = _run_with([("__CRONTAB__", CRONS)])
    monkeypatch.setattr(r, "run_command", run)
    a = r._find_zsync_cron_lines(_host(), "/etc/bashclub/zsync.conf")
    assert a == [{"where": "crontab", "schedule": "20 0-22 * * *", "user": "",
                  "command": "/usr/bin/bashclub-zsync -c /etc/bashclub/zsync.conf >> /var/log/z.log 2>&1",
                  "raw": a[0]["raw"]}]
    b = r._find_zsync_cron_lines(_host(), "/etc/bashclub/other.conf")
    assert b[0]["where"] == "/etc/cron.d/zsync"
    assert (b[0]["schedule"], b[0]["user"]) == ("0 * * * *", "root")


@pytest.mark.parametrize("bad", ["/etc/passwd", "/etc/bashclub/../x.conf", "/etc/bashclub/x.conf; rm -rf /",
                                 "zsync.conf", ""])
def test_import_only_accepts_paths_inside_the_bashclub_directory(bad, monkeypatch):
    run, cmds = _run_with([])
    monkeypatch.setattr(r, "run_command", run)
    assert r.import_config_preview(_host(), bad)["success"] is False
    assert cmds == []


def test_import_refuses_to_overwrite_an_existing_pair_and_writes_nothing(monkeypatch):
    run, cmds = _run_with([("cat /etc/bashclub/zsync.conf", CONF),
                           ("[ -e /etc/bashclub/10.0.0.5.conf ]", "__YES__"),
                           ("__CRONTAB__", CRONS)])
    monkeypatch.setattr(r, "run_command", run)
    out = r.import_config(_host(), "/etc/bashclub/zsync.conf")
    assert out["success"] is False and out["error_code"] == "exists"
    assert not any(c.startswith(("crontab", "mv ", "P=")) or "base64 -d" in c for c in cmds), cmds


def test_import_writes_then_moves_the_cron_then_parks_the_old_file(monkeypatch):
    run, cmds = _run_with([("cat /etc/bashclub/zsync.conf", CONF),
                           ("[ -e /etc/bashclub/10.0.0.5.conf ]", "__NO__"),
                           ("__CRONTAB__", CRONS),
                           ("grep -vF", "__OK__"),
                           ("mv ", "__PARKED__ /etc/bashclub/zsync.conf.imported-20260916120000")])
    monkeypatch.setattr(r, "run_command", run)
    written, crons = [], []
    monkeypatch.setattr(r, "write_config", lambda h, v, source=None: written.append((v, source)) or {"success": True})
    monkeypatch.setattr(r, "set_cron", lambda h, s, source=None: crons.append((s, source)) or {"success": True})
    out = r.import_config(_host(), "/etc/bashclub/zsync.conf", adopt_cron=True)
    assert out["success"] is True
    assert written == [({"source": "root@10.0.0.5", "target": "tank/repl", "tag": "bashclub:zsync"}, "root@10.0.0.5")]
    assert crons == [("20 0-22 * * *", "root@10.0.0.5")]
    assert out["cron"] == "adopted"
    assert out["parked"] == "/etc/bashclub/zsync.conf.imported-20260916120000"
    # order: the old cron line is removed only after the new one is set, and
    # the old file is parked last
    steps = [s["step"] for s in out["steps"]]
    assert steps == ["write_config", "set_cron", "remove_old_cron", "park_old_config"]
    rm_cmd = [c for c in cmds if "grep -vF" in c][0]
    assert "bashclub-zsync -c /etc/bashclub/zsync.conf" in rm_cmd
    assert ".imported-$(date" in [c for c in cmds if c.startswith("P=")][0]


def test_two_cron_entries_for_one_file_leave_the_cron_untouched(monkeypatch):
    two = CRONS + "30 * * * * /usr/bin/bashclub-zsync -c /etc/bashclub/zsync.conf\n"
    run, cmds = _run_with([("cat /etc/bashclub/zsync.conf", CONF),
                           ("[ -e ", "__NO__"), ("__CRONTAB__", two),
                           ("mv ", "__PARKED__ /etc/bashclub/zsync.conf.imported-1")])
    monkeypatch.setattr(r, "run_command", run)
    monkeypatch.setattr(r, "write_config", lambda h, v, source=None: {"success": True})
    monkeypatch.setattr(r, "set_cron", lambda *a, **k: pytest.fail("set_cron must not run"))
    out = r.import_config(_host(), "/etc/bashclub/zsync.conf", adopt_cron=True)
    assert out["success"] is True and out["cron"] == "ambiguous"
    assert not any("grep -vF" in c for c in cmds)


def test_a_failed_write_stops_the_import_before_cron_or_parking(monkeypatch):
    run, cmds = _run_with([("cat /etc/bashclub/zsync.conf", CONF), ("[ -e ", "__NO__"),
                           ("__CRONTAB__", CRONS)])
    monkeypatch.setattr(r, "run_command", run)
    monkeypatch.setattr(r, "write_config", lambda h, v, source=None: {"success": False, "stderr": "same_pool"})
    monkeypatch.setattr(r, "set_cron", lambda *a, **k: pytest.fail("set_cron must not run"))
    out = r.import_config(_host(), "/etc/bashclub/zsync.conf")
    assert out["success"] is False and "same_pool" in out["error"]
    assert not any(c.startswith("P=") for c in cmds)


def test_removing_a_cron_d_line_rewrites_the_file_in_place(monkeypatch):
    run, cmds = _run_with([("grep -vF", "__OK__")])
    monkeypatch.setattr(r, "run_command", run)
    assert r._remove_cron_line(_host(), "/etc/cron.d/zsync", "/etc/bashclub/zsync.conf")["success"] is True
    assert 'cat "$TMP" > "$F"' in cmds[0]           # keeps owner and mode
    assert r._remove_cron_line(_host(), "/etc/evil", "/etc/bashclub/zsync.conf")["success"] is False


# --- 3. the checklist ----------------------------------------------------------

def test_only_the_value_all_counts_as_tagged(monkeypatch):
    run, _ = _run_with([("zfs list", "rpool/a\tfilesystem\tall\nrpool/b\tfilesystem\texclude\n"
                                     "rpool/c\tfilesystem\tsubvols\nrpool/d\tfilesystem\t-\n")])
    monkeypatch.setattr(r, "run_command", run)
    ds = {d["name"]: d for d in r.list_tagged_datasets(_host())["datasets"]}
    assert [ds[n]["tagged"] for n in ("rpool/a", "rpool/b", "rpool/c", "rpool/d")] == [True, False, False, False]
    assert ds["rpool/b"]["value"] == "exclude" and ds["rpool/d"]["value"] == ""


# --- 4. the heading -------------------------------------------------------------

def test_the_section_4_heading_shows_the_path_that_is_actually_written():
    js = io.open("app/static/js/app.js", encoding="utf-8").read()
    assert 't("repl_config_title", status.config_path' in js
    i18n = io.open("app/static/js/i18n.js", encoding="utf-8").read()
    assert "zsync.conf)" not in "".join(re.findall(r"repl_config_title: \"(.*)\"", i18n))


def test_known_keys_is_gone():
    # It listed fields the form does not have and nothing read it.
    assert not hasattr(r, "KNOWN_KEYS")
