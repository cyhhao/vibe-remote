from __future__ import annotations

from types import SimpleNamespace

from core.controller import Controller
from modules.im import MessageContext


class _StubController(Controller):
    def __init__(self):
        pass


def test_resolve_vibe_agent_for_context_uses_catalog_default_when_scope_has_no_agent() -> None:
    controller = _StubController()
    default_agent = SimpleNamespace(name="reviewer", backend="codex")
    controller.primary_platform = "slack"
    controller.vibe_agent_store = SimpleNamespace(
        require=lambda name: (_ for _ in ()).throw(ValueError(f"agent '{name}' not found")),
        get_default_agent=lambda: default_agent,
    )
    controller.get_settings_manager_for_context = lambda context: SimpleNamespace(
        get_channel_routing=lambda settings_key: SimpleNamespace(agent_name=None, agent_backend="opencode")
    )
    context = MessageContext(
        user_id="U123",
        channel_id="C123",
        platform="slack",
        platform_specific={"is_dm": False},
    )

    assert controller.resolve_vibe_agent_for_context(context, required=False) is default_agent
