"""Audit log for destructive actions.

Writes are short JSON blobs to an indexed SQLite table; the Flask layer
calls log_action() after every state-changing endpoint. The UI reads
entries via query() to render the Audit Log view.
"""

import json
import logging
import time

try:
    from flask import session, request, has_request_context
except Exception:  # pragma: no cover — module is importable without Flask
    session = None
    request = None
    def has_request_context():
        return False

from app.database import get_conn

log = logging.getLogger(__name__)

# Retention — keep everything unless explicitly trimmed. Queries paginate.
DEFAULT_QUERY_LIMIT = 200
MAX_QUERY_LIMIT = 5000


def _current_user():
    if not has_request_context():
        return "system"
    try:
        if session and session.get("authenticated"):
            return session.get("username") or "admin"
    except Exception:
        pass
    return "anonymous"


def _current_ip():
    if not has_request_context():
        return ""
    try:
        return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or request.remote_addr or "")
    except Exception:
        return ""


def log_action(action, target="", details=None, success=True,
               host="", user=None, ip=None):
    """Record a single audit entry.

    Parameters
    ----------
    action : str
        Short action code, e.g. ``snapshot.destroy``, ``pool.scrub``,
        ``login.success``, ``config.notifications.save``.
    target : str
        The object affected (pool, dataset, snapshot path, host address…).
    details : str | dict | list | None
        Free-form context. Dicts/lists are JSON-encoded.
    success : bool
        Whether the underlying operation succeeded.
    host : str
        Proxmox host address the action targeted, if applicable.
    user, ip : str | None
        Override the detected user/IP (mainly for tests).
    """
    try:
        if user is None:
            user = _current_user()
        if ip is None:
            ip = _current_ip()
        if isinstance(details, (dict, list)):
            try:
                details = json.dumps(details, ensure_ascii=False, default=str)
            except Exception:
                details = str(details)
        elif details is not None and not isinstance(details, str):
            details = str(details)

        conn = get_conn()
        try:
            conn.execute(
                """INSERT INTO audit_log
                   (timestamp, user, ip, host, action, target, details, success)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (int(time.time()), user or "", ip or "", host or "",
                 action, target or "", details or "",
                 1 if success else 0),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        # Never let audit failures break the main flow
        log.error("audit log_action failed for %s: %s", action, e)


def query(limit=DEFAULT_QUERY_LIMIT, offset=0,
          action=None, host=None, user=None, since=None, until=None,
          only_failures=False):
    """Return audit entries sorted newest-first (list of dicts)."""
    limit = max(1, min(int(limit or DEFAULT_QUERY_LIMIT), MAX_QUERY_LIMIT))
    offset = max(0, int(offset or 0))

    q = "SELECT * FROM audit_log WHERE 1=1"
    args = []
    if action:
        q += " AND action=?"
        args.append(action)
    if host:
        q += " AND host=?"
        args.append(host)
    if user:
        q += " AND user=?"
        args.append(user)
    if since:
        q += " AND timestamp >= ?"
        args.append(int(since))
    if until:
        q += " AND timestamp <= ?"
        args.append(int(until))
    if only_failures:
        q += " AND success=0"
    q += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    args.extend([limit, offset])

    conn = get_conn()
    try:
        rows = conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count(action=None, host=None, user=None, since=None, only_failures=False):
    q = "SELECT COUNT(*) AS n FROM audit_log WHERE 1=1"
    args = []
    if action:
        q += " AND action=?"
        args.append(action)
    if host:
        q += " AND host=?"
        args.append(host)
    if user:
        q += " AND user=?"
        args.append(user)
    if since:
        q += " AND timestamp >= ?"
        args.append(int(since))
    if only_failures:
        q += " AND success=0"
    conn = get_conn()
    try:
        return conn.execute(q, args).fetchone()["n"]
    finally:
        conn.close()


def distinct_actions():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT action FROM audit_log ORDER BY action"
        ).fetchall()
        return [r["action"] for r in rows]
    finally:
        conn.close()
