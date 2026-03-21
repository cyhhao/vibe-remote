import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from modules.im import MessageContext
from modules.im.base import FileAttachment


def _load_message_handler_class():
    with patch.dict(sys.modules, {}, clear=False):
        agents_module = types.ModuleType("modules.agents")
        setattr(agents_module, "AgentRequest", type("AgentRequest", (), {}))
        sys.modules["modules.agents"] = agents_module

        core_pkg = types.ModuleType("core")
        core_pkg.__path__ = [str(ROOT / "core")]
        sys.modules["core"] = core_pkg

        handlers_pkg = types.ModuleType("core.handlers")
        handlers_pkg.__path__ = [str(ROOT / "core" / "handlers")]
        sys.modules["core.handlers"] = handlers_pkg

        base_name = "core.handlers.base"
        base_spec = importlib.util.spec_from_file_location(base_name, ROOT / "core" / "handlers" / "base.py")
        assert base_spec is not None
        assert base_spec.loader is not None
        base_module = importlib.util.module_from_spec(base_spec)
        sys.modules[base_name] = base_module
        base_spec.loader.exec_module(base_module)

        module_name = "core.handlers.message_handler"
        spec = importlib.util.spec_from_file_location(module_name, ROOT / "core" / "handlers" / "message_handler.py")
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module.MessageHandler


MessageHandler = _load_message_handler_class()


class _StubIMClient:
    def __init__(self, download_result=None, stream_result=False):
        self.download_result = download_result
        self.stream_result = stream_result
        self.download_calls = []
        self.stream_calls = []
        self.sent_messages = []
        self.formatter = None

    def should_use_thread_for_reply(self):
        return bool(False)

    async def send_message(self, context, text, parse_mode=None, reply_to=None):
        self.sent_messages.append((context.channel_id, context.thread_id, text, parse_mode, reply_to))
        return "M1"

    async def download_file(self, file_info):
        self.download_calls.append(file_info)
        return self.download_result

    async def download_file_to_path(self, file_info, target_path, max_bytes=None, timeout_seconds=30):
        self.stream_calls.append((file_info, target_path, max_bytes, timeout_seconds))
        if not self.stream_result:
            return False
        Path(target_path).write_bytes(self.download_result or b"")
        return True


class _StubIMClientNoStream:
    def __init__(self, download_result=None):
        self.download_result = download_result
        self.download_calls = []
        self.stream_calls = []
        self.sent_messages = []
        self.formatter = None

    def should_use_thread_for_reply(self):
        return bool(False)

    async def send_message(self, context, text, parse_mode=None, reply_to=None):
        self.sent_messages.append((context.channel_id, context.thread_id, text, parse_mode, reply_to))
        return "M1"

    async def download_file(self, file_info):
        self.download_calls.append(file_info)
        return self.download_result


class _StubController:
    def __init__(self, im_client):
        self.config = type("Config", (), {"platform": "slack", "language": "en"})()
        self.im_client = im_client
        self.settings_manager = type("Settings", (), {})()
        self.session_manager = object()
        self.receiver_tasks = {}


class MessageHandlerAttachmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_file_attachments_streams_large_files_without_app_limit(self):
        im_client = _StubIMClient(download_result=b"%PDF-1.7\n", stream_result=True)
        handler = MessageHandler(_StubController(im_client))
        attachment = FileAttachment(
            name="large.pdf",
            mimetype="application/pdf",
            url="https://example.test/large.pdf",
            size=150 * 1024 * 1024,
        )
        context = MessageContext(user_id="U1", channel_id="C1", thread_id="T1", files=[attachment])

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("config.paths.get_attachments_dir", return_value=Path(tmpdir)):
                processed = await handler._process_file_attachments(context, "/tmp/work")

        self.assertIsNotNone(processed)
        assert processed is not None
        self.assertEqual(len(processed), 1)
        self.assertEqual(im_client.sent_messages, [])
        self.assertEqual(im_client.download_calls, [])
        self.assertEqual(len(im_client.stream_calls), 1)
        self.assertIsNone(im_client.stream_calls[0][2])
        self.assertEqual(processed[0].size, len(b"%PDF-1.7\n"))

    async def test_process_file_attachments_falls_back_to_in_memory_download_when_streaming_unavailable(self):
        im_client = _StubIMClientNoStream(download_result=b"%PDF-1.7\n")
        handler = MessageHandler(_StubController(im_client))
        attachment = FileAttachment(
            name="supported.pdf",
            mimetype="application/pdf",
            url="https://example.test/supported.pdf",
            size=50 * 1024 * 1024,
        )
        context = MessageContext(user_id="U1", channel_id="C1", files=[attachment])

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("config.paths.get_attachments_dir", return_value=Path(tmpdir)):
                processed = await handler._process_file_attachments(context, "/tmp/work")

        self.assertIsNotNone(processed)
        assert processed is not None
        self.assertEqual(len(processed), 1)
        self.assertEqual(im_client.sent_messages, [])
        self.assertEqual(len(im_client.download_calls), 1)
        self.assertEqual(im_client.stream_calls, [])
        self.assertEqual(processed[0].size, len(b"%PDF-1.7\n"))
        self.assertTrue(processed[0].local_path)


if __name__ == "__main__":
    unittest.main()
