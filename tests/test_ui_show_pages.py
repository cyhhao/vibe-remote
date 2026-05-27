import asyncio

from config import paths
from core.show_pages import ShowPageStore, ensure_show_page_dir
from core.show_runtime import ShowRuntimeManager, set_show_runtime_manager_for_tests
from tests.test_ui_remote_access_auth import _remote_peer, _save_config
from vibe.ui_server import app


class _FakeShowRuntimeManager:
    def __init__(self, *, body: bytes = b"Runtime Show Page", fail: bool = False):
        self.body = body
        self.fail = fail
        self.calls = []

    async def request(self, method, path, *, headers=None, body=None):
        import httpx

        self.calls.append((method, path, headers, body))
        if self.fail:
            raise RuntimeError("runtime unavailable")
        return httpx.Response(200, content=self.body, headers={"content-type": "text/html; charset=utf-8"})


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
        response = app.test_client().get("/show/ses123/", base_url="http://127.0.0.1:5123")
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert b"Runtime Page" in response.content
    assert manager.calls[0][0] == "GET"
    assert manager.calls[0][1] == "/sessions/ses123/app/"


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


def test_show_runtime_manager_reports_missing_command(tmp_path):
    manager = ShowRuntimeManager(
        command="definitely-missing-avibe-show-runtime",
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
    )

    result = asyncio.run(manager.ensure())

    assert result.available is False
    assert result.reason == "runtime_command_missing"


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
