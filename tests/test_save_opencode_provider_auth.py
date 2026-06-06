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
        self.remove_calls: List[str] = []
        self.closes: int = 0

    async def set_api_key_auth(self, provider_id: str, api_key: str) -> None:
        self.set_calls.append((provider_id, api_key))

    async def remove_provider_auth(self, provider_id: str) -> None:
        self.remove_calls.append(provider_id)

    async def get_providers(self):
        return {"all": [{"id": "openai", "name": "OpenAI"}], "connected": ["openai"]}

    async def get_provider_auth(self):
        return {}

    async def get_available_models(self, directory):
        return {"providers": [{"id": "openai", "models": {"gpt-5": {}}}]}

    async def get_available_agents(self, directory):
        return []

    async def get_default_config(self, directory):
        return {}

    async def close_http_session(self, loop) -> None:  # type: ignore[override]
        self.closes += 1


class _FakeModelServer:
    async def get_available_models(self, directory):
        return {
            "providers": [
                {
                    "id": "deepseek",
                    "models": {
                        "deepseek-chat": {},
                    },
                }
            ]
        }

    async def close_http_session(self, loop) -> None:  # type: ignore[override]
        pass


@pytest.fixture()
def fake_save_env(monkeypatch, tmp_path):
    """Wire ``save_opencode_provider_auth`` to a temp HOME + fake server."""
    server = _FakeServer()

    async def _fake_get_server():
        return server

    monkeypatch.setattr(api, "_opencode_get_server", _fake_get_server)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(api, "restart_backend", lambda backend: {"ok": True})
    monkeypatch.setattr(api, "_OPENCODE_OPTIONS_CACHE", {})
    return server, tmp_path


@pytest.fixture()
def fake_model_env(monkeypatch, tmp_path):
    async def _fake_get_server():
        return _FakeModelServer()

    monkeypatch.setattr(api, "_opencode_get_server", _fake_get_server)
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.setattr(api, "restart_backend", lambda backend: {"ok": True})
    monkeypatch.setattr(api, "_OPENCODE_OPTIONS_CACHE", {"x": {"data": {}, "updated_at": 1}})
    return tmp_path


def _save(provider_id: str, payload: dict) -> dict:
    return api.save_opencode_provider_auth(provider_id, payload)


def _save_model(provider_id: str, payload: dict) -> dict:
    return asyncio.run(api.save_opencode_provider_model_async(provider_id, payload))


def _delete_model(provider_id: str, model_id: str) -> dict:
    return asyncio.run(api.delete_opencode_provider_model_async(provider_id, model_id))


def _save_custom(payload: dict) -> dict:
    return asyncio.run(api.save_opencode_custom_provider_async(payload))


def _delete_custom(provider_id: str) -> dict:
    return asyncio.run(api.delete_opencode_custom_provider_async(provider_id))


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
    # Save now also triggers ``restart_backend("opencode")`` so the
    # daemon's in-memory cache picks up the new auth; ignore the
    # ``restart`` key for the per-field assertions below.
    assert result.get("ok") is True
    assert ("openai", "sk-new") in server.set_calls
    assert (
        read_opencode_provider_base_url("openai", home=home)
        == "https://existing.example"
    )


def test_save_provider_model_rejects_builtin_duplicate(fake_model_env) -> None:
    result = _save_model("deepseek", {"model_id": "deepseek-chat"})
    assert result == {"ok": False, "message": "model_id already exists"}


def test_save_custom_provider_persists_config_and_key(fake_save_env) -> None:
    from vibe.opencode_config import read_opencode_custom_providers

    server, home = fake_save_env
    result = _save_custom(
        {
            "provider_id": "my-relay",
            "name": "My Relay",
            "adapter": "openai-compatible",
            "base_url": "https://relay.example/v1",
            "api_key": "sk-relay",
        }
    )

    assert result["ok"] is True
    assert result["provider_id"] == "my-relay"
    assert ("my-relay", "sk-relay") in server.set_calls
    assert "my-relay" in read_opencode_custom_providers(home=home)


def test_save_custom_provider_rejects_builtin_id(fake_save_env) -> None:
    result = _save_custom(
        {
            "provider_id": "openai",
            "name": "OpenAI Relay",
            "adapter": "openai-compatible",
            "base_url": "https://relay.example/v1",
            "api_key": "sk-relay",
        }
    )

    assert result == {"ok": False, "message": "provider_id already exists"}


def test_delete_custom_provider_removes_config_and_auth(fake_save_env) -> None:
    from vibe.opencode_config import read_opencode_custom_providers

    server, home = fake_save_env
    _save_custom(
        {
            "provider_id": "my-relay",
            "name": "My Relay",
            "adapter": "openai-compatible",
            "base_url": "https://relay.example/v1",
            "api_key": "sk-relay",
        }
    )

    result = _delete_custom("my-relay")

    assert result["ok"] is True
    assert server.remove_calls == ["my-relay"]
    assert read_opencode_custom_providers(home=home) == {}


def test_save_provider_model_persists_user_model_and_clears_cache(fake_model_env) -> None:
    from vibe.opencode_config import read_opencode_provider_user_models

    home = fake_model_env
    result = _save_model(
        "deepseek",
        {"model_id": "deepseek-v4-flash", "reasoning_efforts": ["low", "high"]},
    )

    assert result["ok"] is True
    assert api._OPENCODE_OPTIONS_CACHE == {}
    model = read_opencode_provider_user_models("deepseek", home=home)["deepseek-v4-flash"]
    assert model["variants"] == {"low": {"effort": "low"}, "high": {"effort": "high"}}


def test_delete_provider_model_only_removes_user_managed_models(fake_model_env) -> None:
    home = fake_model_env
    _save_model("deepseek", {"model_id": "deepseek-v4-flash"})

    result = _delete_model("deepseek", "deepseek-v4-flash")

    assert result["ok"] is True
    from vibe.opencode_config import read_opencode_provider_user_models

    assert read_opencode_provider_user_models("deepseek", home=home) == {}


def test_delete_provider_model_rejects_builtin_model(fake_model_env) -> None:
    result = _delete_model("deepseek", "deepseek-chat")
    assert result == {"ok": False, "message": "Only user-managed models can be removed"}


def test_base_url_explicit_empty_clears_stored_value(fake_save_env) -> None:
    from vibe.opencode_config import (
        read_opencode_provider_base_url,
        upsert_opencode_provider_base_url,
    )

    server, home = fake_save_env
    upsert_opencode_provider_base_url("openai", "https://stale.example", home=home)

    result = _save("openai", {"api_key": "sk-new", "base_url": ""})
    # Save now also triggers ``restart_backend("opencode")`` so the
    # daemon's in-memory cache picks up the new auth; ignore the
    # ``restart`` key for the per-field assertions below.
    assert result.get("ok") is True
    assert read_opencode_provider_base_url("openai", home=home) is None


def test_base_url_explicit_whitespace_clears_stored_value(fake_save_env) -> None:
    from vibe.opencode_config import (
        read_opencode_provider_base_url,
        upsert_opencode_provider_base_url,
    )

    server, home = fake_save_env
    upsert_opencode_provider_base_url("openai", "https://stale.example", home=home)

    result = _save("openai", {"api_key": "sk-new", "base_url": "   "})
    # Save now also triggers ``restart_backend("opencode")`` so the
    # daemon's in-memory cache picks up the new auth; ignore the
    # ``restart`` key for the per-field assertions below.
    assert result.get("ok") is True
    assert read_opencode_provider_base_url("openai", home=home) is None


def test_base_url_persists_when_provided(fake_save_env) -> None:
    from vibe.opencode_config import read_opencode_provider_base_url

    server, home = fake_save_env
    result = _save(
        "openai",
        {"api_key": "sk-new", "base_url": "https://relay.example/v1"},
    )
    # Save now also triggers ``restart_backend("opencode")`` so the
    # daemon's in-memory cache picks up the new auth; ignore the
    # ``restart`` key for the per-field assertions below.
    assert result.get("ok") is True
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
    # Save now also triggers ``restart_backend("opencode")`` so the
    # daemon's in-memory cache picks up the new auth; ignore the
    # ``restart`` key for the per-field assertions below.
    assert result.get("ok") is True
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
