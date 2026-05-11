"""Regression tests for ``vibe/codex_config.py`` TOML round-tripping.

The Codex CLI's ``config.toml`` carries arbitrary user-owned blocks
(``[projects."/abs/path"]`` scopes, deeply nested settings, arrays of
tables). When we save Codex auth state we must preserve those unrelated
sections rather than silently dropping them. These tests pin the
round-trip behavior of the emitter.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py<3.11
    import tomli as tomllib  # type: ignore[no-redef]

from vibe import codex_config


def _round_trip(data: dict) -> dict:
    return tomllib.loads(codex_config._dump_toml(data))


def test_round_trip_quoted_keys() -> None:
    """Keys outside the bare-key character class must be emitted quoted."""
    data = {
        "model_provider": "openai",
        "projects": {
            "/Users/alex/code/vibe-remote": {"trust_level": "trusted"},
            "/tmp/another path": {"trust_level": "ask"},
        },
    }
    rendered = codex_config._dump_toml(data)
    assert '"/Users/alex/code/vibe-remote"' in rendered
    assert '"/tmp/another path"' in rendered
    assert _round_trip(data) == data


def test_round_trip_deep_nesting() -> None:
    """Nesting deeper than two levels must survive the rewrite."""
    data = {
        "a": {"b": {"c": {"x": 1, "y": "hello", "z": True}}},
        "top_level": "kept",
    }
    assert _round_trip(data) == data


def test_round_trip_arrays_of_tables() -> None:
    """``[[plugin]]`` array-of-tables entries must round-trip intact."""
    data = {
        "plugin": [
            {"name": "first", "enabled": True},
            {"name": "second", "settings": {"timeout": 30}},
        ],
    }
    assert _round_trip(data) == data


def test_apply_api_key_mode_preserves_unrelated_blocks(tmp_path: Path) -> None:
    """Switching to api_key mode must not drop user-owned config blocks."""
    home = tmp_path
    codex_home = home / ".codex"
    codex_home.mkdir()
    seed = (
        'model = "gpt-5"\n'
        "\n"
        '[projects."/Users/alex/code/vibe-remote"]\n'
        'trust_level = "trusted"\n'
        "\n"
        "[a.b.c]\n"
        "x = 1\n"
    )
    (codex_home / "config.toml").write_text(seed, encoding="utf-8")

    codex_config.apply_codex_auth(
        auth_mode="api_key",
        api_key="sk-test-1234567890",
        base_url="https://api.example.com/v1",
        home=home,
    )

    parsed = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    assert parsed["model"] == "gpt-5"
    assert parsed["model_provider"] == codex_config.MANAGED_PROVIDER_ID
    assert parsed["model_providers"]["openai"]["base_url"] == "https://api.example.com/v1"
    assert parsed["projects"]["/Users/alex/code/vibe-remote"]["trust_level"] == "trusted"
    assert parsed["a"]["b"]["c"]["x"] == 1

    auth = json.loads((codex_home / "auth.json").read_text(encoding="utf-8"))
    assert auth["OPENAI_API_KEY"] == "sk-test-1234567890"


def test_apply_oauth_mode_clears_managed_base_url(tmp_path: Path) -> None:
    """Switching back to oauth strips the managed base_url and api_key."""
    home = tmp_path
    codex_home = home / ".codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "sk-old", "tokens": {"id_token": "abc"}}),
        encoding="utf-8",
    )
    (codex_home / "config.toml").write_text(
        '[model_providers.openai]\nbase_url = "https://api.example.com/v1"\n',
        encoding="utf-8",
    )

    codex_config.apply_codex_auth(
        auth_mode="oauth",
        api_key=None,
        base_url=None,
        home=home,
    )

    auth = json.loads((codex_home / "auth.json").read_text(encoding="utf-8"))
    assert "OPENAI_API_KEY" not in auth
    assert auth["tokens"] == {"id_token": "abc"}

    parsed = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    assert "model_providers" not in parsed
