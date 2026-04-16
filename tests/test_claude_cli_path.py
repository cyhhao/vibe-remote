from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.handlers.session_handler as session_handler_module
from config.v2_compat import to_app_config
from config.v2_config import AgentsConfig, ClaudeConfig, RuntimeConfig, SlackConfig, V2Config
from core.handlers.session_handler import SessionHandler
from modules.im import MessageContext


@dataclass
class _ClaudeRuntimeConfig:
    permission_mode: str = "bypassPermissions"
    cwd: str = "/tmp/workdir"
    system_prompt: str | None = None
    default_model: str | None = None
    cli_path: str | None = "/usr/local/bin/claude-proxy"


@dataclass
class _Config:
    platform: str = "slack"
    reply_enhancements: bool = False
    claude: _ClaudeRuntimeConfig = field(default_factory=_ClaudeRuntimeConfig)


class _Sessions:
    @staticmethod
    def get_claude_session_id(settings_key, base_session_id):
        assert settings_key == "test::C123"
        assert base_session_id == "slack_C123"
        return None

    @staticmethod
    def get_agent_session_id(settings_key, base_session_id, agent_name):
        return None


class _SettingsManager:
    def __init__(self) -> None:
        self.sessions = _Sessions()

    @staticmethod
    def get_channel_settings(settings_key):
        assert settings_key == "test::C123"
        return None

    @staticmethod
    def get_channel_routing(settings_key):
        return None


class _Controller:
    def __init__(self, working_path: Path) -> None:
        self.config = _Config()
        self.im_client = type("IM", (), {"formatter": None})()
        self.settings_manager = _SettingsManager()
        self.platform_settings_managers = {"slack": self.settings_manager}
        self.session_manager = object()
        self.claude_sessions = {}
        self.receiver_tasks = {}
        self.stored_session_mappings = {}
        self._working_path = working_path

    def get_cwd(self, context) -> str:
        return str(self._working_path)

    @staticmethod
    def _get_settings_key(context) -> str:
        return context.channel_id

    @staticmethod
    def _get_session_key(context) -> str:
        return f"{getattr(context, 'platform', None) or 'test'}::{context.channel_id}"

    def get_settings_manager_for_context(self, context=None):
        return self.settings_manager


def _run_session(handler: SessionHandler, context: MessageContext):
    return asyncio.run(handler.get_or_create_claude_session(context))


class _StubClaudeAgentOptions:
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
        if not hasattr(self, "cli_path"):
            self.cli_path = None
        self.continue_conversation = False


def test_to_app_config_preserves_claude_cli_path() -> None:
    v2 = V2Config(
        mode="self_host",
        version="2",
        slack=SlackConfig(),
        runtime=RuntimeConfig(default_cwd="/tmp/workdir"),
        agents=AgentsConfig(claude=ClaudeConfig(cli_path="/usr/local/bin/claude-proxy")),
    )

    compat = to_app_config(v2)

    assert compat.claude.cli_path == "/usr/local/bin/claude-proxy"


def test_session_handler_passes_configured_claude_cli_path(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _StubClaudeSDKClient:
        def __init__(self, options):
            captured["options"] = options

        async def connect(self) -> None:
            captured["connected"] = True

    monkeypatch.setattr(session_handler_module, "ClaudeAgentOptions", _StubClaudeAgentOptions)
    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)

    controller = _Controller(tmp_path)
    handler = SessionHandler(controller)
    context = MessageContext(user_id="U123", channel_id="C123")

    client = _run_session(handler, context)

    assert captured["connected"] is True
    assert captured["options"].cli_path == "/usr/local/bin/claude-proxy"
    assert controller.claude_sessions[f"slack_C123:{tmp_path}"] is client
    assert getattr(client, "_vibe_runtime_base_session_id") == "slack_C123"
    assert getattr(client, "_vibe_runtime_session_key") == f"slack_C123:{tmp_path}"


def test_session_handler_keeps_sdk_default_for_default_claude_binary(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _StubClaudeSDKClient:
        def __init__(self, options):
            captured["options"] = options

        async def connect(self) -> None:
            captured["connected"] = True

    monkeypatch.setattr(session_handler_module, "ClaudeAgentOptions", _StubClaudeAgentOptions)
    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)

    controller = _Controller(tmp_path)
    controller.config.claude.cli_path = "claude"
    handler = SessionHandler(controller)
    context = MessageContext(user_id="U123", channel_id="C123")

    _run_session(handler, context)

    assert captured["connected"] is True
    assert captured["options"].cli_path is None


def test_session_handler_passes_non_default_claude_command_name(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _StubClaudeSDKClient:
        def __init__(self, options):
            captured["options"] = options

        async def connect(self) -> None:
            captured["connected"] = True

    monkeypatch.setattr(session_handler_module, "ClaudeAgentOptions", _StubClaudeAgentOptions)
    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)

    controller = _Controller(tmp_path)
    controller.config.claude.cli_path = "claude-proxy"
    handler = SessionHandler(controller)
    context = MessageContext(user_id="U123", channel_id="C123")

    _run_session(handler, context)

    assert captured["connected"] is True
    assert captured["options"].cli_path == "claude-proxy"


def test_session_handler_expands_tilde_in_claude_cli_path(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _StubClaudeSDKClient:
        def __init__(self, options):
            captured["options"] = options

        async def connect(self) -> None:
            captured["connected"] = True

    monkeypatch.setattr(session_handler_module, "ClaudeAgentOptions", _StubClaudeAgentOptions)
    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)

    controller = _Controller(tmp_path)
    controller.config.claude.cli_path = "~/bin/claude"
    handler = SessionHandler(controller)
    context = MessageContext(user_id="U123", channel_id="C123")

    _run_session(handler, context)

    assert captured["connected"] is True
    assert captured["options"].cli_path == str(Path("~/bin/claude").expanduser())


def test_session_handler_uses_scheduled_turn_source_for_dm_anchor(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _ScheduledSessions:
        def __init__(self) -> None:
            self.lookup = None

        def get_claude_session_id(self, settings_key, base_session_id):
            self.lookup = (settings_key, base_session_id)
            return None

        @staticmethod
        def get_agent_session_id(settings_key, base_session_id, agent_name):
            return None

    class _ScheduledSettingsManager:
        def __init__(self) -> None:
            self.sessions = _ScheduledSessions()

        @staticmethod
        def get_channel_settings(settings_key):
            return None

        @staticmethod
        def get_channel_routing(settings_key):
            return None

    class _ScheduledController:
        def __init__(self, working_path: Path) -> None:
            self.config = _Config()
            self.im_client = type(
                "IM",
                (),
                {
                    "formatter": None,
                    "should_use_thread_for_dm_session": lambda self: True,
                    "should_use_thread_for_reply": lambda self: True,
                },
            )()
            self.settings_manager = _ScheduledSettingsManager()
            self.platform_settings_managers = {"slack": self.settings_manager}
            self.session_manager = object()
            self.claude_sessions = {}
            self.receiver_tasks = {}
            self.stored_session_mappings = {}
            self._working_path = working_path

        def get_cwd(self, context) -> str:
            return str(self._working_path)

        @staticmethod
        def _get_settings_key(context) -> str:
            return context.user_id if (context.platform_specific or {}).get("is_dm") else context.channel_id

        @staticmethod
        def _get_session_key(context) -> str:
            settings_key = _ScheduledController._get_settings_key(context)
            return f"{getattr(context, 'platform', None) or 'test'}::{settings_key}"

        def get_settings_manager_for_context(self, context=None):
            return self.settings_manager

    class _StubClaudeSDKClient:
        def __init__(self, options):
            captured["options"] = options

        async def connect(self) -> None:
            captured["connected"] = True

    monkeypatch.setattr(session_handler_module, "ClaudeAgentOptions", _StubClaudeAgentOptions)
    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)

    controller = _ScheduledController(tmp_path)
    handler = SessionHandler(controller)
    precomputed_base = "slack_scheduled-anchor-123"
    context = MessageContext(
        user_id="U123",
        channel_id="D123",
        message_id="scheduled:task-1:exec-1",
        platform="slack",
        platform_specific={
            "is_dm": True,
            "turn_source": "scheduled",
            "turn_base_session_id": precomputed_base,
        },
    )

    client = _run_session(handler, context)

    assert captured["connected"] is True
    assert controller.settings_manager.sessions.lookup is not None
    settings_key, base_session_id = controller.settings_manager.sessions.lookup
    assert settings_key == "slack::U123"
    assert base_session_id == precomputed_base
    assert getattr(client, "_vibe_runtime_base_session_id") == base_session_id
    assert getattr(client, "_vibe_runtime_session_key") == f"{base_session_id}:{tmp_path}"


def test_session_handler_evicts_idle_claude_session(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _StubClaudeSDKClient:
        def __init__(self, options):
            captured["options"] = options
            captured["disconnects"] = 0

        async def connect(self) -> None:
            captured["connected"] = True

        async def disconnect(self) -> None:
            captured["disconnects"] += 1

    monkeypatch.setattr(session_handler_module, "ClaudeAgentOptions", _StubClaudeAgentOptions)
    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)
    monkeypatch.setattr(session_handler_module.time, "monotonic", lambda: 1000.0)

    controller = _Controller(tmp_path)
    handler = SessionHandler(controller)
    context = MessageContext(user_id="U123", channel_id="C123")

    _run_session(handler, context)

    composite_key = f"slack_C123:{tmp_path}"
    handler.session_last_activity[composite_key] = 0.0

    evicted = asyncio.run(handler.evict_idle_sessions(600))

    assert evicted == 1
    assert captured["disconnects"] == 1
    assert composite_key not in controller.claude_sessions
    assert composite_key not in handler.session_last_activity


def test_session_handler_keeps_active_claude_session(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _StubClaudeSDKClient:
        def __init__(self, options):
            captured["options"] = options
            captured["disconnects"] = 0

        async def connect(self) -> None:
            captured["connected"] = True

        async def disconnect(self) -> None:
            captured["disconnects"] += 1

    monkeypatch.setattr(session_handler_module, "ClaudeAgentOptions", _StubClaudeAgentOptions)
    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)
    monkeypatch.setattr(session_handler_module.time, "monotonic", lambda: 1000.0)

    controller = _Controller(tmp_path)
    handler = SessionHandler(controller)
    context = MessageContext(user_id="U123", channel_id="C123")

    _run_session(handler, context)

    composite_key = f"slack_C123:{tmp_path}"
    handler.session_last_activity[composite_key] = 0.0
    handler.active_sessions.add(composite_key)

    evicted = asyncio.run(handler.evict_idle_sessions(600))

    assert evicted == 0
    assert captured["disconnects"] == 0
    assert composite_key in controller.claude_sessions


def test_cleanup_session_swallows_cancelled_receiver_task(monkeypatch, tmp_path: Path) -> None:
    class _StubClaudeSDKClient:
        def __init__(self, options):
            self.disconnects = 0

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            self.disconnects += 1

    monkeypatch.setattr(session_handler_module, "ClaudeAgentOptions", _StubClaudeAgentOptions)
    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)

    controller = _Controller(tmp_path)
    handler = SessionHandler(controller)
    context = MessageContext(user_id="U123", channel_id="C123")
    client = _run_session(handler, context)
    composite_key = f"slack_C123:{tmp_path}"

    async def _exercise_cleanup() -> None:
        async def _receiver():
            await asyncio.Future()

        controller.receiver_tasks[composite_key] = asyncio.create_task(_receiver())
        await asyncio.sleep(0)
        await handler.cleanup_session(composite_key)

    asyncio.run(_exercise_cleanup())

    assert client.disconnects == 1
    assert composite_key not in controller.receiver_tasks
    assert composite_key not in controller.claude_sessions


def test_evict_idle_sessions_rechecks_active_state_before_cleanup(monkeypatch, tmp_path: Path) -> None:
    class _StubClaudeSDKClient:
        def __init__(self, options):
            self.disconnects = 0

        async def connect(self) -> None:
            return None

        async def disconnect(self) -> None:
            self.disconnects += 1

    class _FlippingActiveSet(set):
        def __init__(self, target_key: str):
            super().__init__()
            self.target_key = target_key
            self._checks = 0

        def __contains__(self, item):
            if item == self.target_key:
                self._checks += 1
                return self._checks >= 2
            return super().__contains__(item)

    monkeypatch.setattr(session_handler_module, "ClaudeAgentOptions", _StubClaudeAgentOptions)
    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)
    monkeypatch.setattr(session_handler_module.time, "monotonic", lambda: 1000.0)

    controller = _Controller(tmp_path)
    handler = SessionHandler(controller)
    context = MessageContext(user_id="U123", channel_id="C123")
    client = _run_session(handler, context)
    composite_key = f"slack_C123:{tmp_path}"
    handler.session_last_activity[composite_key] = 0.0
    handler.active_sessions = _FlippingActiveSet(composite_key)

    evicted = asyncio.run(handler.evict_idle_sessions(600))

    assert evicted == 0
    assert client.disconnects == 0
    assert composite_key in controller.claude_sessions
