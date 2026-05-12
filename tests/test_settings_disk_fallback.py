"""Regression tests for the Settings → Backends disk-fallback reads.

Page-feedback fix for PR #282: the user configured their Claude /
Codex relay by hand-editing ``~/.claude/settings.json`` /
``~/.codex/config.toml`` (or a prior tool did), but the Settings UI
rendered the API Key input as empty and the Base URL as the default.
The fixes:

1. ``vibe.codex_config.read_codex_auth_state`` now honours the
   top-level ``model_provider`` key when looking up ``base_url`` — a
   user-defined ``[model_providers.OpenAI]`` section is no longer
   ignored just because the key happens to be TitleCase rather than
   our managed lowercase ``openai``.

2. ``vibe.api.get_claude_auth`` falls back to
   ``~/.claude/settings.json`` env values (``ANTHROPIC_API_KEY`` /
   ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_BASE_URL``) when V2Config is
   empty, surfacing the live state via ``api_key_masked`` /
   ``base_url`` and tagging the source as ``settings_json``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe.codex_config import read_codex_auth_state


def test_codex_reads_base_url_from_user_titlecase_provider(tmp_path: Path) -> None:
    """When the user points Codex at a relay via ``[model_providers.OpenAI]``
    (matching their on-disk config.toml literally), the Settings UI must
    pick up that ``base_url`` rather than reporting the default.
    """
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text("{}", encoding="utf-8")
    (codex_dir / "config.toml").write_text(
        "\n".join(
            [
                'model_provider = "OpenAI"',
                "",
                "[model_providers.OpenAI]",
                'name = "OpenAI"',
                'base_url = "https://ai-relay.chainbot.io"',
                'wire_api = "responses"',
                "requires_openai_auth = true",
            ]
        ),
        encoding="utf-8",
    )
    state = read_codex_auth_state(home=tmp_path)
    assert state["base_url"] == "https://ai-relay.chainbot.io"


def test_codex_falls_back_to_managed_section_when_no_active_provider(tmp_path: Path) -> None:
    """If ``model_provider`` is unset but our managed ``openai`` section
    has a ``base_url`` (the shape we write via ``apply_codex_auth``),
    surface that. Preserves the pre-fix behaviour for vibe-initiated
    saves that never touched the top-level ``model_provider`` key.
    """
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text("{}", encoding="utf-8")
    (codex_dir / "config.toml").write_text(
        "\n".join(
            [
                "[model_providers.openai]",
                'base_url = "https://vibe-managed.example.io"',
            ]
        ),
        encoding="utf-8",
    )
    state = read_codex_auth_state(home=tmp_path)
    assert state["base_url"] == "https://vibe-managed.example.io"


def test_codex_no_base_url_when_neither_section_has_one(tmp_path: Path) -> None:
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    (codex_dir / "auth.json").write_text("{}", encoding="utf-8")
    (codex_dir / "config.toml").write_text("model = \"gpt-5.4\"\n", encoding="utf-8")
    state = read_codex_auth_state(home=tmp_path)
    assert state["base_url"] is None


def _write_claude_settings(home: Path, env: dict) -> None:
    claude_dir = home / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(
        json.dumps({"env": env}), encoding="utf-8"
    )


def test_claude_settings_json_auth_token_surfaces_in_get_claude_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude's get_claude_auth must report a masked key + base URL when
    V2Config is empty but ``settings.json`` carries them. Mirrors the
    regression env where the user pre-configured a relay via the env
    block before our Settings UI existed.
    """
    _write_claude_settings(
        tmp_path,
        {
            "ANTHROPIC_AUTH_TOKEN": "sk-9c552_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx_738a",
            "ANTHROPIC_BASE_URL": "https://ai-relay.chainbot.io",
        },
    )
    # Pin Claude's home + V2Config home to the temp dir so the readers
    # find our fake settings.json and the V2Config load returns defaults
    # (i.e. ``api_key=None``).
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path / ".vibe_remote"))
    monkeypatch.setattr("config.paths._home", lambda: tmp_path, raising=False)

    from vibe.api import get_claude_auth

    state = get_claude_auth()
    assert state["has_api_key"] is True
    assert state["api_key_source"] == "settings_json"
    masked = state["api_key_masked"]
    assert masked is not None
    assert masked.endswith("738a")
    assert "xxxxxxxx" not in masked  # plaintext middle must not leak
    assert state["base_url"] == "https://ai-relay.chainbot.io"
    assert state["active_auth_mode"] == "api_key"
    # settings.json alone is not a *conflict*; it's only a conflict when
    # BOTH V2Config and settings.json carry a key.
    assert state["settings_conflict"] is False


def test_claude_v2config_takes_precedence_over_settings_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When V2Config also has a key, that wins for display purposes
    (it's what we'd inject at launch). settings.json being present
    elevates ``settings_conflict`` so the UI can warn the user."""
    _write_claude_settings(
        tmp_path,
        {"ANTHROPIC_API_KEY": "sk-stale-key-from-settings"},
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setattr("config.paths._home", lambda: tmp_path, raising=False)

    # Inject a V2Config with a fresh key by patching load_config.
    class _FakeAgent:
        auth_mode = "api_key"
        api_key = "sk-fresh-key-from-v2config"
        base_url = "https://v2config.example.io"

    class _FakeAgents:
        claude = _FakeAgent()

    class _FakeConfig:
        agents = _FakeAgents()

    monkeypatch.setattr("vibe.api.load_config", lambda: _FakeConfig())

    from vibe.api import get_claude_auth

    state = get_claude_auth()
    assert state["api_key_source"] == "v2config"
    masked = state["api_key_masked"]
    assert masked is not None
    # Mask preserves the trailing 4 characters of the V2Config key.
    assert masked.endswith("nfig")
    assert state["base_url"] == "https://v2config.example.io"
    assert state["settings_conflict"] is True
