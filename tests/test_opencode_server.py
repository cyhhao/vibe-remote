import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

aiohttp_stub = types.ModuleType("aiohttp")
aiohttp_stub.ClientSession = object
aiohttp_stub.ClientTimeout = object
sys.modules.setdefault("aiohttp", aiohttp_stub)

MODULE_PATH = Path(__file__).resolve().parents[1] / "modules" / "agents" / "opencode" / "server.py"
SPEC = importlib.util.spec_from_file_location("opencode_server_for_test", MODULE_PATH)
assert SPEC and SPEC.loader
SERVER_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SERVER_MODULE)
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


if __name__ == "__main__":
    unittest.main()
