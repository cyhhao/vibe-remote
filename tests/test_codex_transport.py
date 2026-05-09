import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.agents.codex.transport import CodexTransport, STREAM_BUFFER_LIMIT


class CodexTransportHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_reader_task_failure_marks_transport_not_alive(self):
        transport = CodexTransport(binary="codex", cwd="/tmp")
        transport._process = SimpleNamespace(returncode=None)

        async def done():
            return None

        task = asyncio.create_task(done())
        await task
        transport._reader_task = task

        self.assertFalse(transport.is_alive)
        self.assertFalse(transport.is_initialized)

    async def test_send_request_fails_fast_when_reader_task_is_done(self):
        transport = CodexTransport(binary="codex", cwd="/tmp")
        transport._process = SimpleNamespace(returncode=None)

        async def done():
            return None

        task = asyncio.create_task(done())
        await task
        transport._reader_task = task

        with self.assertRaises(ConnectionError):
            await transport.send_request("thread/start", {})

        self.assertEqual(transport._pending, {})

    async def test_send_notification_fails_fast_when_reader_task_is_done(self):
        transport = CodexTransport(binary="codex", cwd="/tmp")
        transport._process = SimpleNamespace(returncode=None)

        async def done():
            return None

        task = asyncio.create_task(done())
        await task
        transport._reader_task = task

        with self.assertRaises(ConnectionError):
            await transport.send_notification("initialized")

    def test_stream_buffer_limit_allows_large_codex_thread_responses(self):
        self.assertGreaterEqual(STREAM_BUFFER_LIMIT, 128 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
