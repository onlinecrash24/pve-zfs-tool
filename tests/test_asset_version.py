"""Static assets must be cache-busted by something that actually changes.

The templates carried a hardcoded ``?v=0.9.168`` that nobody ever bumped, so
after a deploy browsers kept serving the OLD app.js/i18n.js against the NEW
backend: new UI elements silently absent, new i18n keys rendered as raw key
names. The token is now derived from the files themselves.
"""

import os

from app import main as m


def _reset():
    m._ASSET_VER["v"] = ""
    m._ASSET_VER["ts"] = 0.0


def test_version_is_non_empty():
    _reset()
    assert m.asset_version()


def test_version_changes_when_a_static_file_changes(monkeypatch):
    _reset()
    monkeypatch.setattr(os.path, "getmtime", lambda p: 1_777_000_000.0)
    first = m.asset_version()
    _reset()
    monkeypatch.setattr(os.path, "getmtime", lambda p: 1_777_000_999.0)
    assert m.asset_version() != first        # a deploy busts the cache


def test_version_is_cached_within_the_window(monkeypatch):
    _reset()
    monkeypatch.setattr(os.path, "getmtime", lambda p: 1_777_000_000.0)
    first = m.asset_version()
    # a later mtime is NOT picked up immediately -- we don't stat on every request
    monkeypatch.setattr(os.path, "getmtime", lambda p: 1_999_999_999.0)
    assert m.asset_version() == first


def test_missing_files_fall_back(monkeypatch):
    _reset()

    def boom(_p):
        raise OSError("nope")

    monkeypatch.setattr(os.path, "getmtime", boom)
    assert m.asset_version() == "dev"


def test_templates_use_the_token():
    _reset()
    client = m.app.test_client()
    body = client.get("/login").get_data(as_text=True)
    assert "/static/css/style.css?v=" in body
    assert "?v=0.9.168" not in body          # the stale hardcoded token is gone
