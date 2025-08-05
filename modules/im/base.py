"""Base classes and data structures for IM platform abstraction"""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# Data structures for platform-agnostic messaging
@dataclass
class MessageContext:
    """Platform-agnostic message context"""
    user_id: str
    channel_id: str
    thread_id: Optional[str] = None
    message_id: Optional[str] = None
    platform_specific: Optional[Dict[str, Any]] = None


@dataclass
class InlineButton:
    """Platform-agnostic inline button"""
    text: str
    callback_data: str


@dataclass
class InlineKeyboard:
    """Platform-agnostic inline keyboard"""
    buttons: list[list[InlineButton]]  # 2D array for row/column layout


# Configuration base class
@dataclass
class BaseIMConfig(ABC):
    """Abstract base class for IM platform configurations"""
    
    @classmethod
    @abstractmethod
    def from_env(cls) -> 'BaseIMConfig':
        """Create configuration from environment variables"""
        pass
    
    @abstractmethod
    def validate(self) -> bool:
        """Validate the configuration
        
        Returns:
            True if configuration is valid
            
        Raises:
            ValueError: If configuration is invalid
        """
        pass
    
    def validate_required_string(self, value: Optional[str], field_name: str) -> None:
        """Helper method to validate required string fields
        
        Args:
            value: The value to validate
            field_name: Name of the field for error messages
            
        Raises:
            ValueError: If value is None or empty
        """
        if not value or not value.strip():
            raise ValueError(f"{field_name} is required and cannot be empty")
    
    def validate_optional_int(self, value: Optional[str], field_name: str) -> Optional[int]:
        """Helper method to validate and convert optional integer fields
        
        Args:
            value: String value to convert
            field_name: Name of the field for error messages
            
        Returns:
            Converted integer or None
            
        Raises:
            ValueError: If value is not a valid integer
        """
        if not value:
            return None
        
        try:
            return int(value)
        except ValueError:
            raise ValueError(f"{field_name} must be a valid integer, got: {value}")


# IM Client base class
class BaseIMClient(ABC):
    """Abstract base class for IM platform clients"""
    
    def __init__(self, config: BaseIMConfig):
        self.config = config
        # Initialize callback storage
        self.on_message_callback: Optional[Callable] = None
        self.on_command_callbacks: Dict[str, Callable] = {}
        self.on_callback_query_callback: Optional[Callable] = None
        # Platform-specific formatter will be set by subclasses
        self.formatter = None
        
    @abstractmethod
    async def send_message(self, context: MessageContext, text: str, 
                          parse_mode: Optional[str] = None,
                          reply_to: Optional[str] = None) -> str:
        """Send a text message
        
        Args:
            context: Message context (channel, thread, etc)
            text: Message text
            parse_mode: Optional formatting mode (markdown, html, etc)
            reply_to: Optional message ID to reply to
            
        Returns:
            Message ID of sent message
        """
        pass
    
    @abstractmethod
    async def send_message_with_buttons(self, context: MessageContext, text: str,
                                      keyboard: InlineKeyboard,
                                      parse_mode: Optional[str] = None) -> str:
        """Send a message with inline buttons
        
        Args:
            context: Message context
            text: Message text
            keyboard: Inline keyboard configuration
            parse_mode: Optional formatting mode
            
        Returns:
            Message ID of sent message
        """
        pass
    
    @abstractmethod
    async def edit_message(self, context: MessageContext, message_id: str,
                          text: Optional[str] = None,
                          keyboard: Optional[InlineKeyboard] = None) -> bool:
        """Edit an existing message
        
        Args:
            context: Message context
            message_id: ID of message to edit
            text: New text (if provided)
            keyboard: New keyboard (if provided)
            
        Returns:
            Success status
        """
        pass
    
    @abstractmethod
    async def answer_callback(self, callback_id: str, text: Optional[str] = None,
                            show_alert: bool = False) -> bool:
        """Answer a callback query from inline button
        
        Args:
            callback_id: Callback query ID
            text: Optional notification text
            show_alert: Show as alert popup
            
        Returns:
            Success status
        """
        pass
    
    @abstractmethod
    def register_handlers(self):
        """Register platform-specific message and command handlers"""
        pass
    
    @abstractmethod
    def run(self):
        """Start the bot/client"""
        pass
    
    @abstractmethod
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """Get information about a user
        
        Args:
            user_id: Platform-specific user ID
            
        Returns:
            User information dict
        """
        pass
    
    @abstractmethod
    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        """Get information about a channel/chat
        
        Args:
            channel_id: Platform-specific channel ID
            
        Returns:
            Channel information dict
        """
        pass
    
    def register_callbacks(self,
                         on_message: Optional[Callable] = None,
                         on_command: Optional[Dict[str, Callable]] = None,
                         on_callback_query: Optional[Callable] = None,
                         **kwargs):
        """Register callback functions for different events
        
        Args:
            on_message: Callback for text messages
            on_command: Dict of command callbacks
            on_callback_query: Callback for button clicks
            **kwargs: Additional platform-specific callbacks
        """
        self.on_message_callback = on_message
        self.on_command_callbacks = on_command or {}
        self.on_callback_query_callback = on_callback_query
        
        # Store any additional callbacks
        for key, value in kwargs.items():
            setattr(self, f"{key}_callback", value)
    
    def log_error(self, message: str, exception: Exception = None):
        """Standardized error logging
        
        Args:
            message: Error message
            exception: Optional exception to log
        """
        if exception:
            logger.error(f"{message}: {exception}")
        else:
            logger.error(message)
    
    def log_info(self, message: str):
        """Standardized info logging
        
        Args:
            message: Info message
        """
        logger.info(message)
    
    @abstractmethod
    def format_markdown(self, text: str) -> str:
        """Format markdown text for the specific platform
        
        Args:
            text: Text with common markdown formatting
            
        Returns:
            Platform-specific formatted text
        """
        pass