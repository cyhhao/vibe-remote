import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_EVENT_HANDLER_PATH = Path(__file__).resolve().parents[1] / "modules/agents/codex/event_handler.py"
_SPEC = importlib.util.spec_from_file_location("test_codex_event_handler_module", _EVENT_HANDLER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)
CodexEventHandler = _MODULE.CodexEventHandler


class _StubSessionManager:
    def __init__(self, active_turn: str):
        self.active_turn = active_turn

    def get_active_turn(self, base_session_id: str):
        return self.active_turn

    def clear_active_turn(self, base_session_id: str) -> None:
        self.active_turn = None


class _StubAgent:
    def __init__(self, active_turn: str):
        self._session_mgr = _StubSessionManager(active_turn)
        self._active_requests = {}
        self._turn_requests = {}
        self.controller = SimpleNamespace(emit_agent_message=AsyncMock())
        self.emit_result_message = AsyncMock()
        self._remove_ack_reaction = AsyncMock()

    def _remember_turn_request(self, turn_id: str, request) -> None:
        self._turn_requests[turn_id] = request

    def _forget_turn_request(self, turn_id: str) -> None:
        self._turn_requests.pop(turn_id, None)


class CodexEventHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_retrying_error_is_suppressed(self):
        agent = _StubAgent(active_turn="turn-1")
        handler = CodexEventHandler(agent)
        request = SimpleNamespace(base_session_id="session-1", context=object(), started_at=0)

        await handler._on_error(
            {
                "error": {"message": "Reconnecting... 5/5"},
                "willRetry": True,
                "turnId": "turn-1",
            },
            request,
        )

        agent.controller.emit_agent_message.assert_not_awaited()

    async def test_terminal_turn_error_is_emitted_immediately_and_not_duplicated_on_completion(self):
        agent = _StubAgent(active_turn="turn-1")
        handler = CodexEventHandler(agent)
        request = SimpleNamespace(base_session_id="session-1", context=object(), started_at=0)

        await handler._on_error(
            {
                "error": {"message": "unexpected status 401 Unauthorized:"},
                "willRetry": False,
                "turnId": "turn-1",
            },
            request,
        )

        agent.controller.emit_agent_message.assert_awaited_once_with(
            request.context,
            "notify",
            "❌ Codex turn failed: unexpected status 401 Unauthorized:",
        )

        await handler._on_turn_completed(
            {
                "turn": {
                    "id": "turn-1",
                    "status": "failed",
                    "error": {"message": "fallback message"},
                }
            },
            request,
        )

        assert agent.controller.emit_agent_message.await_count == 1
        agent._remove_ack_reaction.assert_awaited_once_with(request)

    async def test_turn_failure_falls_back_to_completion_error_when_no_error_notification_arrives(self):
        agent = _StubAgent(active_turn="turn-1")
        handler = CodexEventHandler(agent)
        request = SimpleNamespace(base_session_id="session-1", context=object(), started_at=0)

        await handler._on_turn_completed(
            {
                "turn": {
                    "id": "turn-1",
                    "status": "failed",
                    "error": {"message": "fallback message"},
                }
            },
            request,
        )

        agent.controller.emit_agent_message.assert_awaited_once_with(
            request.context,
            "notify",
            "❌ Codex turn failed: fallback message",
        )
        agent._remove_ack_reaction.assert_awaited_once_with(request)


if __name__ == "__main__":
    unittest.main()
