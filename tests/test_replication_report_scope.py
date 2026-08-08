"""The replication report must describe one source host and the hosts holding
copies of its guests -- nothing else.

Handing the model the full host list made it write a "silent hosts" section
about five unrelated machines, speculate about offsite backup targets and
recommend investigating a "configuration leftover". None of that could be
supported by the data: those hosts were never examined for this report. A report
that invents findings about things it did not look at is worse than one that
stays quiet.
"""

import json

from app import ai_reports as ar


def _fake_matrix():
    """One guest on h-src with a copy on h-copy. Other hosts exist but are
    irrelevant to this report."""
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
                 "missing_from_source": 0, "excluded_labels": ["frequent"],
                 "lag_seconds": 10, "in_sync": True},
            ],
        }],
        # Every registered host, including ones with nothing to do with h-src
        "hosts": ["h-src", "h-copy", "192.168.66.70", "195.201.84.113"],
        "hosts_without_data": ["192.168.178.12"],
        "without_copy_guests": [],
        "replicated_count": 1, "without_copy_count": 0,
        "snapshot_count": 18,
    }


def _capture_prompt(monkeypatch):
    """Run the report with the LLM stubbed out, return the JSON handed to it."""
    seen = {}

    def fake_llm(messages):
        seen["system"] = messages[0]["content"]
        seen["user"] = messages[1]["content"]
        return {"success": True, "content": "## 1. Overview\nfine", "usage": {}}

    # load_config() would create the data directory, which is not writable
    # everywhere the tests run.
    monkeypatch.setattr(ar, "load_config",
                        lambda: {"provider": "openai", "openai": {"model": "m"},
                                 "report_language": "en"})
    monkeypatch.setattr(ar, "load_hosts", lambda: [{"address": "h-src"}], raising=False)
    monkeypatch.setattr("app.ssh_manager.load_hosts", lambda: [{"address": "h-src"}])
    monkeypatch.setattr("app.replication_inventory.collect_inventory",
                        lambda hosts: _fake_matrix())
    monkeypatch.setattr(ar, "call_llm", fake_llm)
    monkeypatch.setattr(ar, "_add_report", lambda r: None)
    res = ar.generate_replication_report(lang_override="en", source_host="h-src")
    assert res["success"], res
    return seen, res


def test_unrelated_hosts_are_not_handed_to_the_model(monkeypatch):
    seen, _ = _capture_prompt(monkeypatch)
    for stranger in ("192.168.66.70", "195.201.84.113", "192.168.178.12"):
        assert stranger not in seen["user"], stranger


def test_payload_names_source_and_copy_hosts(monkeypatch):
    seen, _ = _capture_prompt(monkeypatch)
    body = seen["user"]
    payload = json.loads(body[body.index("{"):body.rindex("}") + 1])
    assert payload["source_host"] == "h-src"
    assert payload["copy_hosts"] == ["h-copy"]
    assert "hosts_without_data" not in payload


def test_prompt_forbids_commenting_on_other_hosts(monkeypatch):
    seen, _ = _capture_prompt(monkeypatch)
    assert "Scope:" in seen["system"]
    assert "outside this report" in seen["system"]


def test_report_header_lists_only_covered_hosts(monkeypatch):
    _, res = _capture_prompt(monkeypatch)
    assert res["report"]["host_names"] == ["h-src", "h-copy"]
    assert res["report"]["host_count"] == 2
    assert res["report"]["kind"] == "replication"
