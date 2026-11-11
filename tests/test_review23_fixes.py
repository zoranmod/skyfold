"""Tests for the fixes after the twenty-third (Codex) review."""

from skyfold.safelog import code


def test_only_known_refusal_codes_are_kept():
    assert code("CONFLICT") == "CONFLICT" and code("item_not_found") == "ITEM_NOT_FOUND"
    assert code(409) == "409" and code("503") == "503"
    assert code("private-response-opaque-nonce") == "other"
    assert code("ERROR-private-response-opaque-nonce") == "other"
    assert code("<b>text</b>") == "other" and code(None) == "other" and code(True) == "other"
