"""Per-session turn ownership for the avibe workbench.

Phase 1b of the turn-lifecycle FSM (``docs/plans/avibe-turn-lifecycle-fsm.md``):
introduce ONE owner of a session's turn state so the gate, dispatcher, scheduler,
and restore paths stop reconciling several separate stores. A session has **at
most one active turn** (IDLE ↔ RUNNING; no turn-duration timeout — a long agent
runs until it emits its terminal result or the user Stops it).

``SessionTurnManager`` is wired as ``controller.session_turns`` by
``core.internal_server.create_app``. It owns the in_flight registry + the
flush-intent sets, and the turn lifecycle: ``submit`` (start + hold-open) and
``flush_queue`` (drain the send-while-busy queue). The internal-server HTTP
handlers and the scheduler are thin callers. Cancel / send-now / turn-state /
terminal-result move onto the manager in subsequent commits.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable, Optional

from core.services.dispatch import SOURCE_HUMAN, dispatch_turn

if TYPE_CHECKING:
    from modules.im import MessageContext

logger = logging.getLogger(__name__)


class SessionTurnManager:
    """Owns the live per-session turn state + lifecycle for avibe sessions.

    Containers (the same shapes the gate used inline):

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
      ``CancelledError``) during the awaited stop.

    ``controller`` reaches the backends + the outbound chokepoint
    (``emit_agent_message``); ``build_context`` rebuilds a session's routing
    ``MessageContext`` for a queued follow-up (injected by the gate because it
    lives in ``internal_server``).
    """

    def __init__(
        self,
        controller: Any = None,
        *,
        build_context: Optional[Callable[[str], "MessageContext"]] = None,
    ) -> None:
        self.controller = controller
        self._build_context = build_context
        self.in_flight: dict[str, tuple[asyncio.Task, "MessageContext"]] = {}
        self.flush_on_cancel: set[str] = set()
        self.stop_no_flush: set[str] = set()

    def is_in_flight(self, session_id: Optional[str]) -> bool:
        """True when ``session_id`` has an active (RUNNING) turn."""
        return bool(session_id) and session_id in self.in_flight

    @staticmethod
    async def _noop_chunk(_envelope: dict) -> None:
        # Chunks are discarded — the browser renders from ``message.new``.
        return None

    async def submit(
        self,
        session_id: Optional[str],
        context: "MessageContext",
        text: str,
        *,
        source: str = SOURCE_HUMAN,
    ) -> None:
        """Start a fire-and-forget turn and HOLD it open until it settles.

        A no-op chunk sink keeps ``dispatch_turn`` alive for the turn's lifetime so
        ``in_flight`` stays populated (Stop works) and the session-level
        ``turn.start`` / ``turn.end`` lifecycle is published for the browser's
        working indicator. On NATURAL completion the queue is flushed: messages the
        user sent while this turn ran are merged + run as the next turn. A user Stop
        (cancellation) does NOT flush — the queue is kept per the user's "don't
        clear the queue on stop" rule — unless ``send-now`` opted this session into
        ``flush_on_cancel``. The reply reaches the browser over ``message.new``.

        ``source`` selects the human vs. scheduler turn path in ``dispatch_turn``;
        a scheduled / watch run passes ``SOURCE_SCHEDULED`` so it goes through the
        SAME gate (in_flight + turn.start/turn.end + queue draining) as a Chat turn.
        There is NO turn-duration timeout: a long agent runs for hours and is freed
        only by a real terminal signal (Phase 1a — STUCK/sentinel removed).
        """
        from core.inbox_events import bus

        async def _runner() -> None:
            cancelled = False
            failed = False
            try:
                await dispatch_turn(
                    self.controller,
                    context,
                    text,
                    source=source,
                    # ALWAYS pass the no-op sink — even for scheduled runs. It isn't
                    # about the browser (chunks are discarded; avibe renders from
                    # message.new); it makes ``dispatch_turn`` HOLD the turn open
                    # until the backend's terminal result, keeping ``in_flight``
                    # populated for the turn's whole lifetime. With ``on_chunk=None``
                    # an async backend (Codex/Claude) returns at prompt-submit, so the
                    # slot would free + a Chat send could preempt the still-running
                    # scheduled turn (Codex P2).
                    on_chunk=self._noop_chunk,
                )
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception:
                # dispatch_turn raised before any backend turn was actually
                # dispatched (missing/disabled backend, synchronous setup error).
                # No agent reply was produced, so this is a terminal FAILURE — it must
                # NOT auto-flush the send-while-busy queue onto a fresh turn (Codex
                # P2). (An explicit send-now flush_on_cancel still flushes.)
                failed = True
                logger.exception("internal async dispatch failed for session=%s", session_id)
            finally:
                if isinstance(session_id, str):
                    # The turn is over — the agent emitted its terminal result, the
                    # user stopped it, or dispatch raised before any backend turn.
                    # NO turn-duration timeout: the slot is freed only by a real
                    # terminal signal here (Phase 1a — STUCK/sentinel removed).
                    self.in_flight.pop(session_id, None)
                    bus.publish("turn.end", {"session_id": session_id})
                    # Converge the no-terminal-result outcome onto the OUTBOUND status
                    # chokepoint. The normal path already emitted a terminal result;
                    # only ``failed`` reaches here without one: dispatch raised before
                    # any backend turn (missing/disabled backend) → empty error result
                    # → dot red. This is a real terminal FAILURE, not a timeout.
                    if failed:
                        await self.controller.emit_agent_message(context, "result", "", is_error=True)
                    # Don't flush after a Stop (keep the queue) or a terminal failure.
                    # send-now still forces a flush via flush_on_cancel.
                    should_flush = (
                        (not cancelled and not failed and session_id not in self.stop_no_flush)
                        or (session_id in self.flush_on_cancel)
                    )
                    self.flush_on_cancel.discard(session_id)
                    self.stop_no_flush.discard(session_id)
                    if should_flush:
                        await self.flush_queue(session_id)

        task = asyncio.create_task(_runner(), name="internal-dispatch-async")
        if isinstance(session_id, str) and session_id:
            self.in_flight[session_id] = (task, context)
            bus.publish("turn.start", {"session_id": session_id})

    async def flush_queue(self, session_id: str) -> bool:
        """Pop the messages queued while a turn ran, merge them into one
        (newline-joined) user message, and run it as the next turn — recursively
        draining the queue. Returns True if a turn was started, False on an empty
        queue / failure (so ``send-now`` can report idle instead of leaving the
        client stuck waiting). The merge is the user's choice (one dispatch, not N);
        the individual queued rows are deleted by ``pop_queued`` and replaced by the
        single merged user row."""
        from core.inbox_events import bus
        from storage import messages_service
        from storage.db import create_sqlite_engine

        if not session_id:
            return False
        user_row = None
        inbox_row = None
        try:
            engine = create_sqlite_engine()
            with engine.begin() as conn:
                rows = messages_service.pop_queued(conn, session_id)
                texts = [r.get("text") for r in rows if (r.get("text") or "").strip()]
                if not texts:
                    return False
                user_row = messages_service.append(
                    conn,
                    scope_id=rows[0]["scope_id"],
                    session_id=session_id,
                    platform="avibe",
                    author="user",
                    source="user",
                    message_type="user",
                    text="\n".join(texts),
                )
                inbox_row = messages_service.get_inbox_session(conn, session_id)
        except Exception:
            logger.exception("queue flush: failed to pop/merge for session=%s", session_id)
            return False
        if user_row is None:
            return False
        # Surface the flushed (merged) user message, bump the inbox card (so other
        # workbench views re-rank + flip 'replied' without waiting for the next
        # result — Codex P2), and mark the queue empty.
        bus.publish("message.new", user_row)
        if inbox_row is not None:
            bus.publish("inbox.session.updated", inbox_row)
        bus.publish("queue.updated", {"session_id": session_id})
        # Rebuild routing from the CURRENT session row so a queued follow-up uses the
        # session's latest agent / model / effort — the user may have changed it while
        # the prior (now-finished) turn was running (Codex P2).
        if self._build_context is None:
            logger.error("queue flush: no build_context bound for session=%s", session_id)
            return False
        try:
            context = self._build_context(session_id)
        except Exception:
            logger.exception("queue flush: failed to build context for session=%s", session_id)
            return False
        await self.submit(session_id, context, user_row.get("text") or "")
        return True
