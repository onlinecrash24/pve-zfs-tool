"""Notification system supporting Telegram, Gotify, and Matrix."""

import json
import os
import threading
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime

DATA_DIR = "/app/data"
NOTIFY_CONFIG_FILE = os.path.join(DATA_DIR, "notifications.json")

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
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    # Ensure all default event types exist (merges new events into old configs)
    default_events = dict(DEFAULT_CONFIG["events"])
    saved_events = cfg.get("events", {})
    default_events.update(saved_events)
    merged["events"] = default_events
    # Ensure matrix section exists for older configs
    if "matrix" not in merged:
        merged["matrix"] = dict(DEFAULT_CONFIG["matrix"])
    return merged


def save_config(config):
    _ensure_data_dir()
    with _lock:
        with open(NOTIFY_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)


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


def _send_matrix(homeserver, access_token, room_id, message, html_message=None):
    """Send a message to a Matrix room via the Client-Server API."""
    import time
    hs = homeserver.rstrip("/")
    room_encoded = urllib.parse.quote(room_id, safe="")
    txn_id = str(int(time.time() * 1000))
    url = f"{hs}/_matrix/client/r0/rooms/{room_encoded}/send/m.room.message/{txn_id}"

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
        return {"success": False, "detail": body_text}
    except Exception as e:
        return {"success": False, "detail": str(e)}


def send_notification(event_type, title, message, priority=5):
    """Send notification through all enabled channels if event type is active."""
    config = load_config()

    if not config.get("events", {}).get(event_type, False):
        return {"skipped": True, "reason": f"Event '{event_type}' is disabled"}

    results = {}
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"{message}\n\n{timestamp}"

    # Telegram
    tg = config.get("telegram", {})
    if tg.get("enabled") and tg.get("bot_token") and tg.get("chat_id"):
        tg_text = f"<b>ZFS Tool \u2013 {title}</b>\n\n{full_message}"
        results["telegram"] = _send_telegram(tg["bot_token"], tg["chat_id"], tg_text)

    # Gotify
    gt = config.get("gotify", {})
    if gt.get("enabled") and gt.get("url") and gt.get("token"):
        results["gotify"] = _send_gotify(gt["url"], gt["token"], f"ZFS Tool \u2013 {title}", full_message, priority)

    # Matrix
    mx = config.get("matrix", {})
    if mx.get("enabled") and mx.get("homeserver") and mx.get("access_token") and mx.get("room_id"):
        plain = f"ZFS Tool \u2013 {title}\n\n{full_message}"
        html = f"<b>ZFS Tool \u2013 {title}</b><br><br>{full_message.replace(chr(10), '<br>')}"
        results["matrix"] = _send_matrix(mx["homeserver"], mx["access_token"], mx["room_id"], plain, html)

    return results


def test_telegram(bot_token, chat_id):
    return _send_telegram(bot_token, chat_id, "<b>ZFS Tool</b>\n\nTest notification \u2013 Telegram is working!")


def test_gotify(server_url, token):
    return _send_gotify(server_url, token, "ZFS Tool", "Test notification \u2013 Gotify is working!", priority=5)


def test_matrix(homeserver, access_token, room_id):
    plain = "ZFS Tool\n\nTest notification \u2013 Matrix is working!"
    html = "<b>ZFS Tool</b><br><br>Test notification \u2013 Matrix is working!"
    return _send_matrix(homeserver, access_token, room_id, plain, html)
