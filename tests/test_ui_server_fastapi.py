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


def test_harness_routes_page_filter_and_return_counts(monkeypatch, tmp_path):
    from storage.background import SQLiteBackgroundTaskStore

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    store = SQLiteBackgroundTaskStore()
    try:
        for index in range(5):
            store.upsert_scheduled_task(
                {
                    "id": f"task-{index}",
                    "name": f"Task {index}",
                    "prompt": "run it",
                    "schedule_type": "cron",
                    "cron": "0 * * * *",
                    "enabled": index < 3,
                    "created_at": f"2026-06-04T00:0{index}:00+00:00",
                    "updated_at": f"2026-06-04T00:0{index}:00+00:00",
                }
            )
        for index in range(6):
            store.upsert_watch(
                {
                    "id": f"watch-{index}",
                    "name": f"Deploy watch {index}",
                    "shell_command": f"tail deploy-{index}.log",
                    "enabled": index == 0,
                    "created_at": f"2026-06-04T00:1{index}:00+00:00",
                    "updated_at": f"2026-06-04T00:1{index}:00+00:00",
                }
            )
        for index, status in enumerate(["pending", "processing", "completed", "failed"]):
            store.enqueue_run(
                {
                    "id": f"run-{index}",
                    "request_type": "watch",
                    "status": status,
                    "message": "deploy status",
                    "created_at": f"2026-06-04T00:2{index}:00+00:00",
                    "updated_at": f"2026-06-04T00:2{index}:00+00:00",
                }
            )
    finally:
        store.close()

    client = app.test_client()
    legacy_tasks = client.get("/api/harness/tasks").get_json()
    legacy_watches = client.get("/api/harness/watches").get_json()
    tasks = client.get("/api/harness/tasks?status=enabled&page=1&limit=2").get_json()
    watches = client.get("/api/harness/watches?status=disabled&query=deploy&page=1&limit=2").get_json()
    runs = client.get("/api/harness/runs?page=1&limit=2").get_json()
    counts = client.get("/api/harness/counts").get_json()

    assert len(legacy_tasks["tasks"]) == 5
    assert legacy_tasks["has_more"] is False
    assert len(legacy_watches["watches"]) == 6
    assert legacy_watches["has_more"] is False
    assert [item["id"] for item in tasks["tasks"]] == ["task-2", "task-1"]
    assert tasks["counts"] == {"all": 5, "enabled": 3, "disabled": 2}
    assert tasks["total"] == 3
    assert tasks["has_more"] is True
    assert [item["id"] for item in watches["watches"]] == ["watch-5", "watch-4"]
    assert watches["counts"] == {"all": 6, "enabled": 1, "disabled": 5}
    assert watches["total"] == 5
    assert watches["has_more"] is True
    assert [item["id"] for item in runs["runs"]] == ["run-3", "run-2"]
    assert runs["total"] == 4
    assert runs["counts"]["queued"] == 1
    assert runs["counts"]["running"] == 1
    assert runs["counts"]["succeeded"] == 1
    assert runs["counts"]["failed"] == 1
    assert counts["tasks"]["all"] == 5
    assert counts["watches"]["disabled"] == 5
    assert counts["runs"]["all"] == 4


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
        json={"subscription": subscription, "device_label": "iPhone", "device_id": "device-1"},
        headers=headers,
    )
    assert created.status_code == 200
    created_body = created.get_json()
    assert created_body["ok"] is True
    assert created_body["subscription"]["endpoint"] == subscription["endpoint"]
    assert created_body["subscription"]["enabled"] is True
    assert created_body["subscription"]["device_label"] == "iPhone"
    assert created_body["subscription"]["device_id"] == "device-1"

    status = client.post("/api/web-push/status", json={"endpoint": subscription["endpoint"]}, headers=headers)
    assert status.status_code == 200
    status_body = status.get_json()
    assert status_body["ok"] is True
    assert status_body["configured"] is True
    assert status_body["public_key"]
    assert status_body["subscription_count"] == 1
    assert status_body["current_subscription_enabled"] is True

    removed = client.delete(
        "/api/web-push/subscriptions",
        json={"endpoint": subscription["endpoint"]},
        headers=headers,
    )
    assert removed.status_code == 200
    assert removed.get_json() == {"ok": True, "disabled": True}

    status_after = client.post("/api/web-push/status", json={"endpoint": subscription["endpoint"]}, headers=headers)
    assert status_after.get_json()["subscription_count"] == 0
    assert status_after.get_json()["current_subscription_enabled"] is False


def test_web_push_status_sync_disables_previous_endpoint_for_same_device(monkeypatch, tmp_path):
    from storage import web_push_service
    from storage.db import create_sqlite_engine

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()

    client = app.test_client()
    headers = csrf_headers(client)
    old_subscription = {
        "endpoint": "https://push.example.test/sub/old",
        "keys": {"p256dh": "old-key", "auth": "old-auth"},
    }
    new_subscription = {
        "endpoint": "https://push.example.test/sub/new",
        "keys": {"p256dh": "new-key", "auth": "new-auth"},
    }

    created = client.post(
        "/api/web-push/subscriptions",
        json={"subscription": old_subscription, "device_id": "device-1"},
        headers=headers,
    )
    assert created.status_code == 200
    created = client.post(
        "/api/web-push/subscriptions",
        json={"subscription": new_subscription},
        headers=headers,
    )
    assert created.status_code == 200

    status = client.post(
        "/api/web-push/status",
        json={
            "endpoint": new_subscription["endpoint"],
            "subscription": new_subscription,
            "device_id": "device-1",
        },
        headers=headers,
    )

    assert status.status_code == 200
    assert status.get_json()["current_subscription_enabled"] is True
    assert status.get_json()["subscription_count"] == 1
    engine = create_sqlite_engine()
    with engine.connect() as conn:
        assert web_push_service.get_enabled_by_endpoint(
            conn,
            endpoint=old_subscription["endpoint"],
            user_key="local",
        ) is None
        assert web_push_service.get_enabled_by_endpoint(
            conn,
            endpoint=new_subscription["endpoint"],
            user_key="local",
        ) is not None


def test_web_push_status_sync_disables_client_known_previous_endpoint(monkeypatch, tmp_path):
    from storage import web_push_service
    from storage.db import create_sqlite_engine

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()

    client = app.test_client()
    headers = csrf_headers(client)
    previous_subscription = {
        "endpoint": "https://push.example.test/sub/previous",
        "keys": {"p256dh": "previous-key", "auth": "previous-auth"},
    }
    current_subscription = {
        "endpoint": "https://push.example.test/sub/current",
        "keys": {"p256dh": "current-key", "auth": "current-auth"},
    }
    other_subscription = {
        "endpoint": "https://push.example.test/sub/other",
        "keys": {"p256dh": "other-key", "auth": "other-auth"},
    }

    for subscription in [previous_subscription, current_subscription, other_subscription]:
        created = client.post(
            "/api/web-push/subscriptions",
            json={"subscription": subscription},
            headers=headers,
        )
        assert created.status_code == 200

    status = client.post(
        "/api/web-push/status",
        json={
            "endpoint": current_subscription["endpoint"],
            "subscription": current_subscription,
            "device_id": "device-1",
            "previous_endpoints": [previous_subscription["endpoint"]],
        },
        headers=headers,
    )

    assert status.status_code == 200
    assert status.get_json()["current_subscription_enabled"] is True
    assert status.get_json()["subscription_count"] == 2
    engine = create_sqlite_engine()
    with engine.connect() as conn:
        assert web_push_service.get_enabled_by_endpoint(
            conn,
            endpoint=previous_subscription["endpoint"],
            user_key="local",
        ) is None
        assert web_push_service.get_enabled_by_endpoint(
            conn,
            endpoint=current_subscription["endpoint"],
            user_key="local",
        ) is not None
        assert web_push_service.get_enabled_by_endpoint(
            conn,
            endpoint=other_subscription["endpoint"],
            user_key="local",
        ) is not None


def test_web_push_status_sync_does_not_reenable_disabled_endpoint(monkeypatch, tmp_path):
    from storage import web_push_service
    from storage.db import create_sqlite_engine

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()

    client = app.test_client()
    headers = csrf_headers(client)
    subscription = {
        "endpoint": "https://push.example.test/sub/dead",
        "keys": {"p256dh": "dead-key", "auth": "dead-auth"},
    }
    created = client.post(
        "/api/web-push/subscriptions",
        json={"subscription": subscription, "device_id": "device-1"},
        headers=headers,
    )
    assert created.status_code == 200

    engine = create_sqlite_engine()
    with engine.begin() as conn:
        web_push_service.mark_send_failure(conn, endpoint=subscription["endpoint"], disable=True)

    status = client.post(
        "/api/web-push/status",
        json={
            "endpoint": subscription["endpoint"],
            "subscription": subscription,
            "device_id": "device-1",
        },
        headers=headers,
    )

    assert status.status_code == 200
    assert status.get_json()["subscription_count"] == 0
    assert status.get_json()["current_subscription_enabled"] is False
    with engine.connect() as conn:
        assert web_push_service.get_enabled_by_endpoint(
            conn,
            endpoint=subscription["endpoint"],
            user_key="local",
        ) is None


def test_web_push_unsubscribe_is_scoped_to_current_user(monkeypatch, tmp_path):
    from storage import web_push_service
    from storage.db import create_sqlite_engine

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
    monkeypatch.setattr(ui_server, "_web_push_user_key", lambda: "remote:user-a")

    endpoint = "https://push.example.test/sub/other"
    engine = create_sqlite_engine()
    with engine.begin() as conn:
        web_push_service.upsert_subscription(
            conn,
            user_key="remote:user-b",
            payload={
                "endpoint": endpoint,
                "keys": {
                    "p256dh": "p256dh-key",
                    "auth": "auth-secret",
                },
            },
        )

    client = app.test_client()
    removed = client.delete(
        "/api/web-push/subscriptions",
        json={"endpoint": endpoint},
        headers=csrf_headers(client),
    )

    assert removed.status_code == 200
    assert removed.get_json() == {"ok": True, "disabled": False}
    with engine.connect() as conn:
        assert web_push_service.count_enabled(conn, user_key="remote:user-b") == 1


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

    missing_endpoint = client.post("/api/web-push/test", json={}, headers=headers)
    assert missing_endpoint.status_code == 400
    assert missing_endpoint.get_json()["error"] == "endpoint_required"

    empty = client.post(
        "/api/web-push/test",
        json={"endpoint": subscription["endpoint"]},
        headers=headers,
    )
    assert empty.status_code == 404
    assert empty.get_json()["error"] == "no_subscription"

    client.post("/api/web-push/subscriptions", json={"subscription": subscription}, headers=headers)
    sent = client.post(
        "/api/web-push/test",
        json={"title": "Hello", "body": "World", "url": "/inbox", "endpoint": subscription["endpoint"]},
        headers=headers,
    )

    assert sent.status_code == 200
    assert sent.get_json() == {"ok": True, "sent": 1, "failed": 0}
    assert sends[0][0]["endpoint"] == subscription["endpoint"]
    assert sends[0][1]["title"] == "Hello"


def test_web_push_test_route_targets_current_endpoint_only(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()

    sends = []
    monkeypatch.setattr(
        "core.web_push.send_web_push",
        lambda *, subscription, payload: sends.append((subscription, payload)),
    )

    client = app.test_client()
    headers = csrf_headers(client)
    subscriptions = [
        {
            "endpoint": "https://push.example.test/sub/desktop",
            "keys": {"p256dh": "desktop-key", "auth": "desktop-auth"},
        },
        {
            "endpoint": "https://push.example.test/sub/mobile",
            "keys": {"p256dh": "mobile-key", "auth": "mobile-auth"},
        },
    ]
    for subscription in subscriptions:
        client.post("/api/web-push/subscriptions", json={"subscription": subscription}, headers=headers)

    sent = client.post(
        "/api/web-push/test",
        json={
            "title": "Hello",
            "body": "World",
            "url": "/inbox",
            "endpoint": subscriptions[0]["endpoint"],
        },
        headers=headers,
    )

    assert sent.status_code == 200
    assert sent.get_json() == {"ok": True, "sent": 1, "failed": 0}
    assert [send[0]["endpoint"] for send in sends] == [subscriptions[0]["endpoint"]]


def test_sessions_create_preserves_metadata_without_web_push_owner(monkeypatch, tmp_path):
    from storage.db import create_sqlite_engine
    from storage.projects_service import create_project

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    ensure_sqlite_state()
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
    assert "_web_push_user_key" not in metadata
