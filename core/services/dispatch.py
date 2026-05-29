"""Shared turn-dispatch entry point.

``dispatch_turn`` is the single business-level function for "run an agent
turn against a session". All three callers go through it so the IM
adapter, the CLI, and the upcoming Web UI / N3 socket path share one
implementation:

* **IM adapter** — same process as ``Controller``; calls ``await
  dispatch_turn(controller, context, text)`` directly. Today wired in
  ``Controller._wire_im_callbacks`` so every Slack / Discord / Telegram
  / Lark / WeChat / avibe inbound message lands here.
* **CLI** (``vibe agent run --sync``, future N3 socket path) — separate
  process; the internal HTTP endpoint built in ``core/internal_server.py``
  (commit C4) will wrap this with SSE chunked output.
* **Scheduled / hook / watch runs** — already routed through
  ``MessageHandler.handle_scheduled_message`` by ``ScheduledTaskService``;
  this layer just gives them a stable entry name.

The implementation today is a thin delegate so we can pin the public
shape now and keep behavior byte-identical with the existing
``MessageHandler._handle_turn`` path. Streaming (``on_chunk``) is
reserved for the N3 work — see ``docs/plans/workbench-dispatch-architecture.md``
§7. Until that lands the callback is silently unused, which is fine
because no caller passes it yet.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Optional, TYPE_CHECKING

from modules.im import MessageContext

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from core.controller import Controller

logger = logging.getLogger(__name__)

# Streaming hook for the N3 socket path. Receives one envelope per
# ``emit_agent_message`` notify/result emit for the turn, on the same loop.
ChunkCallback = Callable[[dict], Awaitable[None]]

SOURCE_HUMAN = "human"
SOURCE_SCHEDULED = "scheduled"

# Safety cap for how long a streaming dispatch holds the SSE stream open
# waiting for a turn to emit its result when the agent never produces one
# (crash, hang, auth failure). Real turns resolve far sooner via the
# result-emit signal; this only bounds the pathological no-result case.
TURN_STREAM_TIMEOUT = 600.0


async def dispatch_turn(
    controller: "Controller",
    context: MessageContext,
    text: str,
    *,
    source: str = SOURCE_HUMAN,
    on_chunk: Optional[ChunkCallback] = None,
) -> Optional[str]:
    """Run one agent turn for ``context`` and return the primary message id.

    ``source`` selects between the human-initiated and scheduler-initiated
    paths in ``MessageHandler``; today they only differ in source tagging.

    ``on_chunk`` (the N3 socket / web Chat path) receives each notify/result
    emit for this turn as it happens. Because the agent backends are
    fire-and-forget — ``handle_user_message`` returns once the message is sent
    and the reply streams in later on a background receiver task — we register
    a per-session sink and hold here until the turn emits its result (or the
    safety timeout fires), so the caller doesn't close the SSE stream before
    any chunk arrives.
    """

    handler = controller.message_handler

    async def _run() -> Optional[str]:
        if source == SOURCE_SCHEDULED:
            return await handler.handle_scheduled_message(context, text)
        return await handler.handle_user_message(context, text)

    if on_chunk is None:
        # IM / CLI: fire-and-forget; no live stream to hold open.
        return await _run()

    session_key = controller._get_session_key(context)
    done = asyncio.Event()
    controller.register_turn_sink(session_key, on_chunk=on_chunk, done_event=done)
    try:
        result = await _run()
        try:
            await asyncio.wait_for(done.wait(), timeout=TURN_STREAM_TIMEOUT)
        except asyncio.TimeoutError:
            logger.warning(
                "dispatch_turn: streaming turn for %s emitted no result within %.0fs; closing stream",
                session_key,
                TURN_STREAM_TIMEOUT,
            )
        return result
    finally:
        # Pass our own done event so a turn that was superseded by a newer
        # concurrent turn doesn't evict the newer turn's sink on cleanup.
        controller.pop_turn_sink(session_key, done)
