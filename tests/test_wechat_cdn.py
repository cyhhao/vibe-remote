import unittest
from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.im import wechat_cdn


class WeChatCdnTests(unittest.IsolatedAsyncioTestCase):
    def test_resolve_cdn_upload_url_accepts_legacy_upload_param(self):
        url = wechat_cdn._resolve_cdn_upload_url(
            "https://novac2c.cdn.weixin.qq.com/c2c",
            {"upload_param": "abc+/="},
            "file-key",
            "upload_image_to_cdn",
        )

        self.assertEqual(
            url,
            "https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param=abc%2B/%3D&filekey=file-key",
        )

    def test_resolve_cdn_upload_url_accepts_upload_full_url(self):
        url = wechat_cdn._resolve_cdn_upload_url(
            "https://novac2c.cdn.weixin.qq.com/c2c",
            {
                "upload_full_url": (
                    "https://novac2c.cdn.weixin.qq.com/c2c/upload?"
                    "encrypted_query_param=abc&amp;filekey=file-key&amp;taskid=task-1"
                )
            },
            "ignored-file-key",
            "upload_image_to_cdn",
        )

        self.assertEqual(
            url,
            "https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param=abc&filekey=file-key&taskid=task-1",
        )

    async def test_upload_image_to_cdn_uses_upload_full_url_response(self):
        with patch(
            "modules.im.wechat_cdn.get_upload_url",
            new=AsyncMock(
                return_value={
                    "upload_full_url": (
                        "https://novac2c.cdn.weixin.qq.com/c2c/upload?"
                        "encrypted_query_param=abc&filekey=file-key&taskid=task-1"
                    )
                }
            ),
        ):
            with patch(
                "modules.im.wechat_cdn.upload_buffer_to_cdn", new=AsyncMock(return_value="download-param")
            ) as mock_upload:
                with patch("os.urandom", side_effect=[bytes.fromhex("11" * 16), bytes.fromhex("22" * 16)]):
                    with patch.object(Path, "read_bytes", return_value=b"png"):
                        result = await wechat_cdn.upload_image_to_cdn(
                            base_url="https://ilinkai.weixin.qq.com",
                            token="token",
                            cdn_base_url="https://novac2c.cdn.weixin.qq.com/c2c",
                            to_user_id="user-1",
                            file_path="/tmp/photo.png",
                        )

        self.assertEqual(result["encrypt_query_param"], "download-param")
        self.assertEqual(result["filekey"], "11111111111111111111111111111111")
        self.assertEqual(
            mock_upload.await_args.kwargs["upload_url"],
            "https://novac2c.cdn.weixin.qq.com/c2c/upload?encrypted_query_param=abc&filekey=file-key&taskid=task-1",
        )


if __name__ == "__main__":
    unittest.main()
