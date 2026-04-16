from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.agents.base import AgentRequest, BaseAgent
from modules.im import MessageContext


class _StubController:
    def __init__(self):
        self.config = SimpleNamespace(show_duration=True)
        self.im_client = SimpleNamespace(formatter=None)
        self.settings_manager = SimpleNamespace(sessions=None)
        self.messages = []

    async def emit_agent_message(self, context, message_type, text, parse_mode="markdown"):
        self.messages.append((message_type, text, parse_mode))


class _StubAgent(BaseAgent):
    name = "stub"

    async def handle_message(self, request: AgentRequest) -> None:
        return None


class AgentSilentResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_silent_only_result_suppresses_duration_wrapper(self):
        controller = _StubController()
        agent = _StubAgent(controller)
        context = MessageContext(user_id="U1", channel_id="C1", platform="slack")

        await agent.emit_result_message(
            context,
            "<silent>not relevant</silent>",
            subtype="success",
            duration_ms=1234,
        )

        self.assertEqual(controller.messages, [("result", "", "markdown")])


if __name__ == "__main__":
    unittest.main()
