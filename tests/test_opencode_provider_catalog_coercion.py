"""Regression tests for the OpenCode ``/provider`` payload normalizer.

Pins the schema-coercion helper that backs the Settings → Backends →
OpenCode catalog. Codex round-4 feedback on PR #282 observed an empty
provider grid in the regression environment: OpenCode 1.x returns
``{all: [Provider, ...]}`` (list), but the previous parser only accepted
``{all: {pid: Provider}}`` (dict). Both shapes — plus the legacy
``{providers: [...]}`` top-level form — must now round-trip into an
id-keyed map so the UI never silently goes empty after an OpenCode
upgrade-in-place.
"""

from __future__ import annotations

from vibe.api import _coerce_opencode_provider_catalog


def test_opencode_1x_list_shape_is_coerced_to_id_map() -> None:
    payload = {
        "all": [
            {"id": "302ai", "name": "302.AI", "models": {}},
            {"id": "anthropic", "name": "Anthropic", "models": {}},
            {"id": "openai", "name": "OpenAI", "models": {}},
        ],
        "default": {"302ai": "qwen3-235b-a22b"},
        "connected": ["anthropic", "openai"],
    }
    result = _coerce_opencode_provider_catalog(payload)
    assert set(result.keys()) == {"302ai", "anthropic", "openai"}
    assert result["anthropic"]["name"] == "Anthropic"


def test_opencode_pre_1x_dict_shape_passes_through() -> None:
    payload = {
        "all": {
            "anthropic": {"id": "anthropic", "name": "Anthropic"},
            "openai": {"id": "openai", "name": "OpenAI"},
        },
        "default": {},
        "connected": [],
    }
    result = _coerce_opencode_provider_catalog(payload)
    assert result == payload["all"]


def test_legacy_top_level_providers_list_is_coerced() -> None:
    payload = {
        "providers": [
            {"id": "anthropic", "name": "Anthropic"},
            {"id": "openai", "name": "OpenAI"},
        ],
    }
    result = _coerce_opencode_provider_catalog(payload)
    assert set(result.keys()) == {"anthropic", "openai"}


def test_list_entries_without_id_are_skipped() -> None:
    payload = {
        "all": [
            {"id": "anthropic", "name": "Anthropic"},
            {"name": "MissingID"},  # malformed — no id
            "not-a-dict",  # malformed — wrong type
            {"id": "openai", "name": "OpenAI"},
        ],
    }
    result = _coerce_opencode_provider_catalog(payload)
    assert set(result.keys()) == {"anthropic", "openai"}


def test_unknown_payload_returns_empty_map() -> None:
    assert _coerce_opencode_provider_catalog(None) == {}
    assert _coerce_opencode_provider_catalog([]) == {}
    assert _coerce_opencode_provider_catalog({"unrelated": "data"}) == {}
    assert _coerce_opencode_provider_catalog({"all": "not-a-collection"}) == {}


def test_list_shape_preserves_provider_payload() -> None:
    payload = {
        "all": [
            {
                "id": "anthropic",
                "name": "Anthropic",
                "env": ["ANTHROPIC_API_KEY"],
                "options": {"baseURL": "https://api.anthropic.com"},
                "models": {"claude-sonnet-4-5": {"id": "claude-sonnet-4-5"}},
            },
        ],
    }
    result = _coerce_opencode_provider_catalog(payload)
    assert result["anthropic"]["env"] == ["ANTHROPIC_API_KEY"]
    assert result["anthropic"]["options"] == {"baseURL": "https://api.anthropic.com"}
    assert "claude-sonnet-4-5" in result["anthropic"]["models"]
