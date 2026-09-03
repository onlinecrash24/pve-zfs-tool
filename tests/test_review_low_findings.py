"""The three low findings from the code review, each pinned.

None of them was an exploit. Each was a place where the code was one step
less careful than it could be for free.
"""

import shlex

import pytest

import app.main as m
from app import zfs_commands as z


# --- /metrics: the token belongs in a header, never in the URL --------------

@pytest.fixture
def metrics(monkeypatch):
    monkeypatch.setenv("PROMETHEUS_TOKEN", "s3cret-token")
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    return m.app.test_client()


def test_the_bearer_header_is_accepted(metrics):
    r = metrics.get("/metrics", headers={"Authorization": "Bearer s3cret-token"})
    assert r.status_code == 200


def test_the_query_string_form_is_no_longer_accepted(metrics):
    # A token in the URL lands in proxy access logs, browser history and
    # Referer headers. Prometheus has had bearer auth in scrape_config for
    # years, so nothing needed the query form.
    r = metrics.get("/metrics?token=s3cret-token")
    assert r.status_code == 401


def test_a_wrong_header_token_is_rejected(metrics):
    assert metrics.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401


# --- N+1: one round trip for the clone-target dropdown -----------------------

def test_clone_targets_take_one_command(monkeypatch):
    cmds = []

    def run(host, cmd, timeout=30, cache_ttl=0):
        cmds.append((cmd, cache_ttl))
        return {"success": True, "stderr": "",
                "stdout": "rpool\nrpool/ROOT\nrpool/data\ntank\ntank/vm\n"}

    monkeypatch.setattr(z, "run_command", run)
    out = z.get_clone_targets({"address": "h"})
    assert [c for c, _ in cmds] == ["zfs list -H -o name -d 1"]
    assert cmds[0][1] > 0, "must be cached -- it populates a dropdown"
    assert out == {"pools": ["rpool", "tank"],
                   "datasets": ["rpool/ROOT", "rpool/data", "tank/vm"]}


def test_clone_targets_on_a_host_that_does_not_answer(monkeypatch):
    monkeypatch.setattr(z, "run_command",
                        lambda h, c, timeout=30, cache_ttl=0: {"success": False, "stdout": "", "stderr": "x"})
    assert z.get_clone_targets({"address": "h"}) == {"pools": [], "datasets": []}


# --- Reads that never change between clicks are cached ------------------------

def test_the_upgrade_check_is_cached(monkeypatch):
    calls = []

    def run(host, cmd, **kw):
        calls.append(kw.get("cache_ttl", 0))
        return {"success": True, "stdout": "already enabled", "stderr": ""}

    monkeypatch.setattr(z, "run_command", run)
    z.check_pool_upgrade({"address": "h"}, "tank")
    assert calls and all(ttl > 0 for ttl in calls), calls


# --- Names read from the host go back to it quoted ---------------------------

def test_vdev_names_from_zpool_status_are_quoted_before_zdb(monkeypatch):
    # A host can only inject into itself this way -- but there is no reason
    # to let it, and quoting is free.
    hostile = "sda; touch /tmp/pwned"
    cmds = []

    def run(host, cmd, timeout=30, cache_ttl=0):
        cmds.append(cmd)
        if cmd.startswith("zpool status"):
            return {"success": True, "stdout": hostile + "\n", "stderr": ""}
        return {"success": True, "stdout": "label\n", "stderr": ""}

    monkeypatch.setattr(z, "run_command", run)
    z.get_zdb_analysis({"address": "h"}, "tank")
    zdb_l = [c for c in cmds if c.startswith("zdb -l ")]
    assert zdb_l, cmds
    for c in zdb_l:
        assert shlex.quote("/dev/" + hostile) in c or shlex.quote("/dev/disk/by-id/" + hostile) in c
        assert "; touch" not in c.replace(shlex.quote("/dev/" + hostile), "").replace(
            shlex.quote("/dev/disk/by-id/" + hostile), ""), c
