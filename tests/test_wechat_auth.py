import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_settings import SettingsStore
from modules.im.wechat_auth import WeChatAuthManager
from vibe import api as vibe_api


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

    async def test_auto_bind_wechat_user_marks_one_time_menu_hint_pending(self):
        SettingsStore.reset_instance()

        with patch("vibe.api.load_config") as load_config:
            load_config.return_value.runtime.default_cwd = "/tmp/vibe"
            load_config.return_value.agents.default_backend = "opencode"
            result = vibe_api.auto_bind_wechat_user("wx-user")

        self.assertTrue(result["ok"])
        self.assertFalse(result["already_bound"])
        self.assertTrue(result["pending_bind_menu_hint"])

        user = SettingsStore.get_instance().get_user("wx-user", platform="wechat")
        self.assertIsNotNone(user)
        self.assertTrue(user.pending_bind_menu_hint)  # type: ignore[union-attr]

    async def test_auto_bind_wechat_user_does_not_rearm_existing_user_hint(self):
        SettingsStore.reset_instance()
        store = SettingsStore.get_instance()
        store.add_user("wx-user", "WeChat User", platform="wechat")

        result = vibe_api.auto_bind_wechat_user("wx-user")

        self.assertTrue(result["ok"])
        self.assertTrue(result["already_bound"])
        self.assertFalse(result["pending_bind_menu_hint"])
