import asyncio
import hashlib
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from config import paths
from core.show_pages import (
    SHOW_RUNTIME_RECOVERY_LOADING_DELAY_SECONDS,
    ShowPageStore,
    ensure_show_page_dir,
    show_cli_event_token,
)
from core.show_runtime import ShowRuntimeManager, _runtime_platform_tag, _safe_extract_tar, set_show_runtime_manager_for_tests
from tests.test_ui_remote_access_auth import _mock_interface, _remote_peer, _save_config
from vibe import remote_access
from vibe.ui_server import app


@pytest.fixture(autouse=True)
def _clear_show_runtime_public_dep_registry():
    from vibe import ui_server

    ui_server._SHOW_RUNTIME_PUBLIC_DEP_REGISTRY.clear()


class _FakeShowRuntimeManager:
    def __init__(
        self,
        *,
        body: bytes = b"Runtime Show Page",
        fail: bool = False,
        status_code: int = 200,
        extra_headers: dict[str, str] | None = None,
        headers_by_path: dict[str, dict[str, str]] | None = None,
        bodies_by_path: dict[str, bytes] | None = None,
    ):
        self.body = body
        self.fail = fail
        self.status_code = status_code
        self.extra_headers = extra_headers or {}
        self.headers_by_path = headers_by_path or {}
        self.bodies_by_path = bodies_by_path or {}
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
        } | self.extra_headers | self.headers_by_path.get(path, {})
        return httpx.Response(self.status_code, content=self.bodies_by_path.get(path, self.body), headers=headers)

    async def websocket_url(self, path):
        self.websocket_paths.append(path)
        return f"ws://127.0.0.1:1{path}"

    def stop(self):
        self.stopped = True


@pytest.fixture(autouse=True)
def _show_runtime_node_version(monkeypatch):
    monkeypatch.setattr("core.show_runtime._node_version", lambda node: (22, 16, 0))


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


def _create_agent_session(session_id: str) -> None:
    from storage import messages_service
    from storage.db import create_sqlite_engine
    from storage.importer import ensure_sqlite_state
    from storage.models import agent_sessions
    from storage.settings_service import upsert_scope

    ensure_sqlite_state()
    engine = create_sqlite_engine()
    now = messages_service._utc_now_iso()
    with engine.begin() as conn:
        scope_id = upsert_scope(conn, platform="avibe", scope_type="project", native_id="proj_show", now=now)
        conn.execute(
            agent_sessions.insert().values(
                id=session_id,
                scope_id=scope_id,
                agent_backend="codex",
                agent_variant="default",
                session_anchor="anchor_" + session_id,
                native_session_id="",
                status="active",
                metadata_json="{}",
                created_at=now,
                updated_at=now,
                last_active_at=now,
            )
        )


def _write_runtime_archive(tmp_path: Path, *, text: str = "#!/usr/bin/env node\n") -> Path:
    archive_root = tmp_path / f"archive-root-{hashlib.sha256(text.encode()).hexdigest()[:8]}"
    cli_path = archive_root / "node_modules" / "@avibe" / "show-runtime" / "dist" / "cli.js"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text(text, encoding="utf-8")
    archive_path = tmp_path / f"vibe-show-runtime-node-{hashlib.sha256(text.encode()).hexdigest()[:8]}.tgz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(archive_root / "node_modules", arcname="node_modules")
    return archive_path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_runtime_manifest(tmp_path: Path, archive_path: Path, *, sha256: str | None = None, size: int | None = None) -> Path:
    manifest_path = tmp_path / "show_runtime_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "runtime_version": "runtime-test-ref",
                "minimum_node": "^20.19.0 || >=22.12.0",
                "archives": {
                    _runtime_platform_tag(): {
                        "name": archive_path.name,
                        "url": archive_path.resolve().as_uri(),
                        "sha256": sha256 or _sha256(archive_path),
                        "size": archive_path.stat().st_size if size is None else size,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


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


def test_private_show_page_injects_runtime_event_config(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    monkeypatch.setattr("vibe.ui_server.show_event_write_token", lambda session_id: f"token-{session_id}")
    manager = _FakeShowRuntimeManager(
        body=b'<!doctype html><html><head></head><body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>',
        extra_headers={
            "cache-control": "public, max-age=3600",
            "etag": '"runtime-etag"',
            "expires": "Wed, 03 Jun 2026 09:00:00 GMT",
            "last-modified": "Wed, 03 Jun 2026 08:00:00 GMT",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get("/show/ses123/", base_url="http://127.0.0.1:5123")
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "globalThis.__AVIBE_SHOW__=Object.assign" in body
    assert '"sessionId":"ses123"' in body
    assert '"basePath":"/show/ses123/"' in body
    assert '"eventsPath":"/show/ses123/__show/events"' in body
    assert '"streamPath":"/show/ses123/__show/events?stream=1"' in body
    assert '"writeToken":"token-ses123"' in body
    assert body.index("globalThis.__AVIBE_SHOW__") < body.index('type="module"')
    assert "cookie" not in manager.calls[0][2]
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers
    assert "expires" not in response.headers
    assert "last-modified" not in response.headers


def test_private_show_page_does_not_inject_runtime_event_config_into_attachment_html(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    monkeypatch.setattr("vibe.ui_server.show_event_write_token", lambda session_id: f"token-{session_id}")
    body = b'<!doctype html><script type="module" src="/src/main.tsx"></script>'
    manager = _FakeShowRuntimeManager(
        body=body,
        extra_headers={
            "content-type": "text/html; charset=utf-8",
            "content-disposition": 'attachment; filename="report.html"',
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get("/show/ses123/report.html", base_url="http://127.0.0.1:5123")
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert response.content == body
    assert "globalThis.__AVIBE_SHOW__" not in response.content.decode("utf-8")
    assert response.headers["content-disposition"] == 'attachment; filename="report.html"'


def test_private_show_page_does_not_inject_runtime_event_config_into_ranged_html(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    monkeypatch.setattr("vibe.ui_server.show_event_write_token", lambda session_id: f"token-{session_id}")
    body = b'<!doctype html><script type="module" src="/src/main.tsx"></script>'
    manager = _FakeShowRuntimeManager(
        body=body,
        status_code=206,
        extra_headers={
            "content-type": "text/html; charset=utf-8",
            "content-range": "bytes 0-63/128",
            "accept-ranges": "bytes",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/",
            base_url="http://127.0.0.1:5123",
            headers={"Range": "bytes=0-63"},
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 206
    assert response.content == body
    assert "globalThis.__AVIBE_SHOW__" not in response.content.decode("utf-8")
    assert response.headers["content-range"] == "bytes 0-63/128"
    assert manager.calls[0][2]["range"] == "bytes=0-63"


def test_private_show_page_runtime_config_overrides_existing_client_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    monkeypatch.setattr("vibe.ui_server.show_event_write_token", lambda session_id: f"token-{session_id}")
    manager = _FakeShowRuntimeManager(
        body=b'<!doctype html><script>globalThis.__AVIBE_SHOW__={eventsPath:"runtime-only"}</script><script type="module" src="/src/main.tsx"></script>'
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get("/show/ses123/app/dashboard", base_url="http://127.0.0.1:5123")
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert '"eventsPath":"/show/ses123/__show/events"' in body
    assert '"writeToken":"token-ses123"' in body


def test_show_runtime_vendor_deps_are_cacheable(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b"export default {}",
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/.vite/deps/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 302
    assert response.headers["location"] == "/_show-runtime/deps/r8-d6d38251/react-dom_client.js"
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers


def test_show_runtime_public_dep_proxy_is_cacheable(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b"export default {}",
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        original = app.test_client().get(
            "/show/ses123/node_modules/.vite/deps/react.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        response = app.test_client().get(
            "/_show-runtime/deps/r8-d6d38251/react.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert original.status_code == 302
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "set-cookie" not in response.headers
    assert manager.calls[-1][1] == "/sessions/ses123/app/node_modules/.vite/deps/react.js?v=d6d38251"


def test_show_runtime_public_dep_proxy_compresses_large_javascript(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=(b"export const value = 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx';\n" * 200),
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
            "content-length": "12000",
            "etag": "source-etag",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        app.test_client().get(
            "/show/ses123/node_modules/.vite/deps/react.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        response = app.test_client().get(
            "/_show-runtime/deps/r8-d6d38251/react.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
            headers={"Accept-Encoding": "gzip"},
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["vary"] == "Accept-Encoding"
    assert response.headers["content-length"] != "12000"
    assert "etag" not in response.headers
    assert response.content.startswith(b"export const value")
    assert "accept-encoding" not in manager.calls[-1][2]


def test_show_runtime_public_dep_proxy_allows_scoped_package_names(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b"export default {}",
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        original = app.test_client().get(
            "/show/ses123/node_modules/.vite/deps/@avibe_show-ui_theme.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        response = app.test_client().get(
            "/_show-runtime/deps/r8-d6d38251/%40avibe_show-ui_theme.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert original.status_code == 302
    assert original.headers["location"] == "/_show-runtime/deps/r8-d6d38251/%40avibe_show-ui_theme.js"
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert manager.calls[-1][1] == "/sessions/ses123/app/node_modules/.vite/deps/@avibe_show-ui_theme.js?v=d6d38251"


def test_show_runtime_public_dep_proxy_registers_sibling_chunks(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b'import "./chunk-OUYO74D4.js?v=108951fb";\nexport default {}',
        bodies_by_path={
            "/sessions/ses123/app/node_modules/.vite/deps/chunk-OUYO74D4.js?v=108951fb": b"export const chunk = true;",
        },
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        original = app.test_client().get(
            "/show/ses123/node_modules/.vite/deps/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        dep = app.test_client().get(
            "/_show-runtime/deps/r8-d6d38251/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        chunk = app.test_client().get(
            "/_show-runtime/deps/r8-108951fb/chunk-OUYO74D4.js?v=108951fb",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert original.status_code == 302
    assert dep.status_code == 200
    assert b'import "/_show-runtime/deps/r8-108951fb/chunk-OUYO74D4.js?v=108951fb"' in dep.content
    assert dep.headers["cache-control"] == "no-store"
    assert chunk.status_code == 200
    assert chunk.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert manager.calls[-1][1] == "/sessions/ses123/app/node_modules/.vite/deps/chunk-OUYO74D4.js?v=108951fb"


def test_show_runtime_public_dep_proxy_rewrites_private_chunk_imports(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=(
            b'import "/show/ses123/@fs/home/avibe/.avibe/runtime/show-runtime/vite-cache/'
            b'abc123/ses123/deps/chunk-QA663NX4.js?v=108951fb";\nexport default {}'
        ),
        bodies_by_path={
            "/sessions/ses123/app/@fs/home/avibe/.avibe/runtime/show-runtime/vite-cache/"
            "abc123/ses123/deps/chunk-QA663NX4.js?v=108951fb": b"export const chunk = true;",
        },
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        original = app.test_client().get(
            "/show/ses123/node_modules/.vite/deps/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        dep = app.test_client().get(
            "/_show-runtime/deps/r8-d6d38251/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        chunk = app.test_client().get(
            "/_show-runtime/deps/r8-108951fb/chunk-QA663NX4.js?v=108951fb",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert original.status_code == 302
    assert dep.status_code == 200
    assert b'import "/_show-runtime/deps/r8-108951fb/chunk-QA663NX4.js?v=108951fb"' in dep.content
    assert b'"/show/ses123/@fs/' not in dep.content
    assert dep.headers["cache-control"] == "no-store"
    assert chunk.status_code == 200
    assert manager.calls[-1][1] == (
        "/sessions/ses123/app/@fs/home/avibe/.avibe/runtime/show-runtime/vite-cache/"
        "abc123/ses123/deps/chunk-QA663NX4.js?v=108951fb"
    )


def test_show_runtime_public_dep_proxy_refreshes_private_chunk_registry(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    _create_show_page("ses456", "private")
    manager = _FakeShowRuntimeManager(
        body=(
            b'import "/show/ses123/@fs/home/avibe/.avibe/runtime/show-runtime/vite-cache/'
            b'old/ses123/deps/chunk-QA663NX4.js?v=old111";'
        ),
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        first_original = app.test_client().get(
            "/show/ses123/node_modules/.vite/deps/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        first_dep = app.test_client().get(
            "/_show-runtime/deps/r8-d6d38251/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        manager.body = (
            b'import "/show/ses456/@fs/home/avibe/.avibe/runtime/show-runtime/vite-cache/'
            b'new/ses456/deps/chunk-QA663NX4.js?v=new222";'
        )
        second_original = app.test_client().get(
            "/show/ses456/node_modules/.vite/deps/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        second_dep = app.test_client().get(
            "/_show-runtime/deps/r8-d6d38251/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        chunk = app.test_client().get(
            "/_show-runtime/deps/r8-new222/chunk-QA663NX4.js?v=new222",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert first_original.status_code == 302
    assert first_dep.status_code == 200
    assert first_dep.headers["cache-control"] == "no-store"
    assert second_original.status_code == 302
    assert second_dep.status_code == 200
    assert b"/_show-runtime/deps/r8-new222/chunk-QA663NX4.js?v=new222" in second_dep.content
    assert second_dep.headers["cache-control"] == "no-store"
    assert chunk.status_code == 200
    assert manager.calls[-1][1] == (
        "/sessions/ses456/app/@fs/home/avibe/.avibe/runtime/show-runtime/vite-cache/"
        "new/ses456/deps/chunk-QA663NX4.js?v=new222"
    )


def test_show_runtime_public_dep_proxy_derives_chunk_paths_when_registry_is_cold(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b"export default {}",
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        original = app.test_client().get(
            "/show/ses123/node_modules/.vite/deps/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        chunk = app.test_client().get(
            "/_show-runtime/deps/r8-d6d38251/chunk-OUYO74D4.js?v=108951fb",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert original.status_code == 302
    assert chunk.status_code == 200
    assert manager.calls[-1][1] == "/sessions/ses123/app/node_modules/.vite/deps/chunk-OUYO74D4.js?v=108951fb"


def test_show_runtime_unversioned_vendor_deps_stay_session_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b"export default {}",
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/node_modules/.vite/deps/react.js",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
    assert "location" not in response.headers


def test_show_runtime_public_dep_rejects_unversioned_cache_key(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get(
        "/_show-runtime/deps/unversioned/react.js",
        base_url="http://127.0.0.1:5123",
    )

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_show_runtime_public_dep_rejects_unregistered_cache_key_without_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get(
        "/_show-runtime/deps/r8-d6d38251/react.js",
        base_url="http://127.0.0.1:5123",
    )

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"


def test_show_runtime_relocated_vendor_deps_are_cacheable(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b"export default {}",
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/@fs/Users/cyh/.vibe_remote/runtime/show-runtime/vite-cache/abc123/ses123/deps/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 302
    assert response.headers["location"] == "/_show-runtime/deps/r8-d6d38251/react-dom_client.js"
    assert "set-cookie" not in response.headers


def test_show_runtime_vendor_dep_errors_are_not_marked_immutable(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b"missing",
        status_code=404,
        extra_headers={
            "content-type": "text/plain",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/.vite/deps/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-cache"


def test_show_runtime_fs_dist_paths_are_not_marked_immutable(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b"export default {}",
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/@fs/Users/cyh/.vibe_remote/runtime/show-runtime/source/github/main/packages/ui/dist/theme.js",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_show_runtime_session_source_is_not_marked_immutable(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b"export default function App() {}",
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/src/App.tsx?t=1780732068677",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"


def test_show_runtime_source_rewrites_dep_imports_to_public_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b'import "/node_modules/.vite/deps/react.js?v=d6d38251";\nimport "./App.tsx";',
        bodies_by_path={
            "/sessions/ses123/app/node_modules/.vite/deps/react.js?v=d6d38251": b"export default {}",
        },
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
            "etag": "source-etag",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/src/main.tsx?t=1780732068677",
            base_url="http://127.0.0.1:5123",
        )
        public_dep = app.test_client().get(
            "/_show-runtime/deps/r8-d6d38251/react.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert b'"/_show-runtime/deps/r8-d6d38251/react.js?v=d6d38251"' in response.content
    assert b'"./App.tsx"' in response.content
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers
    assert public_dep.status_code == 200
    assert public_dep.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_show_runtime_source_rewrites_prefixed_fs_vite_cache_dep_imports(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    manager = _FakeShowRuntimeManager(
        body=(
            f'import "/p/{share_id}/@fs/home/avibe/.avibe/runtime/show-runtime/vite-cache/'
            'abc123/ses123/deps/react-dom_client.js?v=d6d38251";'
        ).encode("utf-8"),
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
            "etag": "source-etag",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            f"/p/{share_id}/src/main.tsx?t=1780732068677",
            base_url="http://127.0.0.1:5123",
        )
        public_dep = app.test_client().get(
            "/_show-runtime/deps/r8-d6d38251/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert b'"/_show-runtime/deps/r8-d6d38251/react-dom_client.js?v=d6d38251"' in response.content
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers
    assert public_dep.status_code == 200
    assert manager.calls[-1][1] == (
        "/sessions/ses123/app/@fs/home/avibe/.avibe/runtime/show-runtime/vite-cache/"
        "abc123/ses123/deps/react-dom_client.js?v=d6d38251"
    )


def test_public_show_runtime_source_rewrites_private_runtime_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    manager = _FakeShowRuntimeManager(
        body=(
            b'import "/show/ses123/@vite/client";\n'
            b'import "/show/ses123/@react-refresh";\n'
            b'const socketPath = "/show/ses123/__vite_hmr";\n'
        ),
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
            "etag": "source-etag",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            f"/p/{share_id}/src/App.tsx?t=1780732068677",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert b'"/_show-runtime/client-shim-r8.js"' in response.content
    assert b'"/_show-runtime/react-refresh-shim-r8.js"' in response.content
    assert f'"/p/{share_id}/@vite/client"'.encode() not in response.content
    assert f'"/p/{share_id}/@react-refresh"'.encode() not in response.content
    assert f'"/p/{share_id}/__vite_hmr"'.encode() in response.content
    assert b'"/show/ses123/' not in response.content
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers


def test_public_show_runtime_html_rewrites_private_runtime_client_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    manager = _FakeShowRuntimeManager(
        body=(
            b'<script type="module">import { injectIntoGlobalHook } from "/show/ses123/@react-refresh";</script>'
            b'<script type="module" src="/show/ses123/@vite/client"></script>'
            b'<script type="module" src="/show/ses123/src/main.tsx"></script>'
        ),
        extra_headers={
            "content-type": "text/html; charset=utf-8",
            "cache-control": "no-cache",
            "etag": "source-etag",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            f"/p/{share_id}/",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert b'"/_show-runtime/client-shim-r8.js"' in response.content
    assert b'"/_show-runtime/react-refresh-shim-r8.js"' in response.content
    assert f'"/p/{share_id}/src/main.tsx"'.encode() in response.content
    assert b'"/show/ses123/' not in response.content
    assert f'"/p/{share_id}/@vite/client"'.encode() not in response.content
    assert f'"/p/{share_id}/@react-refresh"'.encode() not in response.content
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers


def test_public_show_runtime_html_preloads_entry_direct_imports(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    package_path = "/@fs/home/avibe/.avibe/runtime/show-runtime/source/github/avibe-bot_vibe-show-runtime/main/packages/ui/dist/button.js"
    manager = _FakeShowRuntimeManager(
        body=b'<!doctype html><html><head></head><body><script type="module" src="./src/main.tsx"></script></body></html>',
        bodies_by_path={
            "/sessions/ses123/app/": b'<!doctype html><html><head></head><body><script type="module" src="./src/main.tsx"></script></body></html>',
            "/sessions/ses123/app/src/main.tsx": (
                b'import "/node_modules/.vite/deps/react-dom_client.js?v=d6d38251";'
                b'import "./styles.css";'
                b'import App from "./App.tsx";'
            ),
            "/sessions/ses123/app/src/App.tsx": f'import {{ Button }} from "{package_path}";'.encode("utf-8"),
            f"/sessions/ses123/app{package_path}": (
                b'import { motion } from "/node_modules/.vite/deps/motion_react.js?v=d8d245fb";'
                b'import { cn } from "./utils.js";'
            ),
        },
        headers_by_path={
            "/sessions/ses123/app/": {"content-type": "text/html; charset=utf-8"},
            "/sessions/ses123/app/src/main.tsx": {"content-type": "text/javascript"},
            "/sessions/ses123/app/src/App.tsx": {"content-type": "text/javascript"},
            f"/sessions/ses123/app{package_path}": {"content-type": "text/javascript"},
        },
        extra_headers={
            "cache-control": "no-cache",
            "etag": "source-etag",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            f"/p/{share_id}/",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert f'<link rel="modulepreload" href="/p/{share_id}/src/main.tsx">'.encode() in response.content
    assert b'<link rel="modulepreload" href="/_show-runtime/deps/r8-d6d38251/react-dom_client.js?v=d6d38251">' in response.content
    assert f'<link rel="modulepreload" href="/p/{share_id}/src/styles.css">'.encode() in response.content
    assert b'<link rel="modulepreload" href="/_show-runtime/deps/r8-m' in response.content
    assert b'/button.js">' in response.content
    assert b'<link rel="modulepreload" href="/_show-runtime/deps/r8-d8d245fb/motion_react.js?v=d8d245fb">' in response.content
    assert b"/utils.js" in response.content
    assert response.content.index(b'rel="modulepreload"') < response.content.index(b"</head>")
    assert package_path.encode() not in response.content
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers


def test_public_show_runtime_javascript_strips_inline_source_map(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    manager = _FakeShowRuntimeManager(
        body=b"export const ok = true;\n//# sourceMappingURL=data:application/json;base64,AAAA",
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
            "etag": "source-etag",
            "sourcemap": "app.js.map",
            "x-sourcemap": "app.js.map",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            f"/p/{share_id}/src/App.tsx?t=1780732068677",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert b"export const ok = true;" in response.content
    assert b"sourceMappingURL=data:" not in response.content
    assert response.headers["cache-control"] == "no-store"
    assert "etag" not in response.headers
    assert "sourcemap" not in response.headers
    assert "x-sourcemap" not in response.headers


def test_show_runtime_public_dep_strips_inline_source_map_without_disabling_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    manager = _FakeShowRuntimeManager(
        body=b"export const ok = true;\n//# sourceMappingURL=data:application/json;base64,AAAA",
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
            "etag": "source-etag",
            "sourcemap": "react-dom_client.js.map",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        source = app.test_client().get(
            f"/p/{share_id}/node_modules/.vite/deps/react-dom_client.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
        public_dep = app.test_client().get(
            "/_show-runtime/deps/r8-d6d38251/react-dom_client.js",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert source.status_code == 302
    assert public_dep.status_code == 200
    assert b"sourceMappingURL=data:" not in public_dep.content
    assert public_dep.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert "etag" not in public_dep.headers
    assert "sourcemap" not in public_dep.headers


def test_public_show_runtime_rewrites_package_dist_modules_to_public_deps(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    package_path = "/@fs/home/avibe/.avibe/runtime/show-runtime/source/github/avibe-bot_vibe-show-runtime/main/packages/ui/dist/button.js"
    manager = _FakeShowRuntimeManager(
        body=f'import "{package_path}";'.encode("utf-8"),
        bodies_by_path={
            f"/sessions/ses123/app{package_path}": b'export { Button } from "./utils.js";',
        },
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
            "etag": "source-etag",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            f"/p/{share_id}/src/App.tsx?t=1780732068677",
            base_url="http://127.0.0.1:5123",
        )
        public_path = response.content.decode("utf-8").split('"')[1]
        public_dep = app.test_client().get(
            public_path,
            base_url="http://127.0.0.1:5123",
        )
        sibling_path = public_dep.content.decode("utf-8").split('"')[1]
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert public_path.startswith("/_show-runtime/deps/r8-m")
    assert public_path.endswith("/button.js")
    assert package_path.encode() not in response.content
    assert public_dep.status_code == 200
    assert public_dep.headers["cache-control"] == "no-store"
    assert sibling_path.startswith(public_path.rsplit("/", 1)[0])
    assert sibling_path.endswith("/utils.js")
    assert manager.calls[-1][1] == f"/sessions/ses123/app{package_path}"


def test_show_runtime_public_package_dep_rewrites_private_client_imports(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    package_path = "/@fs/home/avibe/.avibe/runtime/show-runtime/source/github/avibe-bot_vibe-show-runtime/main/packages/ui/dist/button.js"
    manager = _FakeShowRuntimeManager(
        body=f'import "{package_path}";'.encode("utf-8"),
        bodies_by_path={
            f"/sessions/ses123/app{package_path}": (
                b'import { createHotContext as __vite__createHotContext } from "/show/ses123/@vite/client";'
                b'import * as RefreshRuntime from "/show/ses123/@react-refresh";'
                b"export const Button = true;"
            ),
        },
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
            "etag": "source-etag",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        source = app.test_client().get(
            f"/p/{share_id}/src/App.tsx?t=1780732068677",
            base_url="http://127.0.0.1:5123",
        )
        public_path = source.content.decode("utf-8").split('"')[1]
        public_dep = app.test_client().get(
            public_path,
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert source.status_code == 200
    assert public_dep.status_code == 200
    assert b'"/_show-runtime/client-shim-r8.js"' in public_dep.content
    assert b'"/_show-runtime/react-refresh-shim-r8.js"' in public_dep.content
    assert b'"/show/ses123/@vite/client"' not in public_dep.content
    assert b'"/show/ses123/@react-refresh"' not in public_dep.content
    assert public_dep.headers["cache-control"] == "no-store"


def test_show_runtime_public_package_dep_siblings_with_vite_version_use_shared_dep_namespace(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    package_path = "/@fs/home/avibe/.avibe/runtime/show-runtime/source/github/avibe-bot_vibe-show-runtime/main/packages/ui/dist/card.js"
    manager = _FakeShowRuntimeManager(
        body=f'import "{package_path}";'.encode("utf-8"),
        bodies_by_path={
            f"/sessions/ses123/app{package_path}": b'import "./react.js?v=d8d245fb";',
            "/sessions/ses123/app/@fs/home/avibe/.avibe/runtime/show-runtime/source/github/avibe-bot_vibe-show-runtime/main/packages/ui/dist/react.js": (
                b"export default {};"
            ),
        },
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        source = app.test_client().get(
            f"/p/{share_id}/src/App.tsx?t=1780732068677",
            base_url="http://127.0.0.1:5123",
        )
        package_public_path = source.content.decode("utf-8").split('"')[1]
        package_dep = app.test_client().get(
            package_public_path,
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert source.status_code == 200
    assert package_dep.status_code == 200
    assert b'"/_show-runtime/deps/r8-d8d245fb/react.js?v=d8d245fb"' in package_dep.content
    assert b"/_show-runtime/deps/r8-m" not in package_dep.content


def test_show_runtime_public_client_shims_are_cacheable():
    client = app.test_client()
    vite_client = client.get("/_show-runtime/client-shim-r8.js", base_url="http://127.0.0.1:5123")
    react_refresh = client.get("/_show-runtime/react-refresh-shim-r8.js", base_url="http://127.0.0.1:5123")

    assert vite_client.status_code == 200
    assert react_refresh.status_code == 200
    assert vite_client.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert react_refresh.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert b"export function createHotContext" in vite_client.content
    assert b"export function injectIntoGlobalHook" in react_refresh.content
    assert b"createSignatureFunctionForTransform" in react_refresh.content
    assert b"performReactRefresh" in react_refresh.content
    assert b"__hmr_import" in react_refresh.content
    assert b"validateRefreshBoundaryAndEnqueueUpdate" in react_refresh.content


def test_public_show_runtime_direct_client_paths_return_shims(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    manager = _FakeShowRuntimeManager(body=b"real vite client")
    set_show_runtime_manager_for_tests(manager)
    try:
        vite_client = app.test_client().get(
            f"/p/{share_id}/@vite/client",
            base_url="http://127.0.0.1:5123",
        )
        react_refresh = app.test_client().get(
            f"/p/{share_id}/@react-refresh",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert vite_client.status_code == 200
    assert react_refresh.status_code == 200
    assert b"export function createHotContext" in vite_client.content
    assert b"export function injectIntoGlobalHook" in react_refresh.content
    assert manager.calls == []


def test_show_runtime_source_preserves_dot_vite_dep_import_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b'import "/.vite/deps/react.js?v=d6d38251";',
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/src/main.tsx?t=1780732068677",
            base_url="http://127.0.0.1:5123",
        )
        public_dep = app.test_client().get(
            "/_show-runtime/deps/r8-d6d38251/react.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert public_dep.status_code == 200
    assert manager.calls[-1][1] == "/sessions/ses123/app/.vite/deps/react.js?v=d6d38251"


def test_show_runtime_source_keeps_unversioned_dep_imports_session_scoped(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b'import "/node_modules/.vite/deps/react.js";',
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/src/main.tsx?t=1780732068677",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert response.content == b'import "/node_modules/.vite/deps/react.js";'
    assert response.headers["cache-control"] == "no-cache"


def test_show_runtime_source_keeps_partial_js_responses_unmodified(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(
        body=b'import "/node_modules/.vite/deps/react.js?v=d6d38251";',
        status_code=206,
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
            "content-range": "bytes 0-52/53",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            "/show/ses123/src/main.tsx?t=1780732068677",
            base_url="http://127.0.0.1:5123",
            headers={"Range": "bytes=0-52"},
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 206
    assert response.content == b'import "/node_modules/.vite/deps/react.js?v=d6d38251";'
    assert response.headers["content-range"] == "bytes 0-52/53"
    assert response.headers["cache-control"] == "no-cache"


def test_public_show_page_does_not_inject_write_runtime_config(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    monkeypatch.setattr("vibe.ui_server.show_event_write_token", lambda session_id: f"token-{session_id}")
    manager = _FakeShowRuntimeManager(
        body=b'<!doctype html><html><body><script type="module" src="/src/main.tsx"></script></body></html>'
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(f"/p/{share_id}/", base_url="http://127.0.0.1:5123")
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    body = response.content.decode("utf-8")
    assert "globalThis.__AVIBE_SHOW__=Object.assign" not in body
    assert "token-ses123" not in body


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
    assert b"Loading Show Page" in response.content
    assert b"Ready to visualize" in response.content
    assert b"Copy prompt" in response.content
    assert b'src="./src/main.tsx"' not in response.content


def test_show_page_recovery_loading_holds_before_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    set_show_runtime_manager_for_tests(_FakeShowRuntimeManager(fail=True))
    try:
        response = app.test_client().get("/show/ses123/", base_url="http://127.0.0.1:5123")
    finally:
        set_show_runtime_manager_for_tests(None)

    body = response.content.decode("utf-8")
    loading_delay = f"{SHOW_RUNTIME_RECOVERY_LOADING_DELAY_SECONDS}s"
    assert f"show-recovery-loading-out 0.18s ease {loading_delay} forwards" in body
    assert f"show-recovery-panel-in 0.22s ease {loading_delay} forwards" in body
    assert "ease 5s forwards" not in body


def test_private_show_page_api_does_not_fall_back_to_static(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    (paths.get_show_pages_dir() / "ses123" / "api" / "health.ts").write_text("export const secret = true\n", encoding="utf-8")
    set_show_runtime_manager_for_tests(_FakeShowRuntimeManager(fail=True))
    try:
        response = app.test_client().get("/show/ses123/api/health.ts", base_url="http://127.0.0.1:5123")
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 503
    assert response.get_json()["error"] == "show_runtime_unavailable"
    assert b"secret" not in response.content


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


def test_private_show_page_records_show_event(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    _create_show_page("ses123", "private")
    token = "session-write-token"
    monkeypatch.setattr("vibe.ui_server.show_event_write_token", lambda session_id: token)
    published = []
    monkeypatch.setattr("vibe.sse_broker.broker.publish", lambda event_type, data: published.append((event_type, data)))

    response = app.test_client().post(
        "/show/ses123/__show/events",
        base_url="http://127.0.0.1:5123",
        headers={
            "Origin": "http://127.0.0.1:5123",
            "Content-Type": "application/json",
            "X-Vibe-Show-Token": token,
        },
        json={
            "type": "assistant.mark.created",
            "mark": {
                "target": "mark-default-summary",
                "body": "Review this summary.",
            },
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["event"]["type"] == "assistant.mark.created"
    assert payload["event"]["message_id"]
    assert "Review this summary." in payload["event"]["transcript_text"]
    assert [event_type for event_type, _data in published] == ["show.event", "message.new", "session.activity"]
    assert published[1][1]["id"] == payload["event"]["message_id"]
    assert published[2][1]["scope_id"] == payload["event"]["scope_id"]

    events_response = app.test_client().get("/show/ses123/__show/events", base_url="http://127.0.0.1:5123")
    assert events_response.status_code == 200
    assert events_response.get_json()["events"][0]["id"] == payload["event"]["id"]


def test_private_show_page_rejects_mismatched_event_session_id(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    _create_show_page("ses123", "private")
    token = "session-write-token"
    monkeypatch.setattr("vibe.ui_server.show_event_write_token", lambda session_id: token)

    response = app.test_client().post(
        "/show/ses123/__show/events",
        base_url="http://127.0.0.1:5123",
        headers={
            "Origin": "http://127.0.0.1:5123",
            "Content-Type": "application/json",
            "X-Vibe-Show-Token": token,
        },
        json={
            "sessionId": "ses_other",
            "type": "human.annotation.created",
            "annotation": {"comment": "Wrong session."},
        },
    )

    assert response.status_code == 400
    assert response.get_json()["code"] == "session_mismatch"


def test_private_show_page_dispatches_human_show_event(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    _create_show_page("ses123", "private")
    token = "session-write-token"
    monkeypatch.setattr("vibe.ui_server.show_event_write_token", lambda session_id: token)
    published = []
    monkeypatch.setattr("vibe.sse_broker.broker.publish", lambda event_type, data: published.append((event_type, data)))
    dispatches = []
    dispatch_done = asyncio.Event()

    async def fake_stream_dispatch(payload, **kwargs):
        dispatches.append(payload)
        dispatch_done.set()
        yield "turn.start", {"session_id": payload["session_id"]}
        yield "turn.end", {"session_id": payload["session_id"]}

    with patch("vibe.internal_client.stream_dispatch", fake_stream_dispatch):
        response = app.test_client().post(
            "/show/ses123/__show/events",
            base_url="http://127.0.0.1:5123",
            headers={
                "Origin": "http://127.0.0.1:5123",
                "Content-Type": "application/json",
                "X-Vibe-Show-Token": token,
            },
            json={
                "type": "human.intent.submitted",
                "payload": {
                    "component": "decision",
                    "intent": "choose",
                    "value": "B",
                    "comment": "Pick B.",
                    "dispatch": True,
                },
            },
        )

    assert response.status_code == 201
    asyncio.run(asyncio.wait_for(dispatch_done.wait(), timeout=1))
    assert dispatches
    assert dispatches[0]["session_id"] == "ses123"
    assert "Pick B." in dispatches[0]["text"]
    assert dispatches[0]["user_message_id"] == response.get_json()["event"]["message_id"]
    assert "show.dispatch" in [event_type for event_type, _data in published]


def test_private_show_page_dispatches_screenshot_annotation_batch(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    _create_show_page("ses123", "private")
    token = "session-write-token"
    monkeypatch.setattr("vibe.ui_server.show_event_write_token", lambda session_id: token)
    dispatches = []
    dispatch_done = asyncio.Event()

    async def fake_stream_dispatch(payload, **kwargs):
        dispatches.append(payload)
        dispatch_done.set()
        yield "turn.start", {"session_id": payload["session_id"]}
        yield "turn.end", {"session_id": payload["session_id"]}

    with patch("vibe.internal_client.stream_dispatch", fake_stream_dispatch):
        response = app.test_client().post(
            "/show/ses123/__show/events",
            base_url="http://127.0.0.1:5123",
            headers={
                "Origin": "http://127.0.0.1:5123",
                "Content-Type": "application/json",
                "X-Vibe-Show-Token": token,
            },
            json={
                "type": "human.annotation.created",
                "annotation": {
                    "intent": "review",
                    "comment": "Review this screenshot batch.",
                    "dispatch": True,
                    "screenshot": {
                        "attachmentId": "show_asset_screenshot_1",
                        "region": {"x": 24, "y": 32, "width": 640, "height": 360},
                        "items": [
                            {
                                "label": "1",
                                "comment": "This counter looks stale.",
                                "point": {"x": 120, "y": 80},
                            },
                            {
                                "label": "2",
                                "comment": "Crop this empty area.",
                                "region": {"x": 420, "y": 240, "width": 160, "height": 72},
                            },
                        ],
                    },
                },
            },
        )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["event"]["payload"]["primaryAnchor"] == "screenshot"
    asyncio.run(asyncio.wait_for(dispatch_done.wait(), timeout=1))
    assert dispatches
    transcript = dispatches[0]["text"]
    assert "Anchor kind: screenshot" in transcript
    assert "Screenshot: show_asset_screenshot_1" in transcript
    assert "Screenshot region: x:24, y:32, 640x360" in transcript
    assert "1. This counter looks stale. (x:120, y:80)" in transcript
    assert "2. Crop this empty area. (x:420, y:240, 160x72)" in transcript


def test_private_show_page_rejects_show_event_without_write_token(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    _create_show_page("ses123", "private")
    monkeypatch.setattr("vibe.ui_server.show_event_write_token", lambda session_id: f"token-{session_id}")

    client = app.test_client()
    page_response = client.get("/show/ses123/", base_url="http://127.0.0.1:5123")
    assert page_response.status_code == 200

    response = client.post(
        "/show/ses123/__show/events",
        base_url="http://127.0.0.1:5123",
        headers={
            "Origin": "http://127.0.0.1:5123",
            "Content-Type": "application/json",
        },
        json={
            "type": "assistant.mark.created",
            "mark": {"target": "mark-default-summary", "body": "Review this summary."},
        },
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "show_event_write_forbidden"


def test_private_show_page_rejects_other_session_write_token(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    _create_show_page("ses123", "private")
    monkeypatch.setattr("vibe.ui_server.show_event_write_token", lambda session_id: f"token-{session_id}")

    response = app.test_client().post(
        "/show/ses123/__show/events",
        base_url="http://127.0.0.1:5123",
        headers={
            "Origin": "http://127.0.0.1:5123",
            "Content-Type": "application/json",
            "X-Vibe-Show-Token": "token-other-session",
        },
        json={
            "type": "assistant.mark.created",
            "mark": {"target": "mark-default-summary", "body": "Review this summary."},
        },
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "show_event_write_forbidden"


def test_private_show_page_sets_show_event_write_cookie(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    _create_show_page("ses123", "private")
    monkeypatch.setattr("vibe.ui_server.show_event_write_token", lambda session_id: f"token-{session_id}")

    response = app.test_client().get("/show/ses123/", base_url="http://127.0.0.1:5123")

    assert response.status_code == 200
    cookies = "\n".join(response.headers.getlist("set-cookie"))
    assert "vibe_show_event_token=token-ses123" in cookies
    assert "Path=/show/ses123/" in cookies
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_public_show_page_clears_show_event_write_cookie(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    share_id = _create_show_page("ses123", "public")

    response = app.test_client().get(f"/p/{share_id}/", base_url="http://127.0.0.1:5123")

    assert response.status_code == 200
    cookies = "\n".join(response.headers.getlist("set-cookie"))
    assert "vibe_show_event_token=" in cookies
    assert "Max-Age=0" in cookies


def test_public_show_page_immutable_deps_do_not_clear_write_cookie(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    share_id = _create_show_page("ses123", "public")
    manager = _FakeShowRuntimeManager(
        body=b"export default {}",
        extra_headers={
            "content-type": "text/javascript",
            "cache-control": "no-cache",
        },
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            f"/p/{share_id}/node_modules/.vite/deps/react.js?v=d6d38251",
            base_url="http://127.0.0.1:5123",
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 302
    assert response.headers["location"] == "/_show-runtime/deps/r8-d6d38251/react.js"
    assert "set-cookie" not in response.headers


def test_show_events_stream_replays_all_persisted_pages_before_live(monkeypatch, tmp_path):
    from core.show_session_events import ShowSessionEventStore
    from vibe.ui_server import _show_events_stream

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    _create_show_page("ses123", "private")
    store = ShowSessionEventStore()
    try:
        for index in range(501):
            store.append(
                "ses123",
                {
                    "id": f"show_evt_{index:03d}",
                    "type": "assistant.mark.created",
                    "mark": {
                        "target": f"target-{index:03d}",
                        "body": f"body-{index:03d}",
                        "createdAt": f"2026-05-30T00:{index // 60:02d}:{index % 60:02d}+00:00",
                    },
                },
            )
    finally:
        store.close()

    async def _collect_replay() -> str:
        response = await _show_events_stream("ses123")
        iterator = response.body_iterator.__aiter__()
        chunks = []
        try:
            for _ in range(502):
                chunk = await iterator.__anext__()
                chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        finally:
            await iterator.aclose()
        return "".join(chunks)

    body = asyncio.run(_collect_replay())

    assert body.startswith(": show events connected")
    assert body.count("event: show.event") == 501
    assert "id: show_evt_000" in body
    assert "id: show_evt_500" in body
    assert '"id": "show_evt_000"' in body
    assert '"id": "show_evt_500"' in body


def test_show_events_stream_forwards_live_dispatch_events(monkeypatch, tmp_path):
    from vibe.sse_broker import broker
    from vibe.ui_server import _show_events_stream

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    _create_show_page("ses123", "private")

    async def _collect_live_dispatch() -> str:
        response = await _show_events_stream("ses123")
        iterator = response.body_iterator.__aiter__()
        chunks = []
        try:
            chunks.append(await iterator.__anext__())
            broker.publish(
                "show.dispatch",
                {
                    "session_id": "ses123",
                    "scope_id": "scope123",
                    "show_event_id": "show_evt_1",
                    "event": "turn.chunk",
                    "data": {"text": "hello"},
                },
            )
            chunks.append(await asyncio.wait_for(iterator.__anext__(), timeout=1))
        finally:
            await iterator.aclose()
        return "".join(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk for chunk in chunks)

    body = asyncio.run(_collect_live_dispatch())

    assert "event: show.dispatch" in body
    assert '"show_event_id": "show_evt_1"' in body


def test_public_show_events_stream_redacts_nested_dispatch_ids(monkeypatch, tmp_path):
    from vibe.sse_broker import broker
    from vibe.ui_server import _show_events_stream

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    _create_show_page("ses123", "public")

    async def _collect_live_dispatch() -> str:
        response = await _show_events_stream("ses123", public=True)
        iterator = response.body_iterator.__aiter__()
        chunks = []
        try:
            chunks.append(await iterator.__anext__())
            broker.publish(
                "show.dispatch",
                {
                    "session_id": "ses123",
                    "scope_id": "scope123",
                    "show_event_id": "show_evt_1",
                    "event": "turn.chunk",
                    "data": {
                        "text": "hello",
                        "session_id": "ses123",
                        "message_id": "msg123",
                        "nested": {"scope_id": "scope123", "user_message_id": "msg123"},
                    },
                },
            )
            chunks.append(await asyncio.wait_for(iterator.__anext__(), timeout=1))
        finally:
            await iterator.aclose()
        return "".join(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk for chunk in chunks)

    body = asyncio.run(_collect_live_dispatch())

    assert "event: show.dispatch" in body
    assert '"show_event_id": "show_evt_1"' in body
    assert '"text": "hello"' in body
    assert '"session_id"' not in body
    assert '"scope_id"' not in body
    assert '"message_id"' not in body
    assert '"user_message_id"' not in body


def test_public_show_page_events_redact_internal_ids(monkeypatch, tmp_path):
    from core.show_session_events import ShowSessionEventStore

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    share_id = _create_show_page("ses123", "public")
    store = ShowSessionEventStore()
    try:
        event = store.append(
            "ses123",
            {
                "type": "assistant.mark.created",
                "mark": {
                    "target": "summary",
                    "body": "body",
                },
            },
        )
    finally:
        store.close()

    response = app.test_client().get(f"/p/{share_id}/__show/events", base_url="http://127.0.0.1:5123")

    assert response.status_code == 200
    public_event = response.get_json()["events"][0]
    assert public_event["id"] == event["id"]
    assert public_event["type"] == "assistant.mark.created"
    assert public_event["payload"]["body"] == "body"
    assert "session_id" not in public_event
    assert "scope_id" not in public_event
    assert "message_id" not in public_event
    assert "message" not in public_event


def test_public_show_events_stream_redacts_internal_ids(monkeypatch, tmp_path):
    from core.show_session_events import ShowSessionEventStore
    from vibe.ui_server import _show_events_stream

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    _create_show_page("ses123", "public")
    store = ShowSessionEventStore()
    try:
        event = store.append(
            "ses123",
            {
                "id": "show_evt_public",
                "type": "assistant.mark.created",
                "mark": {
                    "target": "summary",
                    "body": "body",
                },
            },
        )
    finally:
        store.close()

    async def _collect_replay() -> str:
        response = await _show_events_stream("ses123", public=True)
        iterator = response.body_iterator.__aiter__()
        chunks = []
        try:
            for _ in range(2):
                chunk = await iterator.__anext__()
                chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)
        finally:
            await iterator.aclose()
        return "".join(chunks)

    body = asyncio.run(_collect_replay())

    assert f'"id": "{event["id"]}"' in body
    assert '"session_id"' not in body
    assert '"scope_id"' not in body
    assert '"message_id"' not in body
    assert '"message"' not in body


def test_cli_show_event_ingress_records_and_publishes(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    published = []
    monkeypatch.setattr("vibe.sse_broker.broker.publish", lambda event_type, data: published.append((event_type, data)))

    response = app.test_client().post(
        "/api/show/sessions/ses123/events",
        base_url="http://127.0.0.1:5123",
        headers={
            "Content-Type": "application/json",
            "X-Vibe-Show-Client": "cli",
            "X-Vibe-Show-Cli-Token": show_cli_event_token(),
        },
        json={
            "type": "assistant.mark.created",
            "mark": {
                "target": "mark-default-summary",
                "body": "Review this summary.",
            },
        },
    )

    assert response.status_code == 201
    payload = response.get_json()
    assert payload["event"]["type"] == "assistant.mark.created"
    assert payload["event"]["message_id"]
    assert [event_type for event_type, _data in published] == ["show.event", "message.new", "session.activity"]


def test_cli_show_event_ingress_requires_cli_token(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")

    response = app.test_client().post(
        "/api/show/sessions/ses123/events",
        base_url="http://127.0.0.1:5123",
        headers={
            "Content-Type": "application/json",
            "X-Vibe-Show-Client": "cli",
        },
        json={
            "type": "assistant.mark.created",
            "mark": {"target": "mark-default-summary", "body": "Review this summary."},
        },
    )

    assert response.status_code == 403


def test_cli_show_prewarm_ingress_uses_ui_runtime_manager(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    calls = []

    async def fake_prewarm(session_id, *, base_path=None):
        calls.append((session_id, base_path))
        return SimpleNamespace(available=True, reason=None, base_url="http://127.0.0.1:49200")

    monkeypatch.setattr("core.show_runtime.prewarm_show_page_session", fake_prewarm)

    response = app.test_client().post(
        "/api/show/sessions/ses123/prewarm",
        base_url="http://127.0.0.1:5123",
        headers={
            "Content-Type": "application/json",
            "X-Vibe-Show-Client": "cli",
            "X-Vibe-Show-Cli-Token": show_cli_event_token(),
        },
        json={"base_path": "/p/share123/"},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert calls == [("ses123", "/p/share123/")]


def test_cli_show_prewarm_ingress_requires_cli_token(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().post(
        "/api/show/sessions/ses123/prewarm",
        base_url="http://127.0.0.1:5123",
        headers={
            "Content-Type": "application/json",
            "X-Vibe-Show-Client": "cli",
        },
        json={},
    )

    assert response.status_code == 403


def test_cli_show_event_ingress_allows_configured_host_with_cli_token(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.enabled = False
    config.ui.setup_host = "10.1.2.3"
    config.save()
    _create_agent_session("ses123")

    response = app.test_client().post(
        "/api/show/sessions/ses123/events",
        base_url="http://10.1.2.3:5123",
        environ_base={"REMOTE_ADDR": "10.50.0.5"},
        headers={
            "Content-Type": "application/json",
            "X-Vibe-Show-Client": "cli",
            "X-Vibe-Show-Cli-Token": show_cli_event_token(),
        },
        json={
            "type": "assistant.mark.created",
            "mark": {"target": "mark-default-summary", "body": "Review this summary."},
        },
    )

    assert response.status_code == 201


def test_cli_show_event_ingress_rejects_configured_host_without_cli_token(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.enabled = False
    config.ui.setup_host = "10.1.2.3"
    config.save()
    _create_agent_session("ses123")

    response = app.test_client().post(
        "/api/show/sessions/ses123/events",
        base_url="http://10.1.2.3:5123",
        environ_base={"REMOTE_ADDR": "10.50.0.5"},
        headers={
            "Content-Type": "application/json",
            "X-Vibe-Show-Client": "cli",
        },
        json={
            "type": "assistant.mark.created",
            "mark": {"target": "mark-default-summary", "body": "Review this summary."},
        },
    )

    assert response.status_code == 403


def test_public_show_page_events_are_read_only(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_agent_session("ses123")
    share_id = _create_show_page("ses123", "public")

    response = app.test_client().post(
        f"/p/{share_id}/__show/events",
        base_url="http://127.0.0.1:5123",
        headers={
            "Origin": "http://127.0.0.1:5123",
            "Content-Type": "application/json",
        },
        json={
            "type": "assistant.mark.created",
            "mark": {"target": "summary", "body": "body"},
        },
    )

    assert response.status_code == 403
    assert response.get_json()["code"] == "public_show_events_read_only"


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


def test_show_runtime_manager_passes_runtime_options(monkeypatch, tmp_path):
    from core.show_pages import SHOW_RUNTIME_RECOVERY_LOADING_DELAY_SECONDS

    captured = {}

    class FakeProcess:
        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = command
        return FakeProcess()

    async def fake_startup_url():
        return "http://127.0.0.1:12345"

    manager = ShowRuntimeManager(
        command="/bin/echo",
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: [command])
    monkeypatch.setattr("core.show_runtime.subprocess.Popen", fake_popen)
    monkeypatch.setattr(manager, "_read_startup_url", fake_startup_url)

    result = asyncio.run(manager.ensure())

    assert result.available is True
    cache_index = captured["command"].index("--cache-root")
    assert captured["command"][cache_index + 1] == str(tmp_path / "runtime" / "vite-cache")
    index = captured["command"].index("--fallback-delay-seconds")
    assert captured["command"][index + 1] == str(SHOW_RUNTIME_RECOVERY_LOADING_DELAY_SECONDS)


def test_show_runtime_manager_prewarm_loads_entry_module(monkeypatch, tmp_path):
    responses = {
        "/sessions/ses123/app/": (
            200,
            b'<script type="module" src="/show/ses123/src/main.tsx"></script>',
            {"content-type": "text/html"},
        ),
        "/sessions/ses123/app/src/main.tsx": (
            200,
            b'import App from "/show/ses123/src/App.tsx";',
            {"content-type": "text/javascript"},
        ),
        "/sessions/ses123/app/src/App.tsx": (
            200,
            b'import { Button } from "/show/ses123/@fs/runtime/packages/ui/dist/button.js";',
            {"content-type": "text/javascript"},
        ),
        "/sessions/ses123/app/@fs/runtime/packages/ui/dist/button.js": (
            200,
            b'import { jsx } from "/show/ses123/@fs/runtime/vite-cache/deps/react_jsx-runtime.js?v=abc";',
            {"content-type": "text/javascript"},
        ),
        "/sessions/ses123/app/@fs/runtime/vite-cache/deps/react_jsx-runtime.js?v=abc": (
            200,
            b"export const jsx = () => null;",
            {"content-type": "text/javascript"},
        ),
    }
    calls = []

    async def fake_request(self, method, path, *, headers=None, body=None):
        import httpx

        calls.append((method, path, headers, body))
        status, content, headers_out = responses[path]
        return httpx.Response(status, content=content, headers=headers_out)

    manager = ShowRuntimeManager(
        command="/bin/echo",
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
    )
    monkeypatch.setattr(ShowRuntimeManager, "request", fake_request)

    result = asyncio.run(manager.prewarm_session("ses123", base_path="/show/ses123/"))

    assert result.available is True
    assert calls == [
        ("GET", "/sessions/ses123/app/", {"x-vibe-show-base": "/show/ses123/"}, None),
        ("GET", "/sessions/ses123/app/src/main.tsx", {"x-vibe-show-base": "/show/ses123/"}, None),
        ("GET", "/sessions/ses123/app/src/App.tsx", {"x-vibe-show-base": "/show/ses123/"}, None),
        (
            "GET",
            "/sessions/ses123/app/@fs/runtime/packages/ui/dist/button.js",
            {"x-vibe-show-base": "/show/ses123/"},
            None,
        ),
        (
            "GET",
            "/sessions/ses123/app/@fs/runtime/vite-cache/deps/react_jsx-runtime.js?v=abc",
            {"x-vibe-show-base": "/show/ses123/"},
            None,
        ),
    ]


def test_show_runtime_manager_prewarm_reports_nested_module_failures(monkeypatch, tmp_path):
    responses = {
        "/sessions/ses123/app/": (
            200,
            b'<script type="module" src="/p/share123/src/main.tsx"></script>',
            {"content-type": "text/html"},
        ),
        "/sessions/ses123/app/src/main.tsx": (
            200,
            b'import App from "/p/share123/src/App.tsx";',
            {"content-type": "text/javascript"},
        ),
        "/sessions/ses123/app/src/App.tsx": (
            504,
            b"timeout",
            {"content-type": "text/plain"},
        ),
    }

    async def fake_request(self, method, path, *, headers=None, body=None):
        import httpx

        status, content, headers_out = responses[path]
        return httpx.Response(status, content=content, headers=headers_out)

    manager = ShowRuntimeManager(
        command="/bin/echo",
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
    )
    monkeypatch.setattr(ShowRuntimeManager, "request", fake_request)

    result = asyncio.run(manager.prewarm_session("ses123", base_path="/p/share123/"))

    assert result.available is False
    assert result.reason == "session_prewarm_module_failed:504:/sessions/ses123/app/src/App.tsx"


def test_show_runtime_manager_uses_managed_runtime_bin(tmp_path):
    runtime_dir = tmp_path / "runtime with spaces"
    bin_path = runtime_dir / "package" / "node_modules" / ".bin" / "avibe-show-runtime"
    bin_path.parent.mkdir(parents=True)
    bin_path.write_text("#!/bin/sh\n", encoding="utf-8")
    bin_path.chmod(0o755)

    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        runtime_source="npm",
        auto_install=False,
    )

    assert asyncio.run(manager._resolve_managed_command()) == [str(bin_path)]


def test_show_runtime_archive_platform_tag_maps_macos_universal2_to_machine(monkeypatch):
    monkeypatch.setattr("core.show_runtime.get_platform", lambda: "macosx-14.0-universal2")
    monkeypatch.setattr("core.show_runtime.platform.machine", lambda: "arm64")

    assert _runtime_platform_tag() == "darwin-arm64"


def test_show_runtime_manager_installs_from_prebuilt_archive(monkeypatch, tmp_path):
    archive_root = tmp_path / "archive-root"
    cli_path = archive_root / "node_modules" / "@avibe" / "show-runtime" / "dist" / "cli.js"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    archive_path = tmp_path / "vibe-show-runtime-node.tgz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(archive_root / "node_modules", arcname="node_modules")

    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
        runtime_source="archive",
        archive_path=archive_path,
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)

    assert manager._install_managed_runtime() == [
        "/bin/node",
        str(tmp_path / "runtime" / "prebuilt" / "current" / "node_modules" / "@avibe" / "show-runtime" / "dist" / "cli.js"),
    ]
    assert manager._install_reason is None


def test_show_runtime_manager_installs_prebuilt_archive_with_internal_symlinks(monkeypatch, tmp_path):
    archive_root = tmp_path / "archive-root"
    package_dir = archive_root / "packages" / "runtime"
    cli_path = package_dir / "dist" / "cli.js"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    scope_dir = archive_root / "node_modules" / "@avibe"
    bin_dir = archive_root / "node_modules" / ".bin"
    scope_dir.mkdir(parents=True)
    bin_dir.mkdir(parents=True)
    (scope_dir / "show-runtime").symlink_to("../../packages/runtime")
    (bin_dir / "avibe-show-runtime").symlink_to("../@avibe/show-runtime/dist/cli.js")
    archive_path = tmp_path / "vibe-show-runtime-node.tgz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(archive_root / "packages", arcname="packages")
        tar.add(archive_root / "node_modules", arcname="node_modules")

    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
        runtime_source="archive",
        archive_path=archive_path,
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)

    command = manager._install_managed_runtime()

    assert command == [
        "/bin/node",
        str(tmp_path / "runtime" / "prebuilt" / "current" / "node_modules" / "@avibe" / "show-runtime" / "dist" / "cli.js"),
    ]
    assert Path(command[1]).resolve().read_text(encoding="utf-8") == "#!/usr/bin/env node\n"
    assert manager._install_reason is None


def test_show_runtime_safe_extract_rejects_external_symlink(tmp_path):
    archive_root = tmp_path / "archive-root"
    archive_root.mkdir()
    (archive_root / "escape").symlink_to("../../outside")
    archive_path = tmp_path / "unsafe.tgz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(archive_root / "escape", arcname="escape")

    with tarfile.open(archive_path, "r:gz") as tar:
        with pytest.raises(ValueError, match="Unsafe archive link target"):
            _safe_extract_tar(tar, tmp_path / "destination")


def test_show_runtime_safe_extract_rejects_external_hardlink(tmp_path):
    archive_path = tmp_path / "unsafe-hardlink.tgz"
    with tarfile.open(archive_path, "w:gz") as tar:
        data = b"safe\n"
        safe = tarfile.TarInfo("safe")
        safe.size = len(data)
        tar.addfile(safe, io.BytesIO(data))
        hardlink = tarfile.TarInfo("dir/h")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = "../outside"
        tar.addfile(hardlink)

    with tarfile.open(archive_path, "r:gz") as tar:
        with pytest.raises(ValueError, match="Unsafe archive link target"):
            _safe_extract_tar(tar, tmp_path / "destination")


def test_show_runtime_manager_reuses_installed_prebuilt_runtime_without_archive(monkeypatch, tmp_path):
    cli_path = tmp_path / "runtime" / "prebuilt" / "current" / "node_modules" / "@avibe" / "show-runtime" / "dist" / "cli.js"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text("#!/usr/bin/env node\n", encoding="utf-8")

    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
        runtime_source="archive",
        archive_path=tmp_path / "missing.tgz",
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)

    assert manager._install_managed_runtime() == ["/bin/node", str(cli_path)]
    assert manager._install_reason is None


def test_show_runtime_manager_archive_source_honors_offline_mode(monkeypatch, tmp_path):
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
        runtime_source="archive",
        offline=True,
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)
    monkeypatch.setattr(manager, "_download_runtime_archive", lambda archive_url: (_ for _ in ()).throw(AssertionError("network")))

    result = manager.prepare()

    assert result["ok"] is False
    assert result["reason"] == "runtime_archive_unavailable_offline"


def test_show_runtime_manager_refreshes_stale_prebuilt_archive(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "runtime"
    installed_cli = runtime_dir / "prebuilt" / "current" / "node_modules" / "@avibe" / "show-runtime" / "dist" / "cli.js"
    installed_cli.parent.mkdir(parents=True)
    installed_cli.write_text("old runtime\n", encoding="utf-8")

    archive_root = tmp_path / "archive-root"
    archive_cli = archive_root / "node_modules" / "@avibe" / "show-runtime" / "dist" / "cli.js"
    archive_cli.parent.mkdir(parents=True)
    archive_cli.write_text("new runtime\n", encoding="utf-8")
    archive_path = tmp_path / "vibe-show-runtime-node.tgz"
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(archive_root / "node_modules", arcname="node_modules")

    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        runtime_source="archive",
        archive_path=archive_path,
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)

    assert asyncio.run(manager._resolve_managed_command()) == ["/bin/node", str(installed_cli)]
    assert installed_cli.read_text(encoding="utf-8") == "new runtime\n"


def test_show_runtime_manager_installs_from_manifest_cache(monkeypatch, tmp_path):
    archive_path = _write_runtime_archive(tmp_path)
    manifest_path = _write_runtime_manifest(tmp_path, archive_path)
    runtime_dir = tmp_path / "runtime"
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        manifest_path=manifest_path,
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)

    result = manager.prepare()
    manifest = manager._load_runtime_manifest()
    assert manifest is not None
    archive = manager._manifest_archive_for_platform(manifest)
    assert archive is not None
    installed_cli = Path(manager._manifest_runtime_command(manager._manifest_install_dir(manifest, archive), ["/bin/node"])[1])

    assert result["ok"] is True
    assert result["command"] == ["/bin/node", str(installed_cli)]
    assert manager._install_reason is None
    assert (runtime_dir / "downloads" / f"{_sha256(archive_path)}.tgz").exists()
    metadata = json.loads((installed_cli.parents[4] / ".vibe-show-runtime.json").read_text(encoding="utf-8"))
    assert metadata["provider"] == "manifest-cache"
    assert metadata["archive_sha256"] == _sha256(archive_path)
    status = manager.status()
    assert status["installed"] is True
    assert status["installed_matches_manifest"] is True


def test_show_runtime_manager_manifest_install_dir_includes_manifest_and_archive_identity(monkeypatch, tmp_path):
    old_archive_path = _write_runtime_archive(tmp_path / "old", text="old runtime\n")
    old_manifest_path = _write_runtime_manifest(tmp_path / "old", old_archive_path)
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)

    old_manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        manifest_path=old_manifest_path,
    )
    old_result = old_manager.prepare()
    old_cli = Path(old_result["command"][1])
    assert old_cli.read_text(encoding="utf-8") == "old runtime\n"

    new_archive_path = _write_runtime_archive(tmp_path / "new", text="new runtime\n")
    new_manifest_path = _write_runtime_manifest(tmp_path / "new", new_archive_path)
    new_manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        manifest_path=new_manifest_path,
    )

    new_result = new_manager.prepare()
    new_cli = Path(new_result["command"][1])

    assert new_cli != old_cli
    assert new_cli.read_text(encoding="utf-8") == "new runtime\n"
    assert old_cli.read_text(encoding="utf-8") == "old runtime\n"
    assert new_manager.status()["installed_matches_manifest"] is True


def test_show_runtime_clean_prunes_stale_manifest_fingerprints(monkeypatch, tmp_path):
    old_archive_path = _write_runtime_archive(tmp_path / "old", text="old runtime\n")
    old_manifest_path = _write_runtime_manifest(tmp_path / "old", old_archive_path)
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)

    old_manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        manifest_path=old_manifest_path,
    )
    old_result = old_manager.prepare()
    old_install_dir = Path(old_result["command"][1]).parents[4]

    new_archive_path = _write_runtime_archive(tmp_path / "new", text="new runtime\n")
    new_manifest_path = _write_runtime_manifest(tmp_path / "new", new_archive_path)
    new_manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        manifest_path=new_manifest_path,
    )
    new_result = new_manager.prepare()
    new_install_dir = Path(new_result["command"][1]).parents[4]

    result = new_manager.clean(keep_previous=0)

    assert result["ok"] is True
    assert str(old_install_dir) in result["removed"]
    assert old_install_dir.exists() is False
    assert new_install_dir.exists() is True


def test_show_runtime_manager_reuses_legacy_manifest_install_offline(monkeypatch, tmp_path):
    archive_path = _write_runtime_archive(tmp_path, text="legacy runtime\n")
    manifest_path = _write_runtime_manifest(tmp_path, archive_path)
    runtime_dir = tmp_path / "runtime"
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        manifest_path=manifest_path,
        offline=True,
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)
    manifest = manager._load_runtime_manifest()
    assert manifest is not None
    archive = manager._manifest_archive_for_platform(manifest)
    assert archive is not None
    legacy_install_dir = manager._legacy_manifest_install_dir(manifest, archive)
    legacy_cli = legacy_install_dir / "node_modules" / "@avibe" / "show-runtime" / "dist" / "cli.js"
    legacy_cli.parent.mkdir(parents=True)
    legacy_cli.write_text("legacy runtime\n", encoding="utf-8")
    manager._write_manifest_install_metadata(legacy_install_dir, manifest, archive)

    result = manager.prepare()

    assert result["ok"] is True
    assert result["command"] == ["/bin/node", str(legacy_cli)]


def test_show_runtime_clean_skips_legacy_parent_of_current_fingerprint(monkeypatch, tmp_path):
    archive_path = _write_runtime_archive(tmp_path, text="current runtime\n")
    manifest_path = _write_runtime_manifest(tmp_path, archive_path)
    runtime_dir = tmp_path / "runtime"
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        manifest_path=manifest_path,
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)
    result = manager.prepare()
    current_install_dir = Path(result["command"][1]).parents[4]
    legacy_parent = current_install_dir.parent
    manifest = manager._load_runtime_manifest()
    assert manifest is not None
    archive = manager._manifest_archive_for_platform(manifest)
    assert archive is not None
    manager._write_manifest_install_metadata(legacy_parent, manifest, archive)

    clean_result = manager.clean(keep_previous=0)

    assert str(legacy_parent) not in clean_result["removed"]
    assert current_install_dir.exists() is True
    assert Path(result["command"][1]).exists() is True


def test_show_runtime_manager_rejects_node_below_manifest_minimum(monkeypatch, tmp_path):
    archive_path = _write_runtime_archive(tmp_path)
    manifest_path = _write_runtime_manifest(tmp_path, archive_path)
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
        manifest_path=manifest_path,
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)
    monkeypatch.setattr("core.show_runtime._node_version", lambda node: (20, 18, 0))

    result = manager.prepare()

    assert result["ok"] is False
    assert result["reason"] == "runtime_node_unsupported"
    assert result["status"]["node_supported"] is False


def test_show_runtime_manager_rejects_manifest_archive_checksum_mismatch(monkeypatch, tmp_path):
    archive_path = _write_runtime_archive(tmp_path)
    manifest_path = _write_runtime_manifest(tmp_path, archive_path, sha256="0" * 64)
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
        manifest_path=manifest_path,
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)

    result = manager.prepare()

    assert result["ok"] is False
    assert result["reason"] == "runtime_archive_checksum_mismatch"


def test_show_runtime_manager_does_not_reuse_stale_manifest_install_after_checksum_failure(monkeypatch, tmp_path):
    old_archive_path = _write_runtime_archive(tmp_path, text="old runtime\n")
    old_manifest_path = _write_runtime_manifest(tmp_path / "old", old_archive_path)
    runtime_dir = tmp_path / "runtime"
    old_manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        manifest_path=old_manifest_path,
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)
    assert old_manager.prepare()["ok"] is True

    new_archive_path = _write_runtime_archive(tmp_path, text="new runtime\n")
    new_manifest_path = _write_runtime_manifest(tmp_path / "new", new_archive_path, sha256="f" * 64)
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        manifest_path=new_manifest_path,
    )

    result = manager.prepare()

    assert result["ok"] is False
    assert result["reason"] == "runtime_archive_checksum_mismatch"


def test_show_runtime_manager_installs_manifest_archive_from_verified_offline_cache(monkeypatch, tmp_path):
    archive_path = _write_runtime_archive(tmp_path)
    manifest_path = _write_runtime_manifest(tmp_path, archive_path)
    digest = _sha256(archive_path)
    runtime_dir = tmp_path / "runtime"
    cached = runtime_dir / "downloads" / f"{digest}.tgz"
    cached.parent.mkdir(parents=True)
    cached.write_bytes(archive_path.read_bytes())
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archives"][_runtime_platform_tag()]["url"] = "https://example.invalid/runtime.tgz"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        manifest_path=manifest_path,
        offline=True,
    )
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: ["/bin/node"] if command == "node" else None)

    result = manager.prepare()

    assert result["ok"] is True
    assert result["reason"] is None


def test_show_runtime_manager_status_does_not_read_manifest_for_legacy_sources(tmp_path):
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
        runtime_source="npm",
        auto_install=False,
    )

    status = manager.status()

    assert status["provider"] == "npm"
    assert status["manifest"] is None
    assert status["reason"] is None


def test_show_runtime_manager_can_disable_auto_install(tmp_path):
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
        runtime_source="npm",
        auto_install=False,
    )

    assert asyncio.run(manager._resolve_managed_command()) is None
    assert manager._install_reason == "runtime_command_missing"


def test_show_runtime_manager_installs_without_blocking_event_loop(monkeypatch, tmp_path):
    monkeypatch.setattr("core.show_runtime._packaged_runtime_manifest_exists", lambda: True)
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


def test_show_runtime_manager_defaults_to_archive_when_package_manifest_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr("core.show_runtime._packaged_runtime_manifest_exists", lambda: False)

    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
    )

    assert manager.runtime_source == "archive"


def test_show_runtime_manager_defaults_to_manifest_when_package_manifest_exists(monkeypatch, tmp_path):
    monkeypatch.setattr("core.show_runtime._packaged_runtime_manifest_exists", lambda: True)

    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
    )

    assert manager.runtime_source == "manifest-cache"


def test_show_runtime_manager_installs_from_github_source(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "runtime"
    source_dir = runtime_dir / "source" / "github" / "avibe-bot_vibe-show-runtime" / "main"
    commands = []

    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        runtime_source="github",
        github_repo="https://github.com/avibe-bot/vibe-show-runtime.git",
        github_ref="main",
    )

    monkeypatch.setattr(
        "core.show_runtime._resolve_command",
        lambda command: [f"/bin/{command}"] if command in {"git", "npm", "node"} else None,
    )

    def fake_run(command, *, cwd=None):
        commands.append((command, cwd))
        if command[:2] == ["/bin/npm", "run"]:
            cli_path = source_dir / "packages" / "runtime" / "dist" / "cli.js"
            cli_path.parent.mkdir(parents=True, exist_ok=True)
            cli_path.write_text("#!/usr/bin/env node\n", encoding="utf-8")
        return True

    monkeypatch.setattr(manager, "_run_install_command", fake_run)

    assert manager._install_managed_runtime() == ["/bin/node", str(source_dir / "packages" / "runtime" / "dist" / "cli.js")]
    assert commands == [
        (
            [
                "/bin/git",
                "clone",
                "--depth",
                "1",
                "--branch",
                "main",
                "https://github.com/avibe-bot/vibe-show-runtime.git",
                str(source_dir),
            ],
            None,
        ),
        (["/bin/npm", "ci"], source_dir),
        (["/bin/npm", "run", "build"], source_dir),
    ]


def test_show_runtime_manager_reuses_installed_github_runtime_when_update_fails(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "runtime"
    source_dir = runtime_dir / "source" / "github" / "avibe-bot_vibe-show-runtime" / "main"
    cli_path = source_dir / "packages" / "runtime" / "dist" / "cli.js"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    commands = []

    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        runtime_source="github",
        github_repo="https://github.com/avibe-bot/vibe-show-runtime.git",
        github_ref="main",
    )

    monkeypatch.setattr(
        "core.show_runtime._resolve_command",
        lambda command: [f"/bin/{command}"] if command in {"git", "npm", "node"} else None,
    )

    def fake_run(command, *, cwd=None):
        commands.append((command, cwd))
        return False

    monkeypatch.setattr(manager, "_run_install_command", fake_run)

    assert manager._install_managed_runtime() == ["/bin/node", str(cli_path)]
    assert manager._install_reason is None
    assert commands == [
        (
            ["/bin/git", "-C", str(source_dir), "fetch", "--depth", "1", "origin", "main"],
            None,
        )
    ]


def test_show_runtime_manager_reuses_installed_github_runtime_without_git(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "runtime"
    source_dir = runtime_dir / "source" / "github" / "avibe-bot_vibe-show-runtime" / "main"
    cli_path = source_dir / "packages" / "runtime" / "dist" / "cli.js"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text("#!/usr/bin/env node\n", encoding="utf-8")

    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        runtime_source="github",
        github_repo="https://github.com/avibe-bot/vibe-show-runtime.git",
        github_ref="main",
    )

    monkeypatch.setattr(
        "core.show_runtime._resolve_command",
        lambda command: ["/bin/node"] if command == "node" else None,
    )

    assert manager._install_managed_runtime() == ["/bin/node", str(cli_path)]
    assert manager._install_reason is None


def test_show_runtime_manager_reuses_github_runtime_after_install_attempt(monkeypatch, tmp_path):
    runtime_dir = tmp_path / "runtime"
    source_dir = runtime_dir / "source" / "github" / "avibe-bot_vibe-show-runtime" / "main"
    cli_path = source_dir / "packages" / "runtime" / "dist" / "cli.js"
    cli_path.parent.mkdir(parents=True)
    cli_path.write_text("#!/usr/bin/env node\n", encoding="utf-8")

    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=runtime_dir,
        runtime_source="github",
        github_repo="https://github.com/avibe-bot/vibe-show-runtime.git",
        github_ref="main",
    )
    manager._install_attempted = True

    monkeypatch.setattr(
        "core.show_runtime._resolve_command",
        lambda command: ["/bin/node"] if command == "node" else None,
    )

    assert asyncio.run(manager._resolve_managed_command()) == ["/bin/node", str(cli_path)]
    assert manager._managed_command == ["/bin/node", str(cli_path)]


def test_show_runtime_manager_reuses_cached_managed_command_after_install_attempt(monkeypatch, tmp_path):
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
        runtime_source="github",
    )
    manager._install_attempted = True
    manager._managed_command = ["/bin/node", "/tmp/runtime/cli.js"]
    monkeypatch.setattr("core.show_runtime._resolve_command", lambda command: None)

    assert asyncio.run(manager._resolve_managed_command()) == ["/bin/node", "/tmp/runtime/cli.js"]


def test_show_runtime_manager_can_use_npm_source(monkeypatch, tmp_path):
    manager = ShowRuntimeManager(
        workspace_root=tmp_path / "show",
        runtime_dir=tmp_path / "runtime",
        runtime_source="npm",
    )
    called = []
    monkeypatch.setattr(manager, "_install_npm_runtime", lambda: called.append("npm") or ["/tmp/avibe-show-runtime"])

    assert manager._install_managed_runtime() == ["/tmp/avibe-show-runtime"]
    assert called == ["npm"]


def test_show_runtime_shutdown_stops_manager():
    from vibe.ui_server import stop_show_runtime_on_shutdown

    manager = _FakeShowRuntimeManager()
    set_show_runtime_manager_for_tests(manager)
    try:
        stop_show_runtime_on_shutdown()
    finally:
        set_show_runtime_manager_for_tests(None)

    assert manager.stopped is True


def test_show_runtime_shutdown_cancels_startup_reconcile_before_stopping_manager():
    from vibe.ui_server import _stop_startup_dependency_reconcile, stop_show_runtime_on_shutdown

    shutdown_handlers = app.router.on_shutdown

    assert shutdown_handlers.index(_stop_startup_dependency_reconcile) < shutdown_handlers.index(stop_show_runtime_on_shutdown)


def test_startup_dependency_reconcile_prewarms_runtime_after_prepare(monkeypatch):
    from vibe.ui_server import _reconcile_startup_dependencies_task

    called = {"reconcile": 0, "runtime": 0, "sessions": []}

    def fake_reconcile():
        called["reconcile"] += 1
        return {"ok": True, "show_runtime": {"ok": True}, "askill": {"ok": True}}

    async def fake_runtime_prewarm():
        called["runtime"] += 1
        return SimpleNamespace(available=True, reason=None)

    async def fake_session_prewarm(session_id, *, base_path=None):
        called["sessions"].append((session_id, base_path))
        return SimpleNamespace(available=True, reason=None)

    monkeypatch.setattr("vibe.api.reconcile_startup_dependencies", fake_reconcile)
    monkeypatch.setattr(
        "vibe.api.startup_show_page_prewarm_targets",
        lambda: {
            "ok": True,
            "limit": 2,
            "pages": [
                {"session_id": "ses_private", "base_path": None},
                {"session_id": "ses_public", "base_path": "/p/share123/"},
            ],
        },
    )
    monkeypatch.setattr("core.show_runtime.prewarm_show_runtime", fake_runtime_prewarm)
    monkeypatch.setattr("core.show_runtime.prewarm_show_page_session", fake_session_prewarm)

    asyncio.run(_reconcile_startup_dependencies_task())

    assert called == {
        "reconcile": 1,
        "runtime": 1,
        "sessions": [("ses_private", None), ("ses_public", "/p/share123/")],
    }


def test_show_runtime_proxy_logs_entry_timing(monkeypatch, tmp_path, caplog):
    caplog.set_level("INFO")
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager(body=b"<html><body><div id=\"root\">ready</div></body></html>")
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get("/show/ses123/", base_url="http://127.0.0.1:5123")
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert "Show Runtime proxy GET /sessions/ses123/app/ session=ses123 asset=<entry>" in caplog.text


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


def test_private_show_page_hmr_websocket_accepts_setup_host_local_peer(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.ui.setup_host = "192.168.2.3"
    config.save()
    _mock_interface(monkeypatch, "192.168.2.3", 24)
    _create_show_page("ses123", "private")
    manager = _FakeShowRuntimeManager()
    set_show_runtime_manager_for_tests(manager)
    try:
        with app.test_client().websocket_connect(
            "/show/ses123/__vite_hmr",
            headers={
                "host": "192.168.2.3:5123",
                "x-vibe-test-remote-addr": "192.168.2.44",
            },
            subprotocols=["vite-hmr"],
        ) as websocket:
            websocket.receive_text()
    except Exception as exc:
        assert getattr(exc, "code", None) == 1011
    finally:
        set_show_runtime_manager_for_tests(None)

    assert manager.websocket_paths == ["/show/ses123/__vite_hmr"]


def test_public_show_page_hmr_websocket_uses_share_path(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    manager = _FakeShowRuntimeManager()
    set_show_runtime_manager_for_tests(manager)
    try:
        with app.test_client().websocket_connect(
            f"wss://alex.avibe.bot/p/{share_id}/__vite_hmr?token=test-token",
            headers={"host": "alex.avibe.bot"},
            subprotocols=["vite-hmr"],
        ) as websocket:
            websocket.receive_text()
    except Exception as exc:
        assert getattr(exc, "code", None) == 1011
    finally:
        set_show_runtime_manager_for_tests(None)

    assert manager.websocket_paths == [f"/p/{share_id}/__vite_hmr?token=test-token"]


def test_public_show_page_hmr_websocket_requires_public_page(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "private")

    try:
        with app.test_client().websocket_connect(
            f"wss://alex.avibe.bot/p/{share_id}/__vite_hmr",
            headers={"host": "alex.avibe.bot"},
            subprotocols=["vite-hmr"],
        ):
            raise AssertionError("websocket should not connect")
    except Exception as exc:
        assert getattr(exc, "code", None) == 1008


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
    set_show_runtime_manager_for_tests(_FakeShowRuntimeManager(fail=True))

    try:
        response = app.test_client().get(
            f"/p/{share_id}/",
            base_url="https://alex.avibe.bot",
            environ_base=_remote_peer(),
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert b"Loading Show Page" in response.content
        assert b"Ready to visualize" in response.content
        assert b"Copy prompt" in response.content
        assert b'src="./src/main.tsx"' not in response.content

        mismatch = app.test_client().get(
            f"/p/{share_id}/",
            base_url="https://evil.example",
            environ_base=_remote_peer(),
            follow_redirects=False,
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert mismatch.status_code == 503
    assert mismatch.get_json()["error"] == "remote_access_host_mismatch"


def test_public_show_page_uses_runtime_when_available(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    manager = _FakeShowRuntimeManager(body=b"<h1>Public Runtime Page</h1>")
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            f"/p/{share_id}/",
            base_url="https://alex.avibe.bot",
            environ_base=_remote_peer(),
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 200
    assert b"Public Runtime Page" in response.content
    assert manager.calls[0][0] == "GET"
    assert manager.calls[0][1] == "/sessions/ses123/app/"
    assert manager.calls[0][2]["x-vibe-show-base"] == f"/p/{share_id}/"


def test_public_show_page_rewrites_runtime_redirect_location(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    manager = _FakeShowRuntimeManager(
        body=b"",
        status_code=302,
        extra_headers={"location": "/sessions/ses123/app/foo/"},
    )
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().get(
            f"/p/{share_id}/foo",
            base_url="https://alex.avibe.bot",
            environ_base=_remote_peer(),
            follow_redirects=False,
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 302
    assert response.headers["location"] == f"/p/{share_id}/foo/"


def test_public_show_page_proxies_runtime_api_methods(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    manager = _FakeShowRuntimeManager(body=b'{"ok":true}', extra_headers={"content-type": "application/json"})
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().post(
            f"/p/{share_id}/api/health",
            base_url="https://alex.avibe.bot",
            environ_base=_remote_peer(),
            headers={
                "Origin": "https://alex.avibe.bot",
                "Content-Type": "application/json",
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
    assert manager.calls[0][2]["x-vibe-show-base"] == f"/p/{share_id}/"
    assert "cookie" not in manager.calls[0][2]
    assert manager.calls[0][3] == b'{"ping":true}'


def test_public_show_page_api_mutation_rejects_cross_origin(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    manager = _FakeShowRuntimeManager(body=b'{"ok":true}', extra_headers={"content-type": "application/json"})
    set_show_runtime_manager_for_tests(manager)
    try:
        response = app.test_client().post(
            f"/p/{share_id}/api/health",
            base_url="https://alex.avibe.bot",
            environ_base=_remote_peer(),
            headers={
                "Origin": "https://evil.example",
                "Content-Type": "application/json",
            },
            content=b'{"ping":true}',
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 403
    assert response.get_json()["message"] == "Forbidden: invalid origin"
    assert manager.calls == []


def test_public_show_page_api_does_not_fall_back_to_static(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    share_id = _create_show_page("ses123", "public")
    (paths.get_show_pages_dir() / "ses123" / "api" / "health.ts").write_text("export const secret = true\n", encoding="utf-8")
    set_show_runtime_manager_for_tests(_FakeShowRuntimeManager(fail=True))
    try:
        response = app.test_client().get(
            f"/p/{share_id}/api/health.ts",
            base_url="https://alex.avibe.bot",
            environ_base=_remote_peer(),
        )
    finally:
        set_show_runtime_manager_for_tests(None)

    assert response.status_code == 503
    assert response.get_json()["error"] == "show_runtime_unavailable"
    assert b"secret" not in response.content


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
