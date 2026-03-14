"""Slack test driver using slack_sdk Web API."""

import logging
import os
from typing import Optional

from .base import PlatformDriver

logger = logging.getLogger(__name__)


class SlackDriver(PlatformDriver):
    """Drive tests via Slack Web API using Bot B's token."""

    def __init__(
        self,
        bot_b_token: Optional[str] = None,
        bot_a_id: Optional[str] = None,
        channel_id: Optional[str] = None,
    ):
        _bot_a_id = bot_a_id or os.environ.get("E2E_SLACK_BOT_A_ID", "")
        _channel_id = channel_id or os.environ.get("E2E_SLACK_CHANNEL", "")
        super().__init__(bot_a_id=_bot_a_id, channel_id=_channel_id)
        self.token = bot_b_token or os.environ.get("E2E_SLACK_BOT_B_TOKEN", "")
        self.client = None

    async def setup(self) -> None:
        try:
            from slack_sdk.web.async_client import AsyncWebClient
        except ImportError:
            raise RuntimeError("slack_sdk not installed. Run: pip install slack_sdk")

        if not self.token:
            raise RuntimeError("Slack Bot B token not configured (E2E_SLACK_BOT_B_TOKEN)")

        self.client = AsyncWebClient(token=self.token)
        # Verify auth
        auth = await self.client.auth_test()
        logger.info(
            "Slack Bot B authenticated as: %s (team: %s)",
            auth["user"],
            auth["team"],
        )

    async def teardown(self) -> None:
        self.client = None

    async def send_message(self, text: str, thread_id: Optional[str] = None) -> str:
        assert self.client is not None, "Driver not set up"
        kwargs = {"channel": self.channel_id, "text": text}
        if thread_id:
            kwargs["thread_ts"] = thread_id
        resp = await self.client.chat_postMessage(**kwargs)
        return resp["ts"]

    async def get_messages_after(self, after_ts: str, limit: int = 20) -> list[dict]:
        assert self.client is not None, "Driver not set up"
        resp = await self.client.conversations_history(
            channel=self.channel_id,
            oldest=after_ts,
            limit=limit,
            inclusive=False,
        )
        messages = resp.get("messages", [])
        # Slack returns newest first, reverse for chronological order
        messages.reverse()
        return messages
