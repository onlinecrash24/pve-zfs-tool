"""Security report on v0.9.934, stage 1: the fixes and the guards that keep
them fixed.

1. No data inside inline event handlers (XSS through file names).
2. A placeholder or short ADMIN_PASSWORD refuses to start.
3. The crontab rewrite fails closed, matches the tool's own line only, and
   keeps a backup; mutations need a source.
4. A pinned host key is enforced; the ad-hoc password path never drops a
   registered host's pin; pins are stored under the name paramiko looks up.
6. No AI request leaves without a key.
"""

import io
import os
import re

import pytest

from app import ai_reports as ai
from app import replication as r
from app import ssh_manager as sm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _js():
    return io.open(os.path.join(ROOT, "app", "static", "js", "app.js"), encoding="utf-8").read()


# --- 1. XSS ---------------------------------------------------------------------

def test_no_inline_handler_carries_data():
    js = _js()
    offenders = [ln for ln in js.splitlines() if re.search(r'on[a-z]+="[A-Za-z_]+\([^"]*\$\{', ln)]
    assert offenders == [], offenders


def test_escape_attr_covers_the_single_quote():
    js = _js()
    m = re.search(r"function escapeAttr\(s\) \{(.*?)\n\}", js, re.S)
    assert m and "&#39;" in m.group(1)


def test_every_data_action_has_a_global_handler():
    js = _js()
    names = re.search(r"const DATA_ACTIONS = new Set\(\[(.*?)\]\);", js, re.S)
    assert names
    for n in re.findall(r'"([A-Za-z]+)"', names.group(1)):
        assert re.search(rf"^(?:window\.{n} = |async function {n}\(|function {n}\()", js, re.M), n
    assert js.count("dataAction(") >= 16


# --- 2. password ----------------------------------------------------------------

def test_admin_password_rules():
    import app.main as m
    assert m.admin_password_problem("") == "is empty"
    assert "placeholder" in m.admin_password_problem("password")
    assert "placeholder" in m.admin_password_problem("your-strong-password")
    assert "shorter" in m.admin_password_problem("Tr0ub4dor")
    assert m.admin_password_problem("correct horse battery staple") is None
    assert m.MIN_PASSWORD_LENGTH == 12


# --- 3. crontab -----------------------------------------------------------------

CFG = "/etc/bashclub/10.0.0.5.conf"
OWN = "17 * * * * bashclub-zsync -c /etc/bashclub/10.0.0.5.conf >> /var/log/bashclub-zsync/zsync.log 2>&1"


@pytest.mark.parametrize("line,hit", [
    (OWN, True),
    ("0 3 * * * /usr/bin/bashclub-zsync -c /etc/bashclub/10.0.0.5.conf", True),
    ("0 3 * * * bashclub-zsync -c /etc/bashclub/10.0.0.5.conf -d >> /tmp/x.log", False),   # foreign flag
    ("0 3 * * * bashclub-zsync -c /etc/bashclub/10.0.0.5.conf.alt >> /tmp/x.log", False),  # other file
    ("#17 * * * * bashclub-zsync -c /etc/bashclub/10.0.0.5.conf >> /tmp/x.log", False),     # commented
    ("0 3 * * * bashclub-zsync -c /etc/bashclub/10.0.0.50.conf", False),                   # other pair
    ("0 3 * * * bashclub-zsync -c /etc/bashclub/zsync.conf", False),                       # legacy default
])
def test_only_the_tool_s_own_line_matches(line, hit):
    pat = r.own_cron_line_pattern(CFG, flavor="python")
    assert bool(re.search(pat, line)) is hit, line


def test_the_ere_and_python_flavours_are_one_template():
    ere = r.own_cron_line_pattern(CFG)
    assert ere == r.own_cron_line_pattern(CFG, flavor="python").replace(r"\s", "[[:space:]]").replace(
        re.escape(CFG), r._ere_escape(CFG))
    assert r"\." in ere and "[[:space:]]" in ere


def test_the_rewrite_reads_first_and_stops_on_a_read_error():
    s = r._crontab_rewrite_script(CFG)
    assert 'if crontab -l > "$CUR" 2> "$ERR"; then :;' in s
    assert "no crontab for" in s                       # the one failure that means "empty"
    assert "__CRONTAB_UNREADABLE__" in s and "exit 1" in s
    assert "crontab.bak-" in s                         # backup of what was there
    assert "(crontab -l 2>/dev/null || true)" not in s  # the fail-open form is gone
    assert 'grep -vE -- "$RE" "$CUR"' in s
    assert s.index("crontab -l") < s.index('crontab "$TMP"')


def test_set_and_remove_cron_use_the_rewrite_and_need_a_source(monkeypatch):
    cmds = []
    monkeypatch.setattr(r, "run_command", lambda h, c, **k: cmds.append(c) or {"success": True, "stdout": "__OK__"})
    assert r.set_cron({"address": "h"}, "17 * * * *", source="root@10.0.0.5")["success"]
    assert r.remove_cron({"address": "h"}, source="root@10.0.0.5")["success"]
    for c in cmds:
        assert "__CRONTAB_UNREADABLE__" in c and "grep -vF" not in c
    assert not r.set_cron({"address": "h"}, "17 * * * *")["success"]
    assert not r.remove_cron({"address": "h"})["success"]
    assert not r.delete_config({"address": "h"}, None)["success"]
    assert len(cmds) == 2                                # the refusals sent nothing


def test_import_side_removal_uses_the_same_pattern(monkeypatch):
    cmds = []
    monkeypatch.setattr(r, "run_command", lambda h, c, **k: cmds.append(c) or {"success": True, "stdout": "__OK__"})
    r._remove_cron_line({"address": "h"}, "crontab", CFG)
    r._remove_cron_line({"address": "h"}, "/etc/cron.d/zsync", CFG)
    assert "__CRONTAB_UNREADABLE__" in cmds[0]
    assert 'grep -vE -- "$RE" "$F"' in cmds[1] and ".bak-" in cmds[1]


# --- 4. host keys ---------------------------------------------------------------

def test_known_hosts_name_follows_paramiko():
    assert sm.known_hosts_name("10.0.0.5") == "10.0.0.5"
    assert sm.known_hosts_name("10.0.0.5", 22) == "10.0.0.5"
    assert sm.known_hosts_name("10.0.0.5", 2222) == "[10.0.0.5]:2222"


@pytest.mark.parametrize("registered,pinned,password,want", [
    (True, True, False, "reject"),     # the normal case: pinned key must match
    (True, True, True, "reject"),      # a typed password never loosens a pin
    (True, False, False, "tofu"),      # old entry without a pin: pin now
    (True, False, True, "tofu"),
    (False, False, True, "adhoc"),     # DR to an unregistered box
    (False, True, True, "adhoc"),      # a stale probe entry does not count as a pin
    (False, False, False, "tofu"),     # probe before add
])
def test_host_key_policy(registered, pinned, password, want):
    assert sm.host_key_policy(registered, pinned, password) == want


def test_the_source_never_forgets_a_pin_for_a_password():
    src = io.open(os.path.join(ROOT, "app", "ssh_manager.py"), encoding="utf-8").read()
    body = src[src.index("def get_ssh_client"):src.index("def ", src.index("def get_ssh_client") + 10)]
    assert "WarningPolicy" not in body
    assert "if password and not registered:" in body and "_forget_host_key(address)" in body


# --- 6. AI ----------------------------------------------------------------------

def test_no_request_without_a_key(monkeypatch):
    for fn in ("_call_openai", "_call_anthropic", "_call_ollama"):
        monkeypatch.setattr(ai, fn, lambda *a, **k: pytest.fail(f"{fn} must not be called"))
    monkeypatch.setattr(ai, "load_config", lambda: dict(ai.DEFAULT_CONFIG))
    res = ai.call_llm([{"role": "user", "content": "hi"}])
    assert not res["success"] and "no API key" in res["error"]
    res = ai.test_connection()
    assert not res["success"] and "nothing was sent" in res["error"]


def test_provider_problem_cases():
    assert ai.provider_problem({"provider": "openai", "openai": {"api_key": "sk-x"}}) is None
    assert ai.provider_problem({"provider": "anthropic", "anthropic": {"api_key": ""}})
    assert ai.provider_problem({"provider": "custom", "custom": {"base_url": ""}})
    assert ai.provider_problem({"provider": "custom", "custom": {"base_url": "http://lmstudio:1234/v1"}}) is None
    assert ai.provider_problem({"provider": "ollama", "ollama": {"base_url": "http://localhost:11434"}}) is None
