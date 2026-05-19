import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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

    async def test_send_wechat_bind_success_hint_uses_start_instruction(self):
        sent = {}

        async def fake_send_message(base_url, token, to_user_id, context_token, item_list):
            sent.update(
                {
                    "base_url": base_url,
                    "token": token,
                    "to_user_id": to_user_id,
                    "context_token": context_token,
                    "item_list": item_list,
                }
            )
            return {}

        config = SimpleNamespace(language="zh")

        with patch("vibe.api.load_config", return_value=config), patch(
            "modules.im.wechat_api.send_message",
            new=AsyncMock(side_effect=fake_send_message),
        ):
            result = await vibe_api.send_wechat_bind_success_hint(
                user_id="wx-user",
                bot_token="token",
                base_url="https://wechat.example.com",
                is_admin=True,
            )

        self.assertTrue(result)
        self.assertEqual(sent["to_user_id"], "wx-user")
        self.assertIn("绑定成功", sent["item_list"][0]["text_item"]["text"])
        self.assertIn("第一个用户", sent["item_list"][0]["text_item"]["text"])
        self.assertIn("/start", sent["item_list"][0]["text_item"]["text"])

    async def test_send_wechat_bind_success_hint_uses_neutral_success_for_non_admin(self):
        sent = {}

        async def fake_send_message(base_url, token, to_user_id, context_token, item_list):
            sent["text"] = item_list[0]["text_item"]["text"]
            return {}

        config = SimpleNamespace(language="zh")

        with patch("vibe.api.load_config", return_value=config), patch(
            "modules.im.wechat_api.send_message",
            new=AsyncMock(side_effect=fake_send_message),
        ):
            result = await vibe_api.send_wechat_bind_success_hint(
                user_id="wx-user",
                bot_token="token",
                base_url="https://wechat.example.com",
                is_admin=False,
            )

        self.assertTrue(result)
        self.assertIn("绑定成功", sent["text"])
        self.assertNotIn("第一个用户", sent["text"])
        self.assertIn("/start", sent["text"])

    async def test_send_wechat_bind_success_hint_handles_existing_binding(self):
        sent = {}

        async def fake_send_message(base_url, token, to_user_id, context_token, item_list):
            sent["text"] = item_list[0]["text_item"]["text"]
            return {}

        config = SimpleNamespace(language="zh")

        with patch("vibe.api.load_config", return_value=config), patch(
            "modules.im.wechat_api.send_message",
            new=AsyncMock(side_effect=fake_send_message),
        ):
            result = await vibe_api.send_wechat_bind_success_hint(
                user_id="wx-user",
                bot_token="token",
                base_url="https://wechat.example.com",
                already_bound=True,
            )

        self.assertTrue(result)
        self.assertIn("已经绑定过了", sent["text"])
        self.assertIn("/start", sent["text"])
