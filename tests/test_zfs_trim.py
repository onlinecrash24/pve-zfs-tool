"""zpool trim -- the once-through companion to the autotrim property.

autotrim releases blocks continuously as they are freed; this walks the whole
pool once, which is what a pool that ran for years without it actually needs.
Scrub has had a button and a completion notification all along; on SSDs this is
the other half of routine maintenance.
"""

import pytest

from app import zfs_commands as z


def _run(recorder, success=True, stdout=""):
    def run(host, command, timeout=30, cache_ttl=0):
        recorder.append(command)
        return {"success": success, "stdout": stdout, "stderr": ""}
    return run


# --- starting it ----------------------------------------------------------

def test_the_command_is_what_it_should_be(monkeypatch):
    cmds = []
    monkeypatch.setattr(z, "run_command", _run(cmds))
    monkeypatch.setattr(z, "_invalidate", lambda h: None)
    assert z.trim_pool({"address": "h"}, "tank")["success"] is True
    assert cmds == ["zpool trim tank"]


def test_a_bogus_pool_name_never_reaches_the_shell(monkeypatch):
    # The pool name goes into a command line; validation is what keeps it from
    # being anything else.
    cmds = []
    monkeypatch.setattr(z, "run_command", _run(cmds))
    r = z.trim_pool({"address": "h"}, "tank; rm -rf /")
    assert r["success"] is False
    assert cmds == []


def test_a_failed_start_does_not_invalidate_the_cache(monkeypatch):
    cmds, invalidated = [], []
    monkeypatch.setattr(z, "run_command", _run(cmds, success=False))
    monkeypatch.setattr(z, "_invalidate", lambda h: invalidated.append(h))
    assert z.trim_pool({"address": "h"}, "tank")["success"] is False
    assert invalidated == []


# --- reading the progress -------------------------------------------------

TRIMMING = ("\tNAME        STATE     READ WRITE CKSUM\n"
            "\ttank        ONLINE       0     0     0\n"
            "\t  mirror-0  ONLINE       0     0     0\n"
            "\t    sda     ONLINE       0     0     0  (trimming) 42% done\n"
            "\t    sdb     ONLINE       0     0     0  (trimming) 41% done\n")

UNTRIMMED = ("\tNAME        STATE     READ WRITE CKSUM\n"
             "\ttank        ONLINE       0     0     0\n"
             "\t  mirror-0  ONLINE       0     0     0\n"
             "\t    sda     ONLINE       0     0     0  (untrimmed)\n"
             "\t    sdb     ONLINE       0     0     0  (untrimmed)\n")

UNSUPPORTED = ("\tNAME        STATE     READ WRITE CKSUM\n"
               "\ttank        ONLINE       0     0     0\n"
               "\t  mirror-0  ONLINE       0     0     0\n"
               "\t    sda     ONLINE       0     0     0  (trim unsupported)\n")


def test_an_active_trim_is_recognised():
    state = z.parse_trim_state(TRIMMING)
    assert state["active"] is True
    assert [d["name"] for d in state["devices"]] == ["sda", "sdb"]
    assert state["devices"][0]["progress"] == 42.0


def test_a_finished_trim_is_not_active():
    assert z.parse_trim_state(UNTRIMMED)["active"] is False


def test_spinning_disks_report_unsupported_without_being_active():
    # A pool of HDDs says this on every device. It is a fact about the
    # hardware, not something in progress and not a finding.
    state = z.parse_trim_state(UNSUPPORTED)
    assert state["active"] is False
    assert state["devices"][0]["state"] == "trim unsupported"


def test_a_pool_that_says_nothing_about_trimming():
    plain = ("\tNAME        STATE     READ WRITE CKSUM\n"
             "\ttank        ONLINE       0     0     0\n"
             "\t  sda       ONLINE       0     0     0\n")
    assert z.parse_trim_state(plain) == {"active": False, "devices": []}
    assert z.parse_trim_state("") == {"active": False, "devices": []}
    assert z.parse_trim_state(None) == {"active": False, "devices": []}


# --- the completion notification -----------------------------------------

def test_it_reports_completion_once(monkeypatch):
    sent = []
    monkeypatch.setattr("app.notifications.send_notification",
                        lambda *a, **k: sent.append(a))
    monkeypatch.setattr(z.time, "sleep", lambda *_: None)
    monkeypatch.setattr(z, "run_command",
                        lambda h, c, timeout=30, cache_ttl=0: {
                            "success": True, "stdout": UNTRIMMED, "stderr": ""})
    z._monitor_trim({"address": "h", "name": "pve1"}, "tank")
    assert len(sent) == 1
    assert sent[0][0] == "trim_finished"


def test_a_host_that_stops_answering_does_not_claim_completion(monkeypatch):
    # "Could not ask" is not "it finished" -- announcing a completion nobody
    # observed would be worse than staying quiet.
    sent = []
    monkeypatch.setattr("app.notifications.send_notification",
                        lambda *a, **k: sent.append(a))
    monkeypatch.setattr(z.time, "sleep", lambda *_: None)
    monkeypatch.setattr(z, "run_command",
                        lambda h, c, timeout=30, cache_ttl=0: {
                            "success": False, "stdout": "", "stderr": "ssh gone"})
    z._monitor_trim({"address": "h", "name": "pve1"}, "tank")
    assert sent == []


def test_the_monitor_deregisters_itself(monkeypatch):
    # Otherwise a second trim on the same pool would find a dead thread in the
    # registry and never start a watcher.
    monkeypatch.setattr("app.notifications.send_notification", lambda *a, **k: None)
    monkeypatch.setattr(z.time, "sleep", lambda *_: None)
    monkeypatch.setattr(z, "run_command",
                        lambda h, c, timeout=30, cache_ttl=0: {
                            "success": True, "stdout": UNTRIMMED, "stderr": ""})
    z._trim_monitors["h:tank"] = object()
    z._monitor_trim({"address": "h", "name": "pve1"}, "tank")
    assert "h:tank" not in z._trim_monitors
