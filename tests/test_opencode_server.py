import asyncio
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODULE_PATH = Path(__file__).resolve().parents[1] / "modules" / "agents" / "opencode" / "server.py"


def _load_server_module():
    aiohttp_stub = types.ModuleType("aiohttp")
    aiohttp_stub.ClientSession = object
    aiohttp_stub.ClientTimeout = object
    previous_aiohttp = sys.modules.get("aiohttp")
    sys.modules["aiohttp"] = aiohttp_stub
    try:
        spec = importlib.util.spec_from_file_location("opencode_server_for_test", MODULE_PATH)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_aiohttp is None:
            sys.modules.pop("aiohttp", None)
        else:
            sys.modules["aiohttp"] = previous_aiohttp


SERVER_MODULE = _load_server_module()
OpenCodeServerManager = SERVER_MODULE.OpenCodeServerManager


class _FakeResponse:
    def __init__(self, *, status: int = 204, text: str = ""):
        self.status = status
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self):
        return self._text


class _FakeSession:
    def __init__(self):
        self.posts = []
        self.puts = []
        self.closed = False

    def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()

    def put(self, url, json=None, headers=None):
        self.puts.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse(status=200)

    async def close(self):
        self.closed = True


class OpenCodeServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_async_includes_tools_when_provided(self):
        manager = OpenCodeServerManager(binary="opencode", port=4096)
        fake_session = _FakeSession()

        async def _fake_get_http_session():
            return fake_session

        manager._get_http_session = _fake_get_http_session  # type: ignore[method-assign]

        await manager.prompt_async(
            session_id="ses-1",
            directory="/tmp/work",
            text="hello",
            tools={"question": False},
        )

        self.assertEqual(len(fake_session.posts), 1)
        body = fake_session.posts[0]["json"]
        self.assertEqual(body["tools"], {"question": False})

    async def test_load_opencode_user_config_supports_jsonc(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_home = Path(tmp_dir)
            config_path = tmp_home / ".config" / "opencode" / "opencode.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(
                """{
  // Preserve defaults from JSONC config.
  "model": "openai/gpt-5",
  "reasoningEffort": "high",
}
""",
                encoding="utf-8",
            )

            manager = OpenCodeServerManager(binary="opencode", port=4096)
            with patch("vibe.opencode_config.Path.home", return_value=tmp_home):
                config = manager._load_opencode_user_config()

            self.assertEqual(
                config,
                {
                    "model": "openai/gpt-5",
                    "reasoningEffort": "high",
                },
            )

    async def test_find_opencode_serve_pids_windows_uses_netstat_and_command_lookup(self):
        netstat_output = """
  TCP    127.0.0.1:4096     0.0.0.0:0      LISTENING       1234
  TCP    127.0.0.1:7777     0.0.0.0:0      LISTENING       7777
"""

        with patch.object(SERVER_MODULE.os, "name", "nt"):
            with patch.object(
                SERVER_MODULE.subprocess,
                "run",
                return_value=types.SimpleNamespace(stdout=netstat_output),
            ):
                with patch.object(
                    SERVER_MODULE.runtime,
                    "get_process_command",
                    side_effect=lambda pid: "opencode serve --port=4096" if pid == 1234 else "python app.py",
                ):
                    pids = OpenCodeServerManager._find_opencode_serve_pids(4096)

        self.assertEqual(pids, [1234])

    async def test_restart_for_auth_refresh_stops_known_server_and_clears_state(self):
        manager = OpenCodeServerManager(binary="opencode", port=4096)
        fake_session = _FakeSession()
        manager._http_session = fake_session
        manager._http_session_loop = object()
        manager._process = object()
        manager._base_url = "http://127.0.0.1:4096"
        manager._read_pid_file = lambda: {"pid": 321}  # type: ignore[method-assign]
        manager._pid_exists = lambda pid: pid == 321  # type: ignore[method-assign]
        manager._get_pid_command = lambda pid: "opencode serve --port=4096"  # type: ignore[method-assign]
        terminated = []
        manager._terminate_pid = lambda pid, reason: terminated.append((pid, reason)) or _async_none()  # type: ignore[method-assign]
        manager._clear_pid_file = lambda: terminated.append(("cleared", ""))  # type: ignore[method-assign]

        await manager.restart_for_auth_refresh()

        self.assertTrue(fake_session.closed)
        self.assertIn((321, "auth refresh"), terminated)
        self.assertIn(("cleared", ""), terminated)
        self.assertIsNone(manager._process)
        self.assertIsNone(manager._base_url)

    async def test_restart_for_auth_refresh_trusts_pid_file_when_command_lookup_unavailable(self):
        manager = OpenCodeServerManager(binary="opencode", port=4096)
        fake_session = _FakeSession()
        manager._http_session = fake_session
        manager._http_session_loop = object()
        manager._process = object()
        manager._base_url = "http://127.0.0.1:4096"
        manager._read_pid_file = lambda: {"pid": 654, "port": 4096}  # type: ignore[method-assign]
        manager._pid_exists = lambda pid: pid == 654  # type: ignore[method-assign]
        manager._get_pid_command = lambda pid: None  # type: ignore[method-assign]
        manager._pid_owns_listening_port = lambda pid, port: pid == 654 and port == 4096  # type: ignore[method-assign]
        terminated = []
        manager._terminate_pid = lambda pid, reason: terminated.append((pid, reason)) or _async_none()  # type: ignore[method-assign]
        manager._clear_pid_file = lambda: terminated.append(("cleared", ""))  # type: ignore[method-assign]

        await manager.restart_for_auth_refresh()

        self.assertTrue(fake_session.closed)
        self.assertIn((654, "auth refresh"), terminated)
        self.assertIn(("cleared", ""), terminated)

    async def test_restart_for_auth_refresh_does_not_trust_pid_file_without_port_ownership(self):
        manager = OpenCodeServerManager(binary="opencode", port=4096)
        fake_session = _FakeSession()
        manager._http_session = fake_session
        manager._http_session_loop = object()
        manager._process = object()
        manager._base_url = "http://127.0.0.1:4096"
        manager._read_pid_file = lambda: {"pid": 654, "port": 4096}  # type: ignore[method-assign]
        manager._pid_exists = lambda pid: pid == 654  # type: ignore[method-assign]
        manager._get_pid_command = lambda pid: None  # type: ignore[method-assign]
        manager._pid_owns_listening_port = lambda pid, port: False  # type: ignore[method-assign]
        manager._find_opencode_serve_pids = lambda port: []  # type: ignore[method-assign]
        terminated = []
        manager._terminate_pid = lambda pid, reason: terminated.append((pid, reason)) or _async_none()  # type: ignore[method-assign]
        manager._clear_pid_file = lambda: terminated.append(("cleared", ""))  # type: ignore[method-assign]

        await manager.restart_for_auth_refresh()

        self.assertTrue(fake_session.closed)
        self.assertNotIn((654, "auth refresh"), terminated)
        self.assertEqual(terminated, [("cleared", "")])

    async def test_restart_for_auth_refresh_defers_while_requests_are_active(self):
        manager = OpenCodeServerManager(binary="opencode", port=4096)
        fake_session = _FakeSession()
        manager._http_session = fake_session
        manager._http_session_loop = object()
        manager._process = object()
        manager._base_url = "http://127.0.0.1:4096"
        manager._active_requests = 2
        terminated = []
        manager._terminate_pid = lambda pid, reason: terminated.append((pid, reason)) or _async_none()  # type: ignore[method-assign]
        manager._clear_pid_file = lambda: terminated.append(("cleared", ""))  # type: ignore[method-assign]

        await manager.restart_for_auth_refresh()

        self.assertFalse(fake_session.closed)
        self.assertEqual(terminated, [])
        self.assertTrue(manager._auth_refresh_pending)
        self.assertIsNotNone(manager._process)
        self.assertEqual(manager._base_url, "http://127.0.0.1:4096")

    async def test_restart_for_auth_refresh_defers_while_runs_are_active(self):
        manager = OpenCodeServerManager(binary="opencode", port=4096)
        fake_session = _FakeSession()
        manager._http_session = fake_session
        manager._http_session_loop = object()
        manager._process = object()
        manager._base_url = "http://127.0.0.1:4096"
        manager._active_run_sessions.add("sess-1")
        terminated = []
        manager._terminate_pid = lambda pid, reason: terminated.append((pid, reason)) or _async_none()  # type: ignore[method-assign]
        manager._clear_pid_file = lambda: terminated.append(("cleared", ""))  # type: ignore[method-assign]

        await manager.restart_for_auth_refresh()

        self.assertFalse(fake_session.closed)
        self.assertEqual(terminated, [])
        self.assertTrue(manager._auth_refresh_pending)
        self.assertIsNotNone(manager._process)
        self.assertEqual(manager._base_url, "http://127.0.0.1:4096")

    async def test_request_scope_does_not_restart_pending_auth_refresh_while_run_active(self):
        manager = OpenCodeServerManager(binary="opencode", port=4096)
        manager._auth_refresh_pending = True
        manager._active_run_sessions.add("sess-1")
        restarted = []
        manager._restart_for_auth_refresh_locked = lambda: restarted.append(True) or _async_none()  # type: ignore[method-assign]

        async with manager._request_scope():
            self.assertEqual(manager._active_requests, 1)

        self.assertEqual(restarted, [])
        self.assertEqual(manager._active_requests, 0)
        self.assertTrue(manager._auth_refresh_pending)

    async def test_close_http_session_skips_session_owned_by_another_loop(self):
        manager = OpenCodeServerManager(binary="opencode", port=4096)
        fake_session = _FakeSession()
        manager._http_session = fake_session
        manager._http_session_loop = object()

        await manager.close_http_session(loop=asyncio.get_running_loop())

        self.assertFalse(fake_session.closed)
        self.assertIs(manager._http_session, fake_session)

    async def test_close_http_session_closes_session_for_matching_loop(self):
        manager = OpenCodeServerManager(binary="opencode", port=4096)
        fake_session = _FakeSession()
        current_loop = asyncio.get_running_loop()
        manager._http_session = fake_session
        manager._http_session_loop = current_loop

        await manager.close_http_session(loop=current_loop)

        self.assertTrue(fake_session.closed)
        self.assertIsNone(manager._http_session)

    async def test_set_api_key_auth_uses_official_auth_endpoint(self):
        manager = OpenCodeServerManager(binary="opencode", port=4096)
        fake_session = _FakeSession()

        async def _fake_get_http_session():
            return fake_session

        manager._get_http_session = _fake_get_http_session  # type: ignore[method-assign]
        manager.ensure_running = AsyncMock()  # type: ignore[method-assign]
        manager._base_url = "http://127.0.0.1:4096"

        await manager.set_api_key_auth("opencode", "sk-test-key")

        manager.ensure_running.assert_awaited_once()
        self.assertEqual(
            fake_session.puts,
            [
                {
                    "url": "http://127.0.0.1:4096/auth/opencode",
                    "json": {"type": "api", "key": "sk-test-key"},
                    "headers": None,
                }
            ],
        )


async def _async_none():
    return None


if __name__ == "__main__":
    unittest.main()
