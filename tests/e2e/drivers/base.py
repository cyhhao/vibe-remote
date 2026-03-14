"""Abstract base class for platform-agnostic E2E test drivers."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class BotReply:
    """A reply from Bot A."""

    text: str
    message_id: str
    thread_id: Optional[str] = None
    raw: Optional[dict] = None


class PlatformDriver(ABC):
    """Platform-agnostic test driver interface.

    Each platform implements this to send messages as Bot B
    and read Bot A's replies.
    """

    def __init__(self, bot_a_id: str, channel_id: str):
        self.bot_a_id = bot_a_id
        self.channel_id = channel_id

    @abstractmethod
    async def setup(self) -> None:
        """Initialize the driver (authenticate, etc.)."""

    @abstractmethod
    async def teardown(self) -> None:
        """Clean up resources."""

    @abstractmethod
    async def send_message(self, text: str, thread_id: Optional[str] = None) -> str:
        """Send a message as Bot B. Returns message ID."""

    @abstractmethod
    async def get_messages_after(self, after_ts: str, limit: int = 20) -> list[dict]:
        """Get messages in the channel after a given timestamp/message ID.

        Each dict should have at minimum: user, text, ts (message id).
        """

    async def send_and_wait_reply(
        self,
        text: str,
        timeout: float = 120,
        poll_interval: float = 3,
        thread_id: Optional[str] = None,
    ) -> BotReply:
        """Send a message and wait for Bot A to reply.

        Args:
            text: Message to send as Bot B
            timeout: Max seconds to wait for reply
            poll_interval: Seconds between polls
            thread_id: Send in a thread (optional)

        Returns:
            BotReply with Bot A's response

        Raises:
            TimeoutError if Bot A doesn't reply within timeout
        """
        msg_id = await self.send_message(text, thread_id=thread_id)
        logger.info(
            "Sent message '%s...' (id=%s), waiting for Bot A reply...",
            text[:50],
            msg_id,
        )

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            messages = await self.get_messages_after(msg_id)
            for msg in messages:
                if msg.get("user") == self.bot_a_id:
                    reply_text = msg.get("text", "")
                    logger.info("Bot A replied: '%s...'", reply_text[:100])
                    return BotReply(
                        text=reply_text,
                        message_id=msg.get("ts", msg.get("id", "")),
                        thread_id=msg.get("thread_ts", msg.get("thread_id")),
                        raw=msg,
                    )
            await asyncio.sleep(poll_interval)

        raise TimeoutError(f"Bot A did not reply within {timeout}s after message: {text[:80]}")

    async def send_command(self, command: str, args: str = "", timeout: float = 120) -> BotReply:
        """Send a slash-style text command and wait for reply.

        Args:
            command: Command name without slash (e.g., "start", "cwd")
            args: Command arguments
            timeout: Max seconds to wait for reply

        Returns:
            BotReply with Bot A's response
        """
        text = f"/{command}" + (f" {args}" if args else "")
        return await self.send_and_wait_reply(text, timeout=timeout)
