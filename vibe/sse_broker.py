"""In-process pub/sub for workbench Server-Sent Events.

Every workbench browser opens a long-lived ``GET /api/events`` request
and the handler subscribes here. The REST routes that mutate messages
/ sessions / unread counts publish events back through ``broker.publish``;
the broker fans them out to every subscriber.

Why SSE over WebSocket (per Cloudflare Tunnel research, 2026-05-24):
    SSE rides on plain HTTP, has automatic browser reconnect via
    ``EventSource``, and Cloudflare proxies + tunnels handle it without
    the WS upgrade handshake. A 15-second keep-alive comment line keeps
    intermediaries from killing idle streams.

Threading model:
    - Subscribers hold ``asyncio.Queue``; the SSE generator awaits them.
    - ``publish`` is safe to call from any thread (Flask-style sync
      routes, Agent worker threads, etc.) — it schedules
      ``call_soon_threadsafe`` on the event loop captured at first
      subscribe time. Sync callers don't need to know about the loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class SSEBroker:
    def __init__(self) -> None:
        self._subscribers: dict[int, asyncio.Queue] = {}
        self._next_id = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        """Register a new subscriber. Must be called from an event-loop coroutine."""

        self._loop = asyncio.get_event_loop()
        sub_id = self._next_id
        self._next_id += 1
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._subscribers[sub_id] = queue
        logger.debug("SSE subscriber %s connected (total=%s)", sub_id, len(self._subscribers))
        return sub_id, queue

    def unsubscribe(self, sub_id: int) -> None:
        if self._subscribers.pop(sub_id, None) is not None:
            logger.debug("SSE subscriber %s disconnected (total=%s)", sub_id, len(self._subscribers))

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event_type: str, data: Any) -> None:
        """Fan a JSON event out to every subscriber.

        Safe from any thread. No-op when there are no subscribers (the
        common case during boot / headless setups).
        """

        loop = self._loop
        if loop is None or not self._subscribers:
            return
        payload = json.dumps({"type": event_type, "data": data, "ts": time.time()})
        # Snapshot the queue list before scheduling so a concurrent
        # unsubscribe during fan-out doesn't drop a publish on the floor.
        for queue in list(self._subscribers.values()):
            try:
                loop.call_soon_threadsafe(self._put_nowait, queue, event_type, payload)
            except RuntimeError:
                # Loop was closed; skip silently — next subscribe will
                # capture a fresh loop.
                pass

    @staticmethod
    def _put_nowait(queue: asyncio.Queue, event_type: str, payload: str) -> None:
        try:
            queue.put_nowait((event_type, payload))
        except asyncio.QueueFull:
            logger.warning("SSE subscriber queue full; dropping %s event", event_type)


# Module-level singleton — ui_server.py imports this and Avibe / REST
# routes call ``broker.publish`` from their write paths.
broker = SSEBroker()
