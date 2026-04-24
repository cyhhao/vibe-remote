import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_AGENT_PATH = Path(__file__).resolve().parents[1] / "modules/agents/codex/agent.py"

_modules_pkg = types.ModuleType("modules")
_agents_pkg = types.ModuleType("modules.agents")
_codex_pkg = types.ModuleType("modules.agents.codex")

_base_module = types.ModuleType("modules.agents.base")
setattr(_base_module, "AgentRequest", object)


class _BaseAgent:
    def __init__(self, controller):
        self.controller = controller


setattr(_base_module, "BaseAgent", _BaseAgent)

_event_handler_module = types.ModuleType("modules.agents.codex.event_handler")
setattr(_event_handler_module, "CodexEventHandler", object)

_session_module = types.ModuleType("modules.agents.codex.session")
setattr(_session_module, "CodexSessionManager", object)

_transport_module = types.ModuleType("modules.agents.codex.transport")
setattr(_transport_module, "CodexTransport", object)

_turn_state_module = types.ModuleType("modules.agents.codex.turn_state")
setattr(_turn_state_module, "CodexTurnRegistry", object)

_subagent_router_module = types.ModuleType("modules.agents.subagent_router")


class _StubSubagentDefinition:
    def __init__(
        self,
        name=None,
        description=None,
        developer_instructions=None,
        model=None,
        reasoning_effort=None,
        path=None,
        source=None,
    ):
        self.name = name
        self.description = description
        self.developer_instructions = developer_instructions
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.path = path
        self.source = source


setattr(_subagent_router_module, "SubagentDefinition", _StubSubagentDefinition)
setattr(_subagent_router_module, "load_codex_subagent", lambda *args, **kwargs: None)

_STUBBED_MODULES = {
    "modules": _modules_pkg,
    "modules.agents": _agents_pkg,
    "modules.agents.codex": _codex_pkg,
    "modules.agents.base": _base_module,
    "modules.agents.subagent_router": _subagent_router_module,
    "modules.agents.codex.event_handler": _event_handler_module,
    "modules.agents.codex.session": _session_module,
    "modules.agents.codex.transport": _transport_module,
    "modules.agents.codex.turn_state": _turn_state_module,
}
_saved_modules = {name: sys.modules.get(name) for name in _STUBBED_MODULES}

for name, module in _STUBBED_MODULES.items():
    sys.modules[name] = module

_SPEC = importlib.util.spec_from_file_location("test_codex_agent_module", _AGENT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
CodexAgent = _MODULE.CodexAgent

for name, module in _saved_modules.items():
    if module is None:
        sys.modules.pop(name, None)
    else:
        sys.modules[name] = module


class _StubSessionManager:
    def __init__(self):
        self._threads = {}

    def find_base_session_id_for_thread(self, thread_id: str):
        for base_session_id, stored_thread_id in self._threads.items():
            if stored_thread_id == thread_id:
                return base_session_id
        return None


class _StubTurnRegistry:
    def __init__(self):
        self._turn_requests = {}
        self._latest_requests = {}
        self._pending_requests = {}
        self._active_turns = {}

    def get_request_for_turn(self, turn_id: str):
        return self._turn_requests.get(turn_id)

    def get_latest_request(self, base_session_id: str):
        return self._latest_requests.get(base_session_id)

    def bootstrap_turn(self, turn_id: str, base_session_id: str, thread_id: str):
        request = self._pending_requests.get(base_session_id)
        if not request:
            return None
        self._turn_requests[turn_id] = request
        return SimpleNamespace(request=request)

    def get_active_turn(self, base_session_id: str):
        return self._active_turns.get(base_session_id)

    def finalize_turn_start_response(self, turn_id: str, request):
        self._turn_requests[turn_id] = request
        return SimpleNamespace(request=request)


class CodexAgentNotificationRoutingTests(unittest.TestCase):
    def test_find_request_prefers_turn_mapping_over_replaced_active_request(self):
        agent = object.__new__(CodexAgent)
        agent._session_mgr = _StubSessionManager()
        agent._turn_registry = _StubTurnRegistry()

        old_request = SimpleNamespace(base_session_id="session-1", context="old")
        new_request = SimpleNamespace(base_session_id="session-1", context="new")
        agent._session_mgr._threads["session-1"] = "thread-1"
        agent._turn_registry._latest_requests["session-1"] = new_request
        agent._turn_registry._turn_requests["turn-1"] = old_request

        request = agent._find_request_for_notification("item/completed", {"threadId": "thread-1", "turnId": "turn-1"})

        self.assertIs(request, old_request)

    def test_find_request_falls_back_to_thread_mapping_without_turn_id(self):
        agent = object.__new__(CodexAgent)
        agent._session_mgr = _StubSessionManager()
        agent._turn_registry = _StubTurnRegistry()

        request = SimpleNamespace(base_session_id="session-1", context="current")
        agent._session_mgr._threads["session-1"] = "thread-1"
        agent._turn_registry._latest_requests["session-1"] = request

        resolved = agent._find_request_for_notification("thread/started", {"threadId": "thread-1"})

        self.assertIs(resolved, request)

    def test_find_request_does_not_fall_back_to_thread_when_turn_is_unknown(self):
        agent = object.__new__(CodexAgent)
        agent._session_mgr = _StubSessionManager()
        agent._turn_registry = _StubTurnRegistry()

        request = SimpleNamespace(base_session_id="session-1", context="current")
        agent._session_mgr._threads["session-1"] = "thread-1"
        agent._turn_registry._latest_requests["session-1"] = request

        resolved = agent._find_request_for_notification(
            "item/completed", {"threadId": "thread-1", "turnId": "turn-old"}
        )

        self.assertIsNone(resolved)

    def test_find_request_bootstraps_pending_turn_start(self):
        agent = object.__new__(CodexAgent)
        agent._session_mgr = _StubSessionManager()
        agent._turn_registry = _StubTurnRegistry()

        request = SimpleNamespace(base_session_id="session-1", context="current")
        agent._session_mgr._threads["session-1"] = "thread-1"
        agent._turn_registry._latest_requests["session-1"] = request
        agent._turn_registry._pending_requests["session-1"] = request

        resolved = agent._find_request_for_notification(
            "turn/started", {"threadId": "thread-1", "turn": {"id": "turn-1"}}
        )

        self.assertIs(resolved, request)

    def test_find_request_does_not_bootstrap_items_for_pending_turn(self):
        agent = object.__new__(CodexAgent)
        agent._session_mgr = _StubSessionManager()
        agent._turn_registry = _StubTurnRegistry()

        request = SimpleNamespace(base_session_id="session-1", context="current")
        agent._session_mgr._threads["session-1"] = "thread-1"
        agent._turn_registry._latest_requests["session-1"] = request
        agent._turn_registry._pending_requests["session-1"] = request

        resolved = agent._find_request_for_notification("item/completed", {"threadId": "thread-1", "turnId": "turn-1"})

        self.assertIsNone(resolved)


class CodexAgentStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_stop_does_not_hide_turn_before_interrupt_succeeds(self):
        agent = object.__new__(CodexAgent)
        agent._session_mgr = SimpleNamespace(get_thread_id=lambda base_session_id: "thread-1")
        agent._turn_registry = _StubTurnRegistry()
        agent._turn_registry._active_turns["session-1"] = "turn-1"
        transport = SimpleNamespace(is_alive=True, send_request=AsyncMock(side_effect=RuntimeError("boom")))
        agent._transports = {"/tmp": transport}
        agent._event_handler = SimpleNamespace(clear_pending=Mock(return_value=SimpleNamespace()))
        agent._remove_ack_reaction = AsyncMock()
        agent.controller = SimpleNamespace(emit_agent_message=AsyncMock())

        request = SimpleNamespace(base_session_id="session-1", working_path="/tmp", context=object())

        result = await agent.handle_stop(request)

        self.assertFalse(result)
        agent._event_handler.clear_pending.assert_not_called()
        agent._remove_ack_reaction.assert_not_awaited()

    async def test_handle_stop_hides_turn_after_interrupt_succeeds(self):
        agent = object.__new__(CodexAgent)
        agent._session_mgr = SimpleNamespace(get_thread_id=lambda base_session_id: "thread-1")
        agent._turn_registry = _StubTurnRegistry()
        agent._turn_registry._active_turns["session-1"] = "turn-1"

        events = []

        async def send_request(method, payload):
            events.append(("send", method, payload))
            return {}

        def clear_pending(turn_id):
            events.append(("clear", turn_id))
            return SimpleNamespace()

        agent._transports = {"/tmp": SimpleNamespace(is_alive=True, send_request=send_request)}
        agent._event_handler = SimpleNamespace(clear_pending=clear_pending)
        agent._remove_ack_reaction = AsyncMock(side_effect=lambda request: events.append(("ack", None)))
        agent.controller = SimpleNamespace(emit_agent_message=AsyncMock())

        request = SimpleNamespace(base_session_id="session-1", working_path="/tmp", context=object())

        result = await agent.handle_stop(request)

        self.assertTrue(result)
        self.assertEqual(events[0][0], "send")
        self.assertEqual(events[1][0], "clear")

    async def test_refresh_auth_state_stops_transports_and_invalidates_threads(self):
        agent = object.__new__(CodexAgent)
        stop_calls = []

        async def stop_a():
            stop_calls.append("a")

        async def stop_b():
            stop_calls.append("b")

        invalidated = []
        cleared_sessions = []
        agent._transports = {
            "/tmp/a": SimpleNamespace(stop=stop_a),
            "/tmp/b": SimpleNamespace(stop=stop_b),
        }
        agent._session_mgr = SimpleNamespace(
            all_base_sessions=lambda: ["session-1", "session-2"],
            invalidate_thread=lambda base_session_id: invalidated.append(base_session_id),
        )
        agent._turn_registry = SimpleNamespace(clear_session=lambda base_session_id: cleared_sessions.append(base_session_id))

        await agent.refresh_auth_state()

        self.assertEqual(stop_calls, ["a", "b"])
        self.assertEqual(agent._transports, {})
        self.assertEqual(invalidated, ["session-1", "session-2"])
        self.assertEqual(cleared_sessions, ["session-1", "session-2"])

    async def test_prepare_resume_binding_restarts_unshared_transport(self):
        agent = object.__new__(CodexAgent)
        stop_calls = []

        async def stop_transport():
            stop_calls.append("stop")

        agent._transports = {"/tmp/work": SimpleNamespace(stop=stop_transport)}
        agent._transport_last_activity = {"/tmp/work": 1.0}
        invalidated = []
        cleared_sessions = []
        agent._session_mgr = SimpleNamespace(
            sessions_for_cwd=lambda cwd: ["session-1"] if cwd == "/tmp/work" else [],
            invalidate_thread=lambda base_session_id: invalidated.append(base_session_id),
        )
        agent._turn_registry = SimpleNamespace(clear_session=lambda base_session_id: cleared_sessions.append(base_session_id))

        await agent.prepare_resume_binding(
            base_session_id="session-1",
            session_key="scope-1",
            working_path="/tmp/work",
        )

        self.assertEqual(stop_calls, ["stop"])
        self.assertEqual(agent._transports, {})
        self.assertEqual(agent._transport_last_activity, {})
        self.assertEqual(invalidated, ["session-1"])
        self.assertEqual(cleared_sessions, ["session-1"])

    async def test_prepare_resume_binding_skips_shared_transport(self):
        agent = object.__new__(CodexAgent)
        stop_transport = AsyncMock()
        transport = SimpleNamespace(stop=stop_transport)
        agent._transports = {"/tmp/work": transport}
        agent._transport_last_activity = {"/tmp/work": 1.0}
        invalidated = []
        cleared_sessions = []
        agent._session_mgr = SimpleNamespace(
            sessions_for_cwd=lambda cwd: ["session-1", "session-2"] if cwd == "/tmp/work" else [],
            invalidate_thread=lambda base_session_id: invalidated.append(base_session_id),
        )
        agent._turn_registry = SimpleNamespace(clear_session=lambda base_session_id: cleared_sessions.append(base_session_id))

        await agent.prepare_resume_binding(
            base_session_id="session-1",
            session_key="scope-1",
            working_path="/tmp/work",
        )

        stop_transport.assert_not_awaited()
        self.assertIs(agent._transports["/tmp/work"], transport)
        self.assertEqual(agent._transport_last_activity, {"/tmp/work": 1.0})
        self.assertEqual(invalidated, [])
        self.assertEqual(cleared_sessions, [])

    async def test_evict_idle_transports_stops_idle_codex_runtime(self):
        agent = object.__new__(CodexAgent)
        stop_calls = []
        invalidated_sessions = []
        cleared_turns = []

        async def stop_transport():
            stop_calls.append("stop")

        agent._transports = {"/tmp/work": SimpleNamespace(stop=stop_transport)}
        agent._transport_last_activity = {"/tmp/work": 0.0}
        agent._transport_locks = {"/tmp/work": asyncio.Lock()}
        agent._session_mgr = SimpleNamespace(
            sessions_for_cwd=lambda cwd: ["session-1"] if cwd == "/tmp/work" else [],
            invalidate_thread=lambda base_session_id: invalidated_sessions.append(base_session_id),
        )
        agent._turn_registry = SimpleNamespace(
            get_active_turn=lambda base_session_id: None,
            clear_session=lambda base_session_id: cleared_turns.append(base_session_id),
        )
        agent._session_locks = {"session-1": asyncio.Lock()}
        agent.sessions = SimpleNamespace(clear_agent_session_mapping=Mock())

        with patch.object(_MODULE.time, "monotonic", return_value=1000.0):
            evicted = await agent.evict_idle_transports(600)

        self.assertEqual(evicted, 1)
        self.assertEqual(stop_calls, ["stop"])
        self.assertEqual(invalidated_sessions, ["session-1"])
        self.assertEqual(cleared_turns, ["session-1"])
        agent.sessions.clear_agent_session_mapping.assert_not_called()
        self.assertEqual(agent._transports, {})
        self.assertIn("/tmp/work", agent._transport_locks)
        self.assertEqual(agent._transport_last_activity, {})

    async def test_evict_idle_transports_keeps_active_codex_runtime(self):
        agent = object.__new__(CodexAgent)

        async def stop_transport():
            raise AssertionError("active transport should not be stopped")

        agent._transports = {"/tmp/work": SimpleNamespace(stop=stop_transport)}
        agent._transport_last_activity = {"/tmp/work": 0.0}
        agent._transport_locks = {"/tmp/work": asyncio.Lock()}
        agent._session_mgr = SimpleNamespace(
            sessions_for_cwd=lambda cwd: ["session-1"] if cwd == "/tmp/work" else [],
            invalidate_thread=lambda base_session_id: None,
        )
        agent._turn_registry = SimpleNamespace(
            get_active_turn=lambda base_session_id: "turn-1",
            clear_session=lambda base_session_id: None,
        )
        agent._session_locks = {"session-1": asyncio.Lock()}
        agent.sessions = SimpleNamespace(clear_agent_session_mapping=Mock())

        with patch.object(_MODULE.time, "monotonic", return_value=1000.0):
            evicted = await agent.evict_idle_transports(600)

        self.assertEqual(evicted, 0)
        self.assertIn("/tmp/work", agent._transports)
        agent.sessions.clear_agent_session_mapping.assert_not_called()

    async def test_evict_idle_transports_keeps_pending_turn_start_runtime(self):
        agent = object.__new__(CodexAgent)

        async def stop_transport():
            raise AssertionError("pending turn-start transport should not be stopped")

        agent._transports = {"/tmp/work": SimpleNamespace(stop=stop_transport)}
        agent._transport_last_activity = {"/tmp/work": 0.0}
        agent._transport_locks = {"/tmp/work": asyncio.Lock()}
        agent._session_mgr = SimpleNamespace(
            sessions_for_cwd=lambda cwd: ["session-1"] if cwd == "/tmp/work" else [],
            invalidate_thread=lambda base_session_id: None,
        )
        agent._turn_registry = SimpleNamespace(
            get_active_turn=lambda base_session_id: None,
            has_pending_turn_start=lambda base_session_id: True,
            clear_session=lambda base_session_id: None,
        )
        agent._session_locks = {"session-1": asyncio.Lock()}
        agent.sessions = SimpleNamespace(clear_agent_session_mapping=Mock())

        with patch.object(_MODULE.time, "monotonic", return_value=1000.0):
            evicted = await agent.evict_idle_transports(600)

        self.assertEqual(evicted, 0)
        self.assertIn("/tmp/work", agent._transports)
        agent.sessions.clear_agent_session_mapping.assert_not_called()

    async def test_evict_idle_transports_preserves_state_when_stop_fails(self):
        agent = object.__new__(CodexAgent)
        invalidated_sessions = []
        cleared_turns = []

        async def stop_transport():
            raise RuntimeError("boom")

        transport = SimpleNamespace(stop=stop_transport)
        lock = asyncio.Lock()
        agent._transports = {"/tmp/work": transport}
        agent._transport_last_activity = {"/tmp/work": 0.0}
        agent._transport_locks = {"/tmp/work": lock}
        agent._session_mgr = SimpleNamespace(
            sessions_for_cwd=lambda cwd: ["session-1"] if cwd == "/tmp/work" else [],
            invalidate_thread=lambda base_session_id: invalidated_sessions.append(base_session_id),
        )
        agent._turn_registry = SimpleNamespace(
            get_active_turn=lambda base_session_id: None,
            has_pending_turn_start=lambda base_session_id: False,
            clear_session=lambda base_session_id: cleared_turns.append(base_session_id),
        )
        agent._session_locks = {"session-1": asyncio.Lock()}
        agent.sessions = SimpleNamespace(clear_agent_session_mapping=Mock())

        with patch.object(_MODULE.time, "monotonic", return_value=1000.0):
            evicted = await agent.evict_idle_transports(600)

        self.assertEqual(evicted, 0)
        self.assertIs(agent._transports["/tmp/work"], transport)
        self.assertIs(agent._transport_locks["/tmp/work"], lock)
        self.assertEqual(agent._transport_last_activity["/tmp/work"], 0.0)
        self.assertEqual(invalidated_sessions, [])
        self.assertEqual(cleared_turns, [])
        agent.sessions.clear_agent_session_mapping.assert_not_called()

    async def test_evict_idle_transports_revalidates_activity_before_stop(self):
        agent = object.__new__(CodexAgent)
        stop_calls = []

        async def stop_transport():
            stop_calls.append("stop")

        lock = asyncio.Lock()
        await lock.acquire()
        agent._transports = {"/tmp/work": SimpleNamespace(stop=stop_transport)}
        agent._transport_last_activity = {"/tmp/work": 0.0}
        agent._transport_locks = {"/tmp/work": lock}
        agent._session_mgr = SimpleNamespace(
            sessions_for_cwd=lambda cwd: ["session-1"] if cwd == "/tmp/work" else [],
            invalidate_thread=lambda base_session_id: None,
        )
        agent._turn_registry = SimpleNamespace(
            get_active_turn=lambda base_session_id: None,
            has_pending_turn_start=lambda base_session_id: False,
            clear_session=lambda base_session_id: None,
        )
        agent._session_locks = {"session-1": asyncio.Lock()}
        agent.sessions = SimpleNamespace(clear_agent_session_mapping=Mock())

        with patch.object(_MODULE.time, "monotonic", return_value=1000.0):
            eviction_task = asyncio.create_task(agent.evict_idle_transports(600))
            await asyncio.sleep(0)
            agent._transport_last_activity["/tmp/work"] = 950.0
            lock.release()
            evicted = await eviction_task

        self.assertEqual(evicted, 0)
        self.assertEqual(stop_calls, [])
        self.assertIn("/tmp/work", agent._transports)
        self.assertEqual(agent._transport_last_activity["/tmp/work"], 950.0)
        agent.sessions.clear_agent_session_mapping.assert_not_called()

    async def test_get_or_create_transport_fast_path_waits_for_transport_lock(self):
        agent = object.__new__(CodexAgent)
        lock = asyncio.Lock()
        await lock.acquire()
        transport = SimpleNamespace(is_initialized=True)
        agent._transports = {"/tmp/work": transport}
        agent._transport_locks = {"/tmp/work": lock}
        agent._transport_last_activity = {}

        with patch.object(_MODULE.time, "monotonic", return_value=1000.0):
            transport_task = asyncio.create_task(agent._get_or_create_transport("/tmp/work"))
            await asyncio.sleep(0)
            self.assertFalse(transport_task.done())
            lock.release()
            resolved = await transport_task

        self.assertIs(resolved, transport)
        self.assertEqual(agent._transport_last_activity["/tmp/work"], 1000.0)


class _HandleMessageTurnRegistry:
    def __init__(self, active_turn: str | None):
        self.active_turn = active_turn
        self.remembered_requests = []

    def remember_request(self, request):
        self.remembered_requests.append(request)

    def get_active_turn(self, base_session_id: str):
        return self.active_turn

    def has_pending_turn_start(self, base_session_id: str):
        return False


class CodexAgentHandleMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_message_does_not_hide_turn_before_interrupt_succeeds(self):
        agent = object.__new__(CodexAgent)
        request = SimpleNamespace(
            base_session_id="session-1",
            working_path="/tmp",
            context=object(),
            session_key="settings-1",
            ack_message_id=None,
        )

        transport = SimpleNamespace(
            send_request=AsyncMock(side_effect=RuntimeError("interrupt failed")),
        )
        agent._session_locks = {}
        agent._turn_registry = _HandleMessageTurnRegistry(active_turn="turn-1")
        agent._event_handler = SimpleNamespace(clear_pending=Mock(return_value=SimpleNamespace()))
        agent._remove_ack_reaction = AsyncMock()
        agent.controller = SimpleNamespace(emit_agent_message=AsyncMock())
        agent._get_or_create_transport = AsyncMock(return_value=transport)
        agent._session_mgr = SimpleNamespace(
            set_session_key=lambda base_session_id, session_key: None,
            set_cwd=lambda base_session_id, cwd: None,
            get_thread_id=lambda base_session_id: "thread-1",
        )

        await agent.handle_message(request)

        agent._event_handler.clear_pending.assert_not_called()
        agent._remove_ack_reaction.assert_awaited_once_with(request)
        agent.controller.emit_agent_message.assert_awaited_once_with(
            request.context,
            "notify",
            "❌ Failed to interrupt previous Codex turn: interrupt failed",
        )

    def test_find_request_does_not_bootstrap_turn_completed_for_pending_turn(self):
        agent = object.__new__(CodexAgent)
        agent._session_mgr = _StubSessionManager()
        agent._turn_registry = _StubTurnRegistry()

        request = SimpleNamespace(base_session_id="session-1", context="current")
        agent._session_mgr._threads["session-1"] = "thread-1"
        agent._turn_registry._latest_requests["session-1"] = request
        agent._turn_registry._pending_requests["session-1"] = request

        resolved = agent._find_request_for_notification(
            "turn/completed", {"threadId": "thread-1", "turn": {"id": "turn-1"}}
        )

        self.assertIsNone(resolved)


class CodexAgentPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_thread_requests_danger_full_access(self):
        agent = object.__new__(CodexAgent)
        agent.controller = SimpleNamespace(config=SimpleNamespace(platform="slack", reply_enhancements=False))
        agent.settings_manager = SimpleNamespace(get_channel_settings=lambda session_key: None)
        agent.codex_config = SimpleNamespace(default_model=None)
        agent._session_mgr = SimpleNamespace(set_thread_id=Mock())
        agent.sessions = SimpleNamespace(set_agent_session_mapping=Mock())
        request = SimpleNamespace(
            working_path="/tmp/work",
            context=SimpleNamespace(
                platform="slack",
                platform_specific={},
                user_id="U1",
                channel_id="C1",
                thread_id=None,
            ),
            base_session_id="session-1",
            session_key="channel-1",
            subagent_name=None,
            subagent_model=None,
            subagent_reasoning_effort=None,
        )

        transport = SimpleNamespace(send_request=AsyncMock(return_value={"thread": {"id": "thread-1"}}))

        thread_id = await agent._start_thread(transport, request)

        self.assertEqual(thread_id, "thread-1")
        transport.send_request.assert_awaited_once_with(
            "thread/start",
            {
                "cwd": "/tmp/work",
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
            },
        )

    async def test_start_thread_includes_codex_agent_developer_instructions(self):
        agent = object.__new__(CodexAgent)
        agent.controller = SimpleNamespace(config=SimpleNamespace(platform="slack", reply_enhancements=False))
        agent.settings_manager = SimpleNamespace(get_channel_settings=lambda session_key: None)
        agent.codex_config = SimpleNamespace(default_model=None)
        agent._session_mgr = SimpleNamespace(set_thread_id=Mock())
        agent.sessions = SimpleNamespace(set_agent_session_mapping=Mock())
        request = SimpleNamespace(
            working_path="/tmp/work",
            context=SimpleNamespace(
                platform="slack",
                platform_specific={},
                user_id="U1",
                channel_id="C1",
                thread_id=None,
            ),
            base_session_id="session-1",
            session_key="channel-1",
            subagent_name="reviewer",
            subagent_model=None,
            subagent_reasoning_effort=None,
        )
        transport = SimpleNamespace(send_request=AsyncMock(return_value={"thread": {"id": "thread-1"}}))

        with patch.object(
            _MODULE,
            "load_codex_subagent",
            return_value=SimpleNamespace(
                developer_instructions="Focus on regressions.",
                model="gpt-5.4-mini",
                reasoning_effort="high",
            ),
        ) as load_subagent:
            await agent._start_thread(transport, request)

        load_subagent.assert_called_once_with("reviewer", project_root=Path("/tmp/work"))
        transport.send_request.assert_awaited_once_with(
            "thread/start",
            {
                "cwd": "/tmp/work",
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
                "developerInstructions": "Focus on regressions.",
            },
        )

    async def test_start_thread_does_not_add_codex_generated_image_prompt_to_thread_instructions(self):
        agent = object.__new__(CodexAgent)
        agent.controller = SimpleNamespace(config=SimpleNamespace(platform="slack", reply_enhancements=True))
        agent.settings_manager = SimpleNamespace(get_channel_settings=lambda session_key: None)
        agent.codex_config = SimpleNamespace(default_model=None)
        agent._session_mgr = SimpleNamespace(set_thread_id=Mock())
        agent.sessions = SimpleNamespace(set_agent_session_mapping=Mock())
        request = SimpleNamespace(
            working_path="/tmp/work",
            context=SimpleNamespace(
                platform="slack",
                platform_specific={},
                user_id="U1",
                channel_id="C1",
                thread_id=None,
            ),
            base_session_id="session-1",
            session_key="channel-1",
            subagent_name=None,
            subagent_model=None,
            subagent_reasoning_effort=None,
        )
        transport = SimpleNamespace(send_request=AsyncMock(return_value={"thread": {"id": "thread-1"}}))

        await agent._start_thread(transport, request)

        params = transport.send_request.await_args.args[1]
        self.assertNotIn("If you generate an image with Codex", params["developerInstructions"])

    def test_build_input_adds_codex_generated_image_prompt_to_each_turn(self):
        agent = object.__new__(CodexAgent)
        agent.controller = SimpleNamespace(config=SimpleNamespace(reply_enhancements=True))
        request = SimpleNamespace(message="hello", files=None)

        items = agent._build_input(request)

        self.assertTrue(items[0]["text"].startswith("If you generate an image with Codex"))
        self.assertIn("$CODEX_HOME/generated_images/<thread-id>/<image-file>.png", items[0]["text"])
        self.assertIn("$HOME/.codex/generated_images/<thread-id>/<image-file>.png", items[0]["text"])
        self.assertIn("Use only that local generated_images path", items[0]["text"])
        self.assertIn("never sandbox paths like `/mnt/data/...`", items[0]["text"])
        self.assertIn("leave the final reply empty", items[0]["text"])
        self.assertTrue(items[0]["text"].endswith("hello"))

    async def test_start_turn_uses_sandbox_policy_object(self):
        agent = object.__new__(CodexAgent)
        agent.settings_manager = SimpleNamespace(get_channel_settings=lambda session_key: None)
        agent.codex_config = SimpleNamespace(default_model=None)
        agent._build_input = Mock(return_value=[{"type": "text", "text": "hello"}])
        agent._turn_registry = SimpleNamespace(
            begin_turn_start=Mock(),
            get_bootstrapped_turn_id=Mock(return_value=None),
            finalize_turn_start_response=Mock(return_value=SimpleNamespace()),
        )
        request = SimpleNamespace(
            session_key="channel-1",
            base_session_id="session-1",
            composite_session_id="slack:C1:T1",
            subagent_name=None,
            subagent_model=None,
            subagent_reasoning_effort=None,
        )
        transport = SimpleNamespace(send_request=AsyncMock(return_value={"turn": {"id": "turn-1"}}))

        thread_id = await agent._start_turn(transport, request, "thread-1")

        self.assertEqual(thread_id, "thread-1")
        transport.send_request.assert_awaited_once_with(
            "turn/start",
            {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "hello"}],
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "dangerFullAccess"},
            },
        )

    async def test_start_turn_uses_context_specific_settings_manager_for_routing(self):
        agent = object.__new__(CodexAgent)
        agent.settings_manager = SimpleNamespace(
            get_channel_settings=lambda settings_key: SimpleNamespace(
                routing=SimpleNamespace(codex_model="wrong-model", codex_reasoning_effort="low")
            )
        )
        context_manager = SimpleNamespace(
            get_channel_settings=lambda settings_key: SimpleNamespace(
                routing=SimpleNamespace(codex_model="gpt-5.4", codex_reasoning_effort="high")
            )
        )
        agent.controller = SimpleNamespace(
            _get_settings_key=lambda context: "U123",
            get_settings_manager_for_context=lambda context: context_manager,
        )
        agent.codex_config = SimpleNamespace(default_model="fallback-model")
        agent._build_input = Mock(return_value=[{"type": "text", "text": "hello"}])
        agent._turn_registry = SimpleNamespace(
            begin_turn_start=Mock(),
            get_bootstrapped_turn_id=Mock(return_value=None),
            finalize_turn_start_response=Mock(return_value=SimpleNamespace()),
        )
        request = SimpleNamespace(
            session_key="discord::D123",
            base_session_id="session-1",
            composite_session_id="discord:D1:T1",
            context=SimpleNamespace(platform="discord", platform_specific={"is_dm": True}),
            subagent_name=None,
            subagent_model=None,
            subagent_reasoning_effort=None,
        )
        transport = SimpleNamespace(send_request=AsyncMock(return_value={"turn": {"id": "turn-1"}}))

        await agent._start_turn(transport, request, "thread-1")

        transport.send_request.assert_awaited_once_with(
            "turn/start",
            {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "hello"}],
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "dangerFullAccess"},
                "model": "gpt-5.4",
                "effort": "high",
            },
        )

    async def test_start_turn_does_not_fall_back_to_primary_platform_when_context_manager_has_no_settings(self):
        agent = object.__new__(CodexAgent)
        agent.settings_manager = SimpleNamespace(
            get_channel_settings=lambda settings_key: SimpleNamespace(
                routing=SimpleNamespace(codex_model="wrong-model", codex_reasoning_effort="low")
            )
        )
        context_manager = SimpleNamespace(get_channel_settings=lambda settings_key: None)
        agent.controller = SimpleNamespace(
            _get_settings_key=lambda context: "U123",
            get_settings_manager_for_context=lambda context: context_manager,
        )
        agent.codex_config = SimpleNamespace(default_model="fallback-model")
        agent._build_input = Mock(return_value=[{"type": "text", "text": "hello"}])
        agent._turn_registry = SimpleNamespace(
            begin_turn_start=Mock(),
            get_bootstrapped_turn_id=Mock(return_value=None),
            finalize_turn_start_response=Mock(return_value=SimpleNamespace()),
        )
        request = SimpleNamespace(
            session_key="discord::D123",
            base_session_id="session-1",
            composite_session_id="discord:D1:T1",
            context=SimpleNamespace(platform="discord", platform_specific={"is_dm": True}),
            subagent_name=None,
            subagent_model=None,
            subagent_reasoning_effort=None,
            working_path="/tmp/work",
        )
        transport = SimpleNamespace(send_request=AsyncMock(return_value={"turn": {"id": "turn-1"}}))

        await agent._start_turn(transport, request, "thread-1")

        transport.send_request.assert_awaited_once_with(
            "turn/start",
            {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "hello"}],
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "dangerFullAccess"},
                "model": "fallback-model",
            },
        )

    async def test_start_turn_uses_codex_agent_defaults_when_routing_selects_agent(self):
        agent = object.__new__(CodexAgent)
        agent.settings_manager = SimpleNamespace(
            get_channel_settings=lambda settings_key: SimpleNamespace(
                routing=SimpleNamespace(
                    codex_agent="reviewer",
                    codex_model=None,
                    codex_reasoning_effort=None,
                )
            )
        )
        agent.codex_config = SimpleNamespace(default_model="fallback-model")
        agent._build_input = Mock(return_value=[{"type": "text", "text": "hello"}])
        agent._turn_registry = SimpleNamespace(
            begin_turn_start=Mock(),
            get_bootstrapped_turn_id=Mock(return_value=None),
            finalize_turn_start_response=Mock(return_value=SimpleNamespace()),
        )
        request = SimpleNamespace(
            session_key="channel-1",
            base_session_id="session-1",
            composite_session_id="slack:C1:T1",
            working_path="/tmp/work",
            subagent_name=None,
            subagent_model=None,
            subagent_reasoning_effort=None,
        )
        transport = SimpleNamespace(send_request=AsyncMock(return_value={"turn": {"id": "turn-1"}}))

        with patch.object(
            _MODULE,
            "load_codex_subagent",
            return_value=SimpleNamespace(
                developer_instructions="Focus on regressions.",
                model="gpt-5.4",
                reasoning_effort="high",
            ),
        ) as load_subagent:
            await agent._start_turn(transport, request, "thread-1")

        load_subagent.assert_called_once_with("reviewer", project_root=Path("/tmp/work"))
        transport.send_request.assert_awaited_once_with(
            "turn/start",
            {
                "threadId": "thread-1",
                "input": [{"type": "text", "text": "hello"}],
                "approvalPolicy": "never",
                "sandboxPolicy": {"type": "dangerFullAccess"},
                "model": "gpt-5.4",
                "effort": "high",
            },
        )

class CodexTransportCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_transport_always_starts_app_server_with_global_bypass_flag(self):
        import importlib.util
        from pathlib import Path

        transport_path = Path(__file__).resolve().parents[1] / "modules/agents/codex/transport.py"
        spec = importlib.util.spec_from_file_location("test_codex_transport_module", transport_path)
        assert spec is not None and spec.loader is not None
        transport_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(transport_module)
        Transport = transport_module.CodexTransport

        writes = []
        created_cmd = {}

        class _FakeStdin:
            def __init__(self):
                self._closing = False

            def write(self, data):
                writes.append(data.decode())

            async def drain(self):
                return None

            def is_closing(self):
                return self._closing

            def close(self):
                self._closing = True

        class _FakeStdout:
            def __init__(self):
                self._lines = [b'{"jsonrpc":"2.0","id":1,"result":{}}\n', b""]

            async def readline(self):
                return self._lines.pop(0)

        class _FakeStderr:
            async def readline(self):
                return b""

        class _FakeProcess:
            def __init__(self):
                self.stdin = _FakeStdin()
                self.stdout = _FakeStdout()
                self.stderr = _FakeStderr()
                self.pid = 123
                self.returncode = None

            async def wait(self):
                self.returncode = 0
                return 0

        async def fake_create_subprocess_exec(*cmd, **kwargs):
            created_cmd["cmd"] = list(cmd)
            return _FakeProcess()

        with patch.object(
            transport_module.asyncio,
            "create_subprocess_exec",
            new=fake_create_subprocess_exec,
        ):
            transport = Transport(binary="codex", cwd="/tmp/work")
            await transport.start()
            await transport.stop()

        self.assertEqual(
            created_cmd["cmd"],
            ["codex", "--dangerously-bypass-approvals-and-sandbox", "app-server"],
        )


if __name__ == "__main__":
    unittest.main()
