import asyncio

from config import paths
from core.show_pages import ShowPageStore, ensure_show_page_dir
from core.show_runtime import ShowRuntimeManager, set_show_runtime_manager_for_tests
from tests.test_ui_remote_access_auth import _remote_peer, _save_config
from vibe import remote_access
from vibe.ui_server import app


class _FakeShowRuntimeManager:
    def __init__(
        self,
        *,
        body: bytes = b"Runtime Show Page",
        fail: bool = False,
        status_code: int = 200,
        extra_headers: dict[str, str] | None = None,
    ):
        self.body = body
        self.fail = fail
        self.status_code = status_code
        self.extra_headers = extra_headers or {}
        self.calls = []
        self.websocket_paths = []
        self.stopped = False

    async def request(self, method, path, *, headers=None, body=None):
        import httpx

        self.calls.append((method, path, headers, body))
        if self.fail:
            raise RuntimeError("runtime unavailable")
        headers = {
            "content-type": "text/html; charset=utf-8",
            "set-cookie": "__Host-vibe_remote_session=attacker",
            "x-runtime-private-header": "secret",
        } | self.extra_headers
        return httpx.Response(self.status_code, content=self.body, headers=headers)

    async def websocket_url(self, path):
        self.websocket_paths.append(path)
        return f"ws://127.0.0.1:1{path}"

    def stop(self):
        self.stopped = True


def _create_show_page(session_id: str, visibility: str) -> str | None:
    page_dir = ensure_show_page_dir(session_id)
    (page_dir / "index.html").write_text("<!doctype html><title>Show</title><h1>Show Page</h1>", encoding="utf-8")
    (page_dir / "app.js").write_text("window.showPage = true;", encoding="utf-8")
    store = ShowPageStore()
    try:
        page = store.update_visibility(session_id, visibility)
        return page.share_id
    finally:
        store.close()


def test_private_show_page_requires_remote_login(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")

    response = app.test_client().get(
        "/show/ses123/",
        base_url="https://alex.avibe.bot",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://backend.test/oauth/authorize?")


def test_private_show_page_serves_locally(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")

    response = app.test_client().get("/show/ses123/", base_url="http://127.0.0.1:5123")

    assert response.status_code == 200
    assert b"Show Page" in response.content


def test_private_show_page_uses_runtime_when_available(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(body=b"<h1>Runtime Page</h1>")
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/",
            base_url="http://127.0.0.1:5123",
            headers={
                "Accept": "text/html",
                "Accept-Encoding": "br, zstd",
                "Authorization": "Bearer secret",
                "Cookie": "__Host-vibe_remote_session=secret",
                "X-Vibe-CSRF-Token": "secret",
            },
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert b"Runtime Page" in response.content
    assert "__Host-vibe_remote_session=attacker" not in "\n".join(response.headers.getlist("set-cookie"))
    assert "x-runtime-private-header" not in response.headers
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert manager.calls[0][0] == "GET"
    assert manager.calls[0][1] == "/sessions/ses123/app/"
    assert manager.calls[0][2]["accept"] == "text/html"
    assert "accept-encoding" not in manager.calls[0][2]
    assert "authorization" not in manager.calls[0][2]
    assert "cookie" not in manager.calls[0][2]
    assert "x-vibe-csrf-token" not in manager.calls[0][2]


def test_private_show_page_falls_back_to_static_when_runtime_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    set_show_runtime_manager_for_tests(_FakeShowRuntimeManager(fail=True))
    try:
        response = app.test_client().get("/show/ses123/", base_url="http://127.0.0.1:5123")
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert b"Show Page" in response.content


def test_private_show_page_proxies_runtime_api_methods(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(body=b'{"ok":true}', extra_headers={"content-type": "application/json"})
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().post(
            "/show/ses123/api/health",
            base_url="http://127.0.0.1:5123",
            headers={
                "Origin": "http://127.0.0.1:5123",
                "Content-Type": "application/json",
                "Cookie": "__Host-vibe_remote_session=secret",
            },
            content=b'{"ping":true}',
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert response.content == b'{"ok":true}'
    assert manager.calls[0][0] == "POST"
    assert manager.calls[0][1] == "/sessions/ses123/app/api/health"
    assert manager.calls[0][2]["content-type"] == "application/json"
    assert "cookie" not in manager.calls[0][2]
    assert manager.calls[0][3] == b'{"ping":true}'


def test_private_show_page_api_mutation_rejects_missing_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(body=b'{"ok":true}', extra_headers={"content-type": "application/json"})
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().post(
            "/show/ses123/api/health",
            base_url="http://127.0.0.1:5123",
            headers={"Content-Type": "application/json"},
            content=b'{"ping":true}',
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 403
    assert response.get_json()["message"] == "Forbidden: missing origin header"
    assert manager.calls == []


def test_private_show_page_api_mutation_rejects_cross_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(body=b'{"ok":true}', extra_headers={"content-type": "application/json"})
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().post(
            "/show/ses123/api/health",
            base_url="http://127.0.0.1:5123",
            headers={
                "Origin": "http://evil.example",
                "Content-Type": "application/json",
            },
            content=b'{"ping":true}',
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 403
    assert response.get_json()["message"] == "Forbidden: invalid origin"
    assert manager.calls == []


def test_private_show_page_preserves_runtime_redirect_location(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b"",
        status_code=302,
        extra_headers={"location": "/sessions/ses123/app/foo/"},
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/foo",
            base_url="http://127.0.0.1:5123",
            follow_redirects=False,
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 302
    assert response.headers["location"] == "/show/ses123/foo/"
    assert "__Host-vibe_remote_session=attacker" not in "\n".join(response.headers.getlist("set-cookie"))


def test_private_show_page_rewrites_absolute_runtime_redirect_location(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b"",
        status_code=302,
        extra_headers={"location": "http://127.0.0.1:49321/sessions/ses123/app/foo/?x=1#top"},
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/foo",
            base_url="http://127.0.0.1:5123",
            follow_redirects=False,
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 302
    assert response.headers["location"] == "/show/ses123/foo/?x=1#top"


def test_show_runtime_manager_reports_missing_command(tmp_path):
    manager = ShowRuntimeManager(
        command="definitely-missing-avibe-show-runtime",
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
    )

    result = asyncio.run(manager.ensure())

    assert result.available is False
    assert result.reason == "runtime_command_missing"


def test_show_runtime_manager_uses_managed_runtime_bin(tmp_path):
    runtime_dir = tmp_path / "runtime with spaces"
    bin_path = runtime_dir / "package" / "node_modules" / ".bin" / "avibe-show-runtime"
    bin_path.parent.mkdir(parents=True)
    bin_path.write_text("#!/bin/sh\n", encoding="utf-8")
    bin_path.chmod(0o755)

    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        auto_install=False,
    )

    assert asyncio.run(manager._resolve_managed_command()) == [str(bin_path)]


def test_show_runtime_manager_can_disable_auto_install(tmp_path):
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
        auto_install=False,
    )

    assert asyncio.run(manager._resolve_managed_command()) is None
    assert manager._install_reason == "runtime_command_missing"


def test_show_runtime_manager_installs_without_blocking_event_loop(monkeypatch, tmp_path):
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
    )

    def fake_install():
        bin_path = manager._managed_bin_path()
        bin_path.parent.mkdir(parents=True)
        bin_path.write_text("#!/bin/sh\n", encoding="utf-8")
        bin_path.chmod(0o755)
        return [str(bin_path)]

    monkeypatch.setattr(manager, "_install_managed_runtime", fake_install)
    calls = []

    async def fake_to_thread(func):
        calls.append(func)
        return func()

    monkeypatch.setattr("core.show_runtime.asyncio.to_thread", fake_to_thread)

    assert asyncio.run(manager._resolve_managed_command()) == [str(manager._managed_bin_path())]
    assert calls == [fake_install]


def test_show_runtime_shutdown_stops_manager():
    from vibe.ui_server import stop_show_runtime_on_shutdown

    manager = _FakeShowRuntimeManager()
    set_show_runtime_manager_for_tests(manager)
    try:
        stop_show_runtime_on_shutdown()
    finally:
        set_show_runtime_manager_for_tests(None)

    assert manager.stopped is True


def test_private_show_page_hmr_websocket_requires_private_page(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "offline")

    try:
        with app.test_client().websocket_connect(
            "/show/ses123/__vite_hmr",
            headers={"host": "127.0.0.1:5123"},
            subprotocols=["vite-hmr"],
        ):
            raise AssertionError("websocket should not connect")
    except Exception as exc:
        assert getattr(exc, "code", None) == 1008


def test_private_show_page_hmr_websocket_requires_remote_session(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")

    try:
        with app.test_client().websocket_connect(
            "wss://alex.avibe.bot/show/ses123/__vite_hmr",
            headers={"host": "alex.avibe.bot"},
            subprotocols=["vite-hmr"],
        ):
            raise AssertionError("websocket should not connect")
    except Exception as exc:
        assert getattr(exc, "code", None) == 1008


def test_private_show_page_hmr_websocket_accepts_remote_session(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager()
    set_show_runtime_manager_for_tests(manager)
    client = app.test_client()
    client.set_cookie(
        remote_access.SESSION_COOKIE_NAME,
        remote_access.make_session_cookie(config, "alex@example.com", "user-1"),
        domain="alex.avibe.bot",
    )
    try:
        with client.websocket_connect(
            "wss://alex.avibe.bot/show/ses123/__vite_hmr",
            headers={"host": "alex.avibe.bot"},
            subprotocols=["vite-hmr"],
        ) as websocket:
            websocket.receive_text()
    except Exception as exc:
        assert getattr(exc, "code", None) == 1011
    finally:
        set_show_runtime_manager_for_tests(None)


def test_private_show_page_redirects_without_trailing_slash(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")

    response = app.test_client().get("/show/ses123", base_url="http://127.0.0.1:5123", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"] == "/show/ses123/"

    followed = app.test_client().get("/show/ses123", base_url="http://127.0.0.1:5123", follow_redirects=True)
    assert followed.status_code == 200
    assert b"Show Page" in followed.content


def test_public_show_page_skips_remote_login_but_requires_public_host(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")

    response = app.test_client().get(
        f"/p/{share_id}/",
        base_url="https://alex.avibe.bot",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert b"Show Page" in response.content

    mismatch = app.test_client().get(
        f"/p/{share_id}/",
        base_url="https://evil.example",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert mismatch.status_code == 503
    assert mismatch.get_json()["error"] == "remote_access_host_mismatch"


def test_public_show_page_redirects_without_trailing_slash(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")

    response = app.test_client().get(f"/p/{share_id}", base_url="http://127.0.0.1:5123", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"] == f"/p/{share_id}/"

    followed = app.test_client().get(f"/p/{share_id}", base_url="http://127.0.0.1:5123", follow_redirects=True)
    assert followed.status_code == 200
    assert b"Show Page" in followed.content


def test_public_and_private_paths_are_canonical_by_visibility(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")

    private_response = app.test_client().get("/show/ses123/", base_url="http://127.0.0.1:5123")
    assert private_response.status_code == 404

    store = ShowPageStore()
    try:
        store.update_visibility("ses123", "private")
    finally:
        store.close()

    public_response = app.test_client().get(f"/p/{share_id}/", base_url="http://127.0.0.1:5123")
    assert public_response.status_code == 404


def test_rotated_public_share_url_stops_working(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    old_share_id = _create_show_page("ses123", "public")

    store = ShowPageStore()
    try:
        page, _ = store.rotate_share("ses123")
    finally:
        store.close()

    old_response = app.test_client().get(f"/p/{old_share_id}/", base_url="http://127.0.0.1:5123")
    new_response = app.test_client().get(f"/p/{page.share_id}/", base_url="http://127.0.0.1:5123")

    assert old_response.status_code == 404
    assert new_response.status_code == 200


def test_offline_show_page_returns_explanatory_page(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")

    store = ShowPageStore()
    try:
        store.update_visibility("ses123", "offline")
    finally:
        store.close()

    response = app.test_client().get(f"/p/{share_id}/", base_url="http://127.0.0.1:5123")

    assert response.status_code == 401
    assert b"offline" in response.content
    assert b"deleted" not in response.content.lower()


def test_show_page_rejects_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    (tmp_path / "secret.txt").write_text("secret", encoding="utf-8")

    response = app.test_client().get(f"/p/{share_id}/../secret.txt", base_url="http://127.0.0.1:5123")

    assert response.status_code == 404
    assert b"secret" not in response.content


def test_show_page_serves_assets_with_strict_headers(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")

    response = app.test_client().get(f"/p/{share_id}/app.js", base_url="http://127.0.0.1:5123")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert b"window.showPage" in response.content
