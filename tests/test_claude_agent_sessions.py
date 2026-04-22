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
        self.session = SimpleNamespace(session_active={})

    async def clear_session(self, settings_key):
        self.cleared.append(settings_key)

    async def get_or_create_session(self, user_id, channel_id):
        return self.session


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
    async def test_result_keeps_claude_session_active_when_requests_are_queued(self):
        controller = _StubController()
        mark_idle_calls = []
        controller.session_handler = SimpleNamespace(
            mark_session_idle=lambda composite_key: mark_idle_calls.append(composite_key),
            handle_session_error=AsyncMock(),
            capture_session_id=lambda *_: None,
        )
        controller._get_session_key = lambda context: "wechat-user"
        controller.emit_agent_message = AsyncMock()

        agent = ClaudeAgent(controller)
        agent.emit_result_message = AsyncMock()
        context = SimpleNamespace(user_id="U1", channel_id="C1")
        composite_key = "session-1:/tmp/work"
        queued_request = SimpleNamespace(started_at=None)
        next_request = SimpleNamespace(started_at=None)
        agent._pending_requests[composite_key] = [queued_request, next_request]
        agent._pending_reactions[composite_key] = [("m1", ":eyes:"), ("m2", ":eyes:")]
        agent._last_assistant_text[composite_key] = "last"
        controller.session_manager.session.session_active[composite_key] = True

        result_message = type(
            "ResultMessage",
            (),
            {"subtype": "success", "result": "done", "duration_ms": 1},
        )()

        class _Client:
            def receive_messages(self):
                async def _iterate():
                    yield result_message

                return _iterate()

        await agent._receive_messages(_Client(), "session-1", "/tmp/work", context, composite_key=composite_key)

        self.assertEqual(mark_idle_calls, [])
        agent.emit_result_message.assert_awaited_once_with(
            context,
            "done",
            subtype="success",
            duration_ms=1,
            parse_mode="markdown",
            request=queued_request,
        )
        self.assertEqual(agent._pending_requests[composite_key], [next_request])
        self.assertEqual(agent._pending_reactions[composite_key], [("m2", ":eyes:")])
        self.assertTrue(controller.session_manager.session.session_active[composite_key])

    async def test_handle_message_uses_runtime_session_key_for_claude_tracking(self):
        controller = _StubController()
        controller.emit_agent_message = AsyncMock()
        runtime_key = "wechat_o9:reviewer:/tmp/work"
        client = SimpleNamespace(
            query=AsyncMock(),
            _vibe_runtime_base_session_id="wechat_o9:reviewer",
            _vibe_runtime_session_key=runtime_key,
        )
        controller.session_handler = SimpleNamespace(
            get_or_create_claude_session=AsyncMock(return_value=client),
            mark_session_active=SimpleNamespace(),
            handle_session_error=AsyncMock(),
            capture_session_id=lambda *_: None,
        )
        mark_active_calls = []
        controller.session_handler.mark_session_active = lambda composite_key: mark_active_calls.append(composite_key)

        agent = ClaudeAgent(controller)
        agent._prepare_message_with_files = lambda request: request.message
        agent._delete_ack = AsyncMock()
        agent._receive_messages = AsyncMock()

        request = SimpleNamespace(
            context=SimpleNamespace(),
            message="hello",
            working_path="/tmp/work",
            base_session_id="wechat_o9",
            composite_session_id="wechat_o9:/tmp/work",
            session_key="wechat-user",
            subagent_name=None,
            subagent_model=None,
            subagent_reasoning_effort=None,
            ack_message_id=None,
            ack_reaction_message_id="m1",
            ack_reaction_emoji=":eyes:",
            files=None,
        )

        await agent.handle_message(request)
        await asyncio.sleep(0)

        controller.session_handler.get_or_create_claude_session.assert_awaited_once()
        self.assertEqual(mark_active_calls, [runtime_key])
        client.query.assert_awaited_once_with("hello", session_id=runtime_key)
        self.assertIn(runtime_key, agent._pending_requests)
        self.assertIn(runtime_key, agent._pending_reactions)
        self.assertNotIn(request.composite_session_id, agent._pending_requests)
        self.assertNotIn(request.composite_session_id, agent._pending_reactions)
        self.assertIn(runtime_key, controller.receiver_tasks)
        agent._receive_messages.assert_awaited_once_with(
            client,
            "wechat_o9:reviewer",
            "/tmp/work",
            request.context,
            composite_key=runtime_key,
        )

    async def test_handle_message_error_keeps_session_active_when_requests_remain_queued(self):
        controller = _StubController()
        mark_idle_calls = []
        controller.emit_agent_message = AsyncMock()
        runtime_key = "wechat_o9:reviewer:/tmp/work"
        queued_request = SimpleNamespace()
        client = SimpleNamespace(
            query=AsyncMock(side_effect=RuntimeError("boom")),
            _vibe_runtime_base_session_id="wechat_o9:reviewer",
            _vibe_runtime_session_key=runtime_key,
        )
        controller.session_handler = SimpleNamespace(
            get_or_create_claude_session=AsyncMock(return_value=client),
            mark_session_active=lambda composite_key: None,
            mark_session_idle=lambda composite_key: mark_idle_calls.append(composite_key),
            handle_session_error=AsyncMock(),
            capture_session_id=lambda *_: None,
        )

        agent = ClaudeAgent(controller)
        agent._prepare_message_with_files = lambda request: request.message
        agent._delete_ack = AsyncMock()
        agent._remove_ack_reaction = AsyncMock()

        request = SimpleNamespace(
            context=SimpleNamespace(),
            message="hello",
            working_path="/tmp/work",
            base_session_id="wechat_o9",
            composite_session_id="wechat_o9:/tmp/work",
            session_key="wechat-user",
            subagent_name=None,
            subagent_model=None,
            subagent_reasoning_effort=None,
            ack_message_id=None,
            ack_reaction_message_id="m1",
            ack_reaction_emoji=":eyes:",
            files=None,
        )
        agent._pending_requests[runtime_key] = [queued_request]
        agent._pending_reactions[runtime_key] = [("m2", ":eyes:")]

        await agent.handle_message(request)

        self.assertEqual(mark_idle_calls, [])
        self.assertEqual(agent._pending_requests[runtime_key], [queued_request])
        self.assertEqual(agent._pending_reactions[runtime_key], [("m2", ":eyes:")])
        agent._remove_ack_reaction.assert_awaited_once_with(request)

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
        self.assertTrue(client.disconnected)
        self.assertTrue(task_cancelled.is_set())
        self.assertNotIn(session_key, controller.receiver_tasks)
        self.assertNotIn(session_key, controller.claude_sessions)
        self.assertEqual(controller.session_manager.cleared, ["wechat-user"])

    async def test_clear_sessions_swallows_receiver_task_failure(self):
        controller = _StubController()
        agent = ClaudeAgent(controller)
        session_key = "wechat_o9:/tmp/work"
        client = _StubClient()
        controller.claude_sessions[session_key] = client
        disconnected = asyncio.Event()

        async def _disconnect():
            client.disconnected = True
            disconnected.set()

        client.disconnect = _disconnect

        async def _receiver():
            await disconnected.wait()
            raise RuntimeError("receiver failed")

        controller.receiver_tasks[session_key] = asyncio.create_task(_receiver())
        await asyncio.sleep(0)

        cleared = await agent.clear_sessions("wechat-user")

        self.assertEqual(cleared, 1)
        self.assertTrue(client.disconnected)
        self.assertNotIn(session_key, controller.receiver_tasks)
        self.assertNotIn(session_key, controller.claude_sessions)
        self.assertEqual(controller.session_manager.cleared, ["wechat-user"])

    async def test_clear_sessions_drains_finished_receiver_task_failure(self):
        controller = _StubController()
        agent = ClaudeAgent(controller)
        session_key = "wechat_o9:/tmp/work"
        client = _StubClient()
        controller.claude_sessions[session_key] = client

        class _DoneReceiverTask:
            drained = False

            @staticmethod
            def done():
                return True

            def exception(self):
                self.drained = True
                return RuntimeError("receiver already failed")

        receiver_task = _DoneReceiverTask()
        controller.receiver_tasks[session_key] = receiver_task

        cleared = await agent.clear_sessions("wechat-user")

        self.assertEqual(cleared, 1)
        self.assertTrue(client.disconnected)
        self.assertTrue(receiver_task.drained)
        self.assertNotIn(session_key, controller.receiver_tasks)
        self.assertNotIn(session_key, controller.claude_sessions)
        self.assertEqual(controller.session_manager.cleared, ["wechat-user"])

    async def test_cleanup_runtime_session_cancels_receiver_when_disconnect_is_cancelled(self):
        controller = _StubController()
        agent = ClaudeAgent(controller)
        session_key = "wechat_o9:/tmp/work"
        disconnect_started = asyncio.Event()
        receiver_cancelled = asyncio.Event()

        class _SlowDisconnectClient(_StubClient):
            async def disconnect(self):
                self.disconnected = True
                disconnect_started.set()
                await asyncio.Future()

        client = _SlowDisconnectClient()
        controller.claude_sessions[session_key] = client

        async def _receiver():
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                receiver_cancelled.set()
                raise

        receiver_task = asyncio.create_task(_receiver())
        controller.receiver_tasks[session_key] = receiver_task
        cleanup_task = asyncio.create_task(agent._cleanup_runtime_session(session_key))

        await disconnect_started.wait()
        self.assertNotIn(session_key, controller.receiver_tasks)

        cleanup_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cleanup_task

        self.assertTrue(client.disconnected)
        self.assertTrue(receiver_cancelled.is_set())
        self.assertNotIn(session_key, controller.receiver_tasks)
        self.assertNotIn(session_key, controller.claude_sessions)

    async def test_cleanup_runtime_session_preserves_new_receiver_during_disconnect(self):
        controller = _StubController()
        agent = ClaudeAgent(controller)
        session_key = "wechat_o9:/tmp/work"
        disconnect_started = asyncio.Event()
        old_receiver_cancelled = asyncio.Event()

        class _SlowDisconnectClient(_StubClient):
            async def disconnect(self):
                self.disconnected = True
                disconnect_started.set()
                await asyncio.Future()

        client = _SlowDisconnectClient()
        controller.claude_sessions[session_key] = client

        async def _old_receiver():
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                old_receiver_cancelled.set()
                raise

        old_receiver = asyncio.create_task(_old_receiver())
        new_receiver = asyncio.create_task(asyncio.sleep(3600))
        controller.receiver_tasks[session_key] = old_receiver
        cleanup_task = asyncio.create_task(agent._cleanup_runtime_session(session_key))

        await disconnect_started.wait()
        self.assertNotIn(session_key, controller.receiver_tasks)
        controller.receiver_tasks[session_key] = new_receiver

        cleanup_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cleanup_task

        self.assertTrue(old_receiver_cancelled.is_set())
        self.assertIs(controller.receiver_tasks[session_key], new_receiver)
        new_receiver.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await new_receiver

    async def test_cleanup_runtime_session_defers_disconnect_for_current_receiver(self):
        controller = _StubController()
        agent = ClaudeAgent(controller)
        session_key = "wechat_o9:/tmp/work"
        cleanup_returned = asyncio.Event()
        disconnect_started = asyncio.Event()
        release_disconnect = asyncio.Event()

        class _SlowDisconnectClient(_StubClient):
            async def disconnect(self):
                self.disconnected = True
                disconnect_started.set()
                await release_disconnect.wait()

        client = _SlowDisconnectClient()
        controller.claude_sessions[session_key] = client

        async def _receiver():
            await agent._cleanup_runtime_session(
                session_key,
                current_receiver_task=asyncio.current_task(),
            )
            cleanup_returned.set()

        receiver_task = asyncio.create_task(_receiver())
        controller.receiver_tasks[session_key] = receiver_task

        await cleanup_returned.wait()
        self.assertNotIn(session_key, controller.receiver_tasks)
        self.assertNotIn(session_key, controller.claude_sessions)

        await disconnect_started.wait()
        self.assertTrue(client.disconnected)
        release_disconnect.set()
        await asyncio.sleep(0)

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

    async def test_prepare_resume_binding_cleans_only_target_runtime_session(self):
        controller = _StubController()
        agent = ClaudeAgent(controller)
        target_key = "wechat_o9:/tmp/work"
        other_key = "wechat_o10:/tmp/work"
        target_client = _StubClient()
        other_client = _StubClient()
        controller.claude_sessions[target_key] = target_client
        controller.claude_sessions[other_key] = other_client
        controller.receiver_tasks[target_key] = asyncio.create_task(asyncio.sleep(3600))
        controller.receiver_tasks[other_key] = asyncio.create_task(asyncio.sleep(3600))
        await asyncio.sleep(0)

        await agent.prepare_resume_binding(
            base_session_id="wechat_o9",
            session_key="wechat-user",
            working_path="/tmp/work",
        )

        self.assertTrue(target_client.disconnected)
        self.assertFalse(other_client.disconnected)
        self.assertNotIn(target_key, controller.claude_sessions)
        self.assertIn(other_key, controller.claude_sessions)
        self.assertNotIn(target_key, controller.receiver_tasks)
        self.assertIn(other_key, controller.receiver_tasks)

        controller.receiver_tasks[other_key].cancel()
        with self.assertRaises(asyncio.CancelledError):
            await controller.receiver_tasks[other_key]

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
        pending_request_1 = SimpleNamespace()
        pending_request_2 = SimpleNamespace()
        agent._pending_requests[composite_key] = [pending_request_1, pending_request_2]
        agent._pending_reactions[composite_key] = [("m1", ":eyes:"), ("m2", ":eyes:")]

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
        self.assertEqual(agent._remove_ack_reaction.await_count, 2)
        self.assertEqual(agent._remove_ack_reaction.await_args_list[0].args, (pending_request_1,))
        self.assertEqual(agent._remove_ack_reaction.await_args_list[1].args, (pending_request_2,))
        self.assertNotIn(composite_key, controller.receiver_tasks)
        self.assertNotIn(composite_key, controller.claude_sessions)
        self.assertNotIn(composite_key, agent._pending_requests)
        self.assertNotIn(composite_key, agent._pending_reactions)

    async def test_assistant_auth_error_without_text_blocks_still_triggers_recovery(self):
        controller = _StubController()
        controller.agent_auth_service.maybe_emit_auth_recovery_message = AsyncMock(return_value=True)
        controller._get_session_key = lambda context: "telegram::user::U1"
        controller.emit_agent_message = AsyncMock()
        agent = ClaudeAgent(controller)
        agent._remove_ack_reaction = AsyncMock()
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
        agent._remove_ack_reaction.assert_awaited_once_with(pending_request)
        self.assertNotIn(composite_key, controller.receiver_tasks)
        self.assertNotIn(composite_key, controller.claude_sessions)

    async def test_assistant_auth_error_without_is_api_error_flag_still_triggers_recovery(self):
        """Scenario: AUTH-SETUP-902"""
        controller = _StubController()
        controller.agent_auth_service.maybe_emit_auth_recovery_message = AsyncMock(return_value=True)
        controller._get_session_key = lambda context: "telegram::user::U1"
        controller.emit_agent_message = AsyncMock()
        agent = ClaudeAgent(controller)
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

        assistant_message = type(
            "AssistantMessage",
            (),
            {
                "content": [],
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
        self.assertNotIn(composite_key, controller.receiver_tasks)
        self.assertNotIn(composite_key, controller.claude_sessions)

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
