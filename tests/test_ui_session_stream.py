"""Tests for ``POST /api/sessions/<id>/messages`` (fire-and-forget dispatch)
and ``POST /api/sessions/<id>/cancel`` in ``vibe.ui_server``.

These cover the bridge between the browser and the controller's Unix socket:
the session/page-scoped model persists the user row and fire-and-forgets the
turn (the reply arrives over the ``message.new`` stream). We mock
``vibe.internal_client`` so the tests stay hermetic and don't need a real
controller process.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from storage.importer import ensure_sqlite_state
from storage.models import scope_settings
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
        conn.execute(
            scope_settings.insert().values(
                scope_id=scope_id,
                enabled=1,
                role=None,
                workdir=str(tmp_path),
                agent_name=None,
                agent_backend=None,
                agent_variant=None,
                model=None,
                reasoning_effort=None,
                require_mention=None,
                settings_version=1,
                settings_json="{}",
                created_at="2026-05-26T13:00:00Z",
                updated_at="2026-05-26T13:00:00Z",
            )
        )
        session = sessions_service.create_session(
            conn,
            scope_id=scope_id,
            agent_backend="claude",
            agent_name="worker",
        )
    return scope_id, session["id"]


def test_route_fire_and_forgets_dispatch(isolated_state, tmp_path):
    """The web Chat POST persists the user row AND fire-and-forgets the turn via
    ``/internal/dispatch_async``. The reply arrives over the persistent
    ``message.new`` stream, so the response returns 201 immediately with the row
    (it does NOT hold the turn open).
    """

    from vibe.ui_server import app

    _, session_id = _make_session(tmp_path)

    dispatch_mock = AsyncMock(
        return_value={"status_code": 202, "body": {"ok": True, "session_id": session_id}}
    )
    with patch("vibe.internal_client.dispatch_async", dispatch_mock):
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
    # The turn was kicked off fire-and-forget with the session + text.
    dispatch_mock.assert_awaited_once()
    sent = dispatch_mock.await_args.args[0]
    assert sent["session_id"] == session_id
    assert sent["text"] == "no stream"


def test_route_enqueues_when_turn_in_progress(isolated_state, tmp_path):
    """When the controller reports a turn already running (202 {queued}), the
    route persists the user row, hands its id to the controller to re-type as
    queued, and returns 202 {queued:true} marked as the queued type. (The actual
    re-type is the controller's atomic job, covered in test_internal_server.)"""

    from vibe.ui_server import app

    _, session_id = _make_session(tmp_path)

    dispatch_mock = AsyncMock(return_value={"status_code": 202, "body": {"ok": True, "queued": True}})
    with patch("vibe.internal_client.dispatch_async", dispatch_mock):
        client = app.test_client()
        headers = csrf_headers(client)
        response = client.post(
            f"/api/sessions/{session_id}/messages",
            json={"text": "while busy"},
            headers=headers,
        )
    assert response.status_code == 202
    body = response.get_json()
    assert body["queued"] is True
    assert body["type"] == "queued"
    assert body["text"] == "while busy"
    # The user row was persisted first, and its id handed to the controller to
    # re-type as queued (atomic, no second row).
    dispatch_mock.assert_awaited_once()
    sent = dispatch_mock.await_args.args[0]
    assert sent["user_message_id"] == body["id"]


def test_create_session_without_backend_defers_to_default_agent(isolated_state, tmp_path):
    """POST /api/sessions with no ``agent_backend`` must NOT stamp a concrete
    backend onto the session. A stamped backend is treated by message_handler
    as an explicit override and bypasses default Vibe Agent resolution, so a
    plain "new chat" leaves the backend empty and lets the shared resolver
    pick the configured default agent at dispatch time.
    """

    from storage import projects_service
    from storage.db import create_sqlite_engine
    from vibe.ui_server import app

    engine = create_sqlite_engine()
    with engine.begin() as conn:
        project = projects_service.create_project(conn, folder_path=str(tmp_path))

    client = app.test_client()
    headers = csrf_headers(client)
    response = client.post(
        "/api/sessions",
        json={"project_id": project["id"]},
        headers=headers,
    )
    assert response.status_code == 201
    # Empty/absent backend — resolution is deferred to dispatch, not pinned here.
    assert not response.get_json().get("agent_backend")


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
