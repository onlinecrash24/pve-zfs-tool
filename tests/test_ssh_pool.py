"""Per-(thread, host) SSH connection pool. The subtle parts: a reused
connection must be health-checked, a *command* timeout must NOT be mistaken
for a dead connection (else the command re-runs), and a stale reused
connection must transparently reconnect exactly once."""

import socket
import paramiko
import pytest
from app import ssh_manager as sm


# --- fakes ----------------------------------------------------------------

class _FakeTransport:
    def __init__(self, active):
        self._active = active

    def is_active(self):
        return self._active


class _FakeOut:
    def __init__(self, data=b"", code=0):
        self._data = data
        self.channel = _FakeChan(code)

    def read(self):
        return self._data


class _FakeChan:
    def __init__(self, code):
        self._code = code

    def recv_exit_status(self):
        return self._code


class _FakeSftp:
    def __init__(self, get_exc=None, payload=b"data"):
        self._get_exc = get_exc
        self._payload = payload
        self.closed = False

    def get_channel(self):
        class _Ch:
            def settimeout(self, t):
                pass
        return _Ch()

    def get(self, remote, local):
        if self._get_exc is not None:
            raise self._get_exc
        with open(local, "wb") as f:
            f.write(self._payload)

    def close(self):
        self.closed = True


class FakeClient:
    def __init__(self, active=True, exec_result=None, exec_exc=None,
                 sftp_exc=None, sftp_get_exc=None):
        self._active = active
        self.closed = False
        self._exec_result = exec_result
        self._exec_exc = exec_exc
        self._sftp_exc = sftp_exc
        self._sftp_get_exc = sftp_get_exc

    def get_transport(self):
        return _FakeTransport(self._active)

    def exec_command(self, command, timeout=None):
        if self._exec_exc is not None:
            raise self._exec_exc
        out, err, code = self._exec_result or (b"ok\n", b"", 0)
        return None, _FakeOut(out, code), _FakeOut(err, 0)

    def open_sftp(self):
        if self._sftp_exc is not None:
            raise self._sftp_exc
        return _FakeSftp(get_exc=self._sftp_get_exc)

    def close(self):
        self.closed = True


# --- _host_key ------------------------------------------------------------

def test_host_key_defaults_and_overrides():
    assert sm._host_key({"address": "h"}) == ("h", 22, "root")
    assert sm._host_key({"address": "h", "port": 2222, "user": "bob"}) == ("h", 2222, "bob")


# --- _conn_reusable -------------------------------------------------------

def test_conn_reusable_live_and_young():
    now = 1000.0
    assert sm._conn_reusable(FakeClient(active=True), now - 10, now, ttl=120) is True


def test_conn_reusable_false_when_idle_too_long():
    now = 1000.0
    assert sm._conn_reusable(FakeClient(active=True), now - 999, now, ttl=120) is False


def test_conn_reusable_false_when_transport_dead():
    now = 1000.0
    assert sm._conn_reusable(FakeClient(active=False), now - 1, now, ttl=120) is False


# --- _run_once ------------------------------------------------------------

def test_run_once_success():
    r = sm._run_once(FakeClient(exec_result=(b"hello\n", b"", 0)), "echo hi", 10)
    assert r == {"success": True, "stdout": "hello\n", "stderr": ""}


def test_run_once_nonzero_exit_is_failure():
    r = sm._run_once(FakeClient(exec_result=(b"", b"nope\n", 3)), "false", 10)
    assert r["success"] is False and r["stderr"] == "nope\n"


def test_run_once_command_timeout_returns_result_not_stale():
    # a command-level timeout must be a normal (failed) result, never a retry
    r = sm._run_once(FakeClient(exec_exc=socket.timeout()), "sleep 999", 5)
    assert r["success"] is False
    assert "timed out" in r["stderr"]


@pytest.mark.parametrize("exc", [
    EOFError("x"),
    ConnectionResetError("x"),
    paramiko.SSHException("x"),
    OSError("broken pipe"),
])
def test_run_once_dead_connection_raises_stale(exc):
    with pytest.raises(sm._StaleConnection):
        sm._run_once(FakeClient(exec_exc=exc), "x", 5)


# --- _acquire / pool mechanics -------------------------------------------

def test_acquire_reuses_then_reconnects_when_dead(monkeypatch):
    made = []
    monkeypatch.setattr(sm, "get_ssh_client", lambda h: made.append(FakeClient()) or made[-1])
    host = {"address": "pool-mech-1"}
    sm._drop(host)

    a, reused_a = sm._acquire(host)
    assert reused_a is False and len(made) == 1

    b, reused_b = sm._acquire(host)
    assert reused_b is True and b is a and len(made) == 1     # reused, no new conn

    a._active = False                                         # connection dies
    c, reused_c = sm._acquire(host)
    assert reused_c is False and c is not a and len(made) == 2
    assert a.closed is True                                   # stale one closed

    sm._drop(host)
    assert c.closed is True


# --- _exec_pooled retry semantics ----------------------------------------

def test_exec_pooled_retries_once_on_stale_reused_connection(monkeypatch):
    dead = FakeClient(active=True, exec_exc=EOFError("dead"))   # looks alive, exec fails
    good = FakeClient(active=True, exec_result=(b"ok\n", b"", 0))
    seq = [dead, good]
    monkeypatch.setattr(sm, "get_ssh_client", lambda h: seq.pop(0))
    host = {"address": "pool-retry"}
    sm._drop(host)

    sm._acquire(host)                       # pool now holds `dead` (reused-able)
    r = sm._exec_pooled(host, "echo hi", 10)
    assert r["success"] is True and r["stdout"] == "ok\n"
    assert dead.closed is True              # stale connection was dropped
    sm._drop(host)


def test_exec_pooled_fresh_connection_failure_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(sm, "get_ssh_client",
                        lambda h: calls.append(1) or FakeClient(active=True, exec_exc=EOFError("dead")))
    host = {"address": "pool-fresh-fail"}
    sm._drop(host)
    r = sm._exec_pooled(host, "x", 10)
    assert r["success"] is False
    assert len(calls) == 1                  # brand-new conn failing -> no retry loop
    sm._drop(host)


def test_exec_pooled_unexpected_exception_never_raises(monkeypatch):
    # run_command's contract: always return a dict, never propagate
    monkeypatch.setattr(sm, "get_ssh_client",
                        lambda h: FakeClient(active=True, exec_exc=ValueError("weird")))
    host = {"address": "pool-weird"}
    sm._drop(host)
    r = sm._exec_pooled(host, "x", 10)
    assert r["success"] is False and "weird" in r["stderr"]
    sm._drop(host)


def test_pool_disabled_falls_back_to_fresh_connection(monkeypatch):
    monkeypatch.setattr(sm, "SSH_POOL_ENABLED", False)
    c = FakeClient(exec_result=(b"ok\n", b"", 0))
    monkeypatch.setattr(sm, "get_ssh_client", lambda h: c)
    r = sm._exec_pooled({"address": "x"}, "echo", 10)
    assert r["success"] is True
    assert c.closed is True                 # fresh path always closes


# --- fetch_file (SFTP) on the pool ----------------------------------------

def test_fetch_file_success_keeps_pooled_connection(tmp_path, monkeypatch):
    c = FakeClient()
    monkeypatch.setattr(sm, "get_ssh_client", lambda h: c)
    host = {"address": "sftp-ok"}
    sm._drop(host)
    local = tmp_path / "out.bin"
    r = sm.fetch_file(host, "/remote/x", str(local))
    assert r["success"] is True and r["bytes"] == 4
    assert local.read_bytes() == b"data"
    assert c.closed is False                # stays pooled for reuse
    sm._drop(host)


def test_fetch_file_retries_once_on_stale_reused_connection(tmp_path, monkeypatch):
    dead = FakeClient(sftp_exc=EOFError("dead"))    # alive transport, sftp fails
    good = FakeClient()
    seq = [dead, good]
    monkeypatch.setattr(sm, "get_ssh_client", lambda h: seq.pop(0))
    host = {"address": "sftp-retry"}
    sm._drop(host)
    sm._acquire(host)                                # pool now holds `dead`
    local = tmp_path / "out.bin"
    r = sm.fetch_file(host, "/remote/x", str(local))
    assert r["success"] is True and local.read_bytes() == b"data"
    assert dead.closed is True
    sm._drop(host)


def test_fetch_file_fresh_connection_failure_not_retried(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sm, "get_ssh_client",
                        lambda h: calls.append(1) or FakeClient(sftp_exc=EOFError("dead")))
    host = {"address": "sftp-fresh-fail"}
    sm._drop(host)
    r = sm.fetch_file(host, "/remote/x", str(tmp_path / "o"))
    assert r["success"] is False
    assert len(calls) == 1
    sm._drop(host)


def test_fetch_file_transfer_timeout_is_result_not_retry(tmp_path, monkeypatch):
    c = FakeClient(sftp_get_exc=socket.timeout())
    made = []
    monkeypatch.setattr(sm, "get_ssh_client", lambda h: made.append(c) or c)
    host = {"address": "sftp-timeout"}
    sm._drop(host)
    r = sm.fetch_file(host, "/remote/x", str(tmp_path / "o"), timeout=5)
    assert r["success"] is False and "timed out" in r["error"]
    assert len(made) == 1                   # no second connection = no retry
    sm._drop(host)


def test_fetch_file_pool_disabled_closes_client(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "SSH_POOL_ENABLED", False)
    c = FakeClient()
    monkeypatch.setattr(sm, "get_ssh_client", lambda h: c)
    r = sm.fetch_file({"address": "x"}, "/remote/x", str(tmp_path / "o"))
    assert r["success"] is True
    assert c.closed is True
