"""Tests for ``POST /api/sessions/<id>/messages?stream=1`` and
``POST /api/sessions/<id>/cancel`` in ``vibe.ui_server``.

These cover the C5 + C6 bridge between the browser and the controller's
Unix socket. We mock ``vibe.internal_client`` so the tests stay
hermetic and don't need a real controller process.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from storage.importer import ensure_sqlite_state
from storage.settings_service import upsert_scope
from tests.ui_server_test_helpers import csrf_headers


@pytest.fixture()
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    yield tmp_path


def _make_session(tmp_path: Path) -> tuple[str, str]:
    """Create a real avibe project + session row so the route handler
    can find it. Returns ``(scope_id, session_id)``.
    """

    from core.services import sessions as sessions_service
    from storage.db import create_sqlite_engine

    engine = create_sqlite_engine()
    with engine.begin() as conn:
        scope_id = upsert_scope(
            conn,
            platform="avibe",
            scope_type="project",
            native_id="proj_stream",
            now="2026-05-26T13:00:00Z",
        )
        session = sessions_service.create_session(
            conn,
            scope_id=scope_id,
            agent_backend="claude",
            agent_name="worker",
        )
    return scope_id, session["id"]


def test_stream_route_proxies_internal_sse_frames(isolated_state, tmp_path):
    """``?stream=1`` opens the internal socket, forwards the
    ``turn.start`` envelope with the persisted user message, and then
    relays subsequent ``turn.chunk`` frames verbatim.
    """

    from vibe.ui_server import app

    scope_id, session_id = _make_session(tmp_path)

    upstream_events = [
        ("turn.chunk", {"text": "thinking", "kind": "notify"}),
        ("turn.chunk", {"text": "answer", "kind": "result"}),
        ("turn.end", {"session_id": session_id}),
    ]

    async def fake_stream_dispatch(payload, **kwargs):
        # Lock the request payload shape — the bridge must forward the
        # text + session_id + scope_id from the route body.
        assert payload["session_id"] == session_id
        assert payload["text"] == "hello"
        assert payload["scope_id"] == scope_id
        for event in upstream_events:
            yield event

    with patch("vibe.internal_client.stream_dispatch", fake_stream_dispatch):
        client = app.test_client()
        headers = csrf_headers(client)
        response = client.post(
            f"/api/sessions/{session_id}/messages?stream=1",
            json={"text": "hello"},
            headers=headers,
        )

    assert response.status_code == 200
    assert response.headers.get("content-type", "").startswith("text/event-stream")
    body = response.text
    # The route prepends a ``stream.start`` envelope with the persisted
    # user message so the browser can render it before the agent reply
    # arrives — then forwards the upstream frames verbatim.
    assert "event: stream.start" in body
    assert "event: turn.chunk" in body
    assert '"text": "thinking"' in body
    assert '"text": "answer"' in body
    assert "event: turn.end" in body


def test_stream_route_emits_stream_error_when_socket_down(isolated_state, tmp_path):
    """If the internal socket isn't reachable, the route still persists
    the user message and then surfaces a ``stream.error`` frame so the
    browser can fall back instead of dying mid-stream.
    """

    from vibe import internal_client
    from vibe.ui_server import app

    _, session_id = _make_session(tmp_path)

    async def boom(payload, **kwargs):
        raise internal_client.InternalServerUnavailable("socket missing")
        yield  # pragma: no cover — generator marker

    with patch("vibe.internal_client.stream_dispatch", boom):
        client = app.test_client()
        headers = csrf_headers(client)
        response = client.post(
            f"/api/sessions/{session_id}/messages?stream=1",
            json={"text": "hi"},
            headers=headers,
        )

    body = response.text
    assert "event: stream.start" in body
    assert "event: stream.error" in body
    assert "internal_server_unavailable" in body


def test_non_stream_route_unchanged(isolated_state, tmp_path):
    """Bare POST (no ``stream=1``) keeps the commit-07 behavior:
    persist the user message and return the JSON row. C5 must not
    regress that path.
    """

    from vibe.ui_server import app

    _, session_id = _make_session(tmp_path)

    client = app.test_client()
    headers = csrf_headers(client)
    response = client.post(
        f"/api/sessions/{session_id}/messages",
        json={"text": "no stream"},
        headers=headers,
    )
    assert response.status_code == 201
    payload = response.get_json()
    assert payload["author"] == "user"
    assert payload["text"] == "no stream"


def test_create_session_defaults_backend_to_config(isolated_state, tmp_path):
    """POST /api/sessions with no ``agent_backend`` falls back to the
    configured ``agents.default_backend`` instead of erroring or pinning
    a hard-coded backend. This is what the Workbench canvas relies on so a
    plain "new chat" routes through the user's configured default.
    """

    from core.services import settings as settings_service
    from storage import projects_service
    from storage.db import create_sqlite_engine
    from vibe.runtime import default_config
    from vibe.ui_server import app

    engine = create_sqlite_engine()
    with engine.begin() as conn:
        project = projects_service.create_project(conn, folder_path=str(tmp_path))

    # Seed a config on disk (the UI server's bare load_config() requires the
    # file to exist) and read back the default the route should fall back to.
    expected_backend = settings_service.load_config(
        default_factory=default_config
    ).agents.default_backend

    client = app.test_client()
    headers = csrf_headers(client)
    response = client.post(
        "/api/sessions",
        json={"project_id": project["id"]},
        headers=headers,
    )
    assert response.status_code == 201
    assert response.get_json()["agent_backend"] == expected_backend


def test_cancel_route_proxies_to_internal_socket(isolated_state, tmp_path):
    _, session_id = _make_session(tmp_path)

    from vibe.ui_server import app

    cancel_mock = AsyncMock(
        return_value={"status_code": 200, "body": {"ok": True, "status": "cancel_requested"}}
    )
    with patch("vibe.internal_client.cancel_dispatch", cancel_mock):
        client = app.test_client()
        headers = csrf_headers(client)
        response = client.post(f"/api/sessions/{session_id}/cancel", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "cancel_requested"
    cancel_mock.assert_awaited_once_with(session_id)


def test_cancel_route_returns_503_when_socket_unavailable(isolated_state, tmp_path):
    from vibe import internal_client
    from vibe.ui_server import app

    _, session_id = _make_session(tmp_path)

    async def fail(session_id_inner):
        raise internal_client.InternalServerUnavailable("socket missing")

    with patch("vibe.internal_client.cancel_dispatch", fail):
        client = app.test_client()
        headers = csrf_headers(client)
        response = client.post(f"/api/sessions/{session_id}/cancel", headers=headers)
    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "internal_unavailable"
