"""Notification system supporting Telegram, Gotify, Matrix, and Email.

Supports optional PDF attachments for 'ai_report' events on channels that can
carry files: Email, Telegram (sendDocument), Matrix (media upload + m.file).
Gotify has no native file support, so the report is sent as text only.
"""

import json
import logging
import mimetypes
import os
import smtplib
import ssl
import threading
import urllib.request
import urllib.parse
import urllib.error
from email.message import EmailMessage
from app.timezone import now_str as tz_now_str

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
    "events": {
        "scrub_started": True,
        "scrub_finished": True,
        "rollback": True,
        "snapshot_created": True,
        "snapshot_deleted": True,
        "pool_error": True,
        "health_warning": True,
        "host_offline": True,
        "auto_snapshot": True,
        "ai_report": True,
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
    import time
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
    import time
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

def send_notification(event_type, title, message, priority=5, pdf_attachment=None):
    """Send notification through all enabled channels if event type is active.

    pdf_attachment: optional tuple (filename, bytes) — sent as attachment for
    channels that support it (Email, Telegram, Matrix). Gotify remains text-only.
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
        subject = f"[ZFS Tool] {title}"
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

    return results


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
