"""Custom report logo: box math, upload validation, and resolution order.

The logo can be any shape a user's own artwork happens to have, unlike the
tool's bundled asset whose fixed aspect ratio used to be hardcoded. The box
math below has to handle a wide banner, a tall mark, and a square icon without
ever pushing the report title off the page.
"""

import io

import pytest

from app import ai_pdf as pdf


# --- _logo_box: pure geometry, no I/O -------------------------------------

def test_a_moderately_wide_logo_keeps_the_fixed_height():
    # 3:1, e.g. a horizontal wordmark -- under the 45mm width cap at the
    # standard 11mm header height (33mm < 45mm), so height stays fixed.
    w, h = pdf._logo_box(300, 100, target_h=11.0, max_w=45.0)
    assert h == 11.0
    assert w == pytest.approx(33.0)


def test_the_bundled_logos_own_proportions_are_width_capped():
    # The bundled asset is 2080x480 (~4.33:1) -- at the fixed 11mm height that
    # would be ~47.7mm wide, just over the 45mm cap, so it gives a little on
    # height too. Exercising the real asset's numbers here means a future
    # change to logo-small.png's shape shows up as a test failure, not a
    # silently different-looking header.
    w, h = pdf._logo_box(2080, 480, target_h=11.0, max_w=45.0)
    assert w == 45.0
    assert h == pytest.approx(45.0 * 480 / 2080)
    assert h < 11.0


def test_a_square_logo_is_as_wide_as_tall():
    w, h = pdf._logo_box(500, 500, target_h=11.0, max_w=45.0)
    assert w == h == 11.0


def test_a_wide_banner_gives_way_on_height_not_width():
    # A 10:1 banner at 11mm tall would be 110mm wide -- comically wider than
    # an A4 page's usable width. The width has to be the one that yields.
    w, h = pdf._logo_box(1000, 100, target_h=11.0, max_w=45.0)
    assert w == 45.0
    assert h == pytest.approx(45.0 / 10)
    assert h < 11.0


def test_a_tall_mark_is_narrow_and_never_hits_the_width_cap():
    w, h = pdf._logo_box(100, 1000, target_h=11.0, max_w=45.0)
    assert h == 11.0
    assert w == pytest.approx(1.1)


def test_degenerate_dimensions_render_nothing():
    assert pdf._logo_box(0, 100) == (0.0, 0.0)
    assert pdf._logo_box(100, 0) == (0.0, 0.0)
    assert pdf._logo_box(0, 0) == (0.0, 0.0)


# --- resolution order: custom beats bundled beats nothing ------------------

@pytest.fixture
def logo_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(pdf, "CUSTOM_LOGO_PATH", str(tmp_path / "report_logo.png"))
    bundled_dir = tmp_path / "img"
    bundled_dir.mkdir()
    monkeypatch.setattr(pdf, "IMG_DIR", str(bundled_dir))
    return tmp_path, bundled_dir


def _tiny_png_bytes(size=(20, 8), color=(200, 30, 30)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_no_logo_anywhere_resolves_to_none(logo_paths):
    assert pdf._resolve_logo_path() is None
    assert pdf._has_logo() is False
    assert pdf.get_logo_bytes() is None


def test_the_bundled_logo_is_used_when_no_custom_one_exists(logo_paths, monkeypatch):
    _tmp, bundled_dir = logo_paths
    (bundled_dir / "logo-small.png").write_bytes(_tiny_png_bytes())
    assert pdf._resolve_logo_path() == str(bundled_dir / "logo-small.png")
    assert pdf.has_custom_logo() is False
    assert pdf.get_logo_bytes() is not None


def test_a_custom_upload_takes_precedence_over_the_bundled_logo(logo_paths):
    tmp, bundled_dir = logo_paths
    (bundled_dir / "logo-small.png").write_bytes(_tiny_png_bytes(color=(0, 0, 200)))
    ok, msg = pdf.save_custom_logo(_tiny_png_bytes(color=(0, 200, 0)))
    assert ok, msg
    assert pdf._resolve_logo_path() == pdf.CUSTOM_LOGO_PATH
    assert pdf.has_custom_logo() is True


# --- save_custom_logo: validation and normalisation ------------------------

def test_a_valid_png_upload_is_accepted_and_stored(logo_paths):
    ok, msg = pdf.save_custom_logo(_tiny_png_bytes())
    assert ok, msg
    assert pdf.has_custom_logo() is True


def test_jpeg_and_other_formats_are_normalised_to_png(logo_paths):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (30, 30), (10, 20, 30)).save(buf, format="JPEG")
    ok, _ = pdf.save_custom_logo(buf.getvalue())
    assert ok
    with Image.open(pdf.CUSTOM_LOGO_PATH) as im:
        assert im.format == "PNG"


def test_garbage_bytes_are_rejected(logo_paths):
    ok, msg = pdf.save_custom_logo(b"this is not an image, just some text")
    assert ok is False
    assert "image" in msg.lower()
    assert pdf.has_custom_logo() is False


def test_empty_upload_is_rejected(logo_paths):
    ok, _ = pdf.save_custom_logo(b"")
    assert ok is False


def test_oversized_upload_is_rejected_before_decoding(logo_paths):
    # Doesn't need to be a real image -- the size check runs first.
    huge = b"x" * (pdf.MAX_LOGO_UPLOAD_BYTES + 1)
    ok, msg = pdf.save_custom_logo(huge)
    assert ok is False
    assert "large" in msg.lower()


def test_a_rejected_upload_does_not_clobber_the_existing_logo(logo_paths):
    # The whole point of validating before writing: a bad second upload must
    # not destroy a perfectly good first one.
    ok, _ = pdf.save_custom_logo(_tiny_png_bytes(color=(9, 9, 9)))
    assert ok
    before = open(pdf.CUSTOM_LOGO_PATH, "rb").read()

    ok2, _ = pdf.save_custom_logo(b"garbage")
    assert ok2 is False

    after = open(pdf.CUSTOM_LOGO_PATH, "rb").read()
    assert before == after


def test_an_oversized_image_is_downscaled(logo_paths):
    from PIL import Image
    big = 2000
    ok, _ = pdf.save_custom_logo(_tiny_png_bytes(size=(big, 100)))
    assert ok
    with Image.open(pdf.CUSTOM_LOGO_PATH) as im:
        assert max(im.size) <= pdf.MAX_LOGO_DIMENSION_PX


# --- remove_custom_logo: no-op is reported, not disguised as success ------

def test_removing_an_existing_logo_succeeds(logo_paths):
    pdf.save_custom_logo(_tiny_png_bytes())
    ok, msg = pdf.remove_custom_logo()
    assert ok, msg
    assert pdf.has_custom_logo() is False


def test_removing_when_nothing_is_set_is_a_reported_no_op(logo_paths):
    ok, msg = pdf.remove_custom_logo()
    assert ok is False
    assert "no custom logo" in msg.lower()


def test_removing_falls_back_to_the_bundled_logo(logo_paths):
    tmp, bundled_dir = logo_paths
    (bundled_dir / "logo-small.png").write_bytes(_tiny_png_bytes())
    pdf.save_custom_logo(_tiny_png_bytes(color=(1, 2, 3)))
    assert pdf._resolve_logo_path() == pdf.CUSTOM_LOGO_PATH

    pdf.remove_custom_logo()
    assert pdf._resolve_logo_path() == str(bundled_dir / "logo-small.png")


# --- end-to-end: the header actually has to render, for any shape ---------

def _report(**overrides):
    r = {"timestamp": "2026-08-08 12:00:00", "provider": "openai", "model": "gpt-4o-mini",
         "host_names": ["pve1"], "content": "## 1. Overview\nAll good.", "verdict": "ok"}
    r.update(overrides)
    return r


@pytest.mark.parametrize("size,label", [
    ((300, 100), "wide 3:1"),
    ((1000, 100), "very wide 10:1 banner"),
    ((100, 1000), "tall 1:10 mark"),
    ((500, 500), "square"),
    ((1, 1), "1x1 pixel"),
])
def test_generate_pdf_survives_every_logo_shape(logo_paths, size, label):
    ok, msg = pdf.save_custom_logo(_tiny_png_bytes(size=size))
    assert ok, msg
    out = pdf.generate_pdf(_report())
    assert bytes(out)[:4] == b"%PDF", label


def test_generate_pdf_works_with_no_logo_at_all(logo_paths):
    out = pdf.generate_pdf(_report())
    assert bytes(out)[:4] == b"%PDF"


def test_the_footer_credits_the_tool():
    # fpdf2 keeps the raw text of a cell() call recoverable is not
    # guaranteed, so this checks the source of truth for the wording
    # directly rather than trying to extract text back out of PDF bytes.
    import inspect
    src = inspect.getsource(pdf.ReportPDF.footer)
    assert "Powered by PVE ZFS Tool" in src
