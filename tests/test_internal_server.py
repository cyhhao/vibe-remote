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
from core.services.dispatch import dispatch_turn
from modules.im import MessageContext


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _build_controller_double(handler=None):
    """A MagicMock controller whose ``message_handler.handle_user_message``
    can be patched to invoke the on_chunk callback the dispatcher pulled
    out of ``context.platform_specific``.
    """

    controller = MagicMock()
    controller.message_handler = MagicMock()
    controller.message_handler.handle_user_message = AsyncMock(side_effect=handler or (lambda ctx, text: None))
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
    assert ("/internal/cancel/{run_id}", ("POST",)) in routes


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
        callback = (ctx.platform_specific or {}).get("turn_chunk_callback")
        assert callback is not None, "dispatch_turn should have stashed the callback"
        for c in chunks:
            await callback(c)
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


def test_dispatch_turn_stashes_on_chunk_for_dispatcher_hook():
    """Locks the contract between ``dispatch_turn`` and the dispatcher's
    ``_stream_chunk`` helper: ``on_chunk`` lands on
    ``context.platform_specific["turn_chunk_callback"]`` so the
    dispatcher can pick it up later when ``emit_agent_message`` runs.
    """

    async def on_chunk(envelope):
        pass

    seen_callbacks: list = []

    async def capture_callback(ctx, text):
        seen_callbacks.append((ctx.platform_specific or {}).get("turn_chunk_callback"))

    controller = _build_controller_double(handler=capture_callback)
    ctx = MessageContext(user_id="U", channel_id="C", platform="avibe")
    asyncio.run(dispatch_turn(controller, ctx, "ping", on_chunk=on_chunk))
    assert seen_callbacks == [on_chunk]
