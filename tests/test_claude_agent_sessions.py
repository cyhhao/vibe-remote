import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
        self.disconnected = False

    async def close(self):
        self.closed = True

    async def disconnect(self):
        self.disconnected = True


class _StubSettingsManager:
    sessions = _StubSessions()


class _StubController:
    def __init__(self):
        self.config = type("Config", (), {})()
        self.im_client = SimpleNamespace(formatter=SimpleNamespace())
        self.settings_manager = _StubSettingsManager()
        self.session_handler = SimpleNamespace(cleanup_session=AsyncMock(), capture_session_id=lambda *_: None)
        self.session_manager = _StubSessionManager()
        self.receiver_tasks = {}
        self.claude_sessions = {}
        self.claude_client = SimpleNamespace(_is_skip_message=lambda message: False)
        self.agent_auth_service = SimpleNamespace(maybe_emit_auth_recovery_message=AsyncMock(return_value=False))


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

    async def test_refresh_auth_state_disconnects_runtime_sessions(self):
        controller = _StubController()
        agent = ClaudeAgent(controller)
        session_key = "wechat_o9:/tmp/work"
        client = _StubClient()
        controller.claude_sessions[session_key] = client
        agent._last_assistant_text[session_key] = "hello"
        agent._pending_assistant_message[session_key] = "pending"
        agent._pending_reactions[session_key] = [("m1", "⏳")]
        agent._pending_requests[session_key] = ["request"]

        task_cancelled = asyncio.Event()

        async def _receiver():
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                task_cancelled.set()
                raise

        controller.receiver_tasks[session_key] = asyncio.create_task(_receiver())
        await asyncio.sleep(0)

        await agent.refresh_auth_state()

        self.assertTrue(client.disconnected)
        self.assertTrue(task_cancelled.is_set())
        self.assertNotIn(session_key, controller.receiver_tasks)
        self.assertNotIn(session_key, controller.claude_sessions)
        self.assertNotIn(session_key, agent._last_assistant_text)
        self.assertNotIn(session_key, agent._pending_assistant_message)
        self.assertNotIn(session_key, agent._pending_reactions)
        self.assertNotIn(session_key, agent._pending_requests)

    async def test_receiver_auth_error_prefers_oauth_recovery_message(self):
        controller = _StubController()
        controller.agent_auth_service.maybe_emit_auth_recovery_message = AsyncMock(return_value=True)
        controller._get_session_key = lambda context: "telegram::user::U1"
        agent = ClaudeAgent(controller)
        agent.session_handler = SimpleNamespace(handle_session_error=AsyncMock())
        agent._clear_pending_reactions = AsyncMock()
        context = SimpleNamespace()

        class _FailingClient:
            def receive_messages(self):
                async def _iterate():
                    raise RuntimeError(
                        'Failed to authenticate. API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"Invalid bearer token"}}'
                    )
                    yield  # pragma: no cover

                return _iterate()

        await agent._receive_messages(_FailingClient(), "session-1", "/tmp/work", context)

        controller.agent_auth_service.maybe_emit_auth_recovery_message.assert_awaited_once()
        agent.session_handler.handle_session_error.assert_not_awaited()

    async def test_result_auth_error_prefers_oauth_recovery_message(self):
        controller = _StubController()
        controller.agent_auth_service.maybe_emit_auth_recovery_message = AsyncMock(return_value=True)
        controller._get_session_key = lambda context: "telegram::user::U1"
        controller.emit_agent_message = AsyncMock()
        agent = ClaudeAgent(controller)
        agent._clear_pending_reactions = AsyncMock()
        agent.emit_result_message = AsyncMock()
        context = SimpleNamespace()
        composite_key = "session-1:/tmp/work"
        current_task = asyncio.current_task()
        controller.receiver_tasks[composite_key] = current_task
        controller.claude_sessions[composite_key] = _StubClient()

        ResultMessage = type("ResultMessage", (), {})
        init_message = type(
            "SystemMessage",
            (),
            {"subtype": "init", "data": {"session_id": "session-sdk"}},
        )()
        error_result = ResultMessage()
        error_result.subtype = "error"
        error_result.result = (
            'Failed to authenticate. API Error: 401 {"type":"error","error":{"type":"authentication_error",'
            '"message":"Invalid bearer token"}}'
        )
        error_result.duration_ms = 0

        class _Client:
            def receive_messages(self):
                async def _iterate():
                    yield init_message
                    yield error_result

                return _iterate()

        await agent._receive_messages(_Client(), "session-1", "/tmp/work", context)

        controller.agent_auth_service.maybe_emit_auth_recovery_message.assert_awaited_once()
        controller.session_handler.cleanup_session.assert_not_awaited()
        self.assertNotIn(composite_key, controller.receiver_tasks)
        self.assertNotIn(composite_key, controller.claude_sessions)
        agent.emit_result_message.assert_not_awaited()

    async def test_assistant_auth_error_prefers_oauth_recovery_message(self):
        controller = _StubController()
        controller.agent_auth_service.maybe_emit_auth_recovery_message = AsyncMock(return_value=True)
        controller._get_session_key = lambda context: "telegram::user::U1"
        controller.emit_agent_message = AsyncMock()
        agent = ClaudeAgent(controller)
        agent._clear_pending_reactions = AsyncMock()
        agent._remove_ack_reaction = AsyncMock()
        agent._extract_text_blocks = lambda message, context: (
            'Failed to authenticate. API Error: 401 {"type":"error","error":{"type":"authentication_error",'
            '"message":"Invalid bearer token"}}'
        )
        context = SimpleNamespace()
        composite_key = "session-1:/tmp/work"
        current_task = asyncio.current_task()
        controller.receiver_tasks[composite_key] = current_task
        controller.claude_sessions[composite_key] = _StubClient()
        pending_request = SimpleNamespace()
        agent._pending_requests[composite_key] = [pending_request]
        agent._pending_reactions[composite_key] = [("m1", ":eyes:")]

        assistant_message = type(
            "AssistantMessage",
            (),
            {
                "content": [],
                "isApiErrorMessage": True,
                "error": "authentication_failed",
            },
        )()

        class _Client:
            def receive_messages(self):
                async def _iterate():
                    yield assistant_message

                return _iterate()

        await agent._receive_messages(_Client(), "session-1", "/tmp/work", context)

        controller.agent_auth_service.maybe_emit_auth_recovery_message.assert_awaited_once()
        controller.session_handler.cleanup_session.assert_not_awaited()
        agent._remove_ack_reaction.assert_awaited_once_with(pending_request)
        self.assertNotIn(composite_key, controller.receiver_tasks)
        self.assertNotIn(composite_key, controller.claude_sessions)
        self.assertNotIn(composite_key, agent._pending_requests)
        self.assertNotIn(composite_key, agent._pending_reactions)

    async def test_handle_auth_failure_result_requires_explicit_error_subtype(self):
        controller = _StubController()
        controller.agent_auth_service.maybe_emit_auth_recovery_message = AsyncMock(return_value=True)
        agent = ClaudeAgent(controller)
        context = SimpleNamespace()

        handled = await agent._handle_auth_failure_result(
            context,
            "session-1:/tmp/work",
            "",
            "Let's talk about oauth login after this task finishes.",
        )

        self.assertFalse(handled)
        controller.agent_auth_service.maybe_emit_auth_recovery_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
