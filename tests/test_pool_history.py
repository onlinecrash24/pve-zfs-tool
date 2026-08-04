"""`zpool history` emits the pool's ENTIRE history before `tail` trims it, so on
a pool with years of auto-snapshots it is genuinely slow. A user reported the
history dialog appearing to do nothing and then popping up over a different
page. The frontend fix is the modal loading state; server side the command needs
a timeout that does not abort it silently and a cache so a second look is
instant.
"""

from app import zfs_commands as zc


def _capture(monkeypatch):
    calls = []

    def fake_run(host, cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        return {"success": True, "stdout": "history", "stderr": ""}

    monkeypatch.setattr(zc, "run_command", fake_run)
    return calls


def test_history_is_tailed_to_the_limit(monkeypatch):
    calls = _capture(monkeypatch)
    zc.get_pool_history({"address": "h"}, "tank", limit=50)
    assert calls[0]["cmd"] == "zpool history tank | tail -n 50"


def test_history_timeout_survives_a_long_history(monkeypatch):
    # the 30s default aborted with no explanation on big pools
    calls = _capture(monkeypatch)
    zc.get_pool_history({"address": "h"}, "tank")
    assert calls[0]["timeout"] >= 120


def test_history_is_cached(monkeypatch):
    calls = _capture(monkeypatch)
    zc.get_pool_history({"address": "h"}, "tank")
    assert calls[0]["cache_ttl"] > 0


def test_history_rejects_bad_pool_name(monkeypatch):
    calls = _capture(monkeypatch)
    res = zc.get_pool_history({"address": "h"}, "tank; rm -rf /")
    assert res["success"] is False
    assert not calls              # never reached the host


def test_history_rejects_an_oversized_limit(monkeypatch):
    # validate_limit refuses anything above its maximum instead of clamping,
    # so an absurd limit never reaches the host
    calls = _capture(monkeypatch)
    res = zc.get_pool_history({"address": "h"}, "tank", limit=99999999)
    assert res["success"] is False
    assert not calls


def test_history_accepts_a_custom_limit(monkeypatch):
    calls = _capture(monkeypatch)
    zc.get_pool_history({"address": "h"}, "tank", limit=500)
    assert calls[0]["cmd"].endswith("| tail -n 500")
