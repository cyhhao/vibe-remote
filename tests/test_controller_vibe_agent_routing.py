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
        get_builtin_default_agent_for_backend=lambda backend: None,
        get_default_agent=lambda: default_agent,
    )
    controller.get_settings_manager_for_context = lambda context: SimpleNamespace(
        get_channel_routing=lambda settings_key: SimpleNamespace(agent_name=None, agent_backend="opencode")
    )
    assert controller.resolve_vibe_agent_for_context(_context(), required=False) is default_agent


def test_resolve_vibe_agent_for_context_maps_legacy_backend_to_builtin_agent() -> None:
    controller = _StubController()
    builtin_agent = SimpleNamespace(name="opencode", backend="opencode")
    controller.primary_platform = "slack"
    controller._get_settings_key = lambda context: context.channel_id
    controller.vibe_agent_store = SimpleNamespace(
        require=lambda name: (_ for _ in ()).throw(ValueError(f"agent '{name}' not found")),
        get_builtin_default_agent_for_backend=lambda backend: builtin_agent if backend == "opencode" else None,
        get_default_agent=lambda: SimpleNamespace(name="reviewer", backend="codex"),
    )
    controller.get_settings_manager_for_context = lambda context: SimpleNamespace(
        get_channel_routing=lambda settings_key: SimpleNamespace(agent_name=None, agent_backend="opencode")
    )

    assert controller.resolve_vibe_agent_for_context(_context(), required=False) is builtin_agent


def test_resolve_agent_for_context_uses_legacy_backend_builtin_agent() -> None:
    controller = _StubController()
    builtin_agent = SimpleNamespace(name="opencode", backend="opencode")
    controller.primary_platform = "slack"
    controller._get_settings_key = lambda context: context.channel_id
    controller.agent_service = SimpleNamespace(agents={"opencode": object(), "codex": object()})
    controller.vibe_agent_store = SimpleNamespace(
        require=lambda name: (_ for _ in ()).throw(ValueError(f"agent '{name}' not found")),
        get_builtin_default_agent_for_backend=lambda backend: builtin_agent if backend == "opencode" else None,
        get_default_agent=lambda: SimpleNamespace(name="reviewer", backend="codex"),
    )
    controller.get_settings_manager_for_context = lambda context: SimpleNamespace(
        get_channel_routing=lambda settings_key: SimpleNamespace(agent_name=None, agent_backend="opencode")
    )
    controller.agent_router = SimpleNamespace(resolve=lambda platform, settings_key: "claude")

    assert controller.resolve_agent_for_context(_context()) == "opencode"


def test_codex_overrides_prefer_scope_level_model_and_reasoning() -> None:
    controller = _StubController()
    controller.primary_platform = "slack"
    controller._get_settings_key = lambda context: context.channel_id
    routing = SimpleNamespace(
        codex_agent=None,
        codex_model="gpt-5.4",
        codex_reasoning_effort="high",
        model="gpt-5.5",
        reasoning_effort="xhigh",
    )
    controller.get_settings_manager_for_context = lambda context: SimpleNamespace(
        get_channel_routing=lambda settings_key: routing
    )

    assert controller.get_codex_overrides(_context()) == (None, "gpt-5.5", "xhigh")
