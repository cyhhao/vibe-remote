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

from typing import Awaitable, Callable, Optional, TYPE_CHECKING

from modules.im import MessageContext

if TYPE_CHECKING:  # pragma: no cover - typing-only
    from core.controller import Controller

# Future N3 streaming hook. Receives one envelope per ``emit_agent_message``
# from the controller; same process or async-iterator-friendly. Reserved
# now so the public signature is stable before C4 wires it.
ChunkCallback = Callable[[dict], Awaitable[None]]

SOURCE_HUMAN = "human"
SOURCE_SCHEDULED = "scheduled"


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
    ``on_chunk`` is reserved for the N3 socket endpoint (commit C4) and
    has no effect yet — wiring it changes behavior in a follow-up commit,
    not this one.
    """

    if on_chunk is not None:
        # Stash on the context for the C4 dispatcher hook to pick up.
        # Keeping the storage on ``platform_specific`` lets us add this
        # path-through without changing the ``MessageContext`` dataclass.
        payload = dict(context.platform_specific or {})
        payload["turn_chunk_callback"] = on_chunk
        context.platform_specific = payload

    handler = controller.message_handler
    if source == SOURCE_SCHEDULED:
        return await handler.handle_scheduled_message(context, text)
    return await handler.handle_user_message(context, text)
