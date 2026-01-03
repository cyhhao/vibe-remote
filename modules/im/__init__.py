"""IM platform abstraction package

Provides unified interface for different instant messaging platforms.

Example usage:
    from modules.im import BaseIMClient, IMFactory, MessageContext
    
    # Create client via factory
    client = IMFactory.create_client(config)
    
    # Use platform-agnostic messaging
    context = MessageContext(user_id="123", channel_id="456")
    await client.send_message(context, "Hello!")
"""

# Core abstractions
from .base import (
    BaseIMClient,
    BaseIMConfig,
    MessageContext,
    InlineButton,
    InlineKeyboard,
    ImageData,
)

# Factory for client creation
from .factory import IMFactory

# Platform implementations are available but not imported by default
# to avoid circular import issues. Import them explicitly if needed:
# from .telegram import TelegramBot
# from .slack import SlackBot

# Package metadata
__version__ = "1.0.0"
__all__ = [
    # Core abstractions
    "BaseIMClient",
    "BaseIMConfig",
    "MessageContext",
    "InlineButton",
    "InlineKeyboard",
    "ImageData",
    # Factory
    "IMFactory",
    # Implementations available via dynamic import
    # "TelegramBot",
    # "SlackBot",
]

# Convenience function for quick client creation
def create_client(config):
    """Convenience function to create IM client
    
    Args:
        config: Application configuration
        
    Returns:
        Platform-specific IM client instance
    """
    return IMFactory.create_client(config)


# Platform information
def get_supported_platforms():
    """Get list of supported IM platforms
    
    Returns:
        List of supported platform names
    """
    return IMFactory.get_supported_platforms()


# Version info
def get_version():
    """Get package version
    
    Returns:
        Version string
    """
    return __version__