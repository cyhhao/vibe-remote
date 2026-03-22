import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.agents.claude_agent import ClaudeAgent


class _StubSessions:
    @staticmethod
    def list_agent_sessions(settings_key, agent_name):
        assert settings_key == "wechat-user"
        assert agent_name == "claude"
        return {"wechat_o9": "session-id"}

    @staticmethod
    def clear_agent_sessions(settings_key, agent_name):
        return None


class _StubSessionManager:
    def __init__(self):
        self.cleared = []

    async def clear_session(self, settings_key):
        self.cleared.append(settings_key)


class _StubClient:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class _StubSettingsManager:
    sessions = _StubSessions()


class _StubController:
    def __init__(self):
        self.config = type("Config", (), {})()
        self.im_client = object()
        self.settings_manager = _StubSettingsManager()
        self.session_handler = object()
        self.session_manager = _StubSessionManager()
        self.receiver_tasks = {}
        self.claude_sessions = {}
        self.claude_client = None


class ClaudeAgentSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_sessions_cancels_receiver_tasks_for_cleared_session(self):
        controller = _StubController()
        agent = ClaudeAgent(controller)
        session_key = "wechat_o9:/tmp/work"
        client = _StubClient()
        controller.claude_sessions[session_key] = client

        task_cancelled = asyncio.Event()

        async def _receiver():
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                task_cancelled.set()
                raise

        controller.receiver_tasks[session_key] = asyncio.create_task(_receiver())
        await asyncio.sleep(0)

        cleared = await agent.clear_sessions("wechat-user")

        self.assertEqual(cleared, 1)
        self.assertTrue(client.closed)
        self.assertTrue(task_cancelled.is_set())
        self.assertNotIn(session_key, controller.receiver_tasks)
        self.assertNotIn(session_key, controller.claude_sessions)
        self.assertEqual(controller.session_manager.cleared, ["wechat-user"])


if __name__ == "__main__":
    unittest.main()
