"""Unit tests for the controller-side ``InboxEventBus`` fan-out.

The bus is the Controller-process half of the realtime inbox bridge:
``core.message_mirror`` publishes ``inbox.session.updated`` here, and
``core.internal_server``'s ``GET /internal/events`` subscribes and streams the
events over the dispatch socket to the UI server. These tests pin the contract
the bridge relies on: subscribers receive published events, unsubscribe stops
delivery, and a publish with no subscribers is a harmless no-op.

The repo has no ``pytest-asyncio``; following the existing convention
(``tests/test_dispatcher_stream_chunk.py``) each async scenario runs inside
``asyncio.run`` so the loop captured at ``subscribe`` time is the one driving
``publish``'s ``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.inbox_events import InboxEventBus


def test_publish_delivers_to_subscriber():
    async def scenario():
        bus = InboxEventBus()
        sub_id, queue = bus.subscribe()
        bus.publish("inbox.session.updated", {"session_id": "s1"})
        event_type, data = await asyncio.wait_for(queue.get(), timeout=1.0)
        bus.unsubscribe(sub_id)
        return event_type, data

    event_type, data = asyncio.run(scenario())
    assert event_type == "inbox.session.updated"
    assert data == {"session_id": "s1"}


def test_fanout_to_every_subscriber():
    async def scenario():
        bus = InboxEventBus()
        _, q1 = bus.subscribe()
        _, q2 = bus.subscribe()
        bus.publish("e", {"n": 1})
        return (
            await asyncio.wait_for(q1.get(), timeout=1.0),
            await asyncio.wait_for(q2.get(), timeout=1.0),
        )

    a, b = asyncio.run(scenario())
    assert a == ("e", {"n": 1})
    assert b == ("e", {"n": 1})


def test_unsubscribe_stops_delivery():
    async def scenario():
        bus = InboxEventBus()
        sub_id, queue = bus.subscribe()
        bus.unsubscribe(sub_id)
        bus.publish("inbox.session.updated", {"session_id": "s1"})
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.05)

    asyncio.run(scenario())


def test_publish_without_subscribers_is_noop():
    # No loop captured, no subscribers — must not raise (boot / headless path).
    InboxEventBus().publish("inbox.session.updated", {"x": 1})
