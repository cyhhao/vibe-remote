"""In-memory lifecycle tracking for Codex turns."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from modules.agents.base import AgentRequest


@dataclass
class CodexTurnState:
    turn_id: str
    request: AgentRequest
    pending_assistant: Optional[Tuple[str, Optional[str]]] = None
    terminal_error: Optional[str] = None
    terminal_error_notified: bool = False


class CodexTurnRegistry:
    """Owns Codex turn-scoped request routing and lifecycle state."""

    def __init__(self) -> None:
        self._turns: dict[str, CodexTurnState] = {}
        self._active_turns: dict[str, str] = {}
        self._latest_requests: dict[str, AgentRequest] = {}

    def remember_request(self, request: AgentRequest) -> None:
        self._latest_requests[request.base_session_id] = request

    def get_latest_request(self, base_session_id: str) -> Optional[AgentRequest]:
        return self._latest_requests.get(base_session_id)

    def register_turn(self, turn_id: str, request: AgentRequest) -> CodexTurnState | None:
        if not turn_id:
            return None

        self.remember_request(request)
        state = self._turns.get(turn_id)
        if state is None:
            state = CodexTurnState(turn_id=turn_id, request=request)
            self._turns[turn_id] = state
        else:
            state.request = request

        self._active_turns[request.base_session_id] = turn_id
        return state

    def get_turn(self, turn_id: str) -> Optional[CodexTurnState]:
        if not turn_id:
            return None
        return self._turns.get(turn_id)

    def pop_turn(self, turn_id: str) -> Optional[CodexTurnState]:
        if not turn_id:
            return None

        state = self._turns.pop(turn_id, None)
        if state and self._active_turns.get(state.request.base_session_id) == turn_id:
            self._active_turns.pop(state.request.base_session_id, None)
        return state

    def get_request_for_turn(self, turn_id: str) -> Optional[AgentRequest]:
        state = self.get_turn(turn_id)
        return state.request if state else None

    def get_active_turn(self, base_session_id: str) -> Optional[str]:
        return self._active_turns.get(base_session_id)

    def is_active_turn(self, turn_id: str) -> bool:
        state = self.get_turn(turn_id)
        if not state:
            return False
        return self._active_turns.get(state.request.base_session_id) == turn_id

    def clear_active_turn(self, base_session_id: str) -> None:
        self._active_turns.pop(base_session_id, None)

    def should_emit_progress(self, turn_id: str) -> bool:
        return self.is_active_turn(turn_id)

    def should_emit_terminal_error(self, turn_id: str) -> bool:
        return self.is_active_turn(turn_id)

    def should_emit_result(self, turn_id: str) -> bool:
        return self.is_active_turn(turn_id)

    def clear_session(self, base_session_id: str) -> int:
        self._latest_requests.pop(base_session_id, None)
        self._active_turns.pop(base_session_id, None)

        removed = 0
        for turn_id, state in list(self._turns.items()):
            if state.request.base_session_id == base_session_id:
                self._turns.pop(turn_id, None)
                removed += 1
        return removed
