"""Security stage 2, the web layer (review of 18.10., re-checked 07.11.).

M   GET /api/inventory/matrix started a task without a CSRF token, and
    start_task had neither a ceiling nor a dedupe.
7   The login brake counted per address only; guessing spread over many
    addresses never tripped it.
12  Logout cleared the browser's copy of a signed cookie; a captured cookie
    kept working until it expired. No HSTS with FORCE_HTTPS.
CSP script-src carried 'unsafe-inline' for two inline blocks and a handful
    of onclick attributes.
"""

import os
import re
import threading

import pytest

import app.main as m
from app import tasks

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _client(sid=True):
    m.app.config["TESTING"] = True
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
        if sid:
            s["sid"] = m.session_open("admin")
        s["csrf_token"] = "tok"
    return c


# --- M: the matrix is a POST, one per host, and tasks have a ceiling ------------------

@pytest.fixture
def blocked_matrix(monkeypatch):
    tasks._TASKS.clear()
    gate = threading.Event()

    def slow(progress, host):
        gate.wait(5)
        return {"guests": []}
    monkeypatch.setattr(m, "collect_matrix_task", slow)
    yield gate
    gate.set()
    tasks._TASKS.clear()


def test_the_matrix_no_longer_answers_get():
    assert _client().get("/api/inventory/matrix?host=x").status_code == 405


def test_the_matrix_needs_the_csrf_token(blocked_matrix):
    r = _client().post("/api/inventory/matrix", json={"host": "10.0.0.1"})
    assert r.status_code == 403


def test_a_second_click_gets_the_running_task(blocked_matrix):
    c = _client()
    h = {"X-CSRF-Token": "tok"}
    a = c.post("/api/inventory/matrix", json={"host": "10.0.0.1"}, headers=h).get_json()
    b = c.post("/api/inventory/matrix", json={"host": "10.0.0.1"}, headers=h).get_json()
    other = c.post("/api/inventory/matrix", json={"host": "10.0.0.2"}, headers=h).get_json()
    assert b["task_id"] == a["task_id"] and b["already_running"] is True
    assert other["task_id"] != a["task_id"] and "already_running" not in other


def test_the_task_ceiling_answers_429(blocked_matrix, monkeypatch):
    monkeypatch.setattr(tasks, "MAX_RUNNING_TASKS", 2)
    c = _client()
    h = {"X-CSRF-Token": "tok"}
    assert c.post("/api/inventory/matrix", json={"host": "a"}, headers=h).status_code == 200
    assert c.post("/api/inventory/matrix", json={"host": "b"}, headers=h).status_code == 200
    r = c.post("/api/inventory/matrix", json={"host": "c"}, headers=h)
    assert r.status_code == 429 and "already running" in r.get_json()["error"]


def test_start_task_raises_at_the_ceiling(monkeypatch):
    tasks._TASKS.clear()
    monkeypatch.setattr(tasks, "MAX_RUNNING_TASKS", 1)
    gate = threading.Event()
    tasks.start_task("t1", lambda p: gate.wait(5))
    try:
        with pytest.raises(tasks.TaskLimitError):
            tasks.start_task("t2", lambda p: None)
    finally:
        gate.set()
        tasks._TASKS.clear()


# --- 7: a second counter per account name ------------------------------------------------

@pytest.fixture
def login(monkeypatch):
    audits = []
    monkeypatch.setattr(m, "audit_log", lambda action, **k: audits.append((action, k.get("details"))))
    m._login_attempts.clear()
    m._login_user_attempts.clear()
    c = m.app.test_client()

    def attempt(addr, user="admin", password="definitely-wrong"):
        return c.post("/api/login", json={"username": user, "password": password},
                      environ_base={"REMOTE_ADDR": addr})
    attempt.audits = audits
    yield attempt
    m._login_attempts.clear()
    m._login_user_attempts.clear()


def test_guessing_spread_over_many_addresses_is_stopped(login):
    codes = [login(f"198.51.100.{i}").status_code for i in range(m.MAX_USER_LOGIN_ATTEMPTS + 1)]
    assert codes[:m.MAX_USER_LOGIN_ATTEMPTS] == [401] * m.MAX_USER_LOGIN_ATTEMPTS
    r = login("198.51.100.250")
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    assert [a for a, _ in login.audits].count("login.lockout") == 1
    # Not even the right password gets in while the account is locked.
    assert login("198.51.100.251", password=os.environ["ADMIN_PASSWORD"]).status_code == 429


def test_the_account_key_ignores_case_and_padding(login):
    for i in range(m.MAX_USER_LOGIN_ATTEMPTS):
        login(f"198.51.100.{i}", user=("ADMIN" if i % 2 else " admin "))
    assert login("198.51.100.99").status_code == 429


def test_the_per_address_brake_is_unchanged(login):
    codes = [login("203.0.113.7").status_code for _ in range(m.MAX_LOGIN_ATTEMPTS + 1)]
    assert codes == [401] * m.MAX_LOGIN_ATTEMPTS + [429]


def test_success_clears_both_counters(login):
    for i in range(3):
        login(f"198.51.100.{i}")
    assert login("198.51.100.1", password=os.environ["ADMIN_PASSWORD"]).status_code == 200
    assert m._login_user_attempts == {}
    assert "198.51.100.1" not in m._login_attempts


# --- 12: sessions live on the server; HSTS ----------------------------------------------

def test_a_cookie_without_a_server_session_is_not_logged_in():
    assert _client(sid=False).get("/api/system/notice").status_code == 401


def test_logout_kills_the_captured_cookie(monkeypatch):
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    c = _client()
    assert c.get("/api/system/notice").status_code == 200
    with c.session_transaction() as s:
        captured = dict(s)
    assert c.post("/api/logout", headers={"X-CSRF-Token": "tok"}).status_code == 200
    replay = m.app.test_client()
    with replay.session_transaction() as s:
        s.update(captured)                      # the stolen copy, signed and intact
    assert replay.get("/api/system/notice").status_code == 401


def test_one_logout_does_not_end_another_session(monkeypatch):
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    a, b = _client(), _client()
    a.post("/api/logout", headers={"X-CSRF-Token": "tok"})
    assert b.get("/api/system/notice").status_code == 200


def test_an_idle_session_expires(monkeypatch):
    c = _client()
    with c.session_transaction() as s:
        sid = s["sid"]
    m._sessions[sid]["seen"] -= m.app.permanent_session_lifetime.total_seconds() + 1
    assert c.get("/api/system/notice").status_code == 401
    assert sid not in m._sessions


def test_login_issues_a_fresh_server_session(monkeypatch):
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    m._login_attempts.clear()
    c = m.app.test_client()
    r = c.post("/api/login", json={"username": m.ADMIN_USER, "password": os.environ["ADMIN_PASSWORD"]})
    assert r.status_code == 200
    with c.session_transaction() as s:
        assert s["sid"] in m._sessions


def test_hsts_only_with_force_https(monkeypatch):
    c = m.app.test_client()
    monkeypatch.delenv("FORCE_HTTPS", raising=False)
    assert "Strict-Transport-Security" not in c.get("/login").headers
    monkeypatch.setenv("FORCE_HTTPS", "true")
    assert c.get("/login").headers["Strict-Transport-Security"] == "max-age=31536000"


# --- CSP: script-src 'self', and nothing inline that would need more ----------------------

def test_the_policy_allows_no_inline_script():
    csp = m.app.test_client().get("/login").headers["Content-Security-Policy"]
    script = re.search(r"script-src ([^;]+)", csp).group(1).split()
    assert script == ["'self'"]


@pytest.mark.parametrize("name", ["index.html", "login.html"])
def test_templates_carry_no_inline_script_or_handler(name):
    with open(os.path.join(ROOT, "app", "templates", name), encoding="utf-8") as f:
        html = f.read()
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script> block"
    assert not re.search(r"\son[a-z]+\s*=", html), "inline event handler"
    assert "javascript:" not in html


def test_app_js_builds_no_inline_handlers():
    with open(os.path.join(ROOT, "app", "static", "js", "app.js"), encoding="utf-8") as f:
        js = f.read()
    offenders = [ln.strip() for ln in js.splitlines()
                 if re.search(r"""\son(click|change|input|submit|load|error|mouse[a-z]+|key[a-z]+)=["']""", ln)]
    assert offenders == []


def test_login_page_loads_its_script_from_a_file():
    body = m.app.test_client().get("/login").get_data(as_text=True)
    assert re.search(r'<script src="/static/js/login\.js\?v=[^"]+"></script>', body)
    assert os.path.exists(os.path.join(ROOT, "app", "static", "js", "login.js"))
