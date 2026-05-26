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

    # In-flight ``dispatch_turn`` tasks indexed by session id. The
    # cancel endpoint looks the task up here so the UI can stop a
    # runaway turn without waiting for the agent to settle. Tasks are
    # registered when the SSE response starts and removed in its
    # ``finally`` so cancelled / completed sessions don't leak slots.
    in_flight: dict[str, asyncio.Task] = {}
    app.state.in_flight_dispatches = in_flight

    @app.get("/internal/health")
    async def _health() -> dict[str, Any]:
        return {"ok": True, "service": "vibe-remote-internal", "version": 1}

    @app.post("/internal/dispatch")
    async def _dispatch(request: Request) -> Any:
        payload = await _safe_json(request)
        try:
            text, context = _build_dispatch_payload(payload)
        except ValueError as err:
            return JSONResponse(status_code=400, content={"ok": False, "error": str(err)})

        session_id = payload.get("session_id")

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
            in_flight[session_id] = task

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

    @app.post("/internal/cancel/{session_id}")
    async def _cancel(session_id: str) -> Any:
        # ``session_id`` is the dispatch key — matches the body field
        # the dispatch endpoint registered under. Using the session id
        # (rather than introducing a separate run_id contract) keeps
        # the public surface narrow and lets the UI ``Stop`` button
        # work with just the URL it already has.
        task = in_flight.get(session_id)
        if task is None:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "code": "not_in_flight", "session_id": session_id},
            )
        if task.done():
            return {"ok": True, "session_id": session_id, "status": "already_finished"}
        task.cancel()
        return {"ok": True, "session_id": session_id, "status": "cancel_requested"}

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
    """

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("text is required")

    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id is required")

    user_id = payload.get("user_id") or "workbench"
    channel_id = payload.get("channel_id") or session_id

    context = MessageContext(
        user_id=str(user_id),
        channel_id=str(channel_id),
        platform=payload.get("platform") or "avibe",
        thread_id=payload.get("thread_id"),
        message_id=payload.get("message_id"),
        platform_specific={"workbench_session_id": session_id},
    )
    return text, context


def _sse_event(event_type: str, data: Any) -> str:
    """Format one SSE chunk. Each chunk is a single ``event:``/``data:``
    pair separated by the spec-mandated blank line.
    """

    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"
