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
from types import SimpleNamespace
from typing import Any, Optional, TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from config import paths
from core.services.dispatch import SOURCE_HUMAN, SOURCE_SCHEDULED, dispatch_turn
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
    # Per-session turn state is owned by ``SessionTurnManager`` (FSM, Phase 1b),
    # published as ``controller.session_turns``. The containers bound below are the
    # SAME objects the closures and ``controller.session_turn_gate`` use, so
    # introducing the owner here is behavior-preserving; later commits move the
    # lifecycle logic onto the manager and these names become method calls.
    from core.session_turns import SessionTurnManager

    manager = SessionTurnManager(controller, build_context=_build_session_context)
    controller.session_turns = manager

    in_flight = manager.in_flight
    app.state.in_flight_dispatches = in_flight

    # Sessions whose current turn should flush its send-while-busy queue EVEN
    # though it's ending via cancellation. A plain Stop cancels without flushing
    # (the user asked to keep the queue — "不清空队列"); ``send-now`` cancels the
    # running turn but sets this so the queue runs immediately afterwards.
    flush_on_cancel = manager.flush_on_cancel
    app.state.flush_on_cancel = flush_on_cancel

    # Sessions whose current turn is being stopped by a plain Stop and must NOT
    # flush, even if the backend interrupt lets the turn settle NORMALLY (no
    # CancelledError) during the awaited stop — a Stop keeps the queue ("不清空").
    # Recorded before awaiting the interrupt so the race is covered.
    stop_no_flush = manager.stop_no_flush

    async def _flush_queue(session_id: str) -> bool:
        """Thin delegation to ``SessionTurnManager.flush_queue`` (FSM, Phase 1b):
        pop + merge the send-while-busy queue and run it as the next turn. Returns
        True if a turn was started, False on an empty queue / failure."""
        return await manager.flush_queue(session_id)

    async def _submit_scheduled_turn(session_id: str, context: MessageContext, text: str) -> None:
        """Run a scheduled / watch turn through the SAME unified ``manager.submit``
        the interactive Chat path uses, so a scheduled run can never preempt an
        active Chat turn and gets the full turn lifecycle (in_flight + turn.start /
        turn.end + Stop) the Chat page renders (Codex P2). Unlike Chat there is no
        pre-persisted ``pending`` row to promote, so the enqueue callback ``append``s
        a fresh ``queued`` row attributed to the harness.
        """
        if not session_id:
            await manager.submit(None, context, text, source=SOURCE_SCHEDULED)
            return

        def _enqueue() -> None:
            from core.message_mirror import _scope_id_for_session
            from storage import messages_service
            from storage.db import create_sqlite_engine

            # ``pop_queued`` / ``flush_queue`` key off (session_id, type=queued) +
            # scope_id + text only, so the harness attribution (author/source) is safe
            # to carry — it just records who triggered the queued run.
            engine = create_sqlite_engine()
            with engine.begin() as conn:
                scope_id = _scope_id_for_session(conn, session_id)
                if scope_id is not None:
                    messages_service.append(
                        conn,
                        scope_id=scope_id,
                        session_id=session_id,
                        platform="avibe",
                        author="harness",
                        source="harness",
                        message_type=messages_service.QUEUED_TYPE,
                        text=text,
                    )

        await manager.submit(session_id, context, text, source=SOURCE_SCHEDULED, enqueue=_enqueue)

    @app.get("/internal/health")
    async def _health() -> dict[str, Any]:
        return {"ok": True, "service": "vibe-remote-internal", "version": 1}

    @app.get("/internal/turn-state/{session_id}")
    async def _turn_state(session_id: str) -> Any:
        """HTTP adapter: whether a turn is running, delegated to the turn owner
        (FSM, Phase 1b). A reconnected Chat page asks this to restore working/Stop."""
        return manager.turn_state(session_id)

    @app.post("/internal/dispatch")
    async def _dispatch(request: Request) -> Any:
        """Streaming turn dispatch: runs a turn and proxies its notify/result
        chunks back over an SSE response. The web **Chat** page no longer uses
        this (it's fire-and-forget via ``/internal/dispatch_async`` +
        ``message.new``); this backs the **Show-page** dispatch flow, where
        ``ui_server._run_show_event_dispatch`` re-publishes each event as
        ``show.dispatch`` for the open Show page."""
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
            saw_cancel = False
            reached_end = False
            try:
                yield _sse_event("turn.start", {"session_id": session_id})
                while True:
                    envelope = await chunk_queue.get()
                    if envelope is None:
                        reached_end = True
                        break
                    if envelope.get("kind") == "cancelled":
                        saw_cancel = True
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
                    # This endpoint shares ``in_flight`` with the session, so a Chat
                    # send during a Show-page dispatch enqueues behind it. Drain that
                    # queue on NATURAL completion (not a Stop / consumer disconnect,
                    # mirroring _run_turn's no-flush-on-cancel rule) so the queued
                    # Chat message isn't stranded until manual intervention (Codex P2).
                    if reached_end and not saw_cancel:
                        await _flush_queue(session_id)

        return StreamingResponse(
            _stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    @app.post("/internal/dispatch_async")
    async def _dispatch_async(request: Request) -> Any:
        """Fire-and-forget turn dispatch for the session/page-scoped stream.

        Starts the turn and returns ``202`` immediately. The reply — plus any
        notify/result — reaches the browser over the persistent ``message.new``
        session stream, so the HTTP response isn't held open for the turn's
        duration and a closed browser tab can't cancel an in-flight turn.
        ``_run_turn`` holds the turn open (keeping ``in_flight`` populated so
        Stop works), publishes the turn lifecycle, and flushes the
        send-while-busy queue when it settles.
        """
        payload = await _safe_json(request)
        try:
            text, context = _build_dispatch_payload(payload)
        except ValueError as err:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(err)})

        session_id = payload.get("session_id")
        sid = session_id if isinstance(session_id, str) and session_id else None
        user_message_id = payload.get("user_message_id")

        def _enqueue() -> None:
            # Chat already persisted the user's message as a ``pending`` row; promote
            # it to ``queued`` so it drains via the queue after the active turn.
            if isinstance(user_message_id, str) and user_message_id:
                from storage import messages_service
                from storage.db import create_sqlite_engine

                engine = create_sqlite_engine()
                with engine.begin() as conn:
                    messages_service.promote_pending(conn, user_message_id, messages_service.QUEUED_TYPE)

        outcome = await manager.submit(sid, context, text, enqueue=_enqueue)
        if outcome == "enqueued":
            return JSONResponse(
                status_code=202,
                content={"ok": True, "queued": True, "session_id": session_id, "message_id": user_message_id},
            )
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
                # A REAL ``connected`` event (not a ``:`` comment, which the
                # internal_client parser swallows) so it flows bridge → broker →
                # browser. The UI sidebar refetches on this, which reconciles
                # agent-status dots after a CONTROLLER restart while the UI server
                # + browser SSE stay up: only this bridge reconnects, so the
                # browser's own ``connected`` never fires and the crash-recovery
                # ``running → idle`` reset (broadcast to no subscriber) would
                # otherwise be invisible until a manual reload (Codex P2).
                yield _sse_event("connected", {})
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
        """HTTP adapter: delegate Stop to the turn owner (FSM, Phase 1b) and map its
        result ``code`` to a status — ``not_in_flight`` -> 404, ``stop_failed`` ->
        409. ``session_id`` is the dispatch key the turn registered under, so the UI
        Stop button works with just the URL it already has."""
        result = await manager.cancel(session_id)
        code = result.get("code")
        if code == "not_in_flight":
            return JSONResponse(status_code=404, content=result)
        if code == "stop_failed":
            return JSONResponse(status_code=409, content=result)
        return result

    @app.post("/internal/send-now/{session_id}")
    async def _send_now(session_id: str) -> Any:
        """HTTP adapter: delegate "立即发送" (run the send-while-busy queue now) to
        the turn owner (FSM, Phase 1b); ``stop_failed`` -> 409."""
        result = await manager.send_now(session_id)
        if result.get("code") == "stop_failed":
            return JSONResponse(status_code=409, content=result)
        return result

    # Expose the per-session turn gate to in-process callers (the scheduler)
    # WITHOUT going through the HTTP surface: ``ScheduledTaskService`` runs on the
    # same loop and routes avibe scheduled / watch turns through
    # ``submit_scheduled`` so they share the Chat path's queueing + lifecycle.
    # ``in_flight`` is the SAME dict object as ``app.state.in_flight_dispatches``
    # (the cancel endpoint, turn-state, and the tests all read it), so a scheduled
    # run registered by ``_run_turn`` is Stoppable through ``/internal/cancel``.
    controller.session_turn_gate = SimpleNamespace(
        submit_scheduled=_submit_scheduled_turn,
        in_flight=in_flight,
    )

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
            # Carry the stored anchor so SessionHandler.get_base_session_id reuses it
            # instead of computing ``avibe_<id>`` — otherwise, after a restart, new
            # dispatches look up the native-session map under the wrong anchor and
            # start a fresh backend thread for the same Chat session (Codex P2).
            "session_anchor": session_row.get("session_anchor"),
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
