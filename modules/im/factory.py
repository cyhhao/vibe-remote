"""Factory for creating IM platform clients"""

import logging
from typing import Union, TYPE_CHECKING

from .base import BaseIMClient

# Use delayed imports to avoid circular import issues
if TYPE_CHECKING:
    from config.v2_config import V2Config

logger = logging.getLogger(__name__)


class IMFactory:
    """Factory class to create the appropriate IM client based on platform"""
    
    @staticmethod
    def create_client(config) -> BaseIMClient:
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

        if not config.slack:
            raise ValueError("Slack configuration not found")
        logger.info("Creating Slack client")
        return SlackBot(config.slack)
    
    @staticmethod
    def get_supported_platforms() -> list[str]:
        """Get list of supported platforms

        Returns:
            List of supported platform names
        """
        return ["slack"]
    
    @staticmethod
    def validate_platform_config(config) -> None:
        """Validate platform configuration before creating client
        
        Args:
            config: Application configuration
            
        Raises:
            ValueError: If configuration is invalid
        """
        if config.slack is None:
            raise ValueError("Missing configuration for platform: slack")
        config.slack.validate()
