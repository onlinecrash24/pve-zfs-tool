"""Every label the UI asks for must exist, in both languages.

``t()`` falls back to returning the key itself, so a missing entry does not
throw -- it quietly renders ``gaps_found`` where the user should read "Gaps
found". Nobody notices until someone asks what the odd word in the table header
means, which is why this is a test rather than a convention.
"""

import os
import re
from collections import Counter

_STATIC = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "app", "static", "js")


def _read(name):
    with open(os.path.join(_STATIC, name), encoding="utf-8") as f:
        return f.read()


def _key_counts():
    """How often each key is defined across the file. The file holds one flat
    object per language, so a healthy key appears exactly twice."""
    return Counter(re.findall(r"^        ([A-Za-z0-9_]+):", _read("i18n.js"), re.M))


def test_every_key_is_defined_once_per_language():
    odd = {k: n for k, n in _key_counts().items() if n != 2}
    # A key defined twice in the SAME block is worse than a missing one: a JS
    # object literal silently keeps the last, so the earlier translation is
    # dead text that reads as if it were in use.
    assert not odd, f"keys not defined exactly twice (en + de): {odd}"


def test_the_translation_file_is_two_flat_blocks():
    # The count above assumes 8-space keys are the language blocks and nothing
    # nests deeper. If that ever changes, the check above turns into a
    # rubber stamp.
    assert not re.search(r"^            [A-Za-z0-9_]+:\s", _read("i18n.js"), re.M)


def test_no_t_call_renders_a_raw_key():
    defined = set(_key_counts())
    app = _read("app.js")
    used = set(re.findall(r"""\bt\(\s*["']([A-Za-z0-9_]+)["']""", app))
    # Keys ending in _ are prefixes built at runtime -- t("mig_chk_" + step).
    # Their concrete variants are checked below.
    literal = {k for k in used if not k.endswith("_")}
    assert not (literal - defined), \
        f"t() uses undefined key(s): {sorted(literal - defined)}"


def test_every_runtime_prefix_has_variants():
    # t("prefix_" + value) resolves to nothing at all if the family was never
    # translated, which is the same bug one level up.
    defined = set(_key_counts())
    app = _read("app.js")
    prefixes = {k for k in re.findall(r"""\bt\(\s*["']([A-Za-z0-9_]+_)["']""", app)}
    assert prefixes, "expected at least one runtime-built key family"
    for p in prefixes:
        assert any(k.startswith(p) and k != p for k in defined), \
            f"no translations for the {p}* family"
