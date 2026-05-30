"""Controller-side fan-out bus for inbox change events.

The Controller process persists agent messages (``message_mirror``), but the
browser SSE broker lives in the UI server process. This bus lets the Controller
publish ``inbox.session.updated`` events; ``core/internal_server.py`` exposes
them over ``GET /internal/events`` (a long-lived SSE on the dispatch socket),
and the UI server re-broadcasts them to browsers via its own ``SSEBroker``.

Thread-safe like ``vibe/sse_broker.py``: ``publish`` may be called from any
thread/loop and lands on each subscriber's loop via ``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


class InboxEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}
        self._next_id = 0
        self._lock = threading.Lock()

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        loop = asyncio.get_event_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._subscribers[sub_id] = (loop, queue)
        return sub_id, queue

    def unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._subscribers.pop(sub_id, None)

    def publish(self, event_type: str, data: Any) -> None:
        """Fan ``(event_type, data)`` out to every subscriber. No-op when none."""
        with self._lock:
            subs = list(self._subscribers.values())
        if not subs:
            return
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(self._put_nowait, queue, event_type, data)
            except RuntimeError:
                # Loop closed mid-publish; drop silently.
                pass

    @staticmethod
    def _put_nowait(queue: asyncio.Queue, event_type: str, data: Any) -> None:
        try:
            queue.put_nowait((event_type, data))
        except asyncio.QueueFull:
            logger.warning("inbox event bus subscriber queue full; dropping %s", event_type)


# Process-wide singleton (Controller process). ``message_mirror`` publishes;
# ``internal_server`` subscribes.
bus = InboxEventBus()
