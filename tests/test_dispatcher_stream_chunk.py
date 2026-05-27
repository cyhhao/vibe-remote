"""Unit test for ``core.message_dispatcher._stream_chunk``.

The hook is what bridges the in-process dispatcher to the SSE stream
exposed by ``core.internal_server`` — it must be a no-op when the
callback is absent (IM bots) and must surface unexpected callback
errors without raising into the dispatcher.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.message_dispatcher import _stream_chunk
from modules.im import MessageContext


def test_noop_when_no_callback():
    ctx = MessageContext(user_id="U", channel_id="C", platform="slack")
    # Must not raise, must not return anything truthy.
    asyncio.run(_stream_chunk(ctx, text="hi", message_id="m1", kind="notify"))


def test_invokes_callback_when_present():
    ctx = MessageContext(user_id="U", channel_id="C", platform="avibe")
    cb = AsyncMock()
    ctx.platform_specific = {"turn_chunk_callback": cb}
    asyncio.run(_stream_chunk(ctx, text="agent reply", message_id="m_42", kind="result"))
    cb.assert_awaited_once_with({"text": "agent reply", "message_id": "m_42", "kind": "result"})


def test_swallows_callback_exception():
    ctx = MessageContext(user_id="U", channel_id="C", platform="avibe")

    async def _raises(envelope):
        raise RuntimeError("UI server died")

    ctx.platform_specific = {"turn_chunk_callback": _raises}
    # Must not propagate — a misbehaving UI consumer cannot kill the
    # underlying agent reply path.
    asyncio.run(_stream_chunk(ctx, text="x", message_id=None, kind="notify"))
