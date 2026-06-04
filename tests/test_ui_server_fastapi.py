import pytest

from storage.importer import ensure_sqlite_state
from vibe.ui_compat import CompatApp, normalize_response, route_path_to_fastapi, run_maybe_async, request
from starlette.websockets import WebSocketDisconnect

from vibe import ui_server
from vibe.ui_server import app
from tests.ui_server_test_helpers import csrf_headers


def test_websocket_echo_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("VIBE_UI_ENABLE_WS_ECHO", raising=False)

    with pytest.raises(WebSocketDisconnect) as exc:
        with app.test_client().websocket_connect("/ws/echo"):
            pass

    assert exc.value.code == 1008


def test_websocket_echo_smoke_when_enabled(monkeypatch):
    monkeypatch.setenv("VIBE_UI_ENABLE_WS_ECHO", "1")

    with app.test_client().websocket_connect("/ws/echo") as websocket:
        websocket.send_text("hello")

        assert websocket.receive_text() == "echo: hello"


def test_fastapi_schema_routes_are_not_exposed():
    client = app.test_client()

    docs_response = client.get("/docs")
    assert b"swagger-ui" not in docs_response.content.lower()
    assert client.get("/openapi.json").status_code != 200


def test_route_path_to_fastapi_converts_named_path_converter():
    assert route_path_to_fastapi("/files/<path:file_path>") == "/files/{file_path:path}"


def test_compat_app_matches_named_path_converter():
    compat_app = CompatApp()

    @compat_app.route("/files/<path:file_path>")
    def get_file(file_path):
        return {"file_path": file_path}

    response = compat_app.test_client().get("/files/nested/example.txt")

    assert response.status_code == 200
    assert response.get_json() == {"file_path": "nested/example.txt"}


def test_normalize_response_supports_body_headers_tuple():
    response = normalize_response(("ok", {"X-Test": "yes"}))

    assert response.status_code == 200
    assert response.headers["X-Test"] == "yes"
    assert response.body == b"ok"


def test_run_maybe_async_offloads_sync_handlers_without_losing_context():
    import asyncio
    import threading
    import time

    loop_thread_id = threading.get_ident()

    def blocking_handler():
        assert threading.get_ident() != loop_thread_id
        time.sleep(0.05)
        return request.path

    async def ticker():
        await asyncio.sleep(0.01)
        return "tick"

    async def exercise():
        return await asyncio.gather(
            run_maybe_async(blocking_handler),
            ticker(),
        )

    compat_app = CompatApp()
    with compat_app.test_request_context("/threadpool-check"):
        result, tick = asyncio.run(exercise())

    assert result == "/threadpool-check"
    assert tick == "tick"


def test_wechat_qr_poll_marks_bind_hint_and_schedules_managed_restart(monkeypatch):
    from vibe import runtime

    class _Auth:
        async def poll_status(self, session_key):
            assert session_key == "qr-session"
            return {
                "status": "confirmed",
                "bot_token": "wechat-token",
                "base_url": "https://wechat.example.com",
                "user_id": "wx-user",
            }

    bound_users = []
    restart_calls = []

    runtime.ensure_config()
    monkeypatch.setattr(ui_server, "_get_wechat_auth", lambda: _Auth())
    monkeypatch.setattr(
        ui_server,
        "_schedule_wechat_qr_login_restart",
        lambda: restart_calls.append(True) or {"job_id": "restart-1"},
    )
    monkeypatch.setattr(
        "vibe.api.auto_bind_wechat_user",
        lambda user_id: bound_users.append(user_id)
        or {"ok": True, "already_bound": False, "is_admin": True, "pending_bind_menu_hint": True},
    )

    client = app.test_client()
    response = client.post(
        "/api/wechat/qr_login/poll",
        json={"session_key": "qr-session"},
        headers=csrf_headers(client),
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "confirmed"
    assert bound_users == ["wx-user"]
    assert restart_calls == [True]


def test_web_push_subscription_routes_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()

    client = app.test_client()
    headers = csrf_headers(client)
    subscription = {
        "endpoint": "https://push.example.test/sub/1",
        "keys": {
            "p256dh": "p256dh-key",
            "auth": "auth-secret",
        },
    }

    created = client.post(
        "/api/web-push/subscriptions",
        json={"subscription": subscription, "device_label": "iPhone"},
        headers=headers,
    )
    assert created.status_code == 200
    created_body = created.get_json()
    assert created_body["ok"] is True
    assert created_body["subscription"]["endpoint"] == subscription["endpoint"]
    assert created_body["subscription"]["enabled"] is True
    assert created_body["subscription"]["device_label"] == "iPhone"

    status = client.get("/api/web-push/status")
    assert status.status_code == 200
    status_body = status.get_json()
    assert status_body["ok"] is True
    assert status_body["configured"] is True
    assert status_body["public_key"]
    assert status_body["subscription_count"] == 1

    removed = client.delete(
        "/api/web-push/subscriptions",
        json={"endpoint": subscription["endpoint"]},
        headers=headers,
    )
    assert removed.status_code == 200
    assert removed.get_json() == {"ok": True, "disabled": True}

    status_after = client.get("/api/web-push/status")
    assert status_after.get_json()["subscription_count"] == 0


def test_web_push_test_route_sends_to_enabled_subscriptions(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()

    sends = []
    monkeypatch.setattr(
        "core.web_push.send_web_push",
        lambda *, subscription, payload: sends.append((subscription, payload)),
    )

    client = app.test_client()
    headers = csrf_headers(client)
    subscription = {
        "endpoint": "https://push.example.test/sub/1",
        "keys": {
            "p256dh": "p256dh-key",
            "auth": "auth-secret",
        },
    }

    empty = client.post("/api/web-push/test", json={}, headers=headers)
    assert empty.status_code == 404
    assert empty.get_json()["error"] == "no_subscription"

    client.post("/api/web-push/subscriptions", json={"subscription": subscription}, headers=headers)
    sent = client.post(
        "/api/web-push/test",
        json={"title": "Hello", "body": "World", "url": "/inbox"},
        headers=headers,
    )

    assert sent.status_code == 200
    assert sent.get_json() == {"ok": True, "sent": 1, "failed": 0}
    assert sends[0][0]["endpoint"] == subscription["endpoint"]
    assert sends[0][1]["title"] == "Hello"


def test_sessions_create_stores_web_push_owner(monkeypatch, tmp_path):
    from storage.db import create_sqlite_engine
    from storage.projects_service import create_project

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    monkeypatch.setattr(ui_server, "_web_push_user_key", lambda: "remote:user-a")

    engine = create_sqlite_engine()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    with engine.begin() as conn:
        project = create_project(conn, str(project_dir), display_name="Project")

    client = app.test_client()
    response = client.post(
        "/api/sessions",
        json={"project_id": project["id"], "metadata": {"client": "test"}},
        headers=csrf_headers(client),
    )

    assert response.status_code == 201
    metadata = response.get_json()["metadata"]
    assert metadata["client"] == "test"
    assert metadata["_web_push_user_key"] == "remote:user-a"
