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


# --- corrupted store: the STORED secret is itself a mask -------------------
# An older save could persist the placeholder instead of the real secret.
# Masking a mask returns the same string, so the UI looks identical either
# way -- only a server-side check can tell, and the test endpoints use it to
# report "that's a placeholder" instead of forwarding a bogus 401.

def test_masking_a_mask_is_indistinguishable_in_the_ui():
    real = "SuperSecretToken123"
    shown = mask_secret(real)
    assert mask_secret(shown) == shown          # UI can't reveal the difference


def test_resolution_of_a_corrupted_store_stays_masked_and_is_detectable():
    corrupted = mask_secret("SuperSecretToken123")   # stored value IS a mask
    effective = resolve_masked(corrupted, corrupted)  # what the endpoint sends
    assert effective == corrupted
    assert is_masked(effective) is True          # -> guard fires, no bogus 401


def test_healthy_store_resolves_to_a_usable_secret():
    stored = "syt_realmatrixaccesstoken_abcdef123456"
    effective = resolve_masked(mask_secret(stored), stored)
    assert effective == stored
    assert is_masked(effective) is False         # -> guard does NOT fire
