"""The webhook channel: a JSON template rendered safely, signed, delivered once.

This is the first test in the suite that stubs urllib.request.urlopen itself
rather than one level up -- the point is to see the exact bytes and headers a
receiver would get.
"""

import hashlib
import hmac
import io
import json
import re
import urllib.error
import urllib.request

import pytest

import app.main as m
import app.notifications as n
from app import validators as v


# --- a receiver we can inspect ------------------------------------------------

class _Resp:
    status = 200

    def read(self):
        return b"{}"        # valid JSON: the Gotify sender parses its reply

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Seen(list):
    """The captured requests, plus `fail`: URL substring -> exception to raise."""
    fail = None


@pytest.fixture
def receiver(monkeypatch):
    """Capture every urlopen; optionally fail for a given URL substring."""
    seen = _Seen()
    seen.fail = {}

    def fake(req, timeout=None):
        seen.append(req)
        for needle, exc in seen.fail.items():
            if needle in req.full_url:
                raise exc
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return seen


def _headers(req):
    return {k.lower(): val for k, val in req.header_items()}


def _cfg(**over):
    cfg = {"enabled": True, "url": "https://hooks.example.test/zfs", "secret": "",
           "template": "", "headers": "", "attach_pdf": False}
    cfg.update(over)
    return cfg


# --- the generic document -----------------------------------------------------

def test_the_generic_document_has_every_field_with_its_native_type(receiver):
    ev = n.build_event("host_offline", "Host Offline", "pve1 down", priority=8,
                       state="new", key="host_offline:10.0.0.5", host="pve1",
                       timestamp="2026-09-03T21:14:00+02:00")
    assert n._send_webhook(_cfg(), ev)["success"] is True
    body = json.loads(receiver[0].data.decode("utf-8"))
    assert body == {
        "source": "pve-zfs-tool", "version": ev["version"],
        "event": "host_offline", "state": "new",
        "severity": "critical", "state_code": 2, "priority": 8,
        "title": "Host Offline", "message": "pve1 down",
        "host": "pve1", "key": "host_offline:10.0.0.5",
        "timestamp": "2026-09-03T21:14:00+02:00",
    }
    assert isinstance(body["state_code"], int) and isinstance(body["priority"], int)
    h = _headers(receiver[0])
    assert h["content-type"].startswith("application/json")
    assert h["x-pvezfs-event"] == "host_offline"
    assert re.fullmatch(r"[0-9a-f-]{36}", h["x-pvezfs-delivery"])


@pytest.mark.parametrize("priority,state,severity,code", [
    (9, "new", "critical", 2), (8, "new", "critical", 2),
    (7, "new", "warning", 1), (6, "new", "warning", 1),
    (5, "new", "info", 0), (4, "new", "ok", 0), (3, "new", "ok", 0),
    (9, "resolved", "ok", 0),          # resolved wins over any priority
    ("nonsense", "new", "info", 0),    # garbage priority -> the default
])
def test_severity_edges(priority, state, severity, code):
    assert n._severity(priority, state) == (severity, code)


def test_without_a_key_the_correlation_id_is_stable_but_never_pairs():
    a = n.build_event("scrub_started", "Scrub Started", "x")["key"]
    b = n.build_event("scrub_started", "Scrub Started", "y")["key"]
    c = n.build_event("scrub_started", "Scrub Finished", "x")["key"]
    assert a == b and a != c and a.startswith("scrub_started:")


# --- the signature, checked the way a receiver would ---------------------------

def test_the_signature_verifies_on_the_receiver_side(receiver):
    secret = "shared-secret-42"
    n._send_webhook(_cfg(secret=secret), n.sample_event())
    req = receiver[0]
    expected = "sha256=" + hmac.new(secret.encode(), req.data, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(expected, _headers(req)["x-pvezfs-signature"])


def test_no_secret_means_no_signature_header(receiver):
    n._send_webhook(_cfg(secret=""), n.sample_event())
    assert "x-pvezfs-signature" not in _headers(receiver[0])


def test_a_custom_header_cannot_override_the_signature(receiver):
    with pytest.raises(ValueError):
        n.parse_headers("X-PVEZFS-Signature: sha256=forged")


# --- the renderer: user text can never break the JSON --------------------------

def test_quotes_newlines_and_braces_in_a_message_do_not_break_the_json(receiver):
    nasty = 'He said "boom"\nthen {{ this }} and \\ a backslash'
    ev = n.build_event("rollback", 'Title "quoted"', nasty)
    assert n._send_webhook(_cfg(), ev)["success"] is True
    body = json.loads(receiver[0].data.decode("utf-8"))    # would raise if broken
    assert body["message"] == nasty
    assert body["title"] == 'Title "quoted"'


def test_a_whole_value_placeholder_keeps_its_type_an_embedded_one_becomes_text():
    ev = n.build_event("x", "t", "m", priority=8, host=None)
    out = n.render_template({"n": "{{state_code}}", "s": "code {{state_code}}",
                             "h": "{{host}}", "hs": "host={{host}}",
                             "p": " {{priority}} "}, ev)
    assert out == {"n": 2, "s": "code 2", "h": None, "hs": "host=", "p": 8}


def test_nesting_and_lists_are_rendered_all_the_way_down():
    ev = n.build_event("x", "T", "M", priority=6)
    tpl = {"blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "*{{title}}*"}},
                      {"fields": ["{{severity}}", "{{state_code}}"]}]}
    out = n.render_template(tpl, ev)
    assert out["blocks"][0]["text"]["text"] == "*T*"
    assert out["blocks"][1]["fields"] == ["warning", 1]


# --- validation at save time, not at 3 a.m. -------------------------------------

def test_invalid_json_is_rejected_with_a_position():
    with pytest.raises(ValueError) as e:
        n.validate_template('{"text": "{{title}}",}')
    assert "line 1" in str(e.value) and "column" in str(e.value)


def test_an_unknown_placeholder_is_rejected_by_name():
    with pytest.raises(ValueError) as e:
        n.validate_template('{"s": "{{sevrity}}"}')
    assert "{{sevrity}}" in str(e.value)


def test_an_empty_template_means_the_generic_preset():
    assert n.validate_template("") == json.loads(n.WEBHOOK_PRESETS["generic"])


@pytest.mark.parametrize("name", sorted(n.WEBHOOK_PRESETS))
def test_every_preset_validates_and_renders(name):
    out = n.render_template(n.validate_template(n.WEBHOOK_PRESETS[name]), n.sample_event())
    json.dumps(out)


def test_the_signl4_preset_pairs_new_and_resolved_on_one_external_id():
    tpl = n.validate_template(n.WEBHOOK_PRESETS["signl4"])
    down = n.render_template(tpl, n.build_event("host_offline", "Host Offline", "x", 8, "new",
                                                key="host_offline:10.0.0.5"))
    up = n.render_template(tpl, n.build_event("host_offline", "Host Back Online", "y", 3, "resolved",
                                              key="host_offline:10.0.0.5"))
    assert down["X-S4-Status"] == "new" and up["X-S4-Status"] == "resolved"
    assert down["X-S4-ExternalID"] == up["X-S4-ExternalID"] == "host_offline:10.0.0.5"


def test_the_monitoring_preset_sends_state_as_a_number():
    out = n.render_template(n.validate_template(n.WEBHOOK_PRESETS["monitoring"]),
                            n.build_event("pool_error", "Pool rpool: FAULTED", "x", 9, host="pve1"))
    assert out == {"host": "pve1", "service": "pool_error", "state": 2,
                   "output": "Pool rpool: FAULTED: x"}


# --- the two monitor pairs actually pass state and key --------------------------

def test_the_monitor_pairs_pass_state_and_key():
    # Static: each call is the text from its `send_notification(` up to the
    # next one, which is where its keyword arguments live. Guessing the pair
    # from a title ("Recovered", "Back Online") was rejected as the kind of
    # heuristic that breaks silently; this checks the explicit arguments stay.
    src = io.open("app/monitor.py", encoding="utf-8").read()
    chunks = src.split("send_notification(")[1:]
    for pair in ("host_offline", "pool_error"):
        calls = [c for c in chunks if re.match(r'\s*"%s"' % pair, c)]
        # Other calls for the same event may exist and stay unpaired (a pool
        # that vanished has no "resolved"); only the ones carrying `state=`
        # form the pair, and there must be exactly one new and one resolved
        # sharing one key.
        paired = [c for c in calls if re.search(r'state="', c)]
        states = [re.search(r'state="(\w+)"', c).group(1) for c in paired]
        keys = {re.search(r"key=(\w+)", c).group(1) for c in paired}
        assert states == ["new", "resolved"], f"{pair}: {states}"
        assert len(keys) == 1, f"{pair}: both calls must use the same key variable"


def test_every_listed_placeholder_is_one_the_pattern_can_see():
    # {{pdf_base64}} once stayed literal in every delivery because the pattern
    # had no digits, and validate_template could not flag what it never saw.
    for name in n.WEBHOOK_PLACEHOLDERS:
        assert n._PLACEHOLDER_RE.fullmatch("{{%s}}" % name), name


# --- url and header validation ------------------------------------------------

@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "https://",
                                 "hooks.example.test/x", "", "https://x y/"])
def test_bad_webhook_urls_are_refused(url):
    with pytest.raises(ValueError):
        v.validate_webhook_url(url)


@pytest.mark.parametrize("url", ["http://10.0.0.7:5678/webhook/zfs",   # LAN is the use case
                                 "https://hooks.slack.com/services/T/B/x"])
def test_good_webhook_urls_pass(url):
    assert v.validate_webhook_url(url) == url


def test_headers_parse_and_reject_what_they_should():
    assert n.parse_headers("Authorization: Bearer abc\n# comment\nX-Custom: 1") == {
        "Authorization": "Bearer abc", "X-Custom": "1"}
    # A CRLF cannot smuggle a second header: it only ever splits into lines,
    # and each line is validated on its own.
    assert n.parse_headers("X-A: 1\r\nX-B: 2") == {"X-A": "1", "X-B": "2"}
    for bad in ("no colon here", "Bad Name: x", "Host: evil", "Content-Length: 0"):
        with pytest.raises(ValueError):
            n.parse_headers(bad)


# --- through the routes ---------------------------------------------------------

@pytest.fixture
def client(monkeypatch):
    store = {"webhook": {"enabled": True, "url": "https://h/x", "secret": "OriginalSecret123",
                         "template": n.WEBHOOK_PRESETS["generic"], "headers": "", "attach_pdf": False}}
    saved = []
    monkeypatch.setattr(m, "load_notify_config", lambda: json.loads(json.dumps(store)))
    monkeypatch.setattr(m, "save_notify_config", lambda cfg: saved.append(cfg))
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["csrf_token"] = "t"
    c.saved = saved
    return c


def test_the_config_post_refuses_a_broken_template_and_stores_nothing(client):
    r = client.post("/api/notifications/config",
                    json={"webhook": {"enabled": True, "url": "https://h/x", "template": "{nope"}},
                    headers={"X-CSRF-Token": "t"})
    assert r.status_code == 400
    assert "webhook template" in r.get_json()["error"]
    assert client.saved == []


def test_the_masked_secret_round_trips_through_the_route(client):
    shown = n.mask_secret("OriginalSecret123")
    r = client.post("/api/notifications/config",
                    json={"webhook": {"enabled": True, "url": "https://h/x",
                                      "secret": shown, "template": ""}},
                    headers={"X-CSRF-Token": "t"})
    assert r.status_code == 200
    assert client.saved[0]["webhook"]["secret"] == "OriginalSecret123"


def test_the_get_masks_the_secret_and_ships_the_presets(client):
    body = client.get("/api/notifications/config").get_json()
    assert body["webhook"]["secret"] != "OriginalSecret123"
    assert n.is_masked(body["webhook"]["secret"])
    assert set(body["webhook_presets"]) == set(n.WEBHOOK_PRESETS)


def test_the_preview_route_renders_and_sends_nothing(client, receiver):
    r = client.post("/api/notifications/webhook/preview",
                    json={"template": n.WEBHOOK_PRESETS["slack"]}, headers={"X-CSRF-Token": "t"})
    assert r.get_json() == {"success": True, "body": {"text": "*Host Offline*\npve1 (10.0.0.5) is not reachable via SSH."}}
    assert receiver == []


def test_the_preview_route_reports_a_bad_template(client):
    r = client.post("/api/notifications/webhook/preview",
                    json={"template": '{"a": "{{nope}}"}'}, headers={"X-CSRF-Token": "t"})
    assert r.status_code == 400 and "{{nope}}" in r.get_json()["detail"]


# --- inside the fan-out --------------------------------------------------------

def _fanout_config(**wh):
    cfg = json.loads(json.dumps(n.DEFAULT_CONFIG))
    cfg["webhook"].update({"enabled": True, "url": "https://hooks.example.test/zfs", **wh})
    return cfg


def test_pdf_is_only_included_when_the_option_is_on(monkeypatch, receiver):
    tpl = json.dumps({"f": "{{pdf_filename}}", "b": "{{pdf_base64}}"})
    pdf = ("report.pdf", b"%PDF-1.4 fake")
    monkeypatch.setattr(n, "load_config", lambda: _fanout_config(attach_pdf=False, template=tpl))
    n.send_notification("ai_report", "Report", "done", pdf_attachment=pdf)
    assert json.loads(receiver[-1].data) == {"f": None, "b": None}
    monkeypatch.setattr(n, "load_config", lambda: _fanout_config(attach_pdf=True, template=tpl))
    n.send_notification("ai_report", "Report", "done", pdf_attachment=pdf)
    body = json.loads(receiver[-1].data)
    assert body["f"] == "report.pdf" and body["b"] == "JVBERi0xLjQgZmFrZQ=="


def test_a_dead_receiver_does_not_take_the_other_channels_down(monkeypatch, receiver):
    cfg = _fanout_config()
    cfg["gotify"].update({"enabled": True, "url": "https://gotify.example.test", "token": "tok"})
    monkeypatch.setattr(n, "load_config", lambda: cfg)
    receiver.fail["hooks.example.test"] = urllib.error.URLError("connection refused")
    results = n.send_notification("pool_error", "Pool x", "y", priority=9)
    assert results["webhook"]["success"] is False
    assert results["gotify"]["success"] is True
    assert sorted(results) == ["gotify", "webhook"]


def test_a_disabled_event_sends_nothing_to_the_webhook(monkeypatch, receiver):
    cfg = _fanout_config()
    cfg["events"]["scrub_started"] = False
    monkeypatch.setattr(n, "load_config", lambda: cfg)
    assert n.send_notification("scrub_started", "x", "y")["skipped"] is True
    assert receiver == []


def test_an_http_error_from_the_receiver_is_reported_not_raised(receiver):
    receiver.fail["hooks"] = urllib.error.HTTPError("https://hooks.example.test/zfs", 403, "nope",
                                                    {}, io.BytesIO(b"forbidden"))
    r = n._send_webhook(_cfg(), n.sample_event())
    assert r == {"success": False, "detail": "HTTP 403: forbidden"}
