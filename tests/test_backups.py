"""Backup state per guest, read through the PVE node.

The rule these tests exist to protect: a backup nobody could look at is not the
same as a backup that is missing. A PBS that is down must never turn healthy
guests red -- that is an alert everyone learns to ignore, which is worse than no
alert at all.
"""

import json
import time

import pytest

from app import backups as b

HOUR = 3600
NOW = 1_800_000_000.0


def _pbs_vol(vmid, ctime, verify="ok", protected=0):
    """A PBS backup volume as pvesh reports it."""
    v = {"volid": f"pbs-main:backup/vm/{vmid}/2026-08-07T22:00:02Z",
         "vmid": vmid, "ctime": ctime, "size": 4294967296,
         "format": "pbs-vm", "subtype": "qemu", "protected": protected}
    if verify is not None:
        v["verification"] = {"state": verify, "upid": "UPID:pbs:00001234::"}
    return v


def _dir_vol(vmid, ctime):
    """A vzdump archive on a directory storage -- no verification concept."""
    return {"volid": f"local:backup/vzdump-qemu-{vmid}-2026_08_07-22_00_02.vma.zst",
            "vmid": vmid, "ctime": ctime, "size": 3221225472,
            "format": "vma.zst", "subtype": "qemu"}


# --- storage list ---------------------------------------------------------

def test_storage_list():
    text = json.dumps([
        {"storage": "pbs-main", "type": "pbs", "enabled": 1, "active": 1,
         "total": 8000000000000, "used": 3000000000000, "avail": 5000000000000},
        {"storage": "nfs-bk", "type": "nfs", "enabled": 1, "active": 0},
    ])
    st = b.parse_storage_list(text)
    assert [s["storage"] for s in st] == ["nfs-bk", "pbs-main"]
    assert st[1]["type"] == "pbs" and st[1]["active"] is True
    assert st[0]["active"] is False          # mounted-but-unreachable NFS


def test_storage_list_survives_a_perl_warning_before_the_json():
    # PVE nodes emit locale warnings on stderr often enough that a strict parse
    # would report a perfectly healthy storage as unreadable.
    text = ('perl: warning: Setting locale failed.\n'
            + json.dumps([{"storage": "pbs-main", "type": "pbs",
                           "enabled": 1, "active": 1}]))
    assert [s["storage"] for s in b.parse_storage_list(text)] == ["pbs-main"]


def test_garbage_is_not_a_storage_list():
    assert b.parse_storage_list("500 permission denied") == []
    assert b.parse_storage_list("") == []


# --- content --------------------------------------------------------------

def test_content_reads_the_pbs_verification_state():
    vols = b.parse_backup_content(json.dumps([_pbs_vol("100", 1000)]),
                                  "pbs-main", "pbs")
    assert vols[0]["verify"] == "ok"
    assert vols[0]["verifies"] is True
    assert vols[0]["vmid"] == "100"


def test_a_vzdump_archive_has_no_verification_to_miss():
    # The trap: treating "no verification field" as "not verified" would put a
    # warning on every backup on every NFS and directory storage.
    vols = b.parse_backup_content(json.dumps([_dir_vol("100", 1000)]),
                                  "local", "dir")
    assert vols[0]["verify"] is None
    assert vols[0]["verifies"] is False


def test_content_accepts_the_data_wrapper():
    text = json.dumps({"data": [_pbs_vol("100", 1000)]})
    assert len(b.parse_backup_content(text, "pbs-main", "pbs")) == 1


def test_volumes_without_a_volid_are_dropped():
    text = json.dumps([{"vmid": "100", "ctime": 1000}, _pbs_vol("101", 1000)])
    assert [v["vmid"] for v in b.parse_backup_content(text, "s", "pbs")] == ["101"]


# --- jobs -----------------------------------------------------------------

JOBS_CFG = """\
vzdump: backup-8a1f
\tschedule sat 02:00
\tstorage pbs-main
\tmode snapshot
\tall 1
\texclude 100,101
\tenabled 1

vzdump: backup-daily
\tschedule 22:00
\tstorage nfs-bk
\tvmid 100,205
\tenabled 1

vzdump: backup-old
\tschedule mon 03:00
\tstorage local
\tall 1
\tenabled 0

prune: prune-main
\tschedule daily
\tkeep-last 7
"""


def test_jobs_are_parsed_and_other_job_types_skipped():
    jobs = b.parse_backup_jobs(JOBS_CFG)
    assert [j["id"] for j in jobs] == ["backup-8a1f", "backup-daily", "backup-old"]
    assert jobs[0]["all"] is True and jobs[0]["exclude"] == ["100", "101"]
    assert jobs[1]["vmid"] == ["100", "205"]
    assert jobs[2]["enabled"] is False
    # The prune section's keep-last must not have leaked into the last job.
    assert jobs[2]["storage"] == "local"


def test_coverage_all_minus_exclude():
    jobs = b.parse_backup_jobs(JOBS_CFG)
    all_job = jobs[0]
    assert b.job_covers(all_job, "300") is True
    assert b.job_covers(all_job, "100") is False     # excluded


def test_coverage_explicit_list():
    jobs = b.parse_backup_jobs(JOBS_CFG)
    assert b.job_covers(jobs[1], "100") is True
    assert b.job_covers(jobs[1], "300") is False


def test_a_disabled_job_covers_nothing():
    # Exactly the case worth catching: the job exists, so the estate looks
    # backed up, but nobody noticed it was switched off.
    jobs = b.parse_backup_jobs(JOBS_CFG)
    assert b.job_covers(jobs[2], "300") is False


def test_a_pool_job_is_not_claimed_as_coverage():
    # Resolving pool membership needs another call; guessing would produce a
    # confident wrong answer.
    jobs = b.parse_backup_jobs("vzdump: p\n\tstorage s\n\tpool prod\n\tenabled 1\n")
    assert jobs[0]["pool"] == "prod"
    assert b.job_covers(jobs[0], "300") is False


# --- map ------------------------------------------------------------------

def test_map_takes_the_verdict_from_the_newest_volume():
    # An older backup that verified fine says nothing about last night's.
    vols = b.parse_backup_content(json.dumps([
        _pbs_vol("100", int(NOW - 48 * HOUR), verify="ok"),
        _pbs_vol("100", int(NOW - 2 * HOUR), verify="failed"),
    ]), "pbs-main", "pbs")
    rec = b.guest_backup_map(vols)["100"]
    assert rec["count"] == 2
    assert rec["newest"] == int(NOW - 2 * HOUR)
    assert rec["newest_verify"] == "failed"
    assert rec["verify_failed"] == 1


def test_map_collects_storages_across_targets():
    vols = (b.parse_backup_content(json.dumps([_pbs_vol("100", 1000)]), "pbs-main", "pbs")
            + b.parse_backup_content(json.dumps([_dir_vol("100", 900)]), "local", "dir"))
    rec = b.guest_backup_map(vols)["100"]
    assert rec["storages"] == ["local", "pbs-main"]
    assert rec["types"] == ["dir", "pbs"]


def test_map_records_job_coverage():
    vols = b.parse_backup_content(json.dumps([_pbs_vol("300", 1000)]), "pbs-main", "pbs")
    jobs = b.parse_backup_jobs(JOBS_CFG)
    assert b.guest_backup_map(vols, jobs)["300"]["covered_by"] == ["backup-8a1f"]


# --- state ----------------------------------------------------------------

def _state(age_hours, verify="ok", storage_type="pbs", covered=True, count=1):
    vol = (_pbs_vol("100", int(NOW - age_hours * HOUR), verify=verify)
           if storage_type == "pbs" else _dir_vol("100", int(NOW - age_hours * HOUR)))
    vols = b.parse_backup_content(json.dumps([vol]), "s", storage_type)
    jobs = [{"id": "j", "enabled": True, "all": True, "exclude": [], "vmid": []}] \
        if covered else []
    rec = b.guest_backup_map(vols, jobs)["100"]
    return b.backup_state(rec, now=NOW, warn_hours=36, crit_hours=168)


def test_a_fresh_verified_covered_backup_is_green():
    s = _state(2)
    assert s["state"] == b.STATE_OK and s["reason"] == "ok"
    assert s["age_seconds"] == 2 * HOUR


def test_thresholds_at_the_edges():
    assert _state(35)["state"] == b.STATE_OK
    assert _state(37)["reason"] == "stale_warn"
    assert _state(167)["reason"] == "stale_warn"
    assert _state(169)["reason"] == "stale_crit"
    assert _state(169)["state"] == b.STATE_BAD


def test_no_backup_at_all_is_red():
    s = b.backup_state(None, now=NOW)
    assert s["state"] == b.STATE_BAD and s["reason"] == "none"
    assert s["count"] == 0


def test_a_failed_verification_is_red_not_yellow():
    # A backup that does not verify is not a backup, however recent it is.
    s = _state(1, verify="failed")
    assert s["state"] == b.STATE_BAD and s["reason"] == "verify_failed"


def test_an_unverified_pbs_backup_warns():
    s = _state(1, verify=None)
    assert s["state"] == b.STATE_WARN and s["reason"] == "verify_pending"


def test_a_fresh_vzdump_archive_is_green_despite_no_verification():
    s = _state(1, storage_type="dir")
    assert s["state"] == b.STATE_OK


def test_a_guest_no_job_covers_warns():
    s = _state(1, covered=False)
    assert s["state"] == b.STATE_WARN and s["reason"] == "no_job"


def test_coverage_is_not_judged_when_jobs_could_not_be_read():
    vols = b.parse_backup_content(json.dumps([_pbs_vol("100", int(NOW - HOUR))]),
                                  "s", "pbs")
    rec = b.guest_backup_map(vols, [])["100"]
    s = b.backup_state(rec, now=NOW, covered_known=False)
    assert s["state"] == b.STATE_OK          # not "no_job"


def test_backups_without_a_timestamp_warn_instead_of_faking_an_age():
    vols = b.parse_backup_content(
        json.dumps([{"volid": "s:backup/vm/100/x", "vmid": "100"}]), "s", "pbs")
    rec = b.guest_backup_map(vols, [{"id": "j", "enabled": True, "all": True,
                                     "exclude": [], "vmid": []}])["100"]
    s = b.backup_state(rec, now=NOW)
    assert s["reason"] == "no_timestamp"
    assert s["age_seconds"] is None          # never "older than a week"


# --- states across a host -------------------------------------------------

def test_a_guest_with_no_backup_still_appears():
    # Without the vmid list it would simply be absent, which is the opposite of
    # what should happen to the one guest nobody backs up.
    vols = b.parse_backup_content(json.dumps([_pbs_vol("100", int(NOW - HOUR))]),
                                  "s", "pbs")
    states = b.guest_backup_states(b.guest_backup_map(vols), vmids=["100", "999"],
                                  now=NOW, covered_known=False)
    assert states["999"]["state"] == b.STATE_BAD
    assert states["100"]["state"] == b.STATE_OK


def test_unreadable_storages_make_the_state_unknown_never_red():
    # The whole point: a PBS that is down must not put a fault on every guest.
    states = b.guest_backup_states({}, vmids=["100", "101"], now=NOW, unknown=True)
    assert {s["state"] for s in states.values()} == {b.STATE_UNKNOWN}
    assert states["100"]["reason"] == "unreadable"


# --- thresholds from config ----------------------------------------------

def test_thresholds_default_to_36_and_168():
    assert b.backup_thresholds() == (36, 168)


def test_configured_thresholds_are_used(monkeypatch):
    monkeypatch.setattr("app.notifications.load_config",
                        lambda: {"thresholds": {"backup_warn_hours": 12,
                                                "backup_crit_hours": 72}})
    assert b.backup_thresholds() == (12, 72)


def test_a_warn_above_crit_is_pulled_back_down(monkeypatch):
    # Otherwise the warning could never fire before the critical one.
    monkeypatch.setattr("app.notifications.load_config",
                        lambda: {"thresholds": {"backup_warn_hours": 200,
                                                "backup_crit_hours": 48}})
    assert b.backup_thresholds() == (48, 48)


def test_unreadable_config_falls_back_to_defaults(monkeypatch):
    def boom():
        raise OSError("no config")
    monkeypatch.setattr("app.notifications.load_config", boom)
    assert b.backup_thresholds() == (36, 168)


# --- collection -----------------------------------------------------------

def _fake_run(responses):
    """Route commands to canned results by substring match."""
    def run(host, command, timeout=30, cache_ttl=0):
        for needle, result in responses.items():
            if needle in command:
                return result
        return {"success": False, "stdout": "", "stderr": "unexpected: " + command}
    return run


def test_collect_reads_storages_content_and_jobs(monkeypatch):
    monkeypatch.setattr(b, "run_command", _fake_run({
        "hostname": {"success": True, "stdout": "pve251\n", "stderr": ""},
        "/storage --content backup": {"success": True, "stderr": "", "stdout": json.dumps(
            [{"storage": "pbs-main", "type": "pbs", "enabled": 1, "active": 1}])},
        "/storage/pbs-main/content": {"success": True, "stderr": "", "stdout": json.dumps(
            [_pbs_vol("100", int(NOW - HOUR))])},
        "jobs.cfg": {"success": True, "stdout": JOBS_CFG, "stderr": ""},
    }))
    data = b.collect_backups({"address": "h"})
    assert data["node"] == "pve251"
    assert data["readable"] is True
    assert data["jobs_known"] is True
    assert len(data["volumes"]) == 1
    assert data["unreadable"] == []


def test_an_inactive_storage_is_reported_not_skipped(monkeypatch):
    monkeypatch.setattr(b, "run_command", _fake_run({
        "hostname": {"success": True, "stdout": "pve251\n", "stderr": ""},
        "/storage --content backup": {"success": True, "stderr": "", "stdout": json.dumps(
            [{"storage": "nfs-bk", "type": "nfs", "enabled": 1, "active": 0}])},
        "jobs.cfg": {"success": True, "stdout": "", "stderr": ""},
    }))
    data = b.collect_backups({"address": "h"})
    assert data["unreadable"] == [{"storage": "nfs-bk", "error": "inactive"}]
    assert data["readable"] is False


def test_a_failing_pvesh_is_an_error_not_an_empty_estate(monkeypatch):
    monkeypatch.setattr(b, "run_command", _fake_run({
        "hostname": {"success": True, "stdout": "pve251\n", "stderr": ""},
        "/storage --content backup": {"success": False, "stdout": "",
                                      "stderr": "500 permission denied\n"},
        "jobs.cfg": {"success": True, "stdout": "", "stderr": ""},
    }))
    data = b.collect_backups({"address": "h"})
    assert data["readable"] is False
    assert "permission denied" in data["error"]


def test_a_node_without_any_backup_storage_is_a_real_answer(monkeypatch):
    # pvesh succeeded and returned nothing: the guests here genuinely have no
    # backup target, which IS a finding -- unlike a failed call.
    monkeypatch.setattr(b, "run_command", _fake_run({
        "hostname": {"success": True, "stdout": "pve251\n", "stderr": ""},
        "/storage --content backup": {"success": True, "stdout": "[]", "stderr": ""},
        "jobs.cfg": {"success": True, "stdout": "", "stderr": ""},
    }))
    data = b.collect_backups({"address": "h"})
    assert data["readable"] is True
    assert data["volumes"] == []


def test_host_backup_states_marks_everything_unknown_when_nothing_was_read(monkeypatch):
    monkeypatch.setattr(b, "run_command", _fake_run({
        "hostname": {"success": True, "stdout": "pve251\n", "stderr": ""},
        "/storage --content backup": {"success": False, "stdout": "",
                                      "stderr": "connection refused"},
        "jobs.cfg": {"success": True, "stdout": "", "stderr": ""},
    }))
    res = b.host_backup_states({"address": "h"}, vmids=["100", "101"])
    assert {s["state"] for s in res["states"].values()} == {b.STATE_UNKNOWN}
    assert res["readable"] is False


# --- merge into the replication matrix -----------------------------------

def _matrix():
    return {"guests": [
        {"vmid": "100", "source_host": "h1", "guest_name": "web"},
        {"vmid": "101", "source_host": "h1", "guest_name": "db"},
        {"vmid": "200", "source_host": "h2", "guest_name": "other"},
    ]}


def test_merge_attaches_backup_state_to_the_source_hosts_guests():
    m = b.merge_backups(_matrix(), {
        "100": {"state": b.STATE_OK, "reason": "ok"},
        "101": {"state": b.STATE_BAD, "reason": "none"},
        "200": {"state": b.STATE_BAD, "reason": "none"},
    }, source_host="h1")
    assert m["guests"][0]["backup"]["state"] == b.STATE_OK
    assert m["guests"][1]["backup"]["state"] == b.STATE_BAD
    # A guest of another host is out of scope for this host's backup read.
    assert "backup" not in m["guests"][2]
    assert m["backup_at_risk_count"] == 1


def test_merge_leaves_a_guest_without_backup_data_untouched():
    # Absent data must not read as "no backup".
    m = b.merge_backups(_matrix(), {"100": {"state": b.STATE_OK}}, source_host="h1")
    assert "backup" not in m["guests"][1]
    assert m["backup_at_risk_count"] == 0


def test_merge_flags_when_no_backup_data_arrived_at_all():
    m = b.merge_backups(_matrix(), {}, source_host="h1")
    assert m["backup_states_present"] is False
    assert all("backup" not in g for g in m["guests"])


# --- the contract with the UI --------------------------------------------

def test_every_reason_has_a_label_in_both_languages():
    # The UI renders t("bk_reason_" + reason). A reason added here without its
    # two i18n entries shows up as the raw key in the tooltip -- exactly the
    # kind of thing nobody notices until a user asks what "bk_reason_no_job"
    # means.
    import os
    import re
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "app", "static", "js", "i18n.js")
    with open(path, encoding="utf-8") as f:
        i18n = f.read()

    reasons = {"ok", "none", "stale_warn", "stale_crit", "verify_failed",
               "verify_pending", "no_job", "no_timestamp", "unreadable",
               "unknown"}
    # Everything backup_state / guest_backup_states can put in "reason".
    src_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "app", "backups.py")
    with open(src_path, encoding="utf-8") as f:
        produced = set(re.findall(r'"reason":\s*"([a-z_]+)"', f.read()))
    assert produced <= reasons, f"undeclared reason(s): {produced - reasons}"

    for r in reasons:
        # Twice: once in the English block, once in the German one.
        assert len(re.findall(rf"^\s+bk_reason_{r}:", i18n, re.M)) == 2, r
