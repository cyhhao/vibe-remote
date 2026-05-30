"""Controller-side ASGI server bound to a Unix Domain Socket.

This is the C4 piece of Plan 2 from
``docs/plans/workbench-dispatch-architecture.md``: the controller process
exposes a minimal FastAPI app on
``~/.vibe_remote/state/dispatch.sock`` so cross-process callers (the
separate UI server subprocess, future ``vibe agent run --sync`` flows)
can invoke ``core.services.dispatch.dispatch_turn`` and stream the
agent's output back over SSE chunked response.

Three properties matter:

1. **Same asyncio loop as the controller.** The server runs as a
   background ``asyncio.Task`` on the loop that ``Controller.run()``
   creates. IM adapters share that loop. No cross-loop futures, no
   second uvicorn worker, no thread bridge.
2. **Local-only.** Unix sockets are bind to a file path on the local
   filesystem; no TCP listen, so external network exposure is
   impossible.
3. **0o600 permissions.** The socket file is chmod'd to ``0o600`` so
   only the running user can connect — defense in depth against shared
   hosts.

The endpoint set is intentionally tiny for v1 (``dispatch`` + a stub
``cancel``); follow-ups can grow it without changing the bind contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from config import paths
from core.services.dispatch import dispatch_turn
from modules.im.base import MessageContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.controller import Controller

logger = logging.getLogger(__name__)


def default_socket_path() -> Path:
    """Where the internal server binds by default.

    Lives under ``~/.vibe_remote/state/`` so it shares the rest of the
    runtime state's filesystem permissions and gets cleaned up on home
    directory wipes.
    """

    return paths.get_state_dir() / "dispatch.sock"


def create_app(controller: "Controller") -> FastAPI:
    """Build the minimal FastAPI app the internal server exposes.

    Factored out so tests can mount the same routes against a fake
    controller without spinning up uvicorn.
    """

    app = FastAPI(
        title="Vibe Remote internal dispatch",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # In-flight ``dispatch_turn`` tasks per session, each stored together with
    # the routing ``MessageContext`` the turn STARTED under. The cancel
    # endpoint looks the task up here so the UI can stop a runaway turn without
    # waiting for the agent to settle, and reuses the stored context so it
    # interrupts the backend the turn actually started on — even if the Chat
    # header changed the session's agent / model while the reply was streaming.
    # Tasks are registered when the SSE response starts and removed in its
    # ``finally`` so cancelled / completed sessions don't leak slots.
    in_flight: dict[str, tuple[asyncio.Task, MessageContext]] = {}
    app.state.in_flight_dispatches = in_flight

    # Sessions whose current turn should flush its send-while-busy queue EVEN
    # though it's ending via cancellation. A plain Stop cancels without flushing
    # (the user asked to keep the queue — "不清空队列"); ``send-now`` cancels the
    # running turn but sets this so the queue runs immediately afterwards.
    flush_on_cancel: set[str] = set()
    app.state.flush_on_cancel = flush_on_cancel

    # Sessions whose current turn is being stopped by a plain Stop and must NOT
    # flush, even if the backend interrupt lets the turn settle NORMALLY (no
    # CancelledError) during the awaited stop — a Stop keeps the queue ("不清空").
    # Recorded before awaiting the interrupt so the race is covered.
    stop_no_flush: set[str] = set()

    async def _noop_chunk(_envelope: dict) -> None:
        # Chunks are discarded — the browser renders from ``message.new``.
        return None

    async def _run_turn(session_id: Optional[str], context: MessageContext, text: str) -> None:
        """Start a fire-and-forget turn and HOLD it open until it settles.

        A no-op chunk sink keeps ``dispatch_turn`` alive for the turn's lifetime
        so ``in_flight`` stays populated (Stop works) and the session-level
        ``turn.start`` / ``turn.end`` lifecycle is published for the browser's
        working indicator. On NATURAL completion the queue is flushed: messages
        the user sent while this turn ran are merged + run as the next turn. A
        user Stop (cancellation) does NOT flush — the queue is kept per the
        user's "don't clear the queue on stop" rule — unless ``send-now`` opted
        this session into ``flush_on_cancel``. The reply reaches the browser over
        ``message.new``, not a response stream.
        """
        from core.inbox_events import bus

        async def _runner() -> None:
            cancelled = False
            try:
                await dispatch_turn(controller, context, text, on_chunk=_noop_chunk)
            except asyncio.CancelledError:
                cancelled = True
                raise
            except Exception:
                logger.exception("internal async dispatch failed for session=%s", session_id)
            finally:
                if isinstance(session_id, str):
                    timed_out = bool((context.platform_specific or {}).get("turn_timed_out"))
                    if timed_out:
                        # The 600s wait elapsed with the backend still running.
                        # Interrupt it BEFORE releasing the session, so /turn-state
                        # can't report idle (and a manual send can't start a turn)
                        # while a live backend turn is still producing output
                        # (Codex P2). A turn silent for 10 min is treated as stuck.
                        try:
                            await controller.command_handler.handle_stop(context)
                        except Exception:
                            logger.exception("dispatch timeout: backend stop failed for session=%s", session_id)
                    in_flight.pop(session_id, None)
                    bus.publish("turn.end", {"session_id": session_id})
                    # Don't flush after a Stop (keep the queue) OR after a stream
                    # timeout (the backend was just interrupted; the user can
                    # resume). send-now still forces a flush via flush_on_cancel.
                    should_flush = (
                        (not cancelled and not timed_out and session_id not in stop_no_flush)
                        or (session_id in flush_on_cancel)
                    )
                    flush_on_cancel.discard(session_id)
                    stop_no_flush.discard(session_id)
                    if should_flush:
                        await _flush_queue(session_id)

        task = asyncio.create_task(_runner(), name="internal-dispatch-async")
        if isinstance(session_id, str) and session_id:
            in_flight[session_id] = (task, context)
            bus.publish("turn.start", {"session_id": session_id})

    async def _flush_queue(session_id: str) -> bool:
        """Pop the messages queued while a turn ran, merge them into one
        (newline-joined) user message, and run it as the next turn — recursively
        draining the queue. Returns True if a turn was started, False on an empty
        queue / failure (so ``send-now`` can report idle instead of leaving the
        client stuck waiting). The merge is the user's choice (one dispatch, not
        N); the individual queued rows are deleted by ``pop_queued`` and replaced
        by the single merged user row."""
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
        # Rebuild routing from the CURRENT session row so a queued follow-up uses
        # the session's latest agent / model / effort — the user may have changed
        # it while the prior (now-finished) turn was running (Codex P2).
        try:
            context = _build_session_context(session_id)
        except Exception:
            logger.exception("queue flush: failed to build context for session=%s", session_id)
            return False
        await _run_turn(session_id, context, user_row.get("text") or "")
        return True

    @app.get("/internal/health")
    async def _health() -> dict[str, Any]:
        return {"ok": True, "service": "vibe-remote-internal", "version": 1}

    @app.get("/internal/turn-state/{session_id}")
    async def _turn_state(session_id: str) -> Any:
        """Whether a turn is currently running for the session. The fire-and-
        forget dispatch survives browser disconnects, so a freshly loaded /
        reconnected Chat page asks this to restore its working/Stop state for a
        turn that is still in flight (Codex P2)."""
        entry = in_flight.get(session_id)
        active = entry is not None and not entry[0].done()
        return {"ok": True, "session_id": session_id, "in_flight": active}

    @app.post("/internal/dispatch")
    async def _dispatch(request: Request) -> Any:
        payload = await _safe_json(request)
        try:
            text, context = _build_dispatch_payload(payload)
        except ValueError as err:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(err)})

        session_id = payload.get("session_id")

        # One streaming turn per session. If a turn is already in flight for
        # this session (a second browser tab, or a resend before the first
        # finishes), refuse the new one HERE — before creating a task or
        # touching ``in_flight`` — so we never overwrite the real turn's task
        # handle. Overwriting it would orphan the running turn: its sink keeps
        # streaming but ``/internal/cancel`` could no longer find the task to
        # interrupt, so the Stop button would silently no-op.
        if isinstance(session_id, str) and session_id:
            existing = in_flight.get(session_id)
            if existing is not None and not existing[0].done():
                async def _busy_stream():
                    yield _sse_event("turn.start", {"session_id": session_id})
                    yield _sse_event(
                        "turn.chunk",
                        {
                            "kind": "error",
                            "text": controller._t("error.streamTurnInProgress"),
                            "message_id": None,
                        },
                    )
                    yield _sse_event("turn.end", {"session_id": session_id})

                return StreamingResponse(
                    _busy_stream(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
                )

        # SSE chunked stream — the response body is fed by ``on_chunk``
        # callbacks that the dispatcher fires for every successful
        # ``emit_agent_message`` notify / result during the turn. The
        # turn coroutine and the producer-consumer queue live on the
        # same loop, so ordering is preserved.
        chunk_queue: asyncio.Queue[Optional[dict]] = asyncio.Queue()

        async def on_chunk(envelope: dict) -> None:
            await chunk_queue.put(envelope)

        async def _runner() -> None:
            try:
                await dispatch_turn(controller, context, text, on_chunk=on_chunk)
            except asyncio.CancelledError:
                # Surface a cancel envelope so the SSE consumer can
                # distinguish "user stopped me" from "agent finished".
                await chunk_queue.put({"kind": "cancelled", "text": ""})
                raise
            except Exception as err:
                logger.exception("internal dispatch failed for session=%s", session_id)
                await chunk_queue.put({"kind": "error", "text": str(err)})
            finally:
                # Sentinel signals end-of-stream to the consumer below.
                await chunk_queue.put(None)

        task = asyncio.create_task(_runner(), name="internal-dispatch")
        if isinstance(session_id, str) and session_id:
            in_flight[session_id] = (task, context)

        async def _stream():
            try:
                yield _sse_event("turn.start", {"session_id": session_id})
                while True:
                    envelope = await chunk_queue.get()
                    if envelope is None:
                        break
                    yield _sse_event("turn.chunk", envelope)
                yield _sse_event("turn.end", {"session_id": session_id})
            finally:
                if not task.done():
                    task.cancel()
                # Release the slot whether the task completed normally,
                # was cancelled by the UI, or the SSE consumer
                # disconnected mid-stream. ``pop`` is idempotent.
                if isinstance(session_id, str):
                    in_flight.pop(session_id, None)

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/internal/dispatch_async")
    async def _dispatch_async(request: Request) -> Any:
        """Fire-and-forget turn dispatch for the session/page-scoped stream.

        Unlike ``/internal/dispatch`` (which streams the reply back over the
        response for the legacy per-turn web stream), this starts the turn and
        returns ``202`` immediately. The reply — plus any notify/result —
        reaches the browser over the persistent ``message.new`` session stream
        instead, so the HTTP response isn't held open for the turn's duration
        and a closed browser tab can't cancel an in-flight turn. ``_run_turn``
        holds the turn open (keeping ``in_flight`` populated so Stop works),
        publishes the turn lifecycle, and flushes the send-while-busy queue when
        it settles.
        """
        payload = await _safe_json(request)
        try:
            text, context = _build_dispatch_payload(payload)
        except ValueError as err:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(err)})

        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            from storage import messages_service
            from storage.db import create_sqlite_engine

            existing = in_flight.get(session_id)
            busy = existing is not None and not existing[0].done()
            # Enqueue (rather than start a turn) when EITHER a turn is already
            # running OR a prior Stop left queued rows behind — in the latter case
            # the new message must run AFTER them, so it joins the queue instead of
            # jumping ahead (Codex P2). The in_flight check + the mark below have no
            # ``await`` between them, so the running turn can't end + flush in the
            # gap (single-threaded loop) — the atomic enqueue.
            engine = create_sqlite_engine()
            if busy:
                should_enqueue = True
            else:
                with engine.connect() as conn:
                    should_enqueue = bool(messages_service.list_queued(conn, session_id))
            if should_enqueue:
                user_message_id = payload.get("user_message_id")
                if isinstance(user_message_id, str) and user_message_id:
                    with engine.begin() as conn:
                        messages_service.promote_pending(conn, user_message_id, messages_service.QUEUED_TYPE)
                # Idle + pre-existing queue → no running turn to flush behind, so
                # drain the whole queue (this row included) now, in order.
                if not busy:
                    await _flush_queue(session_id)
                return JSONResponse(
                    status_code=202,
                    content={"ok": True, "queued": True, "session_id": session_id, "message_id": user_message_id},
                )

        await _run_turn(session_id if isinstance(session_id, str) else None, context, text)
        return JSONResponse(status_code=202, content={"ok": True, "session_id": session_id})

    @app.get("/internal/events")
    async def _events() -> Any:
        """Long-lived SSE feed of Controller-side inbox events.

        The UI server opens this once on startup and re-broadcasts each event
        to browsers via its own SSEBroker, so realtime inbox updates (a new
        agent ``result`` bumping a session to the top) work across the
        process boundary.
        """
        from core.inbox_events import bus

        sub_id, queue = bus.subscribe()

        async def _stream():
            try:
                yield ": connected\n\n"
                while True:
                    event_type, data = await queue.get()
                    yield _sse_event(event_type, data)
            finally:
                bus.unsubscribe(sub_id)

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/internal/cancel/{session_id}")
    async def _cancel(session_id: str) -> Any:
        # ``session_id`` is the dispatch key — matches the body field
        # the dispatch endpoint registered under. Using the session id
        # (rather than introducing a separate run_id contract) keeps
        # the public surface narrow and lets the UI ``Stop`` button
        # work with just the URL it already has.
        entry = in_flight.get(session_id)
        if entry is None:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "code": "not_in_flight", "session_id": session_id},
            )
        task, turn_context = entry
        if task.done():
            return {"ok": True, "session_id": session_id, "status": "already_finished"}
        # Interrupt the agent's backend turn through the SAME path the IM
        # ``/stop`` command uses, so the underlying agent run is actually
        # stopped (Claude interrupt / Codex turn-interrupt / OpenCode abort) —
        # not just this SSE proxy task. Without it the agent keeps running and
        # its late reply leaks into the next turn's stream. We pass the context
        # the turn STARTED under (captured at dispatch time), not one rebuilt
        # from the current session row, so the right backend is interrupted
        # even if the Chat header swapped the session's agent / model mid-turn.
        # Record the no-flush intent BEFORE awaiting the interrupt: if the
        # backend stop lets the turn settle normally during the await (no
        # CancelledError), _run_turn's finally would otherwise treat it as a
        # natural completion and flush the queue — but a plain Stop keeps the
        # queue (Codex P2).
        stop_no_flush.add(session_id)
        stopped = False
        try:
            stopped = bool(await controller.command_handler.handle_stop(turn_context))
        except Exception:
            logger.exception("internal cancel: backend stop failed for session=%s", session_id)
        if not stopped:
            # Stop refused — the turn keeps running, so it isn't being stopped;
            # drop the no-flush marker so a later natural completion flushes
            # normally.
            stop_no_flush.discard(session_id)
            # The backend couldn't interrupt this turn (no interruptible active
            # session). Don't cancel the waiter — that would fire a false
            # ``turn.end``, hide Stop, and let follow-up work start while the turn
            # is still producing output. Keep it cancellable; it ends naturally
            # when the backend finishes (Codex P2).
            return JSONResponse(
                status_code=409,
                content={"ok": False, "code": "stop_failed", "session_id": session_id},
            )
        task.cancel()
        return {"ok": True, "session_id": session_id, "status": "cancel_requested"}

    @app.post("/internal/send-now/{session_id}")
    async def _send_now(session_id: str) -> Any:
        """Run the session's send-while-busy queue immediately ("立即发送").

        If a turn is running, interrupt it (the user explicitly chose to cut in)
        and opt into ``flush_on_cancel`` so the queue runs as soon as that turn
        unwinds. If nothing is running (e.g. after a Stop left the queue intact),
        flush directly as a fresh turn. No-op when the queue is empty.
        """
        entry = in_flight.get(session_id)
        if entry is not None and not entry[0].done():
            _task, turn_context = entry
            # Record the flush intent BEFORE awaiting the interrupt: if the stop
            # lets the turn settle normally during the await, _run_turn's finally
            # consumes the flag and flushes (send-now WANTS the queue to run).
            # Adding it afterwards would either be too late (the finished turn
            # already discarded nothing) or leave a STALE flag that makes a later
            # plain Stop wrongly flush (Codex P2). On a refused/failed stop we
            # drop it again and leave the turn + queue untouched.
            flush_on_cancel.add(session_id)
            stopped = False
            try:
                stopped = bool(await controller.command_handler.handle_stop(turn_context))
            except Exception:
                logger.exception("internal send-now: backend stop failed for session=%s", session_id)
            if not stopped:
                flush_on_cancel.discard(session_id)
                return JSONResponse(
                    status_code=409,
                    content={"ok": False, "code": "stop_failed", "session_id": session_id},
                )
            _task.cancel()
            return {"ok": True, "session_id": session_id, "status": "interrupted"}
        # No running turn — flush the queue directly as a new turn (it rebuilds
        # the routing context from the current session row internally). Report
        # ``empty`` when there was nothing to flush (a stale queue item already
        # gone) so the client clears its optimistic working state instead of
        # waiting for a turn that never starts (Codex P2).
        flushed = await _flush_queue(session_id)
        return {"ok": True, "session_id": session_id, "status": "flushed" if flushed else "empty"}

    return app


async def serve(controller: "Controller", *, socket_path: Optional[Path] = None) -> None:
    """Run the internal server forever on the current event loop.

    Returns when the underlying uvicorn server exits (typically when the
    controller's loop is shut down). Each call binds a fresh socket
    file; pre-existing files at ``socket_path`` are removed first so
    restarts don't fail with "address already in use".

    Permissions: we tighten ``os.umask`` to ``0o077`` *before* uvicorn
    binds the socket so the file is created with mode ``0o700`` and is
    never readable / connectable by other local users — even briefly.
    A best-effort post-bind ``os.chmod`` then forces the final mode in
    case the platform's umask handling differs (some BSDs ignore umask
    for AF_UNIX bind). Without the umask wrap there is a TOCTOU window
    where the socket would be world-accessible between bind and chmod.
    """

    import uvicorn

    target = (socket_path or default_socket_path()).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        try:
            target.unlink()
        except OSError:
            logger.warning("could not unlink stale dispatch socket %s; bind may fail", target)

    app = create_app(controller)
    config = uvicorn.Config(
        app,
        uds=str(target),
        log_config=None,
        access_log=False,
        loop="asyncio",
        lifespan="off",
    )
    server = uvicorn.Server(config)

    async def _chmod_when_ready() -> None:
        # Defense-in-depth: re-assert ``0o600`` once the socket file
        # exists, in case the platform's umask handling differs from
        # what we set above. The loop polls because uvicorn binds the
        # socket synchronously during ``serve`` and we don't want to
        # race against it.
        for _ in range(40):
            await asyncio.sleep(0.025)
            if target.exists():
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    logger.warning("failed to chmod internal dispatch socket %s", target)
                return

    previous_umask = os.umask(0o077)
    chmod_task = asyncio.create_task(_chmod_when_ready(), name="internal-dispatch-chmod")
    try:
        await server.serve()
    finally:
        chmod_task.cancel()
        # Restore the previous umask so unrelated file writes from this
        # process aren't permanently affected by our hardening.
        os.umask(previous_umask)


def start(controller: "Controller", *, socket_path: Optional[Path] = None) -> asyncio.Task:
    """Schedule the internal server to run on the controller's loop.

    Called from ``Controller.run`` once the loop is alive. Returns the
    background ``asyncio.Task`` so the caller can keep a handle for
    cancellation on shutdown.
    """

    loop = asyncio.get_event_loop()
    task = loop.create_task(serve(controller, socket_path=socket_path), name="internal-dispatch-server")

    def _on_done(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.error("internal dispatch server exited with exception: %r", exc)

    task.add_done_callback(_on_done)
    return task


# --- Internals --------------------------------------------------------


async def _safe_json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        body = {}
    return body if isinstance(body, dict) else {}


def _build_dispatch_payload(payload: dict[str, Any]) -> tuple[str, MessageContext]:
    """Translate the JSON payload into a ``(text, MessageContext)`` pair.

    Raises ``ValueError`` with a caller-friendly message when the
    payload is missing required fields. The MessageContext defaults to
    ``platform="avibe"`` because the Web UI is the first / only caller;
    future CLI ``--sync`` callers will hand in their own platform.

    We also look up the workbench session's routing fields and copy
    them into ``platform_specific["agent_session_target"]`` /
    ``platform_specific["vibe_agent_name"]`` so ``MessageHandler``'s
    agent-selection branch picks up the Chat header's chosen agent /
    model / effort — matching the shape that scheduled tasks already
    feed in via ``core.scheduled_tasks`` so the handler stays one path.
    """

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is required")

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id is required")

    context = _build_session_context(
        session_id,
        user_id=payload.get("user_id"),
        channel_id=payload.get("channel_id"),
        platform=payload.get("platform"),
        thread_id=payload.get("thread_id"),
        message_id=payload.get("message_id"),
    )
    return text, context


def _build_session_context(
    session_id: str,
    *,
    user_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    platform: Optional[str] = None,
    thread_id: Optional[str] = None,
    message_id: Optional[str] = None,
) -> MessageContext:
    """Build the avibe ``MessageContext`` for a workbench session.

    Shared by the dispatch endpoint and the cancel endpoint so a stop reuses
    the exact same session-routing context (chosen agent / model / effort,
    native session id, workdir) the turn ran under — that's what lets cancel
    reuse the IM ``/stop`` path to interrupt the right backend session.
    Defaults to ``platform="avibe"``.
    """

    # ``agent_session_id`` is the agent_sessions PK; persist_agent_message reads
    # it to attribute avibe agent replies to the right session (IM stamps it at
    # session-resolve time). For avibe the dispatch session_id IS that PK.
    platform_specific: dict[str, Any] = {
        "workbench_session_id": session_id,
        "agent_session_id": session_id,
    }
    session_row = _lookup_session(session_id)
    if session_row is not None:
        target = {
            "id": session_row.get("id"),
            "agent_id": session_row.get("agent_id"),
            "agent_name": session_row.get("agent_name"),
            "agent_backend": session_row.get("agent_backend"),
            "agent_variant": session_row.get("agent_variant"),
            "model": session_row.get("model"),
            "reasoning_effort": session_row.get("reasoning_effort"),
            "native_session_id": session_row.get("native_session_id"),
            "workdir": session_row.get("workdir"),
        }
        platform_specific["agent_session_target"] = target
        if session_row.get("agent_name"):
            platform_specific["vibe_agent_name"] = session_row["agent_name"]

    return MessageContext(
        user_id=str(user_id or "workbench"),
        channel_id=str(channel_id or session_id),
        platform=platform or "avibe",
        thread_id=thread_id,
        message_id=message_id,
        platform_specific=platform_specific,
    )


def _lookup_session(session_id: str) -> Optional[dict[str, Any]]:
    """Load the workbench session row for routing metadata.

    Failures are swallowed and logged: the dispatch still proceeds with
    default routing rather than 5xx'ing the SSE stream. The session
    *not existing* is a real caller error but
    ``MessageHandler._handle_turn`` already produces a meaningful error
    in that case.
    """

    try:
        from core.services import sessions as sessions_service
        from storage.db import create_sqlite_engine

        engine = create_sqlite_engine()
        with engine.connect() as conn:
            return sessions_service.get_session(conn, session_id)
    except LookupError:
        return None
    except Exception:
        logger.exception("internal_server: failed to load session metadata for %s", session_id)
        return None


def _sse_event(event_type: str, data: Any) -> str:
    """Format one SSE chunk. Each chunk is a single ``event:``/``data:``
    pair separated by the spec-mandated blank line.
    """

    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
