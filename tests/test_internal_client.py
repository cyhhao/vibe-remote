"""Tests for ``vibe.internal_client``.

The UI server uses this module to reach the controller's Unix socket and
proxy SSE chunks to the browser. We cover the two failure modes that
matter for the C5 ``?stream=1`` fallback to the queue path:

1. Missing socket file => ``InternalServerUnavailable``.
2. End-to-end SSE parsing against a fake ASGI app via
   ``httpx.ASGITransport``. The fake app produces a known sequence of
   ``event:`` + ``data:`` lines and we assert the client surfaces them
   in order.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibe import internal_client


def test_missing_socket_raises_unavailable(tmp_path):
    sock = tmp_path / "missing.sock"
    assert not sock.exists()

    async def _go():
        async for _ in internal_client.stream_dispatch({"session_id": "s", "text": "hi"}, socket_path=sock):
            pass

    with pytest.raises(internal_client.InternalServerUnavailable):
        asyncio.run(_go())


def _fake_app_emitting(frames: list[str]) -> FastAPI:
    app = FastAPI()

    @app.post("/internal/dispatch")
    async def _dispatch():
        async def _gen() -> AsyncIterator[bytes]:
            for f in frames:
                yield f.encode("utf-8")

        return StreamingResponse(_gen(), media_type="text/event-stream")

    return app


def test_stream_dispatch_parses_sse_via_asgi_transport(tmp_path):
    """End-to-end shape: we feed the client a known SSE byte stream
    through ``httpx.ASGITransport`` (skips uvicorn) and confirm it
    surfaces (event_name, data) pairs in order.
    """

    frames = [
        "event: turn.start\ndata: {\"session_id\": \"s1\"}\n\n",
        "event: turn.chunk\ndata: {\"text\": \"hello\", \"kind\": \"result\"}\n\n",
        "event: turn.end\ndata: {\"session_id\": \"s1\"}\n\n",
    ]
    sock = tmp_path / "dispatch.sock"
    sock.touch()  # presence is all the client checks before opening the transport

    async def _go() -> list[tuple[str, dict]]:
        app = _fake_app_emitting(frames)
        fake_transport = httpx.ASGITransport(app=app)
        with patch("vibe.internal_client.httpx.AsyncHTTPTransport", return_value=fake_transport):
            events = []
            async for event_name, data in internal_client.stream_dispatch(
                {"session_id": "s1", "text": "hi"}, socket_path=sock
            ):
                events.append((event_name, data))
            return events

    events = asyncio.run(_go())
    assert events == [
        ("turn.start", {"session_id": "s1"}),
        ("turn.chunk", {"text": "hello", "kind": "result"}),
        ("turn.end", {"session_id": "s1"}),
    ]


def test_stream_dispatch_surfaces_500_as_unavailable(tmp_path):
    """4xx/5xx from the internal endpoint must raise so the UI route
    falls back instead of leaking error HTML into the SSE response.
    """

    from starlette.responses import Response

    app = FastAPI()

    @app.post("/internal/dispatch")
    async def _dispatch():
        return Response(content=b"internal down", status_code=503)

    sock = tmp_path / "dispatch.sock"
    sock.touch()

    async def _go():
        fake_transport = httpx.ASGITransport(app=app)
        with patch("vibe.internal_client.httpx.AsyncHTTPTransport", return_value=fake_transport):
            async for _ in internal_client.stream_dispatch({"session_id": "s", "text": "x"}, socket_path=sock):
                pass

    with pytest.raises(internal_client.InternalServerUnavailable):
        asyncio.run(_go())


def test_cancel_dispatch_round_trip(tmp_path):
    """``cancel_dispatch`` should forward the session id to the
    controller's ``POST /internal/cancel/<session_id>`` endpoint and
    surface the JSON body verbatim so the UI can render it.
    """

    app = FastAPI()
    captured: dict = {}

    @app.post("/internal/cancel/{session_id}")
    async def _cancel(session_id: str):
        captured["session_id"] = session_id
        return {"ok": True, "session_id": session_id, "status": "cancel_requested"}

    sock = tmp_path / "dispatch.sock"
    sock.touch()

    async def _go():
        fake_transport = httpx.ASGITransport(app=app)
        with patch("vibe.internal_client.httpx.AsyncHTTPTransport", return_value=fake_transport):
            return await internal_client.cancel_dispatch("ses_abc", socket_path=sock)

    result = asyncio.run(_go())
    assert captured["session_id"] == "ses_abc"
    assert result["status_code"] == 200
    assert result["body"] == {"ok": True, "session_id": "ses_abc", "status": "cancel_requested"}


def test_cancel_dispatch_missing_socket_raises_unavailable(tmp_path):
    sock = tmp_path / "missing.sock"
    with pytest.raises(internal_client.InternalServerUnavailable):
        asyncio.run(internal_client.cancel_dispatch("ses_x", socket_path=sock))


def test_data_json_parsing_skips_malformed(tmp_path):
    """A malformed ``data:`` payload should not raise; the client logs
    and continues so a single bad frame doesn't kill the stream.
    """

    frames = [
        "event: turn.chunk\ndata: this is not json\n\n",
        "event: turn.chunk\ndata: " + json.dumps({"ok": True}) + "\n\n",
    ]
    sock = tmp_path / "dispatch.sock"
    sock.touch()

    async def _go() -> list[tuple[str, dict]]:
        app = _fake_app_emitting(frames)
        fake_transport = httpx.ASGITransport(app=app)
        with patch("vibe.internal_client.httpx.AsyncHTTPTransport", return_value=fake_transport):
            return [event async for event in internal_client.stream_dispatch(
                {"session_id": "s", "text": "x"}, socket_path=sock
            )]

    events = asyncio.run(_go())
    assert events == [("turn.chunk", {"ok": True})]
