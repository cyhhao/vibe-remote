import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.agents.opencode.server import OpenCodeServerManager


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


if __name__ == "__main__":
    unittest.main()
