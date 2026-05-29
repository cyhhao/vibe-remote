"""Tests for ``core.internal_server`` — the controller-side Unix socket
ASGI app that exposes ``POST /internal/dispatch`` (SSE chunked) for the
Web UI / future CLI ``--sync`` callers.

We exercise three layers:

1. The app's request/response shape via ``httpx.ASGITransport`` (no
   actual socket; locks the contract independent of uvicorn).
2. The dispatcher hook that streams ``turn_chunk_callback`` envelopes
   into the SSE response (regression for the C4 wiring in
   ``message_dispatcher._stream_chunk``).
3. The boot-time socket file lifecycle (default path + chmod).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import internal_server
from core.message_dispatcher import _stream_chunk
from core.services.dispatch import dispatch_turn
from modules.im import MessageContext


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _build_controller_double(handler=None):
    """A MagicMock controller whose ``message_handler.handle_user_message``
    can be patched to emit chunks via the real ``_stream_chunk`` hook.

    It carries a *real* turn-sink registry (not MagicMock auto-attrs) so
    ``dispatch_turn`` and ``_stream_chunk`` interoperate exactly as in
    production: dispatch_turn registers the sink, the handler's emits
    resolve it by session key, and a result emit releases the dispatch.
    """

    controller = MagicMock()
    controller.message_handler = MagicMock()
    controller.message_handler.handle_user_message = AsyncMock(side_effect=handler or (lambda ctx, text: None))

    sinks: dict = {}
    controller.active_turn_sinks = sinks
    controller._get_session_key = lambda ctx: f"{getattr(ctx, 'platform', None)}::{getattr(ctx, 'channel_id', None)}"

    def _register(session_key, *, on_chunk, done_event):
        sinks[session_key] = {"on_chunk": on_chunk, "done_event": done_event}

    controller.register_turn_sink = _register
    controller.pop_turn_sink = lambda session_key: sinks.pop(session_key, None)
    controller.get_turn_sink = lambda session_key: sinks.get(session_key)
    return controller


# ---------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------


def test_default_socket_path_lives_under_state_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    path = internal_server.default_socket_path()
    assert path.name == "dispatch.sock"
    assert tmp_path in path.parents


def test_create_app_exposes_minimal_endpoints():
    app = internal_server.create_app(_build_controller_double())
    routes = {(r.path, tuple(sorted(r.methods))) for r in app.routes if hasattr(r, "methods")}
    # Endpoints locked by the design doc §7.4 v1 row + the health probe.
    assert ("/internal/health", ("GET",)) in routes
    assert ("/internal/dispatch", ("POST",)) in routes
    assert ("/internal/cancel/{session_id}", ("POST",)) in routes


# ---------------------------------------------------------------------
# ASGI round-trips
# ---------------------------------------------------------------------


async def _health_round_trip():
    app = internal_server.create_app(_build_controller_double())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/internal/health")
    return resp


def test_health_endpoint():
    resp = asyncio.run(_health_round_trip())
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "service": "vibe-remote-internal", "version": 1}


async def _dispatch_round_trip(body: dict) -> httpx.Response:
    app = internal_server.create_app(_build_controller_double())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post("/internal/dispatch", json=body)


def test_dispatch_rejects_missing_text():
    resp = asyncio.run(_dispatch_round_trip({"session_id": "s1"}))
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["ok"] is False
    assert "text" in payload["error"]


def test_dispatch_rejects_missing_session_id():
    resp = asyncio.run(_dispatch_round_trip({"text": "hi"}))
    assert resp.status_code == 400
    assert "session_id" in resp.json()["error"]


def test_dispatch_streams_chunks_emitted_by_handler():
    """Round-trip: simulate the handler invoking the on_chunk callback
    that ``dispatch_turn`` stashed on ``context.platform_specific``.
    The endpoint must surface those envelopes as SSE ``turn.chunk``
    events between a single ``turn.start`` and ``turn.end``.
    """

    chunks = [
        {"text": "thinking", "message_id": "m_1", "kind": "notify"},
        {"text": "here is the answer", "message_id": "m_2", "kind": "result"},
    ]

    async def fake_handle_user_message(ctx, text):
        # Simulate the agent's background receiver emitting via the real
        # dispatcher hook: it resolves the sink dispatch_turn registered
        # (by session key) and forwards each envelope to the SSE stream.
        # The trailing result emit also releases dispatch_turn.
        for c in chunks:
            await _stream_chunk(
                controller, ctx, text=c["text"], message_id=c["message_id"], kind=c["kind"]
            )
        return chunks[-1]["message_id"]

    controller = _build_controller_double(handler=fake_handle_user_message)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async with client.stream(
                "POST", "/internal/dispatch", json={"session_id": "ses_test", "text": "hello"}
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith("text/event-stream")
                events: list[tuple[str, dict]] = []
                current_event: str | None = None
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and current_event is not None:
                        events.append((current_event, json.loads(line[5:].strip())))
                return events

    events = asyncio.run(_go())
    event_kinds = [name for name, _ in events]
    assert event_kinds[0] == "turn.start"
    assert event_kinds[-1] == "turn.end"
    chunk_events = [data for name, data in events if name == "turn.chunk"]
    assert chunk_events == chunks, "chunks must be forwarded in order without rewriting"


def test_dispatch_waits_for_async_result_after_handler_returns():
    """The crux of the streaming lifecycle fix: the agent backends are
    fire-and-forget — ``handle_user_message`` returns after *sending*, and
    the reply is emitted later by a background receiver task. ``dispatch_turn``
    must hold the SSE stream open until that async result emit (done), not
    close it the instant the handler returns. (Pre-fix, ``turn.end`` fired
    immediately and the late result missed the closed stream.)
    """

    emitted = asyncio.Event()

    async def fire_and_forget(ctx, text):
        # Schedule the result emit to happen *after* this returns, then
        # return immediately (the message has been "sent").
        async def _late_emit():
            await asyncio.sleep(0.05)
            await _stream_chunk(controller, ctx, text="late answer", message_id="m_late", kind="result")
            emitted.set()

        asyncio.create_task(_late_emit())
        return None

    controller = _build_controller_double(handler=fire_and_forget)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async with client.stream(
                "POST", "/internal/dispatch", json={"session_id": "ses_async", "text": "hi"}
            ) as resp:
                events: list[tuple[str, dict]] = []
                current = None
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        current = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and current is not None:
                        events.append((current, json.loads(line[5:].strip())))
                return events

    events = asyncio.run(_go())
    kinds = [name for name, _ in events]
    chunk_texts = [data.get("text") for name, data in events if name == "turn.chunk"]
    assert "late answer" in chunk_texts, "a result emitted after the handler returned must still stream"
    assert kinds[-1] == "turn.end"
    assert emitted.is_set()


def test_dispatch_forwards_session_routing_into_platform_specific(monkeypatch, tmp_path):
    """Regression for the Codex P1: ``/internal/dispatch`` must hand the
    workbench session's agent / model / effort to ``MessageHandler`` via
    ``platform_specific["agent_session_target"]`` + ``vibe_agent_name``
    so the Chat header's chosen agent is actually used instead of the
    controller's default routing.
    """

    from core.services import sessions as sessions_service
    from storage.db import create_sqlite_engine
    from storage.importer import ensure_sqlite_state
    from storage.settings_service import upsert_scope

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = upsert_scope(
            conn,
            platform="avibe",
            scope_type="project",
            native_id="proj_routing",
            now="2026-05-26T13:00:00Z",
        )
        session = sessions_service.create_session(
            conn,
            scope_id=scope_id,
            agent_backend="claude",
            agent_name="contract-bot",
            model="claude-sonnet-4-6",
            reasoning_effort="high",
        )
    session_id = session["id"]

    captured: dict = {}

    async def capture(ctx, text):
        captured["platform_specific"] = dict(ctx.platform_specific or {})
        # Release the streaming dispatch (simulate the turn completing) so it
        # doesn't wait out the safety timeout.
        sink = controller.get_turn_sink(controller._get_session_key(ctx))
        if sink:
            sink["done_event"].set()

    controller = _build_controller_double(handler=capture)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async with client.stream(
                "POST", "/internal/dispatch", json={"session_id": session_id, "text": "hi"}
            ) as resp:
                async for _ in resp.aiter_lines():
                    pass

    asyncio.run(_go())
    payload = captured["platform_specific"]
    assert payload.get("workbench_session_id") == session_id
    assert payload.get("vibe_agent_name") == "contract-bot"
    target = payload.get("agent_session_target") or {}
    assert target.get("agent_name") == "contract-bot"
    assert target.get("agent_backend") == "claude"
    assert target.get("model") == "claude-sonnet-4-6"
    assert target.get("reasoning_effort") == "high"


def test_cancel_returns_404_when_session_not_in_flight():
    app = internal_server.create_app(_build_controller_double())
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/internal/cancel/ses_unknown")

    resp = asyncio.run(_go())
    assert resp.status_code == 404
    body = resp.json()
    assert body["ok"] is False
    assert body["code"] == "not_in_flight"


def test_cancel_marks_in_flight_session_as_requested():
    """When a dispatch is in flight, ``cancel`` finds the task and asks
    asyncio to cancel it. The endpoint returns immediately — completion
    of the cancel is observed by the SSE consumer through a ``cancelled``
    chunk.
    """

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_handle_user_message(ctx, text):
        await _stream_chunk(controller, ctx, text="starting", message_id=None, kind="notify")
        started.set()
        try:
            # Sleep long enough that the test's cancel arrives mid-flight.
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return None

    controller = _build_controller_double(handler=long_handle_user_message)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Kick off the dispatch in the background; the request stays
            # open while the handler is sleeping.
            stream_task = asyncio.create_task(
                _drain_stream(client, {"session_id": "ses_long", "text": "go"})
            )
            await asyncio.wait_for(started.wait(), timeout=3)
            cancel_resp = await client.post("/internal/cancel/ses_long")
            events = await asyncio.wait_for(stream_task, timeout=3)
        return cancel_resp, events

    async def _drain_stream(client, body):
        async with client.stream("POST", "/internal/dispatch", json=body) as resp:
            assert resp.status_code == 200
            collected: list[tuple[str, dict]] = []
            current = None
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    current = line.split(":", 1)[1].strip()
                elif line.startswith("data:") and current is not None:
                    collected.append((current, json.loads(line[5:].strip())))
            return collected

    cancel_resp, events = asyncio.run(_go())
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancel_requested"
    assert cancelled.is_set(), "handler must observe CancelledError"
    # The SSE consumer sees the cancelled chunk before turn.end.
    chunk_kinds = [data.get("kind") for name, data in events if name == "turn.chunk"]
    assert "cancelled" in chunk_kinds


def test_dispatch_emits_error_chunk_on_handler_exception():
    async def boom(ctx, text):
        raise RuntimeError("kaboom")

    controller = _build_controller_double(handler=boom)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async with client.stream(
                "POST", "/internal/dispatch", json={"session_id": "s", "text": "go"}
            ) as resp:
                return "".join([line async for line in resp.aiter_lines()])

    body = asyncio.run(_go())
    assert "event: turn.chunk" in body
    assert '"kind": "error"' in body
    assert "event: turn.end" in body


# ---------------------------------------------------------------------
# Dispatcher hook contract
# ---------------------------------------------------------------------


def test_dispatch_turn_registers_sink_for_dispatcher_hook():
    """Locks the contract between ``dispatch_turn`` and the dispatcher's
    ``_stream_chunk`` helper: the streaming ``on_chunk`` is registered as a
    per-session turn sink (resolvable by session key while the turn runs)
    and cleaned up afterward — not stashed on the per-turn context.
    """

    async def on_chunk(envelope):
        pass

    seen: dict = {}

    async def capture(ctx, text):
        sink = controller.get_turn_sink(controller._get_session_key(ctx))
        seen["on_chunk"] = sink["on_chunk"] if sink else None
        # Release the dispatch the way a real result emit would.
        if sink:
            sink["done_event"].set()

    controller = _build_controller_double(handler=capture)
    ctx = MessageContext(user_id="U", channel_id="C", platform="avibe")
    asyncio.run(dispatch_turn(controller, ctx, "ping", on_chunk=on_chunk))
    assert seen["on_chunk"] is on_chunk
    assert controller.get_turn_sink("avibe::C") is None, "sink cleaned up after the turn"
