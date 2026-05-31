"""Tests for ``vibe.internal_client``.

The UI server uses this module to reach the controller's Unix socket to
start fire-and-forget turns and run the turn-control surface (cancel /
send-now / turn-state). We cover the socket-missing degradation and the
round-trip shape of each call against a fake ASGI app via
``httpx.ASGITransport`` (skips uvicorn).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vibe import internal_client


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


def test_dispatch_async_round_trip(tmp_path):
    """``dispatch_async`` posts the payload to ``/internal/dispatch_async`` and
    surfaces the controller's status + body so the UI route can tell a started
    turn (202) from a concurrent-turn refusal (409)."""
    app = FastAPI()
    captured: dict = {}

    @app.post("/internal/dispatch_async")
    async def _async(payload: dict):
        captured["payload"] = payload
        return JSONResponse(status_code=202, content={"ok": True, "session_id": payload.get("session_id")})

    sock = tmp_path / "dispatch.sock"
    sock.touch()

    async def _go():
        fake_transport = httpx.ASGITransport(app=app)
        with patch("vibe.internal_client.httpx.AsyncHTTPTransport", return_value=fake_transport):
            return await internal_client.dispatch_async(
                {"session_id": "ses_z", "text": "hi"}, socket_path=sock
            )

    result = asyncio.run(_go())
    assert captured["payload"] == {"session_id": "ses_z", "text": "hi"}
    assert result["status_code"] == 202
    assert result["body"] == {"ok": True, "session_id": "ses_z"}


def test_dispatch_async_missing_socket_raises_unavailable(tmp_path):
    sock = tmp_path / "missing.sock"
    with pytest.raises(internal_client.InternalServerUnavailable):
        asyncio.run(internal_client.dispatch_async({"session_id": "s", "text": "x"}, socket_path=sock))
