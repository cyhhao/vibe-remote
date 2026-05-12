"""Regression tests for the Settings → Backends API-key masker.

Pins the masked-preview format that the Codex and Claude provider pages
display in place of the stored API key. The full plaintext key must never
leak into the response; only the leading prefix (e.g. ``sk-proj-``,
``sk-ant-``) plus the trailing four characters survive the mask.
"""

from __future__ import annotations

from vibe.api import _mask_api_key


def test_blank_or_none_returns_none() -> None:
    assert _mask_api_key(None) is None
    assert _mask_api_key("") is None
    assert _mask_api_key("   ") is None


def test_codex_sk_proj_key_keeps_prefix_and_last4() -> None:
    result = _mask_api_key("sk-proj-aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456H8mN")
    assert result is not None
    assert result.startswith("sk-proj-")
    assert result.endswith("H8mN")
    # Body must be dots only (no plaintext middle).
    middle = result[len("sk-proj-") : -4]
    assert middle == "•" * 9


def test_claude_sk_ant_key_keeps_prefix_and_last4() -> None:
    result = _mask_api_key("sk-ant-aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456cd34")
    assert result is not None
    assert result.startswith("sk-ant-")
    assert result.endswith("cd34")


def test_short_or_unstructured_key_falls_back_to_dots_plus_last4() -> None:
    assert _mask_api_key("abcd1234") == "••••••1234"
    assert _mask_api_key("plainstring") == "••••••ring"


def test_whitespace_is_stripped() -> None:
    result = _mask_api_key("  sk-proj-foobarbaz12345abcd  ")
    assert result is not None
    assert result.startswith("sk-proj-")
    assert result.endswith("abcd")


def test_mask_never_leaks_plaintext_middle() -> None:
    """No matter the key, the response must not contain any non-prefix
    non-last4 characters of the input."""
    key = "sk-proj-MIDDLECONTENTTHATMUSTNOTLEAK7777"
    result = _mask_api_key(key)
    assert result is not None
    assert "MIDDLE" not in result
    assert "MUSTNOTLEAK" not in result
