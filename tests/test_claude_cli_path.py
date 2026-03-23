from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import sys
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.handlers.session_handler as session_handler_module
import vibe.api as vibe_api
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
        assert settings_key == "C123"
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
        assert settings_key == "C123"
        return None


class _Controller:
    def __init__(self, working_path: Path) -> None:
        self.config = _Config()
        self.im_client = type("IM", (), {"formatter": None})()
        self.settings_manager = _SettingsManager()
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


def _run_session(handler: SessionHandler, context: MessageContext):
    return asyncio.run(handler.get_or_create_claude_session(context))


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

    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)

    controller = _Controller(tmp_path)
    handler = SessionHandler(controller)
    context = MessageContext(user_id="U123", channel_id="C123")

    client = _run_session(handler, context)

    assert captured["connected"] is True
    assert captured["options"].cli_path == "/usr/local/bin/claude-proxy"
    assert controller.claude_sessions[f"slack_C123:{tmp_path}"] is client


def test_session_handler_keeps_sdk_default_for_default_claude_binary(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _StubClaudeSDKClient:
        def __init__(self, options):
            captured["options"] = options

        async def connect(self) -> None:
            captured["connected"] = True

    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)
    monkeypatch.setattr(vibe_api, "resolve_cli_path", lambda binary: None)

    controller = _Controller(tmp_path)
    controller.config.claude.cli_path = "claude"
    handler = SessionHandler(controller)
    context = MessageContext(user_id="U123", channel_id="C123")

    _run_session(handler, context)

    assert captured["connected"] is True
    assert captured["options"].cli_path is None


def test_session_handler_uses_resolved_path_for_claude_binary(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _StubClaudeSDKClient:
        def __init__(self, options):
            captured["options"] = options

        async def connect(self) -> None:
            captured["connected"] = True

    resolved_path = "/opt/homebrew/bin/claude"

    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)
    monkeypatch.setattr(vibe_api, "resolve_cli_path", lambda binary: resolved_path)

    controller = _Controller(tmp_path)
    controller.config.claude.cli_path = "claude"
    handler = SessionHandler(controller)
    context = MessageContext(user_id="U123", channel_id="C123")

    _run_session(handler, context)

    assert captured["connected"] is True
    assert captured["options"].cli_path == resolved_path


def test_session_handler_expands_tilde_in_claude_cli_path(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}

    class _StubClaudeSDKClient:
        def __init__(self, options):
            captured["options"] = options

        async def connect(self) -> None:
            captured["connected"] = True

    monkeypatch.setattr(session_handler_module, "ClaudeSDKClient", _StubClaudeSDKClient)

    controller = _Controller(tmp_path)
    controller.config.claude.cli_path = "~/bin/claude"
    handler = SessionHandler(controller)
    context = MessageContext(user_id="U123", channel_id="C123")

    _run_session(handler, context)

    assert captured["connected"] is True
    assert captured["options"].cli_path == str(Path("~/bin/claude").expanduser())
