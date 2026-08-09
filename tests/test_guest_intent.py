"""Declared exceptions: guests deliberately left without a backup or a replica.

The declaration is a PVE guest tag, so it is visible where the guest is managed
and travels with it. The tag decides; the reason stored here only annotates.

The first test is the one that matters most: PVE copies a guest's whole config,
tags included, into a [snapname] block for every snapshot. Reading tags without
stopping at that boundary returns a snapshot's stale tags as the present state
-- and would silently excuse a guest nobody ever meant to excuse.
"""

import json

import pytest

from app import guest_intent as gi


# --- reading tags out of guest configs ------------------------------------

def test_snapshot_sections_do_not_leak_their_tags():
    # What the awk in _READ_TAGS_CMD produces: one line per file, from the
    # CURRENT config only. If that boundary is ever lost, this is the failure.
    out = gi.parse_tag_lines("/etc/pve/qemu-server/100.conf\ttags: prod;web\n")
    assert out == {"100": ["prod", "web"]}


def test_the_reading_command_stops_at_the_first_section():
    # The guard itself: a plain grep would return the snapshot's tags too.
    assert "/^\\[/{exit}" in gi._READ_TAGS_CMD
    assert "^tags:" in gi._READ_TAGS_CMD


def test_lxc_and_qemu_paths_both_yield_their_vmid():
    out = gi.parse_tag_lines(
        "/etc/pve/qemu-server/100.conf\ttags: a\n"
        "/etc/pve/lxc/253.conf\ttags: b\n")
    assert out == {"100": ["a"], "253": ["b"]}


def test_comma_separated_tags_are_accepted():
    # PVE stores ';' but accepts ',' on input, so both turn up in the wild.
    assert gi.parse_tag_lines("/etc/pve/lxc/1.conf\ttags: a,b;c\n") == {"1": ["a", "b", "c"]}


def test_lines_without_a_vmid_or_tags_are_ignored():
    assert gi.parse_tag_lines("/etc/pve/lxc/notanumber.conf\ttags: a\n") == {}
    assert gi.parse_tag_lines("/etc/pve/lxc/1.conf\tcores: 2\n") == {}
    assert gi.parse_tag_lines("") == {}
    assert gi.parse_tag_lines("no tab here") == {}


def test_an_empty_tag_list_is_no_tags():
    assert gi.parse_tag_lines("/etc/pve/lxc/1.conf\ttags: \n") == {"1": []}


# --- turning tags into declarations ---------------------------------------

def test_the_two_tags_are_recognised():
    got = gi.exceptions_from_tags({"100": ["prod", "no-backup"],
                                   "101": ["no-replication"],
                                   "102": ["no-backup", "no-replication"]})
    assert got["100"] == {"no_backup": True, "no_replication": False}
    assert got["101"] == {"no_backup": False, "no_replication": True}
    assert got["102"] == {"no_backup": True, "no_replication": True}


def test_guests_without_our_tags_declare_nothing():
    assert gi.exceptions_from_tags({"100": ["prod", "web"]}) == {}


def test_tag_matching_ignores_case():
    got = gi.exceptions_from_tags({"100": ["No-Backup"]})
    assert got["100"]["no_backup"] is True


def test_tag_names_are_overridable():
    names = gi.tag_names({"exception_tags": {"backup": "kein-backup"}})
    assert names["backup"] == "kein-backup"
    assert names["replication"] == "no-replication"      # untouched default
    got = gi.exceptions_from_tags({"100": ["kein-backup"]}, names)
    assert got["100"]["no_backup"] is True


def test_an_invalid_override_falls_back_to_the_default():
    # PVE only accepts [A-Za-z0-9_][A-Za-z0-9_\-+.]* -- a colon would be
    # rejected by PVE itself, so honouring it would produce a tag nobody can set.
    assert gi.tag_names({"exception_tags": {"backup": "pvezfs:no-backup"}})["backup"] \
        == gi.TAG_NO_BACKUP
    assert gi.tag_names({"exception_tags": {"backup": "  "}})["backup"] == gi.TAG_NO_BACKUP


# --- writing tags back ----------------------------------------------------

def test_apply_tag_keeps_the_users_own_tags():
    # The trap: `qm set --tags` replaces the WHOLE list, so writing only our
    # tag would delete everything the user had.
    assert gi.apply_tag(["prod", "web"], "no-backup", True) == ["prod", "web", "no-backup"]


def test_apply_tag_is_idempotent():
    once = gi.apply_tag(["prod"], "no-backup", True)
    assert gi.apply_tag(once, "no-backup", True) == once


def test_apply_tag_removes_and_keeps_the_rest():
    assert gi.apply_tag(["prod", "no-backup", "web"], "no-backup", False) == ["prod", "web"]


def test_removing_a_tag_that_is_not_there_changes_nothing():
    assert gi.apply_tag(["prod"], "no-backup", False) == ["prod"]


def test_apply_tag_removal_ignores_case():
    assert gi.apply_tag(["prod", "No-Backup"], "no-backup", False) == ["prod"]


def test_parse_current_tags_stops_at_the_snapshot_section():
    cfg = ("cores: 2\n"
           "tags: prod;web\n"
           "[before-upgrade]\n"
           "tags: old-tag\n")
    assert gi.parse_current_tags(cfg) == ["prod", "web"]


def test_parse_current_tags_without_any():
    assert gi.parse_current_tags("cores: 2\nmemory: 2048\n") == []


# --- reasons: the tag decides, the note annotates -------------------------

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(gi, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(gi, "EXCEPTIONS_FILE", str(tmp_path / "guest_exceptions.json"))
    return tmp_path


def test_a_reason_round_trips(store):
    gi.save_reason("h1", "100", ["backup"], "Testcontainer, wird neu gebaut", "admin")
    got = gi.load_reasons("h1")["100"]
    assert got["reason"] == "Testcontainer, wird neu gebaut"
    assert got["by"] == "admin"
    assert got["kinds"] == ["backup"]
    assert got["at"] > 0


def test_reasons_are_scoped_per_host(store):
    gi.save_reason("h1", "100", ["backup"], "a")
    gi.save_reason("h2", "100", ["replication"], "b")
    assert gi.load_reasons("h1")["100"]["reason"] == "a"
    assert gi.load_reasons("h2")["100"]["reason"] == "b"


def test_saving_no_kinds_removes_the_note(store):
    gi.save_reason("h1", "100", ["backup"], "a")
    gi.save_reason("h1", "100", [], "")
    assert gi.load_reasons("h1") == {}


def test_dropping_reports_whether_anything_was_there(store):
    assert gi.drop_reason("h1", "100") is False
    gi.save_reason("h1", "100", ["backup"], "a")
    assert gi.drop_reason("h1", "100") is True


def test_unknown_kinds_are_discarded(store):
    gi.save_reason("h1", "100", ["backup", "nonsense"], "a")
    assert gi.load_reasons("h1")["100"]["kinds"] == ["backup"]


# --- collection: tag decides, note annotates ------------------------------

def _tags_ok(stdout):
    return lambda h, c, timeout=0, cache_ttl=0: {"success": True, "stdout": stdout,
                                                 "stderr": ""}


def test_collect_marks_an_undocumented_tag_as_such(store, monkeypatch):
    # Somebody tagged the guest in PVE without going through this tool. It
    # still counts -- the tag decides -- but it is flagged as unjustified so a
    # foreign or accidental tag surfaces instead of quietly hiding a real gap.
    monkeypatch.setattr(gi, "run_command",
                        _tags_ok("/etc/pve/lxc/100.conf\ttags: no-backup\n"))
    got = gi.collect_exceptions({"address": "h1"})
    assert got["readable"] is True
    assert got["exceptions"]["100"]["no_backup"] is True
    assert got["exceptions"]["100"]["documented"] is False


def test_collect_attaches_a_recorded_reason(store, monkeypatch):
    gi.save_reason("h1", "100", ["backup"], "Wegwerf-VM", "admin")
    monkeypatch.setattr(gi, "run_command",
                        _tags_ok("/etc/pve/lxc/100.conf\ttags: no-backup\n"))
    got = gi.collect_exceptions({"address": "h1"})["exceptions"]["100"]
    assert got["documented"] is True
    assert got["reason"] == "Wegwerf-VM"


def test_a_note_without_a_tag_is_orphaned_and_ignored(store, monkeypatch):
    # Removing the tag in PVE has to end the exception at once, whatever this
    # tool still has on file.
    gi.save_reason("h1", "100", ["backup"], "stale note")
    monkeypatch.setattr(gi, "run_command",
                        _tags_ok("/etc/pve/lxc/100.conf\ttags: prod\n"))
    assert gi.collect_exceptions({"address": "h1"})["exceptions"] == {}


def test_an_unreadable_host_declares_nothing(store, monkeypatch):
    # The safe direction: a failed read must not excuse anybody.
    monkeypatch.setattr(gi, "run_command",
                        lambda h, c, timeout=0, cache_ttl=0: {
                            "success": False, "stdout": "", "stderr": "ssh gone"})
    got = gi.collect_exceptions({"address": "h1"})
    assert got["exceptions"] == {}
    assert got["readable"] is False
    assert "ssh gone" in got["error"]


# --- writing through set_exception ----------------------------------------

def _router(responses):
    def run(host, command, timeout=30, cache_ttl=0):
        for needle, result in responses.items():
            if needle in command:
                return result
        return {"success": False, "stdout": "", "stderr": "unexpected: " + command}
    return run


def test_set_exception_preserves_foreign_tags(store, monkeypatch):
    sent = {}

    def run(host, command, timeout=30, cache_ttl=0):
        if "qemu-server/100.conf ]" in command:
            return {"success": True, "stdout": "qemu\n", "stderr": ""}
        if command.startswith("cat /etc/pve/qemu-server/100.conf"):
            return {"success": True, "stdout": "cores: 2\ntags: prod;web\n", "stderr": ""}
        if command.startswith("qm set"):
            sent["cmd"] = command
            return {"success": True, "stdout": "", "stderr": ""}
        return {"success": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(gi, "run_command", run)
    res = gi.set_exception({"address": "h1"}, "100", ["backup"], "weil", "admin")
    assert res["success"] is True
    assert res["tags"] == ["prod", "web", "no-backup"]
    assert "prod;web;no-backup" in sent["cmd"]
    assert gi.load_reasons("h1")["100"]["reason"] == "weil"


def test_set_exception_withdraws_what_is_not_listed(store, monkeypatch):
    sent = {}

    def run(host, command, timeout=30, cache_ttl=0):
        if "lxc/253.conf ]" in command:
            return {"success": True, "stdout": "lxc\n", "stderr": ""}
        if command.startswith("cat /etc/pve/lxc/253.conf"):
            return {"success": True, "stdout": "tags: no-backup;no-replication\n",
                    "stderr": ""}
        if command.startswith("pct set"):
            sent["cmd"] = command
            return {"success": True, "stdout": "", "stderr": ""}
        return {"success": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(gi, "run_command", run)
    # Only replication stays declared -> the backup tag must come off.
    res = gi.set_exception({"address": "h1"}, "253", ["replication"])
    assert res["tags"] == ["no-replication"]
    assert "pct set" in sent["cmd"]


def test_clearing_every_kind_drops_the_note_too(store, monkeypatch):
    gi.save_reason("h1", "253", ["backup"], "alt")

    def run(host, command, timeout=30, cache_ttl=0):
        if "lxc/253.conf ]" in command:
            return {"success": True, "stdout": "lxc\n", "stderr": ""}
        if command.startswith("cat /etc/pve/lxc/253.conf"):
            return {"success": True, "stdout": "tags: no-backup\n", "stderr": ""}
        if command.startswith("pct set"):
            return {"success": True, "stdout": "", "stderr": ""}
        return {"success": False, "stdout": "", "stderr": "unexpected"}

    monkeypatch.setattr(gi, "run_command", run)
    gi.set_exception({"address": "h1"}, "253", [])
    assert gi.load_reasons("h1") == {}


def test_a_guest_that_does_not_exist_is_refused(store, monkeypatch):
    monkeypatch.setattr(gi, "run_command",
                        lambda h, c, timeout=0, cache_ttl=0: {
                            "success": True, "stdout": "", "stderr": ""})
    res = gi.set_exception({"address": "h1"}, "999", ["backup"])
    assert res["success"] is False
    assert "not found" in res["error"]


def test_a_non_numeric_vmid_is_refused(store):
    assert gi.set_exception({"address": "h1"}, "; rm -rf /", ["backup"])["success"] is False


def test_a_failed_tag_write_does_not_record_a_reason(store, monkeypatch):
    def run(host, command, timeout=30, cache_ttl=0):
        if "lxc/253.conf ]" in command:
            return {"success": True, "stdout": "lxc\n", "stderr": ""}
        if command.startswith("cat "):
            return {"success": True, "stdout": "tags: prod\n", "stderr": ""}
        return {"success": False, "stdout": "", "stderr": "permission denied"}

    monkeypatch.setattr(gi, "run_command", run)
    res = gi.set_exception({"address": "h1"}, "253", ["backup"], "weil")
    assert res["success"] is False
    # Nothing was declared in PVE, so nothing may claim to be declared here.
    assert gi.load_reasons("h1") == {}
