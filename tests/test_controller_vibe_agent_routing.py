from __future__ import annotations

from types import SimpleNamespace

from core.controller import Controller
from modules.im import MessageContext


class _StubController(Controller):
    def __init__(self):
        pass


def _context() -> MessageContext:
    return MessageContext(
        user_id="U123",
        channel_id="C123",
        platform="slack",
        platform_specific={"is_dm": False},
    )


def test_resolve_vibe_agent_for_context_uses_catalog_default_when_scope_has_no_agent() -> None:
    controller = _StubController()
    default_agent = SimpleNamespace(name="reviewer", backend="codex")
    controller.primary_platform = "slack"
    controller._get_settings_key = lambda context: context.channel_id
    controller.vibe_agent_store = SimpleNamespace(
        require=lambda name: (_ for _ in ()).throw(ValueError(f"agent '{name}' not found")),
        get_default_agent=lambda: default_agent,
    )
    controller.get_settings_manager_for_context = lambda context: SimpleNamespace(
        get_channel_routing=lambda settings_key: SimpleNamespace(agent_name=None, agent_backend="opencode")
    )
    assert controller.resolve_vibe_agent_for_context(_context(), required=False) is default_agent


def test_resolve_agent_for_context_respects_legacy_backend_before_default_agent() -> None:
    controller = _StubController()
    default_agent = SimpleNamespace(name="reviewer", backend="codex")
    controller.primary_platform = "slack"
    controller._get_settings_key = lambda context: context.channel_id
    controller.agent_service = SimpleNamespace(agents={"opencode": object(), "codex": object()})
    controller.vibe_agent_store = SimpleNamespace(
        require=lambda name: (_ for _ in ()).throw(ValueError(f"agent '{name}' not found")),
        get_default_agent=lambda: default_agent,
    )
    controller.get_settings_manager_for_context = lambda context: SimpleNamespace(
        get_channel_routing=lambda settings_key: SimpleNamespace(agent_name=None, agent_backend="opencode")
    )
    controller.agent_router = SimpleNamespace(resolve=lambda platform, settings_key: "claude")

    assert controller.resolve_agent_for_context(_context()) == "opencode"
