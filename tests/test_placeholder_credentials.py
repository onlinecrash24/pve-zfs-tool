"""A placeholder credential must never be quieter than the default it replaces.

The startup checks used to match one literal each: SECRET_KEY against
``dev-key-change-me`` and the pair ``admin``/``password``. Both the compose
file and the README then shipped *different* placeholders, so running either of
them unedited produced a fixed, publicly known session key and a publicly known
password -- and the application said nothing at all. Documentation had quietly
disarmed the warning it was supposed to trigger.

These tests hold every value this repository actually ships against the checks,
by reading the files rather than by restating the strings here: a placeholder
added to a compose file or a README without being added to the blocklist fails
here rather than in someone's deployment.
"""

import io
import os
import re

import pytest

from app.main import is_placeholder_password, is_placeholder_secret_key

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with io.open(os.path.join(_ROOT, name), encoding="utf-8") as f:
        return f.read()


def _env_values(text, key):
    """Every `- KEY=value` this file hands to the container, comments stripped."""
    out = []
    for m in re.finditer(rf"^\s*-\s*{key}=([^\n#]*)", text, re.M):
        out.append(m.group(1).strip())
    return out


SHIPPED_FILES = ["docker-compose.yml", "README.md", "README_DE.md"]


@pytest.mark.parametrize("fname", SHIPPED_FILES)
def test_every_shipped_secret_key_is_recognised_as_a_placeholder(fname):
    values = _env_values(_read(fname), "SECRET_KEY")
    assert values, f"no SECRET_KEY found in {fname} -- did the format change?"
    missed = [v for v in values if not is_placeholder_secret_key(v)]
    assert not missed, (
        f"{fname} ships a SECRET_KEY the startup check would accept as real: "
        f"{missed}. Add it to PLACEHOLDER_SECRET_KEYS, or the reader who "
        f"copies it gets a fixed, publicly known session key in silence.")


@pytest.mark.parametrize("fname", SHIPPED_FILES)
def test_every_shipped_admin_password_is_recognised_as_a_placeholder(fname):
    values = _env_values(_read(fname), "ADMIN_PASSWORD")
    assert values, f"no ADMIN_PASSWORD found in {fname} -- did the format change?"
    missed = [v for v in values if not is_placeholder_password(v)]
    assert not missed, (
        f"{fname} ships an ADMIN_PASSWORD the startup check would accept as "
        f"real: {missed}. Add it to PLACEHOLDER_PASSWORDS.")


def test_the_old_code_defaults_still_warn():
    # The fallbacks in main.py when nothing is set at all.
    assert is_placeholder_secret_key("dev-key-change-me")
    assert is_placeholder_password("password")


def test_the_password_is_judged_alone_not_paired_with_the_username():
    # The old check was `ADMIN_USER == "admin" and ADMIN_PASSWORD == "password"`,
    # so renaming the user to anything else silently excused a default password.
    assert is_placeholder_password("password")


def test_a_real_credential_is_not_flagged():
    assert not is_placeholder_secret_key("f3a9c1d47b0e2a6584cc91de77b0aa31")
    assert not is_placeholder_password("correct-horse-battery-staple")


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_unset_counts_as_a_placeholder(blank):
    # An empty SECRET_KEY yields forgeable sessions; an empty password is worse
    # than a weak one. Neither may pass as a deliberate choice.
    assert is_placeholder_secret_key(blank)
    assert is_placeholder_password(blank)


def test_surrounding_whitespace_does_not_smuggle_a_placeholder_through():
    assert is_placeholder_password(" password ")
    assert is_placeholder_secret_key("\tchange-me-in-production\n")
