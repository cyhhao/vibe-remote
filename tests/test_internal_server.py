"""Tests for ``core.internal_server`` — the controller-side Unix socket
ASGI app that exposes ``POST /internal/dispatch_async`` (fire-and-forget turn
dispatch) plus the turn-control surface (cancel / send-now / turn-state) for
the Web UI / CLI callers.

We exercise three layers:

1. The app's request/response shape via ``httpx.ASGITransport`` (no
   actual socket; locks the contract independent of uvicorn).
2. The fire-and-forget dispatch lifecycle: the turn is held open (in_flight)
   and its ``turn.start`` / ``turn.end`` published on the bus, the reply
   itself arriving over ``message.new`` rather than the response.
3. The boot-time socket file lifecycle (default path + chmod).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import internal_server
from core.services.dispatch import SOURCE_HUMAN, SOURCE_SCHEDULED, dispatch_turn
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
    # Endpoints locked by the design doc §7.4 v1 row + the health probe. Both
    # dispatch shapes exist: ``/internal/dispatch_async`` (fire-and-forget, the
    # Chat page) and the streaming ``/internal/dispatch`` (the Show-page dispatch
    # flow re-publishes its SSE chunks as ``show.dispatch``).
    assert ("/internal/health", ("GET",)) in routes
    assert ("/internal/dispatch_async", ("POST",)) in routes
    assert ("/internal/cancel/{session_id}", ("POST",)) in routes
    assert ("/internal/dispatch", ("POST",)) in routes


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
        return await client.post("/internal/dispatch_async", json=body)


def test_dispatch_rejects_missing_text():
    # Payload validation runs before any turn/queue work, so a bad request 400s
    # the same way on the fire-and-forget endpoint.
    resp = asyncio.run(_dispatch_round_trip({"session_id": "s1"}))
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["ok"] is False
    assert "text" in payload["error"]


def test_dispatch_rejects_missing_session_id():
    resp = asyncio.run(_dispatch_round_trip({"text": "hi"}))
    assert resp.status_code == 400
    assert "session_id" in resp.json()["error"]


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
    """Regression for the Codex P1: ``/internal/dispatch_async`` must hand the
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
        # Release the held turn the way a real result emit would so the
        # fire-and-forget dispatch settles promptly.
        controller.mark_turn_complete(ctx)

    controller = _build_controller_double(handler=capture)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post("/internal/dispatch_async", json={"session_id": session_id, "text": "hi"})
            assert resp.status_code == 202
        # Fire-and-forget: wait for the background turn to run + capture.
        for _ in range(200):
            if "platform_specific" in captured and session_id not in app.state.in_flight_dispatches:
                break
            await asyncio.sleep(0.02)

    asyncio.run(_go())
    payload = captured["platform_specific"]
    assert payload.get("workbench_session_id") == session_id
    assert payload.get("vibe_agent_name") == "contract-bot"
    target = payload.get("agent_session_target") or {}
    assert target.get("agent_name") == "contract-bot"
    assert target.get("agent_backend") == "claude"
    assert target.get("model") == "claude-sonnet-4-6"
    assert target.get("reasoning_effort") == "high"
    # session_anchor is carried so resume binds by the stored anchor after a
    # restart instead of a computed avibe_<id> (Codex P2). Workbench sessions
    # self-anchor to their id.
    assert target.get("session_anchor") == session_id


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


def _run_timeout_dispatch(monkeypatch, tmp_path, *, stop_confirmed: bool):
    """Drive the 600s stuck-turn timeout branch: patch dispatch_turn to flag the
    context as timed-out, set the stop outcome. Returns ``(controller, app, events)``
    where ``events`` are the bus event names published while the runner settled."""
    from core import inbox_events
    from storage.importer import ensure_sqlite_state

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()

    controller = _build_controller_double()
    controller.command_handler.handle_stop = AsyncMock(return_value=stop_confirmed)
    controller.emit_agent_message = AsyncMock()

    async def _timeout_dispatch(ctrl, ctx, text, *, source=SOURCE_HUMAN, on_chunk=None):
        # Simulate the 600s no-result wait: the dispatch machinery flags the context
        # (and stamps a turn_token, as the real streaming dispatch_turn does).
        ctx.platform_specific = dict(ctx.platform_specific or {})
        ctx.platform_specific["turn_timed_out"] = True
        ctx.platform_specific.setdefault("turn_token", "stuck-token")

    monkeypatch.setattr(internal_server, "dispatch_turn", _timeout_dispatch)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)
    events: list[str] = []
    # Captured INSIDE the loop — reading in_flight after asyncio.run() would be wrong,
    # since loop teardown cancels the pending sentinel (its finally pops the slot).
    captured: dict = {}

    async def _go():
        sub_id, queue = inbox_events.bus.subscribe()
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                resp = await client.post("/internal/dispatch_async", json={"session_id": "ses_t", "text": "hi"})
            # handle_stop runs early in the runner's finally; wait for it, then let the
            # rest of the finally (stuck-sentinel setup / idle emit) settle.
            for _ in range(100):
                if controller.command_handler.handle_stop.await_count > 0:
                    break
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.05)
            entry = app.state.in_flight_dispatches.get("ses_t")
            captured["present"] = entry is not None
            captured["blocked"] = entry is not None and not entry[0].done()
            while True:
                try:
                    evt, _data = queue.get_nowait()
                    events.append(evt)
                except asyncio.QueueEmpty:
                    break
        finally:
            inbox_events.bus.unsubscribe(sub_id)
        return resp

    resp = asyncio.run(_go())
    assert resp.status_code == 202
    controller.command_handler.handle_stop.assert_awaited_once()
    return controller, events, captured


def test_dispatch_async_timeout_settles_idle_when_stop_confirmed(monkeypatch, tmp_path):
    # Timeout + the stuck backend was actually interrupted (handle_stop → True):
    # clear the dot to idle (empty non-error result), free the slot, end the turn.
    controller, events, captured = _run_timeout_dispatch(monkeypatch, tmp_path, stop_confirmed=True)
    controller.emit_agent_message.assert_awaited_once_with(ANY, "result", "")
    assert captured["present"] is False  # slot freed → new send can start
    assert "turn.end" in events


def test_dispatch_async_timeout_keeps_session_blocked_and_recoverable_when_stop_unconfirmed(monkeypatch, tmp_path):
    # Timeout but the interrupt could NOT be applied (handle_stop → False): the backend
    # may still be producing output. Keep the session in-flight via a SELF-HEALING
    # sentinel task (not a never-resolving future) so a new Chat send enqueues, and
    # DEFER turn.end so the Chat keeps its Stop control to recover the turn (Codex P2).
    controller, events, captured = _run_timeout_dispatch(monkeypatch, tmp_path, stop_confirmed=False)
    # No premature terminal emit while the turn is still (maybe) running.
    controller.emit_agent_message.assert_not_awaited()
    # Session kept in-flight by a not-done sentinel task.
    assert captured["blocked"] is True
    # turn.end is DEFERRED until the slot frees → Chat keeps working + Stop.
    assert "turn.end" not in events


def test_dispatch_async_timeout_sentinel_releases_on_late_result(monkeypatch, tmp_path):
    # The stuck sentinel must RESOLVE when the backend finishes late: a late terminal
    # result fires the registered sink's done_event, releasing the slot + publishing
    # turn.end — instead of blocking the session forever (Codex P2).
    from core import inbox_events
    from storage.importer import ensure_sqlite_state

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()

    controller = _build_controller_double()
    controller.command_handler.handle_stop = AsyncMock(return_value=False)  # unconfirmed → stuck
    controller.emit_agent_message = AsyncMock()

    async def _timeout_dispatch(ctrl, ctx, text, *, source=SOURCE_HUMAN, on_chunk=None):
        ctx.platform_specific = dict(ctx.platform_specific or {})
        ctx.platform_specific["turn_timed_out"] = True
        ctx.platform_specific.setdefault("turn_token", "stuck-token")

    monkeypatch.setattr(internal_server, "dispatch_turn", _timeout_dispatch)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)

    captured: dict = {}

    async def _go():
        sub_id, queue = inbox_events.bus.subscribe()
        try:
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                await client.post("/internal/dispatch_async", json={"session_id": "ses_t", "text": "hi"})
            # Wait for the sentinel to be installed + its recovery sink registered.
            for _ in range(100):
                entry = app.state.in_flight_dispatches.get("ses_t")
                if entry is not None and not entry[0].done() and controller.active_turn_sinks:
                    break
                await asyncio.sleep(0.02)
            # Simulate the backend finishing late: fire the registered sink's done_event.
            for sink in list(controller.active_turn_sinks.values()):
                sink["done_event"].set()
            # The sentinel resolves → frees the slot + publishes turn.end. Capture the
            # release INSIDE the loop (after asyncio.run, teardown would free it anyway).
            for _ in range(100):
                if "ses_t" not in app.state.in_flight_dispatches:
                    break
                await asyncio.sleep(0.02)
            captured["released"] = "ses_t" not in app.state.in_flight_dispatches
            events: list[str] = []
            while True:
                try:
                    evt, _data = queue.get_nowait()
                    events.append(evt)
                except asyncio.QueueEmpty:
                    break
            captured["events"] = events
        finally:
            inbox_events.bus.unsubscribe(sub_id)

    asyncio.run(_go())
    assert captured["released"] is True  # the late result (not loop teardown) freed the slot
    assert "turn.end" in captured["events"]
    # A late result settled the dot itself, so the sentinel does NOT emit a failed result.
    controller.emit_agent_message.assert_not_awaited()


def test_send_now_during_stuck_sentinel_flushes_queue(monkeypatch, tmp_path):
    """A ``send-now`` that cuts in while a session is held by the stuck-turn
    SENTINEL (timed-out + interrupt-unconfirmed) must still drain the queue: the
    sentinel's release honors the SAME ``flush_on_cancel`` contract as the normal
    runner finally. Before the fix the sentinel only popped ``in_flight`` +
    published ``turn.end``, so the queued message stayed queued forever and the
    stale ``flush_on_cancel`` marker could wrongly flush a later turn (Codex P2).

    The sentinel is reached when the first turn times out and the backend
    interrupt is unconfirmed. ``send-now`` then sets ``flush_on_cancel`` and
    cancels the sentinel; its finally must flush + clear the marker.
    """
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
            conn, platform="avibe", scope_type="project", native_id="proj_stuck_flush", now="2026-06-02T00:00:00Z"
        )
        session = sessions_service.create_session(
            conn, scope_id=scope_id, agent_backend="claude", agent_name="worker"
        )
    session_id = session["id"]

    controller = _build_controller_double()
    # The first ``handle_stop`` is the runner's own timeout interrupt — UNCONFIRMED
    # (False) so the turn becomes the stuck sentinel. The second is ``send-now``'s
    # interrupt — CONFIRMED (True) so it proceeds to cancel the sentinel and flush.
    controller.command_handler.handle_stop = AsyncMock(side_effect=[False, True])
    controller.emit_agent_message = AsyncMock()

    dispatched: list[str] = []

    async def _dispatch(ctrl, ctx, text, *, source=SOURCE_HUMAN, on_chunk=None):
        dispatched.append(text)
        if text == "hi":
            # First turn: simulate the 600s no-result timeout. The runner finally
            # then interrupts (handle_stop → False, unconfirmed) → installs the
            # stuck sentinel that holds in_flight.
            ctx.platform_specific = dict(ctx.platform_specific or {})
            ctx.platform_specific["turn_timed_out"] = True
            ctx.platform_specific.setdefault("turn_token", "stuck-token")
            return
        # The flush turn for the queued message: complete normally so it doesn't
        # spin up a second sentinel — release the held turn like a real result emit.
        controller.mark_turn_complete(ctx)

    monkeypatch.setattr(internal_server, "dispatch_turn", _dispatch)
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)
    flush_on_cancel = app.state.flush_on_cancel
    captured: dict = {}

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Start the first turn; it times out + leaves an unconfirmed-stop sentinel.
            await client.post("/internal/dispatch_async", json={"session_id": session_id, "text": "hi"})
            # Wait for the stuck sentinel to be installed (in_flight holds a not-done task).
            for _ in range(200):
                entry = app.state.in_flight_dispatches.get(session_id)
                if entry is not None and not entry[0].done() and controller.active_turn_sinks:
                    break
                await asyncio.sleep(0.02)
            # Queue a message while the session is stuck, then send-now to cut in.
            with engine.begin() as conn:
                messages_service.enqueue_queued(conn, scope_id=scope_id, session_id=session_id, text="cut in")
            resp = await client.post(f"/internal/send-now/{session_id}")
            captured["send_now_status"] = resp.json().get("status")
            # The cancelled sentinel's finally flushes → a turn runs for "cut in".
            for _ in range(200):
                if "cut in" in dispatched and session_id not in app.state.in_flight_dispatches:
                    break
                await asyncio.sleep(0.02)
            # Capture INSIDE the loop — after asyncio.run, teardown would clear state.
            captured["flush_on_cancel_after"] = session_id in flush_on_cancel
            captured["in_flight_after"] = session_id in app.state.in_flight_dispatches

    asyncio.run(_go())
    assert captured["send_now_status"] == "interrupted", "send-now cut into the stuck sentinel"
    # The queued message ran as a turn (the sentinel's cancellation honored flush_on_cancel).
    assert "cut in" in dispatched, "the stuck-sentinel cancellation flushed the queued message"
    assert captured["flush_on_cancel_after"] is False, "the flush_on_cancel marker was cleared"
    assert captured["in_flight_after"] is False, "the slot freed after the flushed turn settled"
    with engine.connect() as conn:
        assert messages_service.list_queued(conn, session_id) == [], "the queue drained"
        transcript = messages_service.list_session_messages(conn, session_id=session_id, types=("user",))
    assert [m["text"] for m in transcript["messages"]] == ["cut in"], "the flush persisted the merged user row"


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


def test_turn_state_reflects_in_flight():
    """``/internal/turn-state`` reports whether a turn is running, so a freshly
    loaded / reconnected Chat page can restore its Stop state."""
    app = internal_server.create_app(_build_controller_double())
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            idle = (await client.get("/internal/turn-state/ses_ts")).json()
            # Simulate an in-flight turn.
            task = asyncio.create_task(asyncio.sleep(60))
            app.state.in_flight_dispatches["ses_ts"] = (
                task,
                MessageContext(user_id="U", channel_id="C", platform="avibe"),
            )
            busy = (await client.get("/internal/turn-state/ses_ts")).json()
            task.cancel()
            return idle, busy

    idle, busy = asyncio.run(_go())
    assert idle["in_flight"] is False
    assert busy["in_flight"] is True


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


# ---------------------------------------------------------------------
# Scheduled / watch turn gate (controller.session_turn_gate)
# ---------------------------------------------------------------------


def test_scheduled_gate_idle_runs_turn_with_lifecycle(monkeypatch, tmp_path):
    """An IDLE scheduled run goes through ``_run_turn`` like a Chat turn: it
    registers ``in_flight`` + publishes ``turn.start`` / ``turn.end`` on the bus
    (so the Chat page shows the working indicator + Stop works) and calls
    ``dispatch_turn`` with ``source=SOURCE_SCHEDULED`` and the no-op chunk sink —
    NOT ``on_chunk=None``. The sink isn't about the browser (chunks are discarded;
    avibe renders from ``message.new``); it makes ``dispatch_turn`` HOLD the turn
    open until the backend's terminal result, which keeps ``in_flight`` populated
    for the scheduled turn's whole lifetime so a Chat send can't preempt a
    still-running scheduled turn (Codex P2)."""
    from core import inbox_events
    from storage.importer import ensure_sqlite_state

    # submit_scheduled reads the queue (idle → empty-queue happy path), so it
    # needs an initialized state DB.
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()

    captured: dict = {}
    started = asyncio.Event()

    async def _fake_dispatch_turn(ctrl, ctx, text, *, source=SOURCE_HUMAN, on_chunk=None):
        captured["source"] = source
        captured["on_chunk"] = on_chunk
        captured["text"] = text
        captured["in_flight_while_running"] = "ses_sched" in app.state.in_flight_dispatches
        started.set()

    monkeypatch.setattr(internal_server, "dispatch_turn", _fake_dispatch_turn)

    controller = _build_controller_double()
    app = internal_server.create_app(controller)
    ctx = MessageContext(user_id="workbench", channel_id="ses_sched", platform="avibe")

    async def _go():
        sub_id, queue = inbox_events.bus.subscribe()
        events: list[str] = []
        try:
            await controller.session_turn_gate.submit_scheduled("ses_sched", ctx, "digest please")
            await asyncio.wait_for(started.wait(), timeout=3)
            for _ in range(100):
                if "ses_sched" not in app.state.in_flight_dispatches:
                    break
                await asyncio.sleep(0.02)
            for _ in range(2):
                try:
                    evt, _data = await asyncio.wait_for(queue.get(), timeout=1.0)
                    events.append(evt)
                except asyncio.TimeoutError:
                    break
        finally:
            inbox_events.bus.unsubscribe(sub_id)
        return events

    events = asyncio.run(_go())
    assert captured["source"] == SOURCE_SCHEDULED, "scheduled run dispatches on the scheduler path"
    # A scheduled run passes the no-op chunk SINK (callable, NOT None) so dispatch_turn
    # HOLDS the turn open to its terminal result — same as a Chat turn — instead of an
    # async backend returning at prompt-submit and freeing the slot (Codex P2). The sink
    # discards chunks; the reply still surfaces over ``message.new``, not a live stream.
    assert captured["on_chunk"] is not None, "scheduled run holds the turn open via the no-op sink"
    assert callable(captured["on_chunk"]), "the held-open sink is the no-op chunk callable"
    assert captured["text"] == "digest please"
    assert captured["in_flight_while_running"] is True, "registered in_flight (Stop works) while running"
    assert events == ["turn.start", "turn.end"], "publishes the session turn lifecycle on the bus"
    assert "ses_sched" not in app.state.in_flight_dispatches, "slot released after the turn"


def test_scheduled_gate_busy_enqueues_and_leaves_chat_turn_untouched(monkeypatch, tmp_path):
    """A scheduled run for a session that already has a turn in flight ENQUEUES a
    harness-attributed ``queued`` row (so it runs AFTER the active turn via the
    existing flush) instead of preempting it — and it never starts a competing
    turn nor disturbs the in-flight Chat task (Codex P2)."""
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
            conn, platform="avibe", scope_type="project", native_id="proj_sched_busy", now="2026-05-31T00:00:00Z"
        )
        session = sessions_service.create_session(
            conn, scope_id=scope_id, agent_backend="claude", agent_name="worker"
        )
    session_id = session["id"]

    # A scheduled run must NEVER reach dispatch_turn while busy — a call here fails
    # the test loudly.
    async def _explode_dispatch_turn(*args, **kwargs):
        raise AssertionError("a busy scheduled run must enqueue, not dispatch a turn")

    monkeypatch.setattr(internal_server, "dispatch_turn", _explode_dispatch_turn)

    controller = _build_controller_double()
    app = internal_server.create_app(controller)
    ctx = MessageContext(user_id="workbench", channel_id=session_id, platform="avibe")

    async def _go():
        async def _busy():
            await asyncio.sleep(60)

        chat_task = asyncio.create_task(_busy())
        chat_ctx = MessageContext(user_id="U", channel_id="C", platform="avibe")
        app.state.in_flight_dispatches[session_id] = (chat_task, chat_ctx)
        try:
            await controller.session_turn_gate.submit_scheduled(session_id, ctx, "scheduled while busy")
        finally:
            entry = app.state.in_flight_dispatches.get(session_id)
            # The in-flight Chat turn is undisturbed: same task object, not cancelled.
            assert entry is not None and entry[0] is chat_task and not chat_task.done()
            chat_task.cancel()
        return chat_ctx

    chat_ctx = asyncio.run(_go())
    controller.message_handler.handle_user_message.assert_not_awaited()
    with engine.connect() as conn:
        queued = messages_service.list_queued(conn, session_id)
        # The queued row is drainable + carries the session's scope and harness
        # attribution; it stays OUT of the user transcript.
        transcript = messages_service.list_session_messages(conn, session_id=session_id, types=("user",))
    assert [q["text"] for q in queued] == ["scheduled while busy"]
    assert queued[0]["scope_id"] == scope_id
    assert queued[0]["author"] == "harness"
    assert transcript["messages"] == []


def test_scheduled_gate_cancel_stops_scheduled_run(monkeypatch, tmp_path):
    """Stop works for a scheduled run: because the run goes through ``_run_turn``
    it registers the scheduled ``context`` in ``in_flight``, so
    ``/internal/cancel/{session_id}`` finds the task + reuses the IM ``/stop`` path
    to interrupt the backend (mirrors the Chat cancel test)."""
    from core.services import sessions as sessions_service
    from storage.db import create_sqlite_engine
    from storage.importer import ensure_sqlite_state
    from storage.settings_service import upsert_scope

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = upsert_scope(
            conn, platform="avibe", scope_type="project", native_id="proj_sched_cancel", now="2026-05-31T00:00:00Z"
        )
        session = sessions_service.create_session(
            conn, scope_id=scope_id, agent_backend="claude", agent_name="worker"
        )
    session_id = session["id"]

    started = asyncio.Event()

    async def _long_dispatch_turn(ctrl, ctx, text, *, source=SOURCE_HUMAN, on_chunk=None):
        started.set()
        await asyncio.sleep(5)  # held until the test cancels it

    monkeypatch.setattr(internal_server, "dispatch_turn", _long_dispatch_turn)

    controller = _build_controller_double()
    app = internal_server.create_app(controller)
    transport = httpx.ASGITransport(app=app)
    ctx = MessageContext(user_id="workbench", channel_id=session_id, platform="avibe")

    async def _go():
        # Start the scheduled run in the background (it holds in_flight open).
        run = asyncio.create_task(controller.session_turn_gate.submit_scheduled(session_id, ctx, "scheduled run"))
        await asyncio.wait_for(started.wait(), timeout=3)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(f"/internal/cancel/{session_id}")
        for _ in range(200):
            if session_id not in app.state.in_flight_dispatches:
                break
            await asyncio.sleep(0.02)
        run.cancel()
        return resp

    resp = asyncio.run(_go())
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancel_requested"
    # The cancel interrupted the backend through the IM /stop path with the
    # scheduled run's own context.
    controller.command_handler.handle_stop.assert_awaited_once()
    assert session_id not in app.state.in_flight_dispatches, "slot released after the scheduled run was stopped"
