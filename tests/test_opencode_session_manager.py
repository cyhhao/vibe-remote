from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from modules.agents.base import AgentRequest
from modules.agents.opencode.session import OpenCodeSessionManager
from modules.im import MessageContext
from modules.sessions_facade import SessionsFacade


def _request() -> AgentRequest:
    return AgentRequest(
        context=MessageContext(user_id="U1", channel_id="C1", platform_specific={}),
        message="hello",
        working_path="/repo",
        base_session_id="base-1",
        composite_session_id="base-1:/repo",
        session_key="slack::channel::C1",
    )


def test_opencode_reused_session_attaches_agent_session_id() -> None:
    sessions = SimpleNamespace(
        get_agent_session_id=Mock(return_value="oc-session-1"),
        ensure_agent_session_id=Mock(return_value="sesk8m4q2p7x"),
        bind_agent_session=Mock(return_value="sesk8m4q2p7x"),
    )
    manager = OpenCodeSessionManager(SimpleNamespace(sessions=sessions), "opencode")
    server = SimpleNamespace(get_session=AsyncMock(return_value={"id": "oc-session-1"}))
    request = _request()

    session_id = asyncio.run(manager.get_or_create_session_id(request, server))

    assert session_id == "oc-session-1"
    assert request.context.platform_specific["agent_session_id"] == "sesk8m4q2p7x"
    sessions.ensure_agent_session_id.assert_called_once_with(
        "slack::channel::C1",
        "opencode",
        "base-1:/repo",
    )
    sessions.bind_agent_session.assert_called_once_with(
        "slack::channel::C1",
        "opencode",
        "base-1:/repo",
        "oc-session-1",
    )


def test_session_facade_ensure_fallback_does_not_clear_existing_native_session() -> None:
    class _LegacyStore:
        def __init__(self):
            self.maps = {"slack::channel::C1": {"codex": {"base-1": "thread-old"}}}

        def get_agent_map(self, user_id, agent_name):
            return self.maps.setdefault(user_id, {}).setdefault(agent_name, {})

    facade = SessionsFacade(_LegacyStore())

    assert facade.ensure_agent_session_id("slack::channel::C1", "codex", "base-1") is None
    assert facade.get_agent_session_id("slack::channel::C1", "base-1", "codex") == "thread-old"
