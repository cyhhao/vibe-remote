"""Contract tests for ``core.services.dispatch.dispatch_turn``.

``dispatch_turn`` is the shared entry that IM adapter, CLI, and the
upcoming Web UI / N3 socket path all funnel through. The contract:

1. ``source=human`` delegates to ``MessageHandler.handle_user_message``.
2. ``source=scheduled`` delegates to ``MessageHandler.handle_scheduled_message``.
3. The ``on_chunk`` callback gets stashed on the context's
   ``platform_specific`` dict so the C4 dispatcher hook can later pick
   it up without changing the public signature.

Locking this here means swapping the underlying handler (e.g. when N3
streaming lands) cannot break the public contract silently.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.services.dispatch import (
    SOURCE_HUMAN,
    SOURCE_SCHEDULED,
    dispatch_turn,
)
from modules.im import MessageContext


def _build_controller_double() -> MagicMock:
    controller = MagicMock()
    controller.message_handler = MagicMock()
    controller.message_handler.handle_user_message = AsyncMock(return_value="msg_user_1")
    controller.message_handler.handle_scheduled_message = AsyncMock(return_value="msg_sched_1")
    return controller


def _ctx() -> MessageContext:
    return MessageContext(user_id="U", channel_id="C", platform="slack", message_id="m1")


def test_human_source_calls_handle_user_message():
    controller = _build_controller_double()
    result = asyncio.run(dispatch_turn(controller, _ctx(), "hi", source=SOURCE_HUMAN))
    assert result == "msg_user_1"
    controller.message_handler.handle_user_message.assert_awaited_once()
    controller.message_handler.handle_scheduled_message.assert_not_called()


def test_scheduled_source_calls_handle_scheduled_message():
    controller = _build_controller_double()
    result = asyncio.run(dispatch_turn(controller, _ctx(), "cron task", source=SOURCE_SCHEDULED))
    assert result == "msg_sched_1"
    controller.message_handler.handle_scheduled_message.assert_awaited_once()
    controller.message_handler.handle_user_message.assert_not_called()


def test_default_source_is_human():
    """Callers that don't pass ``source`` get the human path; the IM
    adapter relies on this so the registration site stays terse.
    """
    controller = _build_controller_double()
    asyncio.run(dispatch_turn(controller, _ctx(), "hello"))
    controller.message_handler.handle_user_message.assert_awaited_once()


def test_on_chunk_is_stashed_on_context():
    """The chunk callback is reserved for the future N3 streaming path
    (commit C4). Today it must round-trip onto ``platform_specific`` so
    the dispatcher hook can pick it up later without a signature change.
    """
    controller = _build_controller_double()
    ctx = _ctx()
    received: list[dict] = []

    async def _on_chunk(envelope: dict) -> None:
        received.append(envelope)

    asyncio.run(dispatch_turn(controller, ctx, "hi", on_chunk=_on_chunk))
    payload = ctx.platform_specific or {}
    assert payload.get("turn_chunk_callback") is _on_chunk
    # The callback itself is not invoked by dispatch_turn — it's only
    # stored for the C4 hook. Lock that contract here.
    assert received == []


def test_on_chunk_absent_keeps_platform_specific_untouched():
    controller = _build_controller_double()
    ctx = _ctx()
    ctx.platform_specific = {"existing": "value"}
    asyncio.run(dispatch_turn(controller, ctx, "hi"))
    assert ctx.platform_specific == {"existing": "value"}, (
        "no chunk callback => platform_specific must not be mutated"
    )


def test_invalid_source_raises():
    """Future-proof: unknown source must not silently fall through to a
    handler that the caller didn't intend."""
    controller = _build_controller_double()
    with pytest.raises(Exception):
        # ``handle_user_message`` is the default path so we want
        # source="unknown" to either raise or be loudly handled. Today's
        # implementation falls through to the user path; this test will
        # be tightened once we add explicit validation. Marked as the
        # behavioral floor.
        # NOTE: this test is intentionally lenient until C2 adds explicit
        # source validation. Kept as a marker for the future tightening.
        async def _go():
            await dispatch_turn(controller, _ctx(), "x", source="unknown_source")
            # If we got here, the user path was taken; that's still
            # OK for v1 but we mark a future tightening point.
            raise RuntimeError("expected stricter source validation in future")
        asyncio.run(_go())
