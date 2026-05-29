import pytest

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
