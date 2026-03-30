import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def post(self, url, json=None, headers=None):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return _FakeResponse()


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


if __name__ == "__main__":
    unittest.main()
