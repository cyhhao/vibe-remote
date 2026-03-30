from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.im import MessageContext
from modules.im.discord import DiscordBot


class _FakeSessions:
    def has_any_agent_session_base(self, user_id, base_session_id):
        return user_id == "discord::C123" and base_session_id == "discord_555"


class _FakeChannel:
    async def fetch_message(self, message_id):
        return SimpleNamespace(id=message_id, thread=SimpleNamespace(id=777))


class DiscordReplyAnchorTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_turn_context_uses_reply_anchor_thread_when_known_session_exists(self):
        bot = object.__new__(DiscordBot)
        bot.sessions = _FakeSessions()
        bot._loop = None

        async def _fetch_channel(channel_id):
            self.assertEqual(channel_id, "C123")
            return _FakeChannel()

        async def _maybe_create_thread(message):
            raise AssertionError("existing thread should be reused")

        bot._fetch_channel = _fetch_channel
        bot._maybe_create_thread = _maybe_create_thread

        message = SimpleNamespace(guild=object(), reference=SimpleNamespace(message_id=555))
        context = MessageContext(
            user_id="U123",
            channel_id="C123",
            platform="discord",
            message_id="999",
            platform_specific={"message": message, "is_dm": False},
        )

        prepared = await DiscordBot.prepare_turn_context(bot, context, "human")

        self.assertEqual(prepared.thread_id, "777")
        self.assertEqual(prepared.platform_specific["reply_anchor_base_session_id"], "discord_555")
        self.assertEqual(prepared.platform_specific["reply_anchor_message_id"], "555")
