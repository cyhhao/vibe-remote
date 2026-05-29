"""Unit test for ``core.message_dispatcher._stream_chunk``.

The hook bridges the in-process dispatcher to the SSE stream exposed by
``core.internal_server``. It resolves the live turn sink by *session key*
from ``controller.active_turn_sinks`` — not off the context — so a reused
agent session, whose long-lived receiver carries a stale per-turn context,
still streams to the current turn. It forwards the envelope to the
registered ``on_chunk``, swallows callback errors, and a ``result`` emit
sets the sink's done event so ``dispatch_turn`` can close the stream. No
sink (IM bots / CLI) => silent no-op.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.message_dispatcher import _stream_chunk
from modules.im import MessageContext


class _ControllerDouble:
    """Minimal controller exposing the real turn-sink registry surface."""

    def __init__(self):
        self.active_turn_sinks: dict = {}

    def _get_session_key(self, ctx):
        return f"{ctx.platform}::{ctx.channel_id}"

    def register_turn_sink(self, session_key, *, on_chunk, done_event):
        self.active_turn_sinks[session_key] = {"on_chunk": on_chunk, "done_event": done_event}

    def get_turn_sink(self, session_key):
        return self.active_turn_sinks.get(session_key)


def _ctx(platform="avibe", channel="C"):
    return MessageContext(user_id="U", channel_id=channel, platform=platform)


def test_noop_when_no_sink():
    controller = _ControllerDouble()
    # No sink registered for this session => silent no-op (IM / CLI turn).
    asyncio.run(_stream_chunk(controller, _ctx(), text="hi", message_id="m1", kind="notify"))


def test_forwards_envelope_to_sink_on_chunk():
    controller = _ControllerDouble()
    cb = AsyncMock()
    controller.register_turn_sink("avibe::C", on_chunk=cb, done_event=asyncio.Event())
    asyncio.run(_stream_chunk(controller, _ctx(), text="agent reply", message_id="m_42", kind="result"))
    cb.assert_awaited_once_with({"text": "agent reply", "message_id": "m_42", "kind": "result"})


def test_result_kind_sets_done_event():
    controller = _ControllerDouble()
    done = asyncio.Event()
    controller.register_turn_sink("avibe::C", on_chunk=AsyncMock(), done_event=done)
    asyncio.run(_stream_chunk(controller, _ctx(), text="final", message_id="m1", kind="result"))
    assert done.is_set(), "a result emit must release the streaming dispatch"


def test_notify_kind_leaves_done_event_unset():
    controller = _ControllerDouble()
    done = asyncio.Event()
    controller.register_turn_sink("avibe::C", on_chunk=AsyncMock(), done_event=done)
    asyncio.run(_stream_chunk(controller, _ctx(), text="thinking", message_id="m1", kind="notify"))
    assert not done.is_set(), "notify is intermediate; the turn isn't done yet"


def test_resolves_sink_by_session_key_despite_stale_context():
    # The agent receiver emits with a context captured at session start; a
    # later turn registers its sink under the same session key. _stream_chunk
    # must resolve by session key so the reused-session reply still streams,
    # even though the emitting context object is a different instance.
    controller = _ControllerDouble()
    cb = AsyncMock()
    controller.register_turn_sink("avibe::sesX", on_chunk=cb, done_event=asyncio.Event())
    stale_ctx = _ctx(channel="sesX")
    asyncio.run(_stream_chunk(controller, stale_ctx, text="r", message_id="m", kind="notify"))
    cb.assert_awaited_once()


def test_swallows_sink_on_chunk_exception():
    controller = _ControllerDouble()

    async def _raises(envelope):
        raise RuntimeError("UI server died")

    controller.register_turn_sink("avibe::C", on_chunk=_raises, done_event=asyncio.Event())
    # Must not propagate — a misbehaving UI consumer cannot kill the agent reply.
    asyncio.run(_stream_chunk(controller, _ctx(), text="x", message_id=None, kind="notify"))
