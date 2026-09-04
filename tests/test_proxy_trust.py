"""X-Forwarded-For is only believed when an operator says the proxy is real.

ProxyFix used to be applied unconditionally, so on a directly reachable port
-- which is what the shipped compose file does -- any client could name its
own address. Seven failed logins with a rotating X-Forwarded-For never tripped
the rate limit, and the audit log recorded whatever the caller wrote. The
review demonstrated it before fixing it; these are that demonstration, kept.

TRUST_PROXY is unset in the test environment, so the app under test is the
secure default.
"""

import os
import time

import pytest

import app.main as m


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    m._login_attempts.clear()
    yield m.app.test_client()
    m._login_attempts.clear()


def _fail(client, xff=None):
    headers = {"X-Forwarded-For": xff} if xff else {}
    r = client.post("/api/login", json={"username": "x", "password": "definitely-wrong"},
                    headers=headers, environ_base={"REMOTE_ADDR": "203.0.113.9"})
    return r.status_code


def test_the_secure_default_is_not_to_trust_the_header():
    assert "TRUST_PROXY" not in os.environ, "test must run with the default"
    assert m.TRUST_PROXY is False


def test_a_rotating_forwarded_for_no_longer_evades_the_lockout(client):
    codes = [_fail(client, xff=f"10.0.0.{i}") for i in range(m.MAX_LOGIN_ATTEMPTS + 2)]
    assert 429 in codes, f"never locked out: {codes}"
    # ... and it is one bucket, keyed on the real address, not seven.
    assert list(m._login_attempts) == ["203.0.113.9"]


def test_the_honest_client_is_still_locked_out_the_same_way(client):
    codes = [_fail(client) for _ in range(m.MAX_LOGIN_ATTEMPTS + 2)]
    assert codes[:m.MAX_LOGIN_ATTEMPTS] == [401] * m.MAX_LOGIN_ATTEMPTS
    assert codes[m.MAX_LOGIN_ATTEMPTS:] == [429, 429]


def test_expired_lockouts_are_pruned_instead_of_kept_forever():
    m._login_attempts.clear()
    now = time.time()
    old = now - m.LOGIN_LOCKOUT_SECONDS - 1
    for i in range(50):
        m._login_attempts[f"198.51.100.{i}"] = {"count": 5, "last": old}
    m._login_record_failure("203.0.113.1", now)
    assert list(m._login_attempts) == ["203.0.113.1"]
    m._login_attempts.clear()


def test_a_live_lockout_survives_the_prune():
    m._login_attempts.clear()
    now = time.time()
    m._login_attempts["198.51.100.7"] = {"count": 5, "last": now - 10}
    m._login_record_failure("203.0.113.1", now)
    assert "198.51.100.7" in m._login_attempts
    m._login_attempts.clear()


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("1", True), ("yes", True), ("TRUE", True),
    ("false", False), ("0", False), ("", False), ("proxy", False),
])
def test_env_flag_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("PVEZFS_TEST_FLAG", raw)
    assert m._env_flag("PVEZFS_TEST_FLAG") is expected
