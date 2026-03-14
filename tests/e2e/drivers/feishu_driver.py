"""Feishu (Lark) test driver using HTTP API."""

import json
import logging
import os
import time
from typing import Optional

import aiohttp

from .base import PlatformDriver

logger = logging.getLogger(__name__)

FEISHU_API = "https://open.feishu.cn/open-apis"


class FeishuDriver(PlatformDriver):
    """Drive tests via Feishu HTTP API using Bot B's credentials."""

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        bot_a_id: Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        _bot_a_id = bot_a_id or os.environ.get("E2E_FEISHU_BOT_A_ID", "")
        _chat_id = chat_id or os.environ.get("E2E_FEISHU_CHAT_ID", "")
        super().__init__(bot_a_id=_bot_a_id, channel_id=_chat_id)
        self.app_id = app_id or os.environ.get("E2E_FEISHU_BOT_B_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("E2E_FEISHU_BOT_B_APP_SECRET", "")
        self._session: Optional[aiohttp.ClientSession] = None
        self._tenant_token: str = ""
        self._token_expires: float = 0

    async def _refresh_token(self) -> None:
        """Get or refresh tenant access token."""
        if time.monotonic() < self._token_expires - 60:
            return  # Still valid

        assert self._session is not None
        async with self._session.post(
            f"{FEISHU_API}/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        ) as resp:
            data = await resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Feishu auth failed: {data}")
            self._tenant_token = data["tenant_access_token"]
            self._token_expires = time.monotonic() + data.get("expire", 7200)
            logger.info("Feishu Bot B token refreshed")

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._tenant_token}",
            "Content-Type": "application/json",
        }

    async def setup(self) -> None:
        if not self.app_id or not self.app_secret:
            raise RuntimeError("Feishu Bot B credentials not configured")

        self._session = aiohttp.ClientSession()
        await self._refresh_token()
        logger.info("Feishu Bot B authenticated")

    async def teardown(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def send_message(self, text: str, thread_id: Optional[str] = None) -> str:
        assert self._session is not None, "Driver not set up"
        await self._refresh_token()

        payload: dict = {
            "receive_id": self.channel_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        }
        if thread_id:
            payload["reply_in_thread"] = True

        async with self._session.post(
            f"{FEISHU_API}/im/v1/messages",
            headers=self._headers(),
            params={"receive_id_type": "chat_id"},
            json=payload,
        ) as resp:
            data = await resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Feishu send failed: {data}")
            return data["data"]["message_id"]

    async def get_messages_after(self, after_ts: str, limit: int = 20) -> list[dict]:
        assert self._session is not None, "Driver not set up"
        await self._refresh_token()

        # Feishu uses container_id (chat_id) for message list
        async with self._session.get(
            f"{FEISHU_API}/im/v1/messages",
            headers=self._headers(),
            params={
                "container_id_type": "chat",
                "container_id": self.channel_id,
                "page_size": limit,
                "sort_type": "ByCreateTimeAsc",
            },
        ) as resp:
            data = await resp.json()
            if data.get("code") != 0:
                logger.warning("Feishu get_messages failed: %s", data)
                return []

        items = data.get("data", {}).get("items", [])
        # Filter to messages after our reference and normalize
        result = []
        found_reference = False
        for item in items:
            msg_id = item.get("message_id", "")
            if msg_id == after_ts:
                found_reference = True
                continue
            if not found_reference:
                continue

            # Parse content
            content = item.get("body", {}).get("content", "{}")
            try:
                content_data = json.loads(content) if isinstance(content, str) else content
                text = content_data.get("text", "")
            except (json.JSONDecodeError, AttributeError):
                text = str(content)

            sender_id = item.get("sender", {}).get("id", "")
            result.append(
                {
                    "user": sender_id,
                    "text": text,
                    "ts": msg_id,
                    "raw": item,
                }
            )

        return result
