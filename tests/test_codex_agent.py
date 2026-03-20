import importlib.util
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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

_STUBBED_MODULES = {
    "modules": _modules_pkg,
    "modules.agents": _agents_pkg,
    "modules.agents.codex": _codex_pkg,
    "modules.agents.base": _base_module,
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


class _HandleMessageTurnRegistry:
    def __init__(self, active_turn: str | None):
        self.active_turn = active_turn
        self.remembered_requests = []

    def remember_request(self, request):
        self.remembered_requests.append(request)

    def get_active_turn(self, base_session_id: str):
        return self.active_turn


class CodexAgentHandleMessageTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_message_does_not_hide_turn_before_interrupt_succeeds(self):
        agent = object.__new__(CodexAgent)
        request = SimpleNamespace(
            base_session_id="session-1",
            working_path="/tmp",
            context=object(),
            settings_key="settings-1",
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
            set_settings_key=lambda base_session_id, settings_key: None,
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


if __name__ == "__main__":
    unittest.main()
