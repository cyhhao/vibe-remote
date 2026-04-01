"""Factory for creating IM platform clients"""

import logging
from typing import Union, TYPE_CHECKING

from .base import BaseIMClient
from .multi import MultiIMClient

# Use delayed imports to avoid circular import issues
if TYPE_CHECKING:
    from config.v2_config import V2Config

logger = logging.getLogger(__name__)


class IMFactory:
    """Factory class to create the appropriate IM client based on platform"""

    @staticmethod
    def create_client(config) -> BaseIMClient:
        clients = IMFactory.create_clients(config)
        primary = getattr(getattr(config, "platforms", None), "primary", getattr(config, "platform", "slack"))
        if len(clients) == 1 and primary in clients:
            return clients[primary]
        return MultiIMClient(clients, primary_platform=primary)

    @staticmethod
    def create_clients(config) -> dict[str, BaseIMClient]:
        """Create and return the appropriate IM client based on configuration

        Args:
            config: Application configuration

        Returns:
            Instance of platform-specific IM client

        Raises:
            ValueError: If platform is not supported
        """
        # Dynamic imports to avoid circular dependency
        from .slack import SlackBot
        from .discord import DiscordBot
        from .telegram import TelegramBot

        enabled_platforms = list(getattr(config, "enabled_platforms", lambda: [getattr(config, "platform", "slack")])())
        clients: dict[str, BaseIMClient] = {}
        for platform in enabled_platforms:
            if platform == "slack":
                if not config.slack:
                    raise ValueError("Slack configuration not found")
                logger.info("Creating Slack client")
                clients[platform] = SlackBot(config.slack)
                continue
            if platform == "discord":
                if not config.discord:
                    raise ValueError("Discord configuration not found")
                logger.info("Creating Discord client")
                clients[platform] = DiscordBot(config.discord)
                continue
            if platform == "telegram":
                if not getattr(config, "telegram", None):
                    raise ValueError("Telegram configuration not found")
                logger.info("Creating Telegram client")
                clients[platform] = TelegramBot(config.telegram)
                continue
            if platform == "lark":
                from .feishu import FeishuBot

                if not config.lark:
                    raise ValueError("Lark configuration not found")
                logger.info("Creating Lark/Feishu client")
                clients[platform] = FeishuBot(config.lark)
                continue
            if platform == "wechat":
                from .wechat import WeChatBot

                if not config.wechat:
                    raise ValueError("WeChat configuration not found")
                logger.info("Creating WeChat client")
                clients[platform] = WeChatBot(config.wechat)
                continue
            raise ValueError(f"Unsupported platform: {platform}")
        return clients

    @staticmethod
    def get_supported_platforms() -> list[str]:
        """Get list of supported platforms

        Returns:
            List of supported platform names
        """
        return ["slack", "discord", "telegram", "lark", "wechat"]

    @staticmethod
    def validate_platform_config(config) -> None:
        """Validate platform configuration before creating client

        Args:
            config: Application configuration

        Raises:
            ValueError: If configuration is invalid
        """
        for platform in getattr(config, "enabled_platforms", lambda: [getattr(config, "platform", "slack")])():
            if platform == "slack":
                if config.slack is None:
                    raise ValueError("Missing configuration for platform: slack")
                config.slack.validate()
                continue
            if platform == "discord":
                if config.discord is None:
                    raise ValueError("Missing configuration for platform: discord")
                config.discord.validate()
                continue
            if platform == "telegram":
                if getattr(config, "telegram", None) is None:
                    raise ValueError("Missing configuration for platform: telegram")
                config.telegram.validate()
                continue
            if platform == "lark":
                if config.lark is None:
                    raise ValueError("Missing configuration for platform: lark")
                config.lark.validate()
                continue
            if platform == "wechat":
                if config.wechat is None:
                    raise ValueError("Missing configuration for platform: wechat")
                config.wechat.validate()
                continue
            raise ValueError(f"Unsupported platform: {platform}")
