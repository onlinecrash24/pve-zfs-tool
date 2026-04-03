"""Notification system supporting Telegram and Gotify."""

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
    if "events" not in merged:
        merged["events"] = dict(DEFAULT_CONFIG["events"])
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
        tg_text = f"<b>ZFS Tool – {title}</b>\n\n{full_message}"
        results["telegram"] = _send_telegram(tg["bot_token"], tg["chat_id"], tg_text)

    # Gotify
    gt = config.get("gotify", {})
    if gt.get("enabled") and gt.get("url") and gt.get("token"):
        results["gotify"] = _send_gotify(gt["url"], gt["token"], f"ZFS Tool – {title}", full_message, priority)

    return results


def test_telegram(bot_token, chat_id):
    return _send_telegram(bot_token, chat_id, "<b>ZFS Tool</b>\n\nTest notification – Telegram is working!")


def test_gotify(server_url, token):
    return _send_gotify(server_url, token, "ZFS Tool", "Test notification – Gotify is working!", priority=5)
