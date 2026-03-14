"""Discord test driver using HTTP API (no gateway connection needed)."""

import logging
import os
from typing import Optional

import aiohttp

from .base import PlatformDriver

logger = logging.getLogger(__name__)

DISCORD_API = "https://discord.com/api/v10"


class DiscordDriver(PlatformDriver):
    """Drive tests via Discord HTTP API using Bot B's token."""

    def __init__(
        self,
        bot_b_token: Optional[str] = None,
        bot_a_id: Optional[str] = None,
        channel_id: Optional[str] = None,
    ):
        _bot_a_id = bot_a_id or os.environ.get("E2E_DISCORD_BOT_A_ID", "")
        _channel_id = channel_id or os.environ.get("E2E_DISCORD_CHANNEL", "")
        super().__init__(bot_a_id=_bot_a_id, channel_id=_channel_id)
        self.token = bot_b_token or os.environ.get("E2E_DISCORD_BOT_B_TOKEN", "")
        self._session: Optional[aiohttp.ClientSession] = None

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bot {self.token}",
            "Content-Type": "application/json",
        }

    async def setup(self) -> None:
        if not self.token:
            raise RuntimeError("Discord Bot B token not configured (E2E_DISCORD_BOT_B_TOKEN)")

        self._session = aiohttp.ClientSession()
        # Verify auth
        async with self._session.get(f"{DISCORD_API}/users/@me", headers=self._headers()) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Discord auth failed: {resp.status}")
            data = await resp.json()
            logger.info(
                "Discord Bot B authenticated as: %s#%s",
                data["username"],
                data.get("discriminator", "0"),
            )

    async def teardown(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def send_message(self, text: str, thread_id: Optional[str] = None) -> str:
        assert self._session is not None, "Driver not set up"
        payload: dict = {"content": text}
        if thread_id:
            # Discord uses message_reference for threads
            payload["message_reference"] = {"message_id": thread_id}

        async with self._session.post(
            f"{DISCORD_API}/channels/{self.channel_id}/messages",
            headers=self._headers(),
            json=payload,
        ) as resp:
            data = await resp.json()
            return data["id"]

    async def get_messages_after(self, after_ts: str, limit: int = 20) -> list[dict]:
        assert self._session is not None, "Driver not set up"
        async with self._session.get(
            f"{DISCORD_API}/channels/{self.channel_id}/messages",
            headers=self._headers(),
            params={"after": after_ts, "limit": limit},
        ) as resp:
            messages = await resp.json()

        # Discord returns newest first, reverse for chronological
        messages.reverse()
        # Normalize to common format
        return [
            {
                "user": msg["author"]["id"],
                "text": msg.get("content", ""),
                "ts": msg["id"],
                "raw": msg,
            }
            for msg in messages
        ]
