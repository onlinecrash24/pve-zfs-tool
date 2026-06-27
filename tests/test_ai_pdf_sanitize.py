"""PDF text sanitiser -- fixes the '10.?Mai?2026' Unicode-whitespace bug.

Uses explicit \\u escapes for the invisible/ambiguous codepoints so the test
intent can't be corrupted by an editor that normalises whitespace.
"""

from app.ai_pdf import _sanitize_for_pdf


def test_narrow_nbsp_becomes_regular_space():
    # U+202F NARROW NO-BREAK SPACE (the original "10.?Mai?2026" culprit)
    src = "10. Mai 2026"
    out = _sanitize_for_pdf(src, use_unicode=False)
    assert out == "10. Mai 2026"
    assert " " not in out


def test_various_unicode_whitespace_collapsed():
    # NBSP, EN space, EM space, THIN space, IDEOGRAPHIC space
    src = "a b c d e　f"
    out = _sanitize_for_pdf(src, use_unicode=True)
    assert out == "a b c d e f"


def test_zero_width_chars_removed():
    src = "a​b‍c﻿d⁠e"  # ZWSP, ZWJ, BOM, WORD JOINER
    out = _sanitize_for_pdf(src, use_unicode=True)
    assert out == "abcde"


def test_latin1_fallback_for_punctuation_when_no_unicode_font():
    src = "„Test“ — ok…"  # „Test" — ok…
    out = _sanitize_for_pdf(src, use_unicode=False)
    assert out == '"Test" - ok...'


def test_emoji_status_markers_become_ascii_without_unicode():
    src = "✅ ok ⚠ warn ❌ crit"  # ✅ ⚠ ❌
    out = _sanitize_for_pdf(src, use_unicode=False)
    assert "[OK]" in out and "[!]" in out and "[X]" in out


def test_unicode_preserved_when_font_supports_it():
    src = "„Test“ — ✅"
    out = _sanitize_for_pdf(src, use_unicode=True)
    assert "„" in out and "—" in out and "✅" in out


def test_latin1_umlauts_survive_both_modes():
    for u in (True, False):
        assert _sanitize_for_pdf("äöüß", use_unicode=u) == "äöüß"


def test_empty_input():
    assert _sanitize_for_pdf("", use_unicode=False) == ""
    assert _sanitize_for_pdf(None, use_unicode=False) == ""
