from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from config.v2_settings import RoutingSettings
from core.handlers.settings_handler import SettingsHandler


class _StubSettingsManager:
    def __init__(self, routing: RoutingSettings | None):
        self.routing = routing
        self.saved_routing: RoutingSettings | None = None

    def get_channel_routing(self, settings_key: str) -> RoutingSettings | None:
        assert settings_key == "telegram::-100123"
        return self.routing

    def set_channel_routing(self, settings_key: str, routing: RoutingSettings) -> None:
        assert settings_key == "telegram::-100123"
        self.saved_routing = routing


def _make_handler(settings_manager: _StubSettingsManager) -> tuple[SettingsHandler, AsyncMock]:
    send_message = AsyncMock()
    controller = SimpleNamespace(
        config=SimpleNamespace(platform="telegram", language="en"),
        im_client=SimpleNamespace(send_message=send_message),
        settings_manager=settings_manager,
        _get_settings_key=lambda context: "telegram::-100123",
        _get_lang=lambda: "en",
    )
    return SettingsHandler(controller), send_message


def test_handle_routing_update_preserves_existing_codex_agent_when_omitted() -> None:
    settings_manager = _StubSettingsManager(
        RoutingSettings(
            agent_backend="codex",
            codex_agent="reviewer",
            codex_model="gpt-5.4-mini",
            codex_reasoning_effort="low",
        )
    )
    handler, send_message = _make_handler(settings_manager)

    asyncio.run(
        handler.handle_routing_update(
            user_id="42",
            channel_id="-100123",
            backend="codex",
            opencode_agent=None,
            opencode_model=None,
            claude_agent=None,
            claude_model=None,
            codex_model="gpt-5.4",
            codex_reasoning_effort="high",
            notify_user=False,
            platform="telegram",
        )
    )

    assert settings_manager.saved_routing is not None
    assert settings_manager.saved_routing.codex_agent == "reviewer"
    assert settings_manager.saved_routing.codex_model == "gpt-5.4"
    assert settings_manager.saved_routing.codex_reasoning_effort == "high"
    send_message.assert_not_awaited()


def test_handle_routing_update_allows_explicit_codex_agent_clear() -> None:
    settings_manager = _StubSettingsManager(
        RoutingSettings(
            agent_backend="codex",
            codex_agent="reviewer",
            codex_model="gpt-5.4-mini",
            codex_reasoning_effort="low",
        )
    )
    handler, _ = _make_handler(settings_manager)

    asyncio.run(
        handler.handle_routing_update(
            user_id="42",
            channel_id="-100123",
            backend="codex",
            opencode_agent=None,
            opencode_model=None,
            claude_agent=None,
            claude_model=None,
            codex_agent=None,
            codex_model="gpt-5.4",
            codex_reasoning_effort="high",
            notify_user=False,
            platform="telegram",
        )
    )

    assert settings_manager.saved_routing is not None
    assert settings_manager.saved_routing.codex_agent is None
    assert settings_manager.saved_routing.codex_model == "gpt-5.4"
    assert settings_manager.saved_routing.codex_reasoning_effort == "high"


def test_handle_routing_update_handles_first_codex_save_without_existing_routing() -> None:
    settings_manager = _StubSettingsManager(None)
    handler, send_message = _make_handler(settings_manager)

    asyncio.run(
        handler.handle_routing_update(
            user_id="42",
            channel_id="-100123",
            backend="codex",
            opencode_agent=None,
            opencode_model=None,
            claude_agent=None,
            claude_model=None,
            codex_model="gpt-5.4",
            codex_reasoning_effort="high",
            notify_user=False,
            platform="telegram",
        )
    )

    assert settings_manager.saved_routing is not None
    assert settings_manager.saved_routing.agent_backend == "codex"
    assert settings_manager.saved_routing.codex_agent is None
    assert settings_manager.saved_routing.codex_model == "gpt-5.4"
    assert settings_manager.saved_routing.codex_reasoning_effort == "high"
    send_message.assert_not_awaited()
