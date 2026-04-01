from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.agents.base import AgentRequest
from modules.agents.opencode.session import OpenCodeSessionManager
from modules.im import MessageContext


class _FakeSessions:
    def __init__(self) -> None:
        self.mappings: list[tuple[str, str, str, str]] = []

    def get_agent_session_id(self, session_key, base_session_id, agent_name=None):
        return None

    def set_agent_session_mapping(self, session_key, agent_name, base_session_id, session_id):
        self.mappings.append((session_key, agent_name, base_session_id, session_id))


class OpenCodeSessionManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_or_create_session_id_does_not_force_title(self):
        sessions = _FakeSessions()
        manager = OpenCodeSessionManager(SimpleNamespace(sessions=sessions), "opencode")
        server = SimpleNamespace(
            create_session=AsyncMock(return_value={"id": "session-1"}),
            get_session=AsyncMock(return_value=None),
        )
        request = AgentRequest(
            context=MessageContext(user_id="U1", channel_id="C1", platform="slack"),
            message="hello",
            working_path="/tmp/work",
            base_session_id="slack_123",
            composite_session_id="slack_123:/tmp/work",
            session_key="slack::channel::C1",
        )

        session_id = await manager.get_or_create_session_id(request, server)

        self.assertEqual(session_id, "session-1")
        server.create_session.assert_awaited_once_with(directory="/tmp/work")

