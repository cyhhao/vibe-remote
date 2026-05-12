"""Contract tests for ``save_opencode_provider_auth`` payload parsing.

These pin the three-state semantics for the optional ``base_url`` field
that Codex flagged in PR #282 round 3:

  * key absent             → leave the stored value untouched
  * key present + blank    → clear the stored value
  * key present + non-blank → upsert (after http(s):// validation)

Without these, re-saving just the API key would silently wipe the
``baseURL`` override.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Tuple

import pytest

from vibe import api


class _FakeServer:
    """Stand-in for the OpenCode HTTP daemon used by the save flow.

    The real ``set_api_key_auth`` PUTs to the OpenCode HTTP server; we
    only need it to succeed so the JSON-write side effects fire.
    """

    def __init__(self) -> None:
        self.set_calls: List[Tuple[str, str]] = []
        self.closes: int = 0

    async def set_api_key_auth(self, provider_id: str, api_key: str) -> None:
        self.set_calls.append((provider_id, api_key))

    async def close_http_session(self, loop) -> None:  # type: ignore[override]
        self.closes += 1


@pytest.fixture()
def fake_save_env(monkeypatch, tmp_path):
    """Wire ``save_opencode_provider_auth`` to a temp HOME + fake server."""
    server = _FakeServer()

    async def _fake_get_server():
        return server

    monkeypatch.setattr(api, "_opencode_get_server", _fake_get_server)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    return server, tmp_path


def _save(provider_id: str, payload: dict) -> dict:
    return api.save_opencode_provider_auth(provider_id, payload)


def test_base_url_absent_leaves_existing_value_untouched(fake_save_env) -> None:
    from vibe.opencode_config import (
        read_opencode_provider_base_url,
        upsert_opencode_provider_base_url,
    )

    server, home = fake_save_env
    upsert_opencode_provider_base_url("openai", "https://existing.example", home=home)

    # Caller re-saves just the api_key: this used to wipe baseURL because
    # the server treated "absent" the same as "empty".
    result = _save("openai", {"api_key": "sk-new"})
    assert result == {"ok": True}
    assert ("openai", "sk-new") in server.set_calls
    assert (
        read_opencode_provider_base_url("openai", home=home)
        == "https://existing.example"
    )


def test_base_url_explicit_empty_clears_stored_value(fake_save_env) -> None:
    from vibe.opencode_config import (
        read_opencode_provider_base_url,
        upsert_opencode_provider_base_url,
    )

    server, home = fake_save_env
    upsert_opencode_provider_base_url("openai", "https://stale.example", home=home)

    result = _save("openai", {"api_key": "sk-new", "base_url": ""})
    assert result == {"ok": True}
    assert read_opencode_provider_base_url("openai", home=home) is None


def test_base_url_explicit_whitespace_clears_stored_value(fake_save_env) -> None:
    from vibe.opencode_config import (
        read_opencode_provider_base_url,
        upsert_opencode_provider_base_url,
    )

    server, home = fake_save_env
    upsert_opencode_provider_base_url("openai", "https://stale.example", home=home)

    result = _save("openai", {"api_key": "sk-new", "base_url": "   "})
    assert result == {"ok": True}
    assert read_opencode_provider_base_url("openai", home=home) is None


def test_base_url_persists_when_provided(fake_save_env) -> None:
    from vibe.opencode_config import read_opencode_provider_base_url

    server, home = fake_save_env
    result = _save(
        "openai",
        {"api_key": "sk-new", "base_url": "https://relay.example/v1"},
    )
    assert result == {"ok": True}
    assert (
        read_opencode_provider_base_url("openai", home=home)
        == "https://relay.example/v1"
    )


@pytest.mark.parametrize(
    "bad_value",
    [
        "relay.example",
        "ftp://relay.example",
        "javascript:alert(1)",
        "//relay.example",
    ],
)
def test_base_url_must_be_http_or_https(fake_save_env, bad_value) -> None:
    server, home = fake_save_env
    result = _save(
        "openai",
        {"api_key": "sk-new", "base_url": bad_value},
    )
    assert result["ok"] is False
    assert "http://" in result["message"] and "https://" in result["message"]
    # The daemon call must not fire for a rejected payload.
    assert server.set_calls == []


def test_base_url_must_be_string(fake_save_env) -> None:
    server, _ = fake_save_env
    result = _save("openai", {"api_key": "sk-new", "base_url": 123})
    assert result["ok"] is False
    assert "string" in result["message"].lower()
    assert server.set_calls == []


def test_base_url_null_clears_stored_value(fake_save_env) -> None:
    from vibe.opencode_config import (
        read_opencode_provider_base_url,
        upsert_opencode_provider_base_url,
    )

    server, home = fake_save_env
    upsert_opencode_provider_base_url("openai", "https://stale.example", home=home)
    result = _save("openai", {"api_key": "sk-new", "base_url": None})
    assert result == {"ok": True}
    assert read_opencode_provider_base_url("openai", home=home) is None


def test_missing_api_key_rejected_before_any_side_effect(fake_save_env) -> None:
    server, _ = fake_save_env
    result = _save("openai", {"base_url": "https://x.example"})
    assert result["ok"] is False
    assert "api_key" in result["message"]
    assert server.set_calls == []


def test_base_url_persist_failure_surfaces_to_caller(monkeypatch, tmp_path) -> None:
    """If the JSON write blows up after the daemon call succeeds, the
    response must say so — silently returning ``ok: True`` is the exact
    "save success, value lost on reload" bug we're fixing.
    """

    from vibe import opencode_config

    server = _FakeServer()

    async def _fake_get_server():
        return server

    monkeypatch.setattr(api, "_opencode_get_server", _fake_get_server)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)

    def _explode(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(opencode_config, "upsert_opencode_provider_base_url", _explode)

    result = _save(
        "openai",
        {"api_key": "sk-new", "base_url": "https://relay.example/v1"},
    )
    assert result["ok"] is False
    assert "disk full" in result["message"]
    # Daemon call happened — the partial state is documented in the
    # error message so the UI can prompt the user.
    assert ("openai", "sk-new") in server.set_calls
