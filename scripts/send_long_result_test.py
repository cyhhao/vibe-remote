import asyncio
import os
from dataclasses import dataclass

from config.settings import AppConfig
from core.controller import Controller
from modules.im import MessageContext


@dataclass
class DummyRequest:
    started_at: float = 0.0


async def main() -> None:
    channel_id = os.getenv("TEST_SLACK_CHANNEL_ID") or "C0A6U2GH6P5"
    thread_id = os.getenv("TEST_SLACK_THREAD_TS")

    config = AppConfig.from_env()
    controller = Controller(config)

    context = MessageContext(
        user_id="test_user",
        channel_id=channel_id,
        thread_id=thread_id,
        message_id=None,
        platform_specific=None,
    )

    content = "".join([f"Line {idx:04d}: Lorem ipsum dolor sit amet.\n" for idx in range(1200)])
    await controller.emit_agent_message(context, "result", content, parse_mode="markdown")


if __name__ == "__main__":
    asyncio.run(main())
