"""The report-logo endpoints as the settings page uses them.

test_ai_pdf_logo covers validation and geometry; this covers the HTTP layer:
that an upload actually reaches save_custom_logo, that a too-large upload is
rejected before the body is even read, and that DELETE reports a genuine no-op
rather than claiming success.
"""

import io

import pytest

from app import ai_pdf as pdf
from app import main as m


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pdf, "CUSTOM_LOGO_PATH", str(tmp_path / "report_logo.png"))
    # The repo checkout always ships the bundled logo, so "no logo at all"
    # would otherwise be unreachable through these routes -- point IMG_DIR at
    # an empty directory to make that state testable too.
    monkeypatch.setattr(pdf, "IMG_DIR", str(tmp_path / "img"))
    monkeypatch.setattr(m, "audit_log", lambda *a, **k: None)
    m.app.config["TESTING"] = True
    c = m.app.test_client()
    with c.session_transaction() as s:
        s["authenticated"] = True
        s["csrf_token"] = "tok"
    return c


def _png_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (20, 20), (5, 5, 5)).save(buf, format="PNG")
    buf.seek(0)
    return buf


def test_status_reports_no_custom_logo_initially(client):
    r = client.get("/api/ai/report-logo/status")
    assert r.get_json() == {"has_custom_logo": False}


def test_get_returns_404_when_nothing_is_set(client):
    assert client.get("/api/ai/report-logo").status_code == 404


def test_upload_stores_the_logo_and_flips_status(client):
    r = client.post("/api/ai/report-logo",
                    data={"logo": (_png_bytes(), "mylogo.png")},
                    content_type="multipart/form-data",
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 200
    assert r.get_json()["success"] is True
    assert client.get("/api/ai/report-logo/status").get_json()["has_custom_logo"] is True

    img = client.get("/api/ai/report-logo")
    assert img.status_code == 200
    assert img.mimetype == "image/png"


def test_upload_without_a_file_is_a_client_error(client):
    r = client.post("/api/ai/report-logo", data={},
                    content_type="multipart/form-data",
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 400
    assert r.get_json()["success"] is False


def test_garbage_upload_is_rejected_and_leaves_no_file(client):
    r = client.post("/api/ai/report-logo",
                    data={"logo": (io.BytesIO(b"not an image"), "x.png")},
                    content_type="multipart/form-data",
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 400
    assert r.get_json()["success"] is False
    assert client.get("/api/ai/report-logo/status").get_json()["has_custom_logo"] is False


def test_an_oversized_body_is_rejected_via_content_length_without_reading_it(client, monkeypatch):
    # Content-Length alone must be enough to reject -- the route must not
    # need to buffer the whole (potentially huge) body first.
    monkeypatch.setattr(pdf, "MAX_LOGO_UPLOAD_BYTES", 10)
    called = {}
    monkeypatch.setattr(pdf, "save_custom_logo",
                        lambda data: called.setdefault("hit", True) or (True, "ok"))
    r = client.post("/api/ai/report-logo",
                    data={"logo": (io.BytesIO(b"x" * 1000), "big.png")},
                    content_type="multipart/form-data",
                    headers={"X-CSRF-Token": "tok"})
    assert r.status_code == 413
    assert "hit" not in called


def test_delete_removes_an_existing_logo(client):
    client.post("/api/ai/report-logo", data={"logo": (_png_bytes(), "l.png")},
               content_type="multipart/form-data", headers={"X-CSRF-Token": "tok"})
    r = client.delete("/api/ai/report-logo", headers={"X-CSRF-Token": "tok"})
    assert r.get_json()["success"] is True
    assert client.get("/api/ai/report-logo/status").get_json()["has_custom_logo"] is False


def test_delete_with_nothing_set_reports_a_no_op_not_success(client):
    r = client.delete("/api/ai/report-logo", headers={"X-CSRF-Token": "tok"})
    body = r.get_json()
    assert body["success"] is False
    assert "no custom logo" in body["message"].lower()


def test_mutating_routes_require_the_csrf_token(client):
    r = client.post("/api/ai/report-logo", data={"logo": (_png_bytes(), "l.png")},
                    content_type="multipart/form-data")
    assert r.status_code == 403
