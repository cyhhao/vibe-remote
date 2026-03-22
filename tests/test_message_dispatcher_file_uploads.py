import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.message_dispatcher import ConsolidatedMessageDispatcher
from core.reply_enhancer import FileLink
from modules.im import MessageContext


class _StubController:
    def __init__(self):
        self.config = type("Config", (), {"platform": "slack"})()


class _StubIMClient:
    def __init__(self):
        self.file_uploads = []
        self.image_uploads = []
        self.video_uploads = []

    async def upload_file_from_path(self, context, file_path, title=None):
        self.file_uploads.append((context.channel_id, file_path, title))

    async def upload_image_from_path(self, context, file_path, title=None):
        self.image_uploads.append((context.channel_id, file_path, title))

    async def upload_video_from_path(self, context, file_path, title=None):
        self.video_uploads.append((context.channel_id, file_path, title))


class MessageDispatcherFileUploadTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_file_link_allows_path_outside_cwd(self):
        dispatcher = ConsolidatedMessageDispatcher(_StubController())
        im_client = _StubIMClient()
        context = MessageContext(user_id="U1", channel_id="C1")

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "report.txt"
            file_path.write_text("hello", encoding="utf-8")
            resolved_path = str(file_path.resolve())

            await dispatcher._upload_file_links(
                im_client,
                context,
                [FileLink(label="report", path=str(file_path))],
            )

        self.assertEqual(im_client.file_uploads, [("C1", resolved_path, "report.txt")])
        self.assertEqual(im_client.image_uploads, [])

    async def test_upload_image_link_allows_path_outside_cwd(self):
        dispatcher = ConsolidatedMessageDispatcher(_StubController())
        im_client = _StubIMClient()
        context = MessageContext(user_id="U1", channel_id="C1")

        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "screenshot.png"
            image_path.write_bytes(b"png")
            resolved_path = str(image_path.resolve())

            await dispatcher._upload_file_links(
                im_client,
                context,
                [FileLink(label="preview", path=str(image_path), is_image=True)],
            )

        self.assertEqual(im_client.file_uploads, [])
        self.assertEqual(im_client.image_uploads, [("C1", resolved_path, "preview.png")])
        self.assertEqual(im_client.video_uploads, [])

    async def test_upload_video_link_uses_video_channel_even_for_image_syntax(self):
        dispatcher = ConsolidatedMessageDispatcher(_StubController())
        im_client = _StubIMClient()
        context = MessageContext(user_id="U1", channel_id="C1")

        with tempfile.TemporaryDirectory() as tmpdir:
            video_path = Path(tmpdir) / "clip.mp4"
            video_path.write_bytes(b"mp4")
            resolved_path = str(video_path.resolve())

            await dispatcher._upload_file_links(
                im_client,
                context,
                [FileLink(label="preview", path=str(video_path), is_image=True)],
            )

        self.assertEqual(im_client.file_uploads, [])
        self.assertEqual(im_client.image_uploads, [])
        self.assertEqual(im_client.video_uploads, [("C1", resolved_path, "preview.mp4")])


if __name__ == "__main__":
    unittest.main()
