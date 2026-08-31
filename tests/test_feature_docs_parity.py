"""FEATURES.md and FEATURES_DE.md must describe the same tool.

They drifted twice in one day: four bullets (product detection, backup state,
backup overview, declared exceptions) and later one more (the custom report
logo) existed only in English -- every one of them a feature that had been
specified in German in the first place. Nothing noticed, because two long prose
files diverge silently.

Comparing per section rather than a single total, so a bullet added to one
section and dropped from another cannot cancel out into a passing count.
"""

import io
import os

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sections(name):
    """[(heading, bullet_count)] in file order, top-level bullets only."""
    out, cur = [], None
    with io.open(os.path.join(_ROOT, name), encoding="utf-8") as f:
        for line in f:
            if line.startswith("## "):
                cur = [line.strip()[3:], 0]
                out.append(cur)
            elif line.startswith("- **") and cur is not None:
                cur[1] += 1
    return [(h, n) for h, n in out]


def test_both_feature_files_have_the_same_sections_in_the_same_order():
    en, de = _sections("FEATURES.md"), _sections("FEATURES_DE.md")
    assert len(en) == len(de), (
        f"FEATURES.md has {len(en)} sections, FEATURES_DE.md has {len(de)}. "
        "A whole section is missing from one of them.")


def test_no_section_has_more_bullets_in_one_language_than_the_other():
    en, de = _sections("FEATURES.md"), _sections("FEATURES_DE.md")
    drift = [(e[0], d[0], e[1], d[1]) for e, d in zip(en, de) if e[1] != d[1]]
    assert not drift, "sections whose bullet counts differ:\n" + "\n".join(
        f"  {en_h!r} has {en_n} bullets, {de_h!r} has {de_n}"
        for en_h, de_h, en_n, de_n in drift)


@pytest.mark.parametrize("name", ["FEATURES.md", "FEATURES_DE.md"])
def test_the_file_is_still_shaped_the_way_this_test_reads_it(name):
    # Without this, a reformat that stopped using "## " headings or "- **"
    # bullets would turn both tests above into rubber stamps: zero sections
    # compared equal to zero sections.
    sections = _sections(name)
    assert len(sections) >= 15, f"{name}: only {len(sections)} sections found"
    assert sum(n for _, n in sections) >= 100, f"{name}: too few bullets found"
