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


def emit_matches_active_turn(sink: dict, context: "MessageContext") -> bool:
    """The ONE active-turn token rule (FSM Phase 2 — collapses the three previously
    duplicated guards: ``_stream_chunk`` completion, ``_is_active_turn``, and
    ``Controller.mark_turn_complete``).

    A live sink WITH a token means an interactive turn is in flight; only its OWN
    result (matching token) is the active turn's. A result whose token DIFFERS or is
    ABSENT is stale — a superseded / stopped / older turn, or a scheduled / watch run
    that carries no token — and must NOT complete the turn (set ``done_event``) or
    settle its dot. Fail-open when the sink itself is tokenless, so non-streaming
    turns still settle. (Chunk FORWARDING is deliberately NOT gated — see
    ``_stream_chunk``; only COMPLETION + dot-settle are.)

    NOTE (no-timeout invariant): with the turn-duration timeout gone, a turn whose
    OWN terminal result is tokenless would hang here forever. The FSM therefore must
    guarantee every terminal result carries the active turn's token (Claude adoption
    / FSM-attached token); this guard is intentionally strict.
    """
    sink_token = sink.get("turn_token")
    ctx_token = (getattr(context, "platform_specific", None) or {}).get("turn_token")
    return not (sink_token is not None and ctx_token != sink_token)


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
        # The live streaming turn sink per SESSION KEY (avibe/web-Chat only; IM/CLI
        # turns register none). Each is ``{on_chunk, done_event, turn_token}`` — the
        # turn's stream callback + completion event + correlation token. Keyed by
        # session_key (stable across a session's turns) so a reused agent receiver
        # carrying a stale per-turn context still resolves the current turn's sink.
        self.active_turn_sinks: dict[str, dict] = {}

    def is_in_flight(self, session_id: Optional[str]) -> bool:
        """True when ``session_id`` has an active (RUNNING) turn."""
        return bool(session_id) and session_id in self.in_flight

    def bind_context(self, build_context: Callable[[str], "MessageContext"]) -> None:
        """Inject the routing-context builder (it lives in ``internal_server``) once
        the gate is built, so ``flush_queue`` can rebuild a queued follow-up's
        routing from the current session row."""
        self._build_context = build_context

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
        enqueue: Optional[Callable[[], None]] = None,
    ) -> str:
        """Unified turn entry for BOTH Chat and the scheduler: idle → run now; busy
        (or a pre-existing send-while-busy queue) → enqueue and run later.

        Returns ``"ran"`` or ``"enqueued"``. The busy / pre-existing-queue decision,
        the idle-with-queue drain, and the run are unified here; the caller supplies
        ``enqueue`` — a 0-arg callable that persists the SOURCE-specific queued row
        (Chat promotes its pre-saved pending row; the scheduler appends a harness
        row) — because that row's shape depends on the request. The in_flight check
        and the enqueue have no ``await`` between them (single-threaded loop), so a
        running turn cannot end + flush in the gap — the enqueue stays atomic.
        """
        if not (isinstance(session_id, str) and session_id):
            # No session key (CLI-style) — just run; nothing to queue against.
            await self._run(None, context, text, source=source)
            return "ran"

        from storage import messages_service
        from storage.db import create_sqlite_engine

        entry = self.in_flight.get(session_id)
        busy = entry is not None and not entry[0].done()
        # Enqueue when a turn is running OR a prior Stop left queued rows behind — the
        # new message must run AFTER them, not jump ahead (Codex P2).
        if busy:
            should_enqueue = True
        else:
            engine = create_sqlite_engine()
            with engine.connect() as conn:
                should_enqueue = bool(messages_service.list_queued(conn, session_id))
        if should_enqueue:
            if enqueue is not None:
                enqueue()
            # Idle + pre-existing queue → no running turn to flush behind, so drain
            # the whole queue (this row included) now, in order.
            if not busy:
                await self.flush_queue(session_id)
            return "enqueued"
        await self._run(session_id, context, text, source=source)
        return "ran"

    async def _run(
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
        await self._run(session_id, context, user_row.get("text") or "")
        return True

    def turn_state(self, session_id: str) -> dict:
        """Whether a turn is currently RUNNING for the session. The fire-and-forget
        dispatch survives browser disconnects, so a freshly loaded / reconnected
        Chat page asks this to restore its working / Stop state (Codex P2)."""
        entry = self.in_flight.get(session_id)
        active = entry is not None and not entry[0].done()
        return {"ok": True, "session_id": session_id, "in_flight": active}

    async def cancel(self, session_id: str) -> dict:
        """Stop the active turn: interrupt the agent's backend run via the SAME path
        the IM ``/stop`` command uses (Claude interrupt / Codex turn-interrupt /
        OpenCode abort) — not just the waiter — keeping the send-while-busy queue
        ("不清空"). Returns a result dict; ``code`` is ``not_in_flight`` /
        ``stop_failed`` for the HTTP adapter to map to 404 / 409, else a 200 status.
        """
        entry = self.in_flight.get(session_id)
        if entry is None:
            return {"ok": False, "code": "not_in_flight", "session_id": session_id}
        task, turn_context = entry
        if task.done():
            return {"ok": True, "session_id": session_id, "status": "already_finished"}
        # Record the no-flush intent BEFORE awaiting the interrupt: if the backend
        # stop lets the turn settle normally during the await (no CancelledError),
        # submit()'s finally would otherwise treat it as a natural completion and
        # flush — but a plain Stop keeps the queue (Codex P2). We pass the context the
        # turn STARTED under so the right backend is interrupted even if the Chat
        # header swapped the session's agent / model mid-turn.
        self.stop_no_flush.add(session_id)
        stopped = False
        try:
            stopped = bool(await self.controller.command_handler.handle_stop(turn_context))
        except Exception:
            logger.exception("internal cancel: backend stop failed for session=%s", session_id)
        if not stopped:
            # Stop refused — the turn keeps running, so it isn't being stopped; drop
            # the no-flush marker so a later natural completion flushes normally.
            # Don't cancel the waiter — that would fire a false ``turn.end``, hide
            # Stop, and let follow-up work start while the turn still produces output
            # (Codex P2).
            self.stop_no_flush.discard(session_id)
            return {"ok": False, "code": "stop_failed", "session_id": session_id}
        task.cancel()
        return {"ok": True, "session_id": session_id, "status": "cancel_requested"}

    async def send_now(self, session_id: str) -> dict:
        """Run the session's send-while-busy queue immediately ("立即发送").

        If a turn is running (and something is queued), interrupt it (the user chose
        to cut in) and opt into ``flush_on_cancel`` so the queue runs as that turn
        unwinds. If nothing is running, flush directly as a fresh turn. No-op when
        the queue is empty. Returns a result dict (``code='stop_failed'`` → 409 for
        the HTTP adapter).
        """
        from storage import messages_service
        from storage.db import create_sqlite_engine

        entry = self.in_flight.get(session_id)
        if entry is not None and not entry[0].done():
            # Don't interrupt a live turn unless there is actually something queued to
            # cut in with — a stale queue item already flushed by another tab would
            # otherwise make send-now an unintended Stop (Codex P2).
            engine = create_sqlite_engine()
            with engine.connect() as conn:
                has_queue = bool(messages_service.list_queued(conn, session_id))
            if not has_queue:
                return {"ok": True, "session_id": session_id, "status": "empty"}
            _task, turn_context = entry
            # Record the flush intent BEFORE awaiting the interrupt (same race as
            # cancel, opposite intent: send-now WANTS the queue to run). Drop it on a
            # refused stop and leave the turn + queue untouched (Codex P2).
            self.flush_on_cancel.add(session_id)
            stopped = False
            try:
                stopped = bool(await self.controller.command_handler.handle_stop(turn_context))
            except Exception:
                logger.exception("internal send-now: backend stop failed for session=%s", session_id)
            if not stopped:
                self.flush_on_cancel.discard(session_id)
                return {"ok": False, "code": "stop_failed", "session_id": session_id}
            _task.cancel()
            return {"ok": True, "session_id": session_id, "status": "interrupted"}
        # No running turn — flush the queue directly as a new turn (rebuilds routing
        # from the current session row internally). ``empty`` when nothing flushed.
        flushed = await self.flush_queue(session_id)
        return {"ok": True, "session_id": session_id, "status": "flushed" if flushed else "empty"}

    # --- the two status chokepoints (the dot is a projection of the turn) ---------

    def on_running(self, context: "MessageContext") -> None:
        """INBOUND status chokepoint: mark the avibe session ``running`` when a turn
        starts (every source / backend funnels through AgentService.handle_message).
        Non-avibe turns carry no workbench session id and are skipped."""
        if self.controller is None:
            return
        session_id = self.controller._session_id_from_context(context)
        if session_id:
            self.controller.set_agent_status(session_id, "running")

    def on_terminal_result(self, context: "MessageContext", *, is_error: bool) -> None:
        """OUTBOUND status chokepoint: settle the avibe dot when the ACTIVE turn's
        terminal ``result`` is emitted — ``idle`` normally, ``failed`` on
        ``is_error``. A late result from a superseded / stopped turn (the active-turn
        guard) or a non-avibe context (no session id) is skipped, so it can't flip a
        newer turn's ``running`` back."""
        if self.controller is None:
            return
        session_id = self.controller._session_id_from_context(context)
        if not session_id or not self.is_active_emit(context):
            return
        self.controller.set_agent_status(session_id, "failed" if is_error else "idle")

    def is_active_emit(self, context: "MessageContext") -> bool:
        """Whether an emit belongs to the live turn (not a superseded one). Fail-open
        when there's no sink registry / no live sink (non-streaming turns still
        settle), else apply the one token rule. Centralizes the old
        ``ConsolidatedMessageDispatcher._is_active_turn``."""
        get_sink = getattr(self.controller, "get_turn_sink", None)
        get_key = getattr(self.controller, "_get_session_key", None)
        if not callable(get_sink) or not callable(get_key):
            return True
        try:
            sink = get_sink(get_key(context))
        except Exception:
            return True
        if sink is None:
            return True
        return emit_matches_active_turn(sink, context)

    # --- the live streaming turn sink (owned here; Controller delegates) ----------

    def register_turn_sink(self, session_key: str, *, on_chunk, done_event, turn_token=None) -> None:
        if session_key in self.active_turn_sinks:
            # dispatch_turn serializes streaming turns per session, so this should not
            # happen; if it does, keep the in-flight turn's sink rather than clobbering
            # it (replacing it once let a stale result satisfy a replacement sink).
            logger.warning("Ignoring duplicate turn sink registration for %s", session_key)
            return
        # turn_token correlates emits to this exact turn so a late straggler from a
        # superseded turn (same session key) is dropped in _stream_chunk.
        self.active_turn_sinks[session_key] = {
            "on_chunk": on_chunk,
            "done_event": done_event,
            "turn_token": turn_token,
        }

    def pop_turn_sink(self, session_key: str, done_event=None) -> None:
        # Identity-guarded: only remove the sink THIS turn registered. A concurrent /
        # retried turn may have replaced it (same session key, different done_event);
        # the older turn's cleanup must not evict the newer turn's sink. done_event=None
        # pops unconditionally (non-streaming / legacy callers).
        sink = self.active_turn_sinks.get(session_key)
        if sink is None:
            return
        if done_event is not None and sink.get("done_event") is not done_event:
            return
        self.active_turn_sinks.pop(session_key, None)

    def get_turn_sink(self, session_key: str) -> Optional[dict]:
        return self.active_turn_sinks.get(session_key)

    # --- boot / restore edge transitions -----------------------------------------

    @staticmethod
    def reset_stale() -> None:
        """Crash recovery (boot): no turn survives a restart, so any avibe session
        left ``running`` in the table is stale → reset it to ``idle`` so the sidebar
        dot doesn't show a phantom green forever. Runs in ``Controller.__init__``
        BEFORE any ``/internal/events`` subscriber exists, so it does NOT broadcast
        ``session.status`` (the bus drops events with no subscribers); the browser
        reconciles by refetching sessions when its inbox stream (re)connects."""
        try:
            from core.services import sessions as workbench_sessions_service
            from storage.db import create_sqlite_engine

            engine = create_sqlite_engine()
            try:
                with engine.begin() as conn:
                    reset = workbench_sessions_service.reset_running_agent_status(conn)
            finally:
                engine.dispose()
            if reset:
                logger.info("Reset %s stale 'running' agent session(s) to idle on startup", reset)
        except Exception:
            logger.debug("agent_status startup reset failed", exc_info=True)

    def restore_running(self, session_id: Optional[str]) -> None:
        """Re-mark an avibe session ``running`` when its OpenCode poll is restored
        after a restart: the restored poll resumes the backend turn WITHOUT
        re-entering the inbound chokepoint (``handle_message``), so without this the
        dot would read idle for a still-live turn until the poll's terminal result
        settles it back. IM polls carry no workbench session id, so they pass nothing
        here and stay dot-less."""
        if session_id and self.controller is not None:
            self.controller.set_agent_status(session_id, "running")
