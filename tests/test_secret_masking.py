"""Secret mask round-trip: the UI gets "xx...yy" and may send it back on save
OR test. A masked value must always resolve to the stored secret -- using the
literal mask as credential produced Gotify 401s that looked like a broken
token."""

from app.notifications import mask_secret, is_masked, resolve_masked


def test_mask_shape():
    assert mask_secret("A1b2C3d4E5f6") == "A1...f6"


def test_short_or_empty_not_masked():
    assert mask_secret("") == ""
    assert mask_secret(None) is None
    assert mask_secret("abcde") == "abcde"   # < 6 chars stays plain


def test_mask_is_recognised():
    assert is_masked(mask_secret("A1b2C3d4E5f6")) is True
    assert is_masked("") is False
    assert is_masked(None) is False
    assert is_masked("realtokenwithoutdots") is False


def test_roundtrip_resolves_to_stored():
    stored = "SuperSecretToken123"
    shown = mask_secret(stored)
    assert resolve_masked(shown, stored) == stored


def test_new_value_wins_over_stored():
    assert resolve_masked("BrandNewToken42", "OldToken") == "BrandNewToken42"


def test_empty_input_stays_empty():
    # clearing the field must NOT resurrect the old secret
    assert resolve_masked("", "OldToken") == ""


def test_long_value_with_dots_is_not_treated_as_mask():
    val = "x" * 30 + "..." + "y" * 30   # >= 32 chars -> real value
    assert resolve_masked(val, "stored") == val
