"""Backups in the AI replication report.

The rule carried over from the scope fix: what was not examined must not appear
as a finding. A guest whose backups nobody could read has to reach the model
without a backup field at all, so the report cannot turn silence into "no
backup".
"""

import json

import pytest

from app import ai_reports as ar
from app import backups as b
from app.replication_inventory import condense_for_report


def _matrix():
    return {
        "guests": [{
            "vmid": "100", "guest_type": "qemu", "guest_name": "web",
            "source_host": "h-src", "source_dataset": "rpool/data/vm-100-disk-0",
            "copy_count": 1, "config_mismatch": "", "no_snapshots": False,
            "copies": [
                {"host": "h-src", "dataset": "rpool/data/vm-100-disk-0",
                 "is_source": True, "snapshot_count": 10, "oldest": 1, "newest": 100,
                 "newest_snapshot": "s", "shared_snapshots": 10,
                 "missing_from_source": 0, "excluded_labels": [], "lag_seconds": 0,
                 "in_sync": True},
                {"host": "h-copy", "dataset": "tank/repl/vm-100-disk-0",
                 "is_source": False, "snapshot_count": 8, "oldest": 1, "newest": 90,
                 "newest_snapshot": "s", "shared_snapshots": 8,
                 "missing_from_source": 0, "excluded_labels": [], "lag_seconds": 10,
                 "in_sync": True},
            ],
        }],
        "hosts": ["h-src", "h-copy"], "hosts_without_data": [],
        "without_copy_guests": [], "replicated_count": 1, "without_copy_count": 0,
        "snapshot_count": 18,
    }


# --- condense -------------------------------------------------------------

def test_condense_passes_the_backup_state_through():
    m = _matrix()
    m["guests"][0]["backup"] = {
        "state": "red", "reason": "none", "age_seconds": None, "count": 0,
        "storages": [], "newest_volid": "irrelevant", "covered_by": ["j1"],
    }
    out = condense_for_report(m)
    bk = out["guests"][0]["backup"]
    assert bk == {"state": "red", "reason": "none", "age_seconds": None,
                  "count": 0, "storages": []}
    assert out["guests_without_backup"] == 1


def test_condense_omits_the_field_when_backups_were_not_examined():
    out = condense_for_report(_matrix())
    assert "backup" not in out["guests"][0]
    assert out["guests_without_backup"] == 0


# --- report ---------------------------------------------------------------

def _capture(monkeypatch, backup_result):
    """Run the replication report with the LLM and the backup read stubbed."""
    seen = {}

    def fake_llm(messages):
        seen["system"] = messages[0]["content"]
        seen["user"] = messages[1]["content"]
        return {"success": True, "content": "## 1. Overview\nfine", "usage": {}}

    monkeypatch.setattr(ar, "load_config",
                        lambda: {"provider": "openai", "openai": {"model": "m"},
                                 "report_language": "en"})
    monkeypatch.setattr("app.ssh_manager.load_hosts", lambda: [{"address": "h-src"}])
    monkeypatch.setattr("app.replication_inventory.collect_inventory",
                        lambda hosts: _matrix())
    monkeypatch.setattr("app.backups.host_backup_states",
                        lambda host, vmids=None, cache_ttl=300: backup_result)
    monkeypatch.setattr(ar, "call_llm", fake_llm)
    monkeypatch.setattr(ar, "_add_report", lambda r: None)
    res = ar.generate_replication_report(lang_override="en", source_host="h-src")
    assert res["success"], res
    body = seen["user"]
    payload = json.loads(body[body.index("{"):body.rindex("}") + 1])
    return seen, payload, res


def test_a_readable_backup_state_reaches_the_model(monkeypatch):
    _, payload, _ = _capture(monkeypatch, {
        "states": {"100": {"state": "green", "reason": "ok", "age_seconds": 3600,
                           "count": 7, "storages": ["pbs-main"]}},
        "unreadable": [], "readable": True, "error": "", "node": "pve251",
        "storages": [], "warn_hours": 36, "crit_hours": 168,
    })
    assert payload["guests"][0]["backup"]["state"] == "green"
    assert payload["guests"][0]["backup"]["storages"] == ["pbs-main"]


def test_an_unreadable_storage_leaves_the_guest_without_a_backup_field(monkeypatch):
    # The whole point: the model must not be able to conclude "no backup" from
    # data that was never collected.
    seen, payload, _ = _capture(monkeypatch, {
        "states": {"100": {"state": "unknown", "reason": "unreadable"}},
        "unreadable": [{"storage": "pbs-main", "error": "connection refused"}],
        "readable": False, "error": "connection refused", "node": "pve251",
        "storages": [], "warn_hours": 36, "crit_hours": 168,
    })
    assert "backup" not in payload["guests"][0]
    assert payload["guests_without_backup"] == 0
    # ... but the failure itself is named, so the report can say why it is silent
    assert payload["backup_storages_unreadable"] == [
        {"storage": "pbs-main", "error": "connection refused"}]


def test_a_failing_backup_read_does_not_sink_the_report(monkeypatch):
    def boom(host, vmids=None, cache_ttl=300):
        raise RuntimeError("ssh gone")
    monkeypatch.setattr("app.backups.host_backup_states", boom)
    _, payload, res = _capture(monkeypatch, {})     # backup_result unused here
    assert res["success"] is True
    assert "backup" not in payload["guests"][0]


def test_the_prompt_explains_what_a_missing_backup_field_means(monkeypatch):
    seen, _, _ = _capture(monkeypatch, {
        "states": {}, "unreadable": [], "readable": True, "error": "",
        "node": "n", "storages": [], "warn_hours": 36, "crit_hours": 168,
    })
    # Collapsed: where the prompt happens to wrap is incidental.
    sys = " ".join(seen["system"].split())
    assert "Backups:" in sys
    assert "was not examined for backups at all" in sys
    # The distinction between the two protections has to be stated, otherwise a
    # replicated-but-unbacked-up guest reads as safe.
    assert "ransomware" in sys
    # And the scope rule must still forbid inventing backup targets.
    assert "beyond what the backup fields actually contain" in sys


# --- the reported case: backed up, not replicated -------------------------

def _unreplicated_matrix():
    """A host whose guests are backed up but never replicated -- the shape that
    used to make the report refuse to run at all."""
    return {
        "guests": [{
            "vmid": str(200 + i), "guest_type": "lxc", "guest_name": f"ct{i}",
            "source_host": "h-solo", "source_dataset": f"rpool/data/subvol-{200+i}-disk-0",
            "copy_count": 0, "config_mismatch": "", "no_snapshots": False,
            "copies": [{"host": "h-solo", "dataset": f"rpool/data/subvol-{200+i}-disk-0",
                        "is_source": True, "snapshot_count": 30, "oldest": 1,
                        "newest": 100, "newest_snapshot": "s", "shared_snapshots": 30,
                        "missing_from_source": 0, "excluded_labels": [],
                        "lag_seconds": 0, "in_sync": True}],
        } for i in range(3)],
        "hosts": ["h-solo"], "hosts_without_data": [], "without_copy_guests": [],
        "replicated_count": 0, "without_copy_count": 3, "snapshot_count": 90,
    }


def _run(monkeypatch, matrix, backup_result):
    seen = {}

    def fake_llm(messages):
        seen["system"] = messages[0]["content"]
        seen["user"] = messages[1]["content"]
        return {"success": True, "content": "## 1. Overall state\nfine", "usage": {}}

    monkeypatch.setattr(ar, "load_config",
                        lambda: {"provider": "openai", "openai": {"model": "m"},
                                 "report_language": "en"})
    monkeypatch.setattr("app.ssh_manager.load_hosts", lambda: [{"address": "h-solo"}])
    monkeypatch.setattr("app.replication_inventory.collect_inventory",
                        lambda hosts: matrix)
    monkeypatch.setattr("app.backups.host_backup_states",
                        lambda host, vmids=None, cache_ttl=300: backup_result)
    monkeypatch.setattr(ar, "call_llm", fake_llm)
    monkeypatch.setattr(ar, "_add_report", lambda r: None)
    return seen, ar.generate_replication_report(lang_override="en", source_host="h-solo")


def _all_backed_up(vmids):
    return {
        "states": {v: {"state": "green", "reason": "ok", "age_seconds": 3600,
                       "count": 7, "storages": ["pbs-main"]} for v in vmids},
        "unreadable": [], "readable": True, "error": "", "node": "n",
        "storages": [], "warn_hours": 36, "crit_hours": 168,
    }


def test_a_backed_up_unreplicated_host_can_produce_a_report(monkeypatch):
    # This returned {"success": False, "error": "No replicated guests found"}
    # -- the one host whose protection rests entirely on backups could not be
    # reported on at all.
    _, res = _run(monkeypatch, _unreplicated_matrix(),
                  _all_backed_up(["200", "201", "202"]))
    assert res["success"] is True


def test_that_host_is_judged_ok_not_critical(monkeypatch):
    # The verdict used to be "crit" purely because without_copy_count > 0.
    _, res = _run(monkeypatch, _unreplicated_matrix(),
                  _all_backed_up(["200", "201", "202"]))
    assert res["report"]["verdict"] == "ok"
    assert res["report"]["critical_findings"] == 0


def test_the_same_host_without_backups_is_critical(monkeypatch):
    none_backed_up = {
        "states": {v: {"state": "red", "reason": "none", "count": 0,
                       "age_seconds": None, "storages": []}
                   for v in ("200", "201", "202")},
        "unreadable": [], "readable": True, "error": "", "node": "n",
        "storages": [], "warn_hours": 36, "crit_hours": 168,
    }
    _, res = _run(monkeypatch, _unreplicated_matrix(), none_backed_up)
    assert res["report"]["verdict"] == "crit"
    assert res["report"]["critical_findings"] == 3


def test_a_host_with_no_guests_at_all_still_reports_nothing_to_say(monkeypatch):
    empty = {"guests": [], "hosts": ["h-solo"], "hosts_without_data": [],
             "without_copy_guests": [], "replicated_count": 0,
             "without_copy_count": 0, "snapshot_count": 0}
    _, res = _run(monkeypatch, empty, _all_backed_up([]))
    assert res["success"] is False
    assert "No guests found" in res["error"]


def test_the_prompt_asks_for_an_assessment_not_just_counts(monkeypatch):
    seen, _ = _run(monkeypatch, _unreplicated_matrix(),
                   _all_backed_up(["200", "201", "202"]))
    sys = " ".join(seen["system"].split())
    assert "Overall state" in sys
    # The instruction that stops it reading a backup-only host as a failure.
    assert "backs up every guest is in good shape" in sys
