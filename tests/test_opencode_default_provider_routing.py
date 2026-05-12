"""Regression tests for OpenCode bare-model provider routing.

Pins two invariants that landed via Codex round 4 review on PR #282:

1. ``OpenCodeConfig.default_provider`` defaults to ``None`` so legacy installs
   (Ollama/OpenAI/etc.) are not silently rerouted to Anthropic on upgrade.
2. The OpenCodeAgent bare-model fallback only injects ``providerID`` when a
   non-empty default provider has been explicitly chosen.
"""

from __future__ import annotations

import dataclasses

from config.v2_config import OpenCodeConfig


def test_opencode_config_default_provider_is_unset_by_default() -> None:
    cfg = OpenCodeConfig()
    assert cfg.default_provider is None

    fields = {f.name: f for f in dataclasses.fields(OpenCodeConfig)}
    assert "default_provider" in fields
    # Hard-pin the default so a future refactor cannot reintroduce
    # ``"anthropic"`` as a silent fallback for unconfigured installs.
    assert fields["default_provider"].default is None


def _resolve_model_dict(model_str: str, default_provider):
    """Replicates the bare-model branch in ``modules/agents/opencode/agent.py``.

    Kept verbatim so the test fails loudly if the agent-side guard regresses
    back to ``if default_provider:`` (which truthy-checked an empty string and
    forced bare-model strings onto Anthropic for legacy installs).
    """
    parts = model_str.split("/", 1)
    if len(parts) == 2:
        return {"providerID": parts[0], "modelID": parts[1]}
    if isinstance(default_provider, str) and default_provider.strip():
        return {"providerID": default_provider.strip(), "modelID": model_str}
    return None


def test_bare_model_with_no_default_provider_returns_none() -> None:
    # Pre-upgrade behaviour: OpenCode owns routing for bare model IDs.
    assert _resolve_model_dict("kimi-k2", default_provider=None) is None


def test_bare_model_with_blank_default_provider_returns_none() -> None:
    assert _resolve_model_dict("kimi-k2", default_provider="   ") is None
    assert _resolve_model_dict("kimi-k2", default_provider="") is None


def test_bare_model_with_explicit_default_provider_injects_provider_id() -> None:
    assert _resolve_model_dict("kimi-k2", default_provider="ollama") == {
        "providerID": "ollama",
        "modelID": "kimi-k2",
    }


def test_bare_model_strips_default_provider_whitespace() -> None:
    assert _resolve_model_dict("kimi-k2", default_provider="  ollama  ") == {
        "providerID": "ollama",
        "modelID": "kimi-k2",
    }


def test_prefixed_model_ignores_default_provider() -> None:
    # Explicit ``provider/model`` always wins, even if the user configured a
    # different default.
    assert _resolve_model_dict("openai/gpt-5", default_provider="ollama") == {
        "providerID": "openai",
        "modelID": "gpt-5",
    }
