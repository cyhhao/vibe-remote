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

    def _register(session_key, *, on_chunk, done_event, turn_token=None):
        sinks[session_key] = {"on_chunk": on_chunk, "done_event": done_event, "turn_token": turn_token}

    controller.register_turn_sink = _register

    def _pop(session_key, done_event=None):
        s = sinks.get(session_key)
        if s is None:
            return
        if done_event is not None and s.get("done_event") is not done_event:
            return
        sinks.pop(session_key, None)

    controller.pop_turn_sink = _pop
    controller.get_turn_sink = lambda session_key: sinks.get(session_key)

    def _mark_turn_complete(ctx):
        sink = sinks.get(controller._get_session_key(ctx))
        if sink and sink.get("done_event") is not None:
            sink["done_event"].set()

    controller.mark_turn_complete = _mark_turn_complete

    # Cancel reuses the IM /stop path to interrupt the backend turn.
    controller.command_handler = MagicMock()
    controller.command_handler.handle_stop = AsyncMock(return_value=True)

    # ``_t`` returns the key verbatim so refusal chunks stay JSON-serializable
    # (a bare MagicMock would blow up ``json.dumps`` in ``_sse_event``).
    controller._t = lambda key, **kwargs: key
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
    assert ("/internal/dispatch_async", ("POST",)) in routes
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


def test_dispatch_closes_when_turn_completes_without_result():
    """P1: a turn that finishes WITHOUT emitting a result (missing/disabled
    backend, dedup, error, ...) must close the stream promptly via
    ``mark_turn_complete`` rather than hang until the 600s safety timeout.
    ``MessageHandler._handle_turn`` signals this on every no-dispatch exit;
    here we drive it directly.
    """

    async def no_result_handler(ctx, text):
        await _stream_chunk(controller, ctx, text="working", message_id=None, kind="notify")
        # Turn finished without a result emit — release the SSE waiter.
        controller.mark_turn_complete(ctx)
        return None

    controller = _build_controller_double(handler=no_result_handler)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            async with client.stream(
                "POST", "/internal/dispatch", json={"session_id": "ses_nr", "text": "hi"}
            ) as resp:
                events: list[tuple[str, dict]] = []
                current = None
                async for line in resp.aiter_lines():
                    if line.startswith("event:"):
                        current = line.split(":", 1)[1].strip()
                    elif line.startswith("data:") and current is not None:
                        events.append((current, json.loads(line[5:].strip())))
                return events

    # Bounded so a regression (no-result hang) fails fast instead of 600s.
    events = asyncio.run(asyncio.wait_for(_go(), timeout=10))
    kinds = [name for name, _ in events]
    assert kinds[-1] == "turn.end"
    assert any(d.get("text") == "working" for n, d in events if n == "turn.chunk")


def test_register_turn_sink_ignores_duplicate_and_pop_is_identity_guarded():
    """Streaming turns are serialized per session (dispatch_turn rejects a
    concurrent one). As defense in depth, register_turn_sink must NOT clobber
    an in-flight sink, and pop_turn_sink must only remove the sink whose
    done_event matches the caller's — so no stale turn can satisfy or evict
    another turn's sink."""
    import types

    from core.controller import Controller

    fake = types.SimpleNamespace(active_turn_sinks={})
    first = asyncio.Event()
    Controller.register_turn_sink(fake, "avibe::s", on_chunk=AsyncMock(), done_event=first)
    second = asyncio.Event()
    Controller.register_turn_sink(fake, "avibe::s", on_chunk=AsyncMock(), done_event=second)

    # The in-flight sink is kept; the duplicate is dropped and NOT released.
    assert fake.active_turn_sinks["avibe::s"]["done_event"] is first
    assert not first.is_set()

    # pop is identity-guarded: a non-matching done_event is a no-op.
    Controller.pop_turn_sink(fake, "avibe::s", second)
    assert "avibe::s" in fake.active_turn_sinks
    Controller.pop_turn_sink(fake, "avibe::s", first)
    assert "avibe::s" not in fake.active_turn_sinks


def test_dispatch_rejects_concurrent_same_session_turn():
    """dispatch_turn serializes per session: when a streaming turn is already
    in flight (a sink is registered), a second streaming dispatch is refused
    with a terminal error chunk and never starts a competing agent turn —
    so two streams can't race over one session and cross-feed."""
    chunks: list[dict] = []

    async def on_chunk(env):
        chunks.append(env)

    handler_calls: list = []

    async def handler(ctx, text):
        handler_calls.append(text)

    controller = _build_controller_double(handler=handler)
    controller._t = lambda key, **kw: f"i18n:{key}"
    ctx = MessageContext(user_id="U", channel_id="C", platform="avibe")
    # Simulate a streaming turn already in flight for this session.
    controller.register_turn_sink(
        controller._get_session_key(ctx), on_chunk=AsyncMock(), done_event=asyncio.Event()
    )

    asyncio.run(dispatch_turn(controller, ctx, "second", on_chunk=on_chunk))

    assert handler_calls == [], "a concurrent turn must not start the agent"
    assert any(c.get("kind") == "error" for c in chunks), "a terminal error chunk must be emitted"


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


def test_dispatch_async_starts_turn_and_returns_202(monkeypatch, tmp_path):
    """The fire-and-forget path starts the turn and returns 202 immediately.
    It still holds the turn open (via a no-op on_chunk) so ``in_flight`` is set
    for the turn's lifetime, then released when the turn completes — the reply
    itself reaches the browser over ``message.new``, not this response.

    It also publishes the session-level ``turn.start`` / ``turn.end`` lifecycle
    on the inbox bus (the browser's working-indicator signal)."""
    from core import inbox_events
    from storage.importer import ensure_sqlite_state

    # dispatch_async reads the queue (to preserve order after a Stop), so it needs
    # an initialized state DB even on the empty-queue happy path.
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()

    started = asyncio.Event()

    async def handler(ctx, text):
        started.set()
        # Release the held turn the way a real result emit would.
        controller.mark_turn_complete(ctx)
        return None

    controller = _build_controller_double(handler=handler)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    async def _go():
        sub_id, queue = inbox_events.bus.subscribe()
        events: list[str] = []
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.post("/internal/dispatch_async", json={"session_id": "ses_a", "text": "hi"})
            await asyncio.wait_for(started.wait(), timeout=3)
            for _ in range(100):
                if "ses_a" not in app.state.in_flight_dispatches:
                    break
                await asyncio.sleep(0.02)
            # Drain the bus: turn.start (at accept) + turn.end (at settle).
            for _ in range(2):
                try:
                    evt, _data = await asyncio.wait_for(queue.get(), timeout=1.0)
                    events.append(evt)
                except asyncio.TimeoutError:
                    break
        finally:
            inbox_events.bus.unsubscribe(sub_id)
        return resp, events

    resp, events = asyncio.run(_go())
    assert resp.status_code == 202
    assert resp.json()["ok"] is True
    controller.message_handler.handle_user_message.assert_awaited()
    assert "ses_a" not in app.state.in_flight_dispatches, "slot released after the turn"
    assert events == ["turn.start", "turn.end"], "publishes session turn lifecycle on the bus"


def test_dispatch_async_enqueues_during_busy_turn(monkeypatch, tmp_path):
    """A dispatch for a session that already has a turn in flight ENQUEUES
    (send-while-busy) instead of refusing: it atomically re-types the
    pre-persisted user row as queued and returns 202 {queued}, and never starts
    a competing agent turn. The row flushes when the running turn ends."""
    from core.services import sessions as sessions_service
    from storage import messages_service
    from storage.db import create_sqlite_engine
    from storage.importer import ensure_sqlite_state
    from storage.settings_service import upsert_scope

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = upsert_scope(
            conn, platform="avibe", scope_type="project", native_id="proj_enq", now="2026-05-31T00:00:00Z"
        )
        session = sessions_service.create_session(
            conn, scope_id=scope_id, agent_backend="claude", agent_name="worker"
        )
        # The UI reserves the user row as 'pending' before dispatching; the
        # controller promotes it to 'queued' when it finds a turn in flight.
        user_row = messages_service.append(
            conn, scope_id=scope_id, session_id=session["id"], platform="avibe", author="user",
            source="user", message_type=messages_service.PENDING_TYPE, text="while busy",
        )
    session_id = session["id"]

    controller = _build_controller_double()
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async def _busy():
            await asyncio.sleep(60)

        task = asyncio.create_task(_busy())
        app.state.in_flight_dispatches[session_id] = (
            task,
            MessageContext(user_id="U", channel_id="C", platform="avibe"),
        )
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.post(
                    "/internal/dispatch_async",
                    json={"session_id": session_id, "text": "while busy", "user_message_id": user_row["id"]},
                )
        finally:
            task.cancel()
        return resp

    resp = asyncio.run(_go())
    assert resp.status_code == 202
    assert resp.json()["queued"] is True
    controller.message_handler.handle_user_message.assert_not_awaited()
    with engine.connect() as conn:
        # The row was atomically re-typed to queued (now out of the transcript).
        assert [q["text"] for q in messages_service.list_queued(conn, session_id)] == ["while busy"]
        transcript = messages_service.list_session_messages(conn, session_id=session_id, types=("user",))
    assert transcript["messages"] == []


def test_async_dispatch_flushes_queue_on_turn_end(monkeypatch, tmp_path):
    """When a turn ends, messages queued (send-while-busy) during it are popped,
    merged (newline-joined) into ONE user row, and run as the next turn —
    draining the queue. Exercises the controller-side flush wiring end to end."""
    from core.services import sessions as sessions_service
    from storage import messages_service
    from storage.db import create_sqlite_engine
    from storage.importer import ensure_sqlite_state
    from storage.settings_service import upsert_scope

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = upsert_scope(
            conn, platform="avibe", scope_type="project", native_id="proj_flush", now="2026-05-31T00:00:00Z"
        )
        session = sessions_service.create_session(
            conn, scope_id=scope_id, agent_backend="claude", agent_name="worker"
        )
    session_id = session["id"]

    seen_texts: list[str] = []

    async def handler(ctx, text):
        seen_texts.append(text)
        # Simulate the user queueing two messages WHILE the first turn runs (the
        # real flow — queued rows only exist during an active turn).
        if text == "first turn":
            with engine.begin() as conn:
                messages_service.enqueue_queued(conn, scope_id=scope_id, session_id=session_id, text="q1")
                messages_service.enqueue_queued(conn, scope_id=scope_id, session_id=session_id, text="q2")
        controller.mark_turn_complete(ctx)  # release each turn immediately
        return None

    controller = _build_controller_double(handler=handler)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.post("/internal/dispatch_async", json={"session_id": session_id, "text": "first turn"})
        # Wait for the first turn AND the flush turn to both drain the queue.
        for _ in range(200):
            if len(seen_texts) >= 2 and session_id not in app.state.in_flight_dispatches:
                break
            await asyncio.sleep(0.02)

    asyncio.run(_go())
    # First the user's turn, then ONE merged flush turn for the two queued msgs.
    assert seen_texts == ["first turn", "q1\nq2"]
    with engine.connect() as conn:
        assert messages_service.list_queued(conn, session_id) == []
        transcript = messages_service.list_session_messages(conn, session_id=session_id, types=("user",))
    assert [m["text"] for m in transcript["messages"]] == ["q1\nq2"], "the flush persisted one merged user row"


def test_cancel_does_not_flush_queue(monkeypatch, tmp_path):
    """A user Stop interrupts the turn but must NOT flush the queue — the user
    asked to keep queued messages on stop ('不清空队列'). The queued rows survive
    the cancellation; only a natural turn end (or send-now) runs them."""
    from core.services import sessions as sessions_service
    from storage import messages_service
    from storage.db import create_sqlite_engine
    from storage.importer import ensure_sqlite_state
    from storage.settings_service import upsert_scope

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = upsert_scope(
            conn, platform="avibe", scope_type="project", native_id="proj_noflush", now="2026-05-31T00:00:00Z"
        )
        session = sessions_service.create_session(
            conn, scope_id=scope_id, agent_backend="claude", agent_name="worker"
        )
    session_id = session["id"]

    started = asyncio.Event()

    async def long_handler(ctx, text):
        started.set()
        await asyncio.sleep(5)  # held until the test cancels it
        return None

    controller = _build_controller_double(handler=long_handler)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            await client.post("/internal/dispatch_async", json={"session_id": session_id, "text": "first"})
            await asyncio.wait_for(started.wait(), timeout=3)
            # Queue a message while the turn runs, then Stop.
            with engine.begin() as conn:
                messages_service.enqueue_queued(conn, scope_id=scope_id, session_id=session_id, text="q1")
            await client.post(f"/internal/cancel/{session_id}")
            for _ in range(200):
                if session_id not in app.state.in_flight_dispatches:
                    break
                await asyncio.sleep(0.02)

    asyncio.run(_go())
    with engine.connect() as conn:
        queued = messages_service.list_queued(conn, session_id)
        transcript = messages_service.list_session_messages(conn, session_id=session_id, types=("user",))
    assert [q["text"] for q in queued] == ["q1"], "Stop must keep the queue intact"
    assert transcript["messages"] == [], "Stop must not flush the queue into a turn"


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


def test_cancel_marks_in_flight_session_as_requested(monkeypatch, tmp_path):
    """When a dispatch is in flight, ``cancel`` interrupts the backend turn
    through the shared ``/stop`` path AND cancels the SSE proxy task. The
    endpoint returns immediately; the SSE consumer observes a ``cancelled``
    chunk.
    """

    # Hermetic SQLite so cancel's session-context lookup doesn't touch the
    # real home (the test session isn't persisted; lookup resolves to None).
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    from storage.importer import ensure_sqlite_state

    ensure_sqlite_state()

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


def test_concurrent_dispatch_refused_preserves_cancellable_slot(monkeypatch, tmp_path):
    """A second dispatch for a session that already has a turn in flight is
    refused with a terminal error chunk WITHOUT evicting the first turn's task
    handle — so ``/internal/cancel`` can still interrupt the original
    long-running turn (regression: the refusal task used to overwrite the slot
    and orphan the real turn, leaving the Stop button a silent no-op).
    """

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    from storage.importer import ensure_sqlite_state

    ensure_sqlite_state()

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def long_handle(ctx, text):
        await _stream_chunk(controller, ctx, text="starting", message_id=None, kind="notify")
        started.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return None

    controller = _build_controller_double(handler=long_handle)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    async def _drain(client, body):
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

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            first = asyncio.create_task(_drain(client, {"session_id": "ses_x", "text": "one"}))
            await asyncio.wait_for(started.wait(), timeout=3)
            # Second concurrent send for the SAME session: refused immediately.
            second = await asyncio.wait_for(_drain(client, {"session_id": "ses_x", "text": "two"}), timeout=3)
            # The original turn must still be cancellable.
            cancel_resp = await client.post("/internal/cancel/ses_x")
            first_events = await asyncio.wait_for(first, timeout=3)
            return second, cancel_resp, first_events

    second_events, cancel_resp, _first_events = asyncio.run(_go())
    # The refused second dispatch got a terminal error chunk, not a real turn.
    second_kinds = [data.get("kind") for name, data in second_events if name == "turn.chunk"]
    assert "error" in second_kinds
    # The first turn's slot survived the refusal: cancel finds it, interrupts
    # the backend via the shared /stop path, and the handler is cancelled.
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancel_requested"
    assert cancelled.is_set(), "the original turn must still be cancellable"
    controller.command_handler.handle_stop.assert_awaited()
    # Cancel also interrupted the backend turn via the shared /stop path.
    controller.command_handler.handle_stop.assert_awaited()


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
