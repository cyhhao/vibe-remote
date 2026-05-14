"""Regression tests for the OpenCode per-provider auth.json reader.

Powers the Settings → Backends → OpenCode page's masked-key preview
("``sk-proj-•••H8mN``") for each provider that has an api-type entry
in ``~/.local/share/opencode/auth.json``. Plaintext keys are returned
to in-process callers (``vibe.api.get_opencode_providers`` masks them
before serialising); OAuth-type providers map to ``None`` so the UI
can switch affordances accordingly.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vibe.opencode_config import (
    get_opencode_auth_path,
    read_opencode_provider_keys,
)


def _write_auth(home: Path, payload: dict) -> Path:
    auth_path = home / ".local" / "share" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(json.dumps(payload), encoding="utf-8")
    return auth_path


def test_missing_file_returns_empty_dict(tmp_path: Path) -> None:
    # OpenCode lazily creates auth.json on first PUT /auth/<id>; absence
    # should not be treated as an error.
    assert read_opencode_provider_keys(home=tmp_path) == {}


def test_api_type_provider_yields_plaintext_key(tmp_path: Path) -> None:
    _write_auth(
        tmp_path,
        {
            "anthropic": {"type": "api", "key": "sk-ant-aBcDeFgHiJkLmNoPqRsTuV12cd34"},
            "openai": {"type": "api", "key": "sk-proj-zyxw9876v_5432"},
        },
    )
    result = read_opencode_provider_keys(home=tmp_path)
    assert result["anthropic"] == "sk-ant-aBcDeFgHiJkLmNoPqRsTuV12cd34"
    assert result["openai"] == "sk-proj-zyxw9876v_5432"


def test_oauth_type_provider_yields_none(tmp_path: Path) -> None:
    """OAuth entries carry no static key; map to ``None`` so the UI
    drops the masked-preview affordance and shows the OAuth status
    instead."""
    _write_auth(
        tmp_path,
        {"google": {"type": "oauth", "access_token": "ya29.abc", "refresh_token": "rt-x"}},
    )
    result = read_opencode_provider_keys(home=tmp_path)
    assert result == {"google": None}


def test_api_type_with_missing_or_blank_key_yields_none(tmp_path: Path) -> None:
    _write_auth(
        tmp_path,
        {
            "missing": {"type": "api"},
            "blank": {"type": "api", "key": ""},
            "non_string": {"type": "api", "key": 12345},
        },
    )
    result = read_opencode_provider_keys(home=tmp_path)
    assert result == {"missing": None, "blank": None, "non_string": None}


def test_malformed_entries_are_skipped(tmp_path: Path) -> None:
    _write_auth(
        tmp_path,
        {
            "valid": {"type": "api", "key": "sk-keepme1234"},
            123: {"type": "api", "key": "wrong-id-type"},
            "bad_shape": "not-a-dict",
        },
    )
    # JSON serialises integer keys back to strings, so verify by content:
    result = read_opencode_provider_keys(home=tmp_path)
    assert result.get("valid") == "sk-keepme1234"
    # Non-dict entries are skipped silently.
    assert "bad_shape" not in result


def test_invalid_json_returns_empty_dict(tmp_path: Path) -> None:
    auth_path = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text("not-actually-json{", encoding="utf-8")
    assert read_opencode_provider_keys(home=tmp_path) == {}


def test_auth_path_helper_resolves_under_home(tmp_path: Path) -> None:
    assert get_opencode_auth_path(home=tmp_path) == (
        tmp_path / ".local" / "share" / "opencode" / "auth.json"
    )


def test_top_level_array_is_rejected(tmp_path: Path) -> None:
    """OpenCode's auth file is always an object map; defensive coverage."""
    auth_path = tmp_path / ".local" / "share" / "opencode" / "auth.json"
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    auth_path.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    assert read_opencode_provider_keys(home=tmp_path) == {}


def test_called_with_no_home_uses_real_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``home=None`` defaults to ``Path.home()``; smoke-test the codepath
    without overwriting the real user's auth.json by mocking ``Path.home``."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    _write_auth(tmp_path, {"anthropic": {"type": "api", "key": "sk-fakeAB12cd34"}})
    result = read_opencode_provider_keys()
    assert result == {"anthropic": "sk-fakeAB12cd34"}
