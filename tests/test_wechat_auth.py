import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.im.wechat_auth import WeChatAuthManager


class WeChatAuthManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_login_persists_base_url_on_session(self):
        manager = WeChatAuthManager()

        with patch(
            "modules.im.wechat_auth.get_bot_qrcode",
            new=AsyncMock(return_value={"qrcode": "qr-token", "qrcode_img_content": "https://example.com/qr.png"}),
        ):
            result = await manager.start_login(base_url="https://wechat.example.com")

        session = manager.get_session(result["session_key"])
        self.assertIsNotNone(session)
        self.assertEqual(session.base_url, "https://wechat.example.com")

    async def test_poll_status_returns_expired_when_session_missing(self):
        manager = WeChatAuthManager()

        result = await manager.poll_status("missing-session")

        self.assertEqual(result["status"], "expired")
        self.assertIn("start a new login", result["message"].lower())

    async def test_start_login_returns_error_payload_when_qr_fetch_fails(self):
        manager = WeChatAuthManager()

        with patch(
            "modules.im.wechat_auth.get_bot_qrcode",
            new=AsyncMock(side_effect=RuntimeError("upstream unavailable")),
        ):
            result = await manager.start_login(base_url="https://wechat.example.com")

        self.assertFalse(result["ok"])
        self.assertIn("Failed to start login", result["error"])
        self.assertIsNone(manager.get_session(result["session_key"]))

    async def test_wait_for_login_returns_immediately_when_session_missing(self):
        manager = WeChatAuthManager()

        result = await manager.wait_for_login("missing-session", timeout_s=5)

        self.assertEqual(result["status"], "expired")
        self.assertIn("start a new login", result["message"].lower())
