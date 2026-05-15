from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from modules.agents.base import AgentRequest
from modules.agents.opencode.session import OpenCodeSessionManager
from modules.im import MessageContext


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
        get_agent_session_row_id=Mock(return_value="sesk8m4q2p7x"),
    )
    manager = OpenCodeSessionManager(SimpleNamespace(sessions=sessions), "opencode")
    server = SimpleNamespace(get_session=AsyncMock(return_value={"id": "oc-session-1"}))
    request = _request()

    session_id = asyncio.run(manager.get_or_create_session_id(request, server))

    assert session_id == "oc-session-1"
    assert request.context.platform_specific["agent_session_id"] == "sesk8m4q2p7x"
    sessions.get_agent_session_row_id.assert_called_once_with(
        "slack::channel::C1",
        "base-1:/repo",
        "opencode",
    )
