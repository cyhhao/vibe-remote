"""Per-session turn ownership for the avibe workbench.

Phase 1b of the turn-lifecycle FSM (``docs/plans/avibe-turn-lifecycle-fsm.md``):
introduce ONE owner of a session's turn state so the gate, dispatcher, scheduler,
and restore paths stop reconciling several separate stores. A session has **at
most one active turn** (IDLE ↔ RUNNING; no turn-duration timeout — a long agent
runs until it emits its terminal result or the user Stops it).

This first step **re-homes the state containers** that lived inline in
``core.internal_server.create_app`` behind ``SessionTurnManager``, wired as
``controller.session_turns`` — WITHOUT changing behavior. The containers exposed
here are the SAME objects the ``internal_server`` closures and
``controller.session_turn_gate`` continue to use. Later commits move the lifecycle
logic (submit / terminal-result / cancel / send-now / queue flush) into methods on
this class so those call sites become thin callers, per Part 2 of the design.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from modules.im import MessageContext


class SessionTurnManager:
    """Owns the live per-session turn state for avibe sessions.

    Containers (currently the same shapes the gate used inline):

    - ``in_flight``: ``session_id -> (task, context)`` for the active turn. It is
      the Stop target (``/internal/cancel``), the ``/turn-state`` source, and the
      trigger for draining the send-while-busy queue. The stored ``MessageContext``
      is the one the turn STARTED under, so Stop interrupts the backend the turn
      actually ran on even if the Chat header later changed agent/model.
    - ``flush_on_cancel``: sessions whose queue should flush even though the turn is
      ending via cancellation — ``send-now`` cancels the running turn but wants the
      queue to run immediately after. A plain Stop keeps the queue ("不清空队列").
    - ``stop_no_flush``: sessions being stopped by a plain Stop that must NOT flush,
      even if the backend interrupt lets the turn settle normally (no
      ``CancelledError``) during the awaited stop. Recorded before awaiting the
      interrupt so the race is covered.

    These remain plain attributes (not yet wrapped in accessors) so existing
    closure code keeps operating on the identical objects — making the
    introduction of this owner a no-op behaviorally.
    """

    def __init__(self) -> None:
        self.in_flight: dict[str, tuple[asyncio.Task, "MessageContext"]] = {}
        self.flush_on_cancel: set[str] = set()
        self.stop_no_flush: set[str] = set()

    def is_in_flight(self, session_id: Optional[str]) -> bool:
        """True when ``session_id`` has an active (RUNNING) turn."""
        return bool(session_id) and session_id in self.in_flight
