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

    async def emit_agent_message(self, context, message_type, text, parse_mode="markdown", *, is_error=False):
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

    async def test_no_visible_result_with_duration_hidden_settles_via_outbound(self):
        # show_duration off + empty result/suffix is still a TERMINAL turn: it is
        # settled through the OUTBOUND status chokepoint — an empty terminal result
        # emit (→ dot idle/failed + releases the web-Chat stream) instead of being
        # left to hang to the 600s timeout (Codex P2).
        controller = _StubController()
        controller.config = SimpleNamespace(show_duration=False)
        agent = _StubAgent(controller)
        context = MessageContext(user_id="U1", channel_id="C1", platform="avibe")

        await agent.emit_result_message(context, "", subtype="success", duration_ms=0)

        # An empty terminal result is emitted (no visible text); the dispatcher's
        # result path settles the dot + releases the stream.
        self.assertEqual(controller.messages, [("result", "", "markdown")])


class AgentSessionIdContextTests(unittest.TestCase):
    def test_bind_agent_session_id_attaches_returned_public_session_id(self):
        controller = _StubController()
        controller.sessions = SimpleNamespace(
            bind_agent_session=lambda session_key, agent_name, anchor, native_id: "sesk8m4q2p7x"
        )
        agent = _StubAgent(controller)
        context = MessageContext(
            user_id="U1",
            channel_id="C1",
            platform="slack",
            platform_specific={"platform": "slack"},
        )
        request = AgentRequest(
            context=context,
            message="hello",
            working_path="/tmp/work",
            base_session_id="slack_171717.123",
            composite_session_id="slack_171717.123:/tmp/work",
            session_key="slack::C1",
        )

        session_id = agent.bind_agent_session_id(request, "thread-native-1")

        self.assertEqual(session_id, "sesk8m4q2p7x")
        self.assertEqual(request.context.platform_specific["agent_session_id"], "sesk8m4q2p7x")


if __name__ == "__main__":
    unittest.main()
