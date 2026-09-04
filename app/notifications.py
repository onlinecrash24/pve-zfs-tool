"""Notification system supporting Telegram, Gotify, Matrix, and Email.

Supports optional PDF attachments for 'ai_report' events on channels that can
carry files: Email, Telegram (sendDocument), Matrix (media upload + m.file).
Gotify has no native file support, so the report is sent as text only.
"""

import base64
import hashlib
import json
import logging
import mimetypes
import os
import re
import smtplib
import ssl
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
import uuid
from datetime import datetime
from email.message import EmailMessage
from app.timezone import now_str as tz_now_str
from app.validators import validate_webhook_url

DATA_DIR = "/app/data"
NOTIFY_CONFIG_FILE = os.path.join(DATA_DIR, "notifications.json")

log = logging.getLogger(__name__)
_lock = threading.Lock()

DEFAULT_CONFIG = {
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "chat_id": "",
    },
    "gotify": {
        "enabled": False,
        "url": "",
        "token": "",
    },
    "matrix": {
        "enabled": False,
        "homeserver": "",
        "access_token": "",
        "room_id": "",
    },
    "email": {
        "enabled": False,
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
        "from_address": "",
        "to_addresses": "",
        "security": "starttls",
    },
    "webhook": {
        "enabled": False,
        "url": "",             # the URL is the credential (Slack, n8n, ... put a token in it)
        "template": "",        # JSON with {{placeholders}}; empty = generic preset
        "headers": "",         # one "Name: value" per line
        "attach_pdf": False,   # populate {{pdf_*}} for AI reports (can be large)
    },
    "events": {
        "scrub_started": True,
        "scrub_finished": True,
        "trim_started": True,
        "trim_finished": True,
        "rollback": True,
        "snapshot_created": True,
        "snapshot_deleted": True,
        "pool_error": True,
        "health_warning": True,
        "host_offline": True,
        "auto_snapshot": True,
        "ai_report": True,
        "replication_lag": True,
        "host_backup_failed": True,
    },
    # Pool fill levels that trigger health_warning. ZFS gets uncomfortable well
    # before it is actually full -- fragmentation rises and the allocator
    # switches strategy -- and snapshots can eat the remaining space quickly,
    # so the default warns early rather than at the last moment.
    # Pool fill levels, plus how old a guest's newest backup may get. A daily
    # backup job that misses one run should warn; a whole week without a backup
    # is a different kind of problem.
    "thresholds": {
        "capacity_warn_pct": 70,
        "capacity_crit_pct": 80,
        "backup_warn_hours": 36,
        "backup_crit_hours": 168,
    },
}


def _ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_config():
    _ensure_data_dir()
    if not os.path.exists(NOTIFY_CONFIG_FILE):
        return dict(DEFAULT_CONFIG)
    with open(NOTIFY_CONFIG_FILE, "r") as f:
        cfg = json.load(f)
    merged = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_CONFIG.items()}
    for key, val in cfg.items():
        if isinstance(val, dict) and key in merged and isinstance(merged[key], dict):
            merged[key].update(val)
        else:
            merged[key] = val
    # Merge in any new default events
    default_events = dict(DEFAULT_CONFIG["events"])
    default_events.update(cfg.get("events", {}))
    merged["events"] = default_events
    return merged


def save_config(config):
    _ensure_data_dir()
    with _lock:
        with open(NOTIFY_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def _send_telegram(bot_token, chat_id, message):
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            return {"success": body.get("ok", False), "detail": body}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"success": False, "detail": body}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def _send_telegram_document(bot_token, chat_id, file_bytes, filename, caption=""):
    """Send a file as a document via Telegram sendDocument (multipart/form-data)."""
    url = f"https://api.telegram.org/bot{bot_token}/sendDocument"
    boundary = "----ZFSToolBoundary" + os.urandom(8).hex()
    crlf = b"\r\n"
    parts = []

    def add_field(name, value):
        parts.append(f"--{boundary}".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        parts.append(b"")
        parts.append(str(value).encode("utf-8"))

    add_field("chat_id", chat_id)
    if caption:
        add_field("caption", caption[:1024])
        add_field("parse_mode", "HTML")

    # File part
    parts.append(f"--{boundary}".encode())
    parts.append(
        f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode()
    )
    parts.append(b"Content-Type: application/pdf")
    parts.append(b"")
    body = crlf.join(parts) + crlf + file_bytes + crlf + f"--{boundary}--{crlf.decode()}".encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            return {"success": result.get("ok", False), "detail": result}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        return {"success": False, "detail": body_text}
    except Exception as e:
        return {"success": False, "detail": str(e)}


# ---------------------------------------------------------------------------
# Gotify
# ---------------------------------------------------------------------------

def _send_gotify(server_url, token, title, message, priority=5):
    url = f"{server_url.rstrip('/')}/message"
    data = json.dumps({
        "title": title,
        "message": message,
        "priority": priority,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{url}?token={token}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"success": True, "detail": json.loads(resp.read())}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return {"success": False, "detail": body}
    except Exception as e:
        return {"success": False, "detail": str(e)}


# ---------------------------------------------------------------------------
# Matrix (Client-Server API v3 — r0 is deprecated since Synapse 1.48)
# ---------------------------------------------------------------------------

def _send_matrix(homeserver, access_token, room_id, message, html_message=None):
    """Send a text message to a Matrix room via the Client-Server API v3."""
    hs = homeserver.rstrip("/")
    room_encoded = urllib.parse.quote(room_id, safe="")
    txn_id = str(int(time.time() * 1000))
    url = f"{hs}/_matrix/client/v3/rooms/{room_encoded}/send/m.room.message/{txn_id}"

    body = {
        "msgtype": "m.text",
        "body": message,
    }
    if html_message:
        body["format"] = "org.matrix.custom.html"
        body["formatted_body"] = html_message

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return {"success": True, "detail": result}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        log.warning("Matrix send failed: HTTP %s — %s", e.code, body_text[:500])
        return {"success": False, "detail": f"HTTP {e.code}: {body_text[:500]}"}
    except Exception as e:
        log.warning("Matrix send failed: %s", e)
        return {"success": False, "detail": str(e)}


def _matrix_upload_media(homeserver, access_token, file_bytes, filename, content_type):
    """Upload a file to the Matrix media repo. Returns mxc:// URI or None."""
    hs = homeserver.rstrip("/")
    params = urllib.parse.urlencode({"filename": filename})
    url = f"{hs}/_matrix/media/v3/upload?{params}"
    req = urllib.request.Request(
        url,
        data=file_bytes,
        headers={
            "Content-Type": content_type,
            "Authorization": f"Bearer {access_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
            return result.get("content_uri")
    except Exception as e:
        log.warning("Matrix media upload failed: %s", e)
        return None


def _send_matrix_file(homeserver, access_token, room_id, file_bytes, filename,
                      content_type="application/pdf", caption=""):
    """Upload file to Matrix media and post as m.file message."""
    mxc_uri = _matrix_upload_media(homeserver, access_token, file_bytes, filename, content_type)
    if not mxc_uri:
        return {"success": False, "detail": "Media upload failed"}

    hs = homeserver.rstrip("/")
    room_encoded = urllib.parse.quote(room_id, safe="")
    txn_id = str(int(time.time() * 1000)) + "-file"
    url = f"{hs}/_matrix/client/v3/rooms/{room_encoded}/send/m.room.message/{txn_id}"

    body = {
        "msgtype": "m.file",
        "body": filename,
        "url": mxc_uri,
        "info": {
            "mimetype": content_type,
            "size": len(file_bytes),
        },
    }
    if caption:
        body["body"] = caption

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}",
        },
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return {"success": True, "detail": result}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        return {"success": False, "detail": f"HTTP {e.code}: {body_text[:500]}"}
    except Exception as e:
        return {"success": False, "detail": str(e)}


# ---------------------------------------------------------------------------
# Email (SMTP)
# ---------------------------------------------------------------------------

def _parse_recipients(to_addresses):
    """Split a comma or semicolon separated string into a list of addresses."""
    if not to_addresses:
        return []
    raw = to_addresses.replace(";", ",")
    return [a.strip() for a in raw.split(",") if a.strip()]


def _send_email(cfg, subject, body_text, body_html=None, attachments=None):
    """Send an email via SMTP.

    cfg: dict with smtp_host, smtp_port, smtp_user, smtp_password,
         from_address, to_addresses, security ('starttls'|'ssl'|'none').
    attachments: list of tuples (filename, bytes, content_type).
    """
    host = cfg.get("smtp_host", "").strip()
    port = int(cfg.get("smtp_port") or 587)
    user = cfg.get("smtp_user", "").strip()
    password = cfg.get("smtp_password", "")
    from_addr = cfg.get("from_address", "").strip() or user
    to_list = _parse_recipients(cfg.get("to_addresses", ""))
    security = (cfg.get("security") or "starttls").lower()

    if not host or not from_addr or not to_list:
        return {"success": False, "detail": "SMTP host, From or To addresses missing"}

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_list)
    msg.set_content(body_text)
    if body_html:
        msg.add_alternative(body_html, subtype="html")

    for att in attachments or []:
        fname, blob, ctype = att
        maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
        msg.add_attachment(blob, maintype=maintype, subtype=subtype or "octet-stream", filename=fname)

    try:
        if security == "ssl":
            ctx = ssl.create_default_context()
            with smtplib.SMTP_SSL(host, port, timeout=30, context=ctx) as s:
                if user:
                    s.login(user, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as s:
                s.ehlo()
                if security == "starttls":
                    ctx = ssl.create_default_context()
                    s.starttls(context=ctx)
                    s.ehlo()
                if user:
                    s.login(user, password)
                s.send_message(msg)
        return {"success": True, "detail": f"Sent to {len(to_list)} recipient(s)"}
    except Exception as e:
        log.warning("Email send failed: %s", e)
        return {"success": False, "detail": str(e)}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_VERDICT_RE = re.compile(
    r"\[\s*VERDICT\s*:\s*(ok|warn|crit)\s*\]", re.IGNORECASE
)
_CRIT_COUNT_RE = re.compile(
    r"\[\s*CRITICAL_FINDINGS\s*:\s*(\d+)\s*\]", re.IGNORECASE
)
_WARN_COUNT_RE = re.compile(
    r"\[\s*WARNINGS\s*:\s*(\d+)\s*\]", re.IGNORECASE
)


def _parse_llm_verdict(content: str):
    """Look for the structured verdict block the system prompt asks the
    LLM to emit at the end of the report. Returns (verdict, crit, warn)
    or None if the block is missing / malformed."""
    if not content:
        return None
    m = _VERDICT_RE.search(content)
    if not m:
        return None
    verdict = m.group(1).lower()
    crit = 0
    warn = 0
    mc = _CRIT_COUNT_RE.search(content)
    if mc:
        try:
            crit = int(mc.group(1))
        except ValueError:
            pass
    mw = _WARN_COUNT_RE.search(content)
    if mw:
        try:
            warn = int(mw.group(1))
        except ValueError:
            pass
    return verdict, crit, warn


def _heuristic_verdict(content: str):
    """Negation-aware fallback when the LLM forgets the verdict block.

    The previous naive substring counter raised false positives every time
    the LLM wrote "keine kritischen Probleme" or echoed the section header
    "❌ Kritisch" from the legend. Filter those before counting:
      - skip lines that look like headers (start with #, *, -, or are
        markdown emphasis)
      - skip lines containing a negation immediately before the keyword
        ("keine kritisch...", "no critical...", "kein kritischer", "0
        critical", etc.)
      - skip lines that mention the keyword as a definition or example
        rather than a finding ("kritisch:", "= kritisch", "z. B. kritisch")
    """
    if not content:
        return "ok", 0, 0
    crit_kw = ("critical", "kritisch", "🚨", "❌",
               "action required", "handlung erforderlich",
               "handlung zwingend", "immediate action")
    warn_kw = ("warning", "warnung", "achtung", "⚠")
    # Negations that disqualify a line. Order matters — we want the
    # negation to appear BEFORE the keyword.
    neg_prefixes = ("keine", "kein", "no ", "0 ", "zero", "nicht",
                    "not critical", "not crit", "not kritisch")
    crit = 0
    warn = 0
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        low = line.lower()
        # Strip markdown emphasis / list markers so the legend line
        # "**❌ Kritisch**" doesn't get a free hit.
        stripped = low.lstrip("#*-> ").strip("*_ ")
        # Skip pure legend / header lines that just enumerate emoji
        # meanings.
        if stripped in ("✅ ok", "⚠️ warnung", "⚠️ warning",
                        "❌ kritisch", "❌ critical"):
            continue
        # Skip the verdict block lines themselves
        if stripped.startswith("[verdict:") or stripped.startswith("[critical_findings") or stripped.startswith("[warnings"):
            continue
        is_negated = any(n in stripped for n in neg_prefixes)
        has_crit = any(k in low for k in crit_kw)
        has_warn = any(k in low for k in warn_kw)
        if has_crit and not is_negated:
            # Definitions of severity levels are not findings.
            if not (": kritisch" in low or "= kritisch" in low
                    or ": critical" in low or "= critical" in low):
                crit += 1
        if has_warn and not is_negated and not has_crit:
            if not (": warnung" in low or "= warnung" in low
                    or ": warning" in low or "= warning" in low):
                warn += 1
    if crit > 0:
        return "crit", crit, warn
    if warn > 0:
        return "warn", 0, warn
    return "ok", 0, 0


def _summarize_ai_report(content: str, lang: str = "en"):
    """Return (verdict, short_text) for an AI report body.

    Preferred path: the LLM ends its report with the structured
    ``[VERDICT: …]`` block our system prompts now demand. We trust
    those values directly. If the block is missing (legacy reports or
    a non-compliant model), fall back to a negation-aware keyword
    heuristic that ignores headers, legend lines and the verdict
    block itself -- avoids the "5 critical findings" false positive
    that triggered when the LLM wrote "keine kritischen Probleme".
    """
    parsed = _parse_llm_verdict(content)
    if parsed:
        verdict, crit_hits, warn_hits = parsed
    else:
        verdict, crit_hits, warn_hits = _heuristic_verdict(content or "")

    de = (lang or "").lower().startswith("de")
    if verdict == "crit":
        n = max(crit_hits, 1)
        txt = (f"🚨 Handlung zwingend nötig — {n} kritische(r) Hinweis(e) im Bericht."
               if de else
               f"🚨 Action required — {n} critical finding(s) in the report.")
    elif verdict == "warn":
        n = max(warn_hits, 1)
        txt = (f"⚠️ Aufmerksamkeit empfohlen — {n} Warnung(en) im Bericht."
               if de else
               f"⚠️ Attention recommended — {n} warning(s) in the report.")
    else:
        txt = ("✅ Alles im grünen Bereich — keine kritischen Hinweise gefunden."
               if de else
               "✅ All clear — no critical findings.")
    return verdict, txt


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------
#
# Generic JSON to any HTTP endpoint. The body is a user-editable JSON template
# with {{placeholders}}; the generic document and a Slack shape ship as
# starting templates. Anything that takes JSON -- Teams, Discord, Mattermost,
# n8n, a monitoring bridge -- is reachable without this file knowing about it.
#
# No request signing: with Slack, n8n and their kind the URL itself carries
# the token, so the URL is the credential and is treated like one.
#
# The template is parsed as JSON first and placeholders are substituted inside
# string values by walking the parsed object; the body is then re-serialised
# by json.dumps. A quote or newline in a message therefore cannot break the
# JSON, and a placeholder that is the WHOLE string value becomes its native
# type ("{{state_code}}" -> 2, not "2").

WEBHOOK_PLACEHOLDERS = (
    "title", "message", "event", "state", "severity", "state_code", "priority",
    "host", "key", "timestamp", "version", "pdf_filename", "pdf_base64",
)
# Digits included: {{pdf_base64}} silently stayed literal while this was
# [a-z_]+, and validate_template could not flag it because the pattern never
# saw it. A test now holds every listed placeholder against this pattern.
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z0-9_]+)\s*\}\}")
_RESERVED_HEADERS = {"content-length", "host", "x-pvezfs-event", "x-pvezfs-delivery"}

_GENERIC_BODY = {
    "source": "pve-zfs-tool",
    "version": "{{version}}",
    "event": "{{event}}",
    "state": "{{state}}",
    "severity": "{{severity}}",
    "state_code": "{{state_code}}",
    "priority": "{{priority}}",
    "title": "{{title}}",
    "message": "{{message}}",
    "host": "{{host}}",
    "key": "{{key}}",
    "timestamp": "{{timestamp}}",
}

WEBHOOK_PRESETS = {
    "generic": json.dumps(_GENERIC_BODY, indent=2),
    "slack": json.dumps({"text": "*{{title}}*\n{{message}}"}, indent=2),
}


def _severity(priority, state="new"):
    """(severity word, Nagios state code) from the priority every caller
    already passes: >= 8 critical, 6-7 warning, 5 info, <= 4 ok. A resolved
    event is ok whatever its priority says."""
    if state == "resolved":
        return "ok", 0
    try:
        p = int(priority)
    except (TypeError, ValueError):
        p = 5
    if p >= 8:
        return "critical", 2
    if p >= 6:
        return "warning", 1
    if p >= 5:
        return "info", 0
    return "ok", 0


def build_event(event_type, title, message, priority=5, state="new", key=None,
                host=None, pdf=None, timestamp=None):
    """The placeholder values for one notification, with native types.

    ``key`` is the correlation id a receiver pairs new/resolved on; callers
    that know their object pass it (host offline/online share one key).
    Otherwise it derives from event + title, which is stable but never pairs.
    """
    severity, code = _severity(priority, state)
    if not key:
        key = f"{event_type}:{hashlib.sha1((title or '').encode('utf-8')).hexdigest()[:12]}"
    fname, b64 = None, None
    if pdf:
        try:
            fname, data = pdf
            b64 = base64.b64encode(data).decode("ascii")
        except Exception:
            fname, b64 = None, None
    return {
        "title": title or "",
        "message": message or "",
        "event": event_type or "",
        "state": "resolved" if state == "resolved" else "new",
        "severity": severity,
        "state_code": code,
        "priority": int(priority) if str(priority).lstrip("-").isdigit() else 5,
        "host": host or None,
        "key": key,
        "timestamp": timestamp or datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": os.environ.get("APP_VERSION") or "dev",
        "pdf_filename": fname,
        "pdf_base64": b64,
    }


def sample_event():
    """What the preview and the test button render: a host-offline alert."""
    return build_event("host_offline", "Host Offline",
                       "pve1 (10.0.0.5) is not reachable via SSH.",
                       priority=8, state="new", key="host_offline:10.0.0.5", host="pve1")


def validate_template(text):
    """Parse a template, or raise ValueError saying exactly what is wrong.

    Two things are rejected at save time rather than at 3 a.m. when the first
    alert fires: JSON that does not parse (with the position), and a
    placeholder that is not on the list (a typo would otherwise ship as the
    literal text ``{{sevrity}}`` to every receiver, silently).
    """
    text = (text or "").strip() or WEBHOOK_PRESETS["generic"]
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON: {e.msg} (line {e.lineno}, column {e.colno})")
    unknown = sorted({m for m in _PLACEHOLDER_RE.findall(text)
                      if m not in WEBHOOK_PLACEHOLDERS})
    if unknown:
        raise ValueError("unknown placeholder(s): " + ", ".join("{{%s}}" % u for u in unknown)
                         + "; available: " + ", ".join("{{%s}}" % p for p in WEBHOOK_PLACEHOLDERS))
    return obj


def render_template(obj, values):
    """Substitute placeholders inside the parsed template's string values."""
    if isinstance(obj, dict):
        return {k: render_template(v, values) for k, v in obj.items()}
    if isinstance(obj, list):
        return [render_template(v, values) for v in obj]
    if isinstance(obj, str):
        whole = _PLACEHOLDER_RE.fullmatch(obj.strip())
        if whole and whole.group(1) in values:
            return values[whole.group(1)]           # native type, may be None
        return _PLACEHOLDER_RE.sub(
            lambda m: "" if values.get(m.group(1)) is None else str(values[m.group(1)]), obj)
    return obj


def render_preview(template_text):
    """The body a template produces for the sample event -- what the UI shows."""
    return render_template(validate_template(template_text), sample_event())


def parse_headers(text):
    """``Name: value`` per line -> dict. Names limited to token characters,
    and the headers this tool sets itself cannot be overridden."""
    out = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition(":")
        name, value = name.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Za-z0-9-]+", name):
            raise ValueError(f"invalid header line: {line[:60]!r}")
        if name.lower() in _RESERVED_HEADERS:
            raise ValueError(f"header {name} is set by the tool and cannot be overridden")
        if any(ord(c) < 32 for c in value):
            raise ValueError(f"header {name}: control characters are not allowed")
        out[name] = value
    return out


def _send_webhook(cfg, values):
    """POST the rendered template. Never raises: a bad receiver must not take
    the other channels down with it, and a webhook can point anywhere."""
    try:
        url = validate_webhook_url(cfg.get("url", ""))
        body = json.dumps(render_template(validate_template(cfg.get("template")), values),
                          ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "pve-zfs-tool",
        }
        headers.update(parse_headers(cfg.get("headers", "")))
        # Set after the user's headers so neither can be overridden.
        headers["X-PVEZFS-Event"] = values.get("event", "")
        headers["X-PVEZFS-Delivery"] = str(uuid.uuid4())
    except ValueError as e:
        return {"success": False, "detail": str(e)}
    except Exception as e:
        return {"success": False, "detail": f"could not build request: {e}"}
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return {"success": True, "detail": f"HTTP {resp.status}"}
    except urllib.error.HTTPError as e:
        return {"success": False,
                "detail": f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}"}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def send_notification(event_type, title, message, priority=5, pdf_attachment=None,
                      email_short=None, lang=None, state="new", key=None, host=None):
    """Send notification through all enabled channels if event type is active.

    pdf_attachment: optional tuple (filename, bytes) — sent as attachment for
    channels that support it (Email, Telegram, Matrix). Gotify remains text-only.

    email_short: when set, the email body uses this short text instead of the
    full ``message``. Useful for ai_report events where the PDF carries the
    full content and the email should only show a verdict. If not set but
    event_type=='ai_report' and a PDF is attached, a verdict is auto-derived
    from the message via _summarize_ai_report().
    """
    config = load_config()

    if not config.get("events", {}).get(event_type, False):
        return {"skipped": True, "reason": f"Event '{event_type}' is disabled"}

    results = {}
    timestamp = tz_now_str()
    full_message = f"{message}\n\n{timestamp}"

    pdf_filename = None
    pdf_bytes = None
    if pdf_attachment:
        try:
            pdf_filename, pdf_bytes = pdf_attachment
        except Exception:
            pdf_filename, pdf_bytes = None, None

    # Telegram
    tg = config.get("telegram", {})
    if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
        tg_text = f"<b>ZFS Tool \u2013 {title}</b>\n\n{full_message}"
        if pdf_bytes and pdf_filename:
            # Send short intro then document
            _send_telegram(tg["bot_token"], tg["chat_id"], tg_text[:4000])
            results["telegram"] = _send_telegram_document(
                tg["bot_token"], tg["chat_id"], pdf_bytes, pdf_filename,
                caption=f"<b>ZFS Tool \u2013 {title}</b>",
            )
        else:
            results["telegram"] = _send_telegram(tg["bot_token"], tg["chat_id"], tg_text)

    # Gotify
    gt = config.get("gotify", {})
    if gt.get("enabled") and gt.get("url") and gt.get("token"):
        results["gotify"] = _send_gotify(
            gt["url"], gt["token"], f"ZFS Tool \u2013 {title}", full_message, priority
        )

    # Matrix
    mx = config.get("matrix", {})
    if mx.get("enabled") and mx.get("homeserver") and mx.get("access_token") and mx.get("room_id"):
        plain = f"ZFS Tool \u2013 {title}\n\n{full_message}"
        html = f"<b>ZFS Tool \u2013 {title}</b><br><br>{full_message.replace(chr(10), '<br>')}"
        # Always send the text message first
        results["matrix"] = _send_matrix(
            mx["homeserver"], mx["access_token"], mx["room_id"], plain, html
        )
        # Then the PDF if present
        if pdf_bytes and pdf_filename:
            results["matrix_file"] = _send_matrix_file(
                mx["homeserver"], mx["access_token"], mx["room_id"],
                pdf_bytes, pdf_filename,
                content_type="application/pdf",
                caption=pdf_filename,
            )

    # Email
    em = config.get("email", {})
    if em.get("enabled") and em.get("smtp_host") and em.get("to_addresses"):
        attachments = []
        if pdf_bytes and pdf_filename:
            attachments.append((pdf_filename, pdf_bytes, "application/pdf"))

        # When a PDF is attached (or the caller passed an explicit short text)
        # the email body should be a brief verdict, not a copy of the report.
        verdict = "info"
        short = (email_short or "").strip()
        if not short and pdf_bytes and event_type == "ai_report":
            verdict, short = _summarize_ai_report(message, lang=lang or "en")

        subject = f"[ZFS Tool] {title}"
        if short:
            # Concise body: verdict line + reference to the attached PDF.
            de = (lang or "").lower().startswith("de")
            attach_line = ("Der vollständige Bericht liegt als PDF im Anhang."
                           if de else
                           "The full report is attached as a PDF.")
            body_text = f"{short}\n\n{attach_line}\n\n{timestamp}\n— ZFS Tool"
            verdict_color = {"crit": "#c0392b", "warn": "#d4a017",
                             "ok": "#1f8a4c"}.get(verdict, "#1a73a7")
            body_html = (
                f"<html><body style='font-family:sans-serif;color:#222'>"
                f"<h3 style='color:#1a73a7;margin-bottom:8px'>ZFS Tool &ndash; {title}</h3>"
                f"<p style='font-size:15px;color:{verdict_color};"
                f"font-weight:600;margin:6px 0 12px 0'>{short}</p>"
                f"<p style='font-size:13px;color:#444'>{attach_line}</p>"
                f"<p style='color:#888;font-size:12px;margin-top:18px'>{timestamp}</p>"
                f"</body></html>"
            )
        else:
            body_text = f"{full_message}\n\n— ZFS Tool"
            body_html = (
                f"<html><body style='font-family:sans-serif'>"
                f"<h3 style='color:#1a73a7'>ZFS Tool &ndash; {title}</h3>"
                f"<pre style='background:#f5f5f5;padding:12px;border-radius:6px;"
                f"white-space:pre-wrap'>{message}</pre>"
                f"<p style='color:#888;font-size:12px'>{timestamp}</p>"
                f"</body></html>"
            )
        results["email"] = _send_email(em, subject, body_text, body_html, attachments)

    # Webhook -- the message as structured JSON, not the "message + timestamp"
    # text the chat channels get; the receiver has the timestamp as a field.
    wh = config.get("webhook", {})
    if wh.get("enabled") and wh.get("url"):
        try:
            values = build_event(event_type, title, message, priority, state, key, host,
                                 pdf=pdf_attachment if wh.get("attach_pdf") else None)
            results["webhook"] = _send_webhook(wh, values)
        except Exception as e:      # belt and braces: _send_webhook already never raises
            results["webhook"] = {"success": False, "detail": str(e)}

    return results


# ---------------------------------------------------------------------------
# Secret masking (UI round-trip)
#
# GET /api/notifications/config masks secrets as "xx...yy". When the UI sends
# a value back (save OR test), a still-masked value means "unchanged" and must
# resolve to the stored secret -- otherwise the literal mask is used as the
# credential (Gotify then answers 401 and the user thinks their token broke).
# ---------------------------------------------------------------------------

def mask_secret(val):
    if not val or len(val) < 6:
        return val
    return val[:2] + "..." + val[-2:]


def is_masked(val):
    return bool(val) and "..." in val and len(val) < 32


def resolve_masked(val, stored):
    """Return the stored secret when the UI sent back a masked placeholder."""
    return stored if is_masked(val) else val


# ---------------------------------------------------------------------------
# Test endpoints
# ---------------------------------------------------------------------------

def test_telegram(bot_token, chat_id):
    return _send_telegram(bot_token, chat_id, "<b>ZFS Tool</b>\n\nTest notification \u2013 Telegram is working!")


def test_gotify(server_url, token):
    return _send_gotify(server_url, token, "ZFS Tool", "Test notification \u2013 Gotify is working!", priority=5)


def test_matrix(homeserver, access_token, room_id):
    plain = "ZFS Tool\n\nTest notification \u2013 Matrix is working!"
    html = "<b>ZFS Tool</b><br><br>Test notification \u2013 Matrix is working!"
    return _send_matrix(homeserver, access_token, room_id, plain, html)


def test_email(cfg):
    """Test SMTP delivery with a short message."""
    return _send_email(
        cfg,
        subject="[ZFS Tool] Test",
        body_text="Test notification — Email is working!",
        body_html="<p>Test notification — <b>Email is working!</b></p>",
    )


def test_webhook(cfg):
    """Deliver the sample event with the given webhook settings, so what the
    receiver gets is exactly what the preview showed."""
    return _send_webhook(cfg, sample_event())
