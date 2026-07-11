"""Audit log retention: cleanup_old trims by age and can be disabled."""

import time
import pytest

from app import database, audit


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(database, "_initialized", False)
    database.init_db()
    yield


def _insert(ts, action="test.action"):
    conn = database.get_conn()
    try:
        conn.execute(
            "INSERT INTO audit_log (timestamp, user, ip, host, action, target, "
            "details, success) VALUES (?,?,?,?,?,?,?,?)",
            (ts, "u", "", "", action, "", "", 1),
        )
        conn.commit()
    finally:
        conn.close()


def test_cleanup_removes_old_keeps_recent(temp_db):
    now = int(time.time())
    _insert(now)                       # recent
    _insert(now - 100 * 86400)         # 100 days — within default 365
    _insert(now - 400 * 86400)         # 400 days — older than 365
    deleted = audit.cleanup_old(365)
    assert deleted == 1
    assert audit.count() == 2


def test_cleanup_disabled_when_retention_not_positive(temp_db):
    now = int(time.time())
    _insert(now - 400 * 86400)
    assert audit.cleanup_old(0) == 0        # disabled
    assert audit.cleanup_old(-5) == 0
    assert audit.count() == 1               # nothing trimmed


def test_cleanup_custom_window(temp_db):
    now = int(time.time())
    _insert(now - 5 * 86400)
    _insert(now - 20 * 86400)
    # keep only the last 7 days
    assert audit.cleanup_old(7) == 1
    assert audit.count() == 1
