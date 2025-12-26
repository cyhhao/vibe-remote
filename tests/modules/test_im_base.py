"""Tests for modules/im/base.py"""

import pytest
from unittest.mock import Mock, AsyncMock
from dataclasses import dataclass
from typing import Optional, Dict, Any


class TestMessageContext:
    """Tests for MessageContext dataclass"""

    def test_basic_context(self):
        """Test creating basic MessageContext"""
        from modules.im.base import MessageContext

        context = MessageContext(
            user_id="12345",
            channel_id="67890",
        )

        assert context.user_id == "12345"
        assert context.channel_id == "67890"
        assert context.thread_id is None
        assert context.message_id is None
        assert context.platform_specific == {}

    def test_full_context(self):
        """Test creating MessageContext with all fields"""
        from modules.im.base import MessageContext

        context = MessageContext(
            user_id="12345",
            channel_id="67890",
            thread_id="11111",
            message_id="msg_001",
            platform_specific={"key": "value"},
        )

        assert context.user_id == "12345"
        assert context.channel_id == "67890"
        assert context.thread_id == "11111"
        assert context.message_id == "msg_001"
        assert context.platform_specific == {"key": "value"}


class TestInlineButton:
    """Tests for InlineButton dataclass"""

    def test_basic_button(self):
        """Test creating basic InlineButton"""
        from modules.im.base import InlineButton

        button = InlineButton(
            text="Click me",
            callback_data="action_1",
        )

        assert button.text == "Click me"
        assert button.callback_data == "action_1"
        assert button.url is None

    def test_button_with_url(self):
        """Test creating InlineButton with URL"""
        from modules.im.base import InlineButton

        button = InlineButton(
            text="Open link",
            callback_data=None,
            url="https://example.com",
        )

        assert button.url == "https://example.com"


class TestInlineKeyboard:
    """Tests for InlineKeyboard dataclass"""

    def test_basic_keyboard(self):
        """Test creating basic InlineKeyboard"""
        from modules.im.base import InlineKeyboard, InlineButton

        keyboard = InlineKeyboard(
            buttons=[
                [InlineButton(text="Button 1", callback_data="action_1")],
                [InlineButton(text="Button 2", callback_data="action_2")],
            ]
        )

        assert len(keyboard.buttons) == 2
        assert len(keyboard.buttons[0]) == 1
        assert keyboard.buttons[0][0].text == "Button 1"

    def test_empty_keyboard(self):
        """Test creating empty InlineKeyboard"""
        from modules.im.base import InlineKeyboard

        keyboard = InlineKeyboard(buttons=[])

        assert keyboard.buttons == []


class TestBaseIMConfig:
    """Tests for BaseIMConfig abstract class"""

    def test_parse_channel_list_null(self):
        """Test parsing null channel list"""
        from modules.im.base import BaseIMConfig

        result = BaseIMConfig._parse_channel_list(None)
        assert result is None

    def test_parse_channel_list_empty(self):
        """Test parsing empty channel list"""
        from modules.im.base import BaseIMConfig

        result = BaseIMConfig._parse_channel_list("")
        assert result == []

    def test_parse_channel_list_comma_separated(self):
        """Test parsing comma-separated channel IDs"""
        from modules.im.base import BaseIMConfig

        result = BaseIMConfig._parse_channel_list("[C123,C456,C789]")
        assert result == ["C123", "C456", "C789"]


class TestBaseIMClient:
    """Tests for BaseIMClient abstract class"""

    def test_client_initialization(self):
        """Test BaseIMClient can be subclassed"""
        from modules.im.base import BaseIMClient, BaseIMConfig, MessageContext

        # Create a concrete implementation for testing
        class TestClient(BaseIMClient):
            def __init__(self, config):
                self.config = config
                self.formatter = None

            def run(self):
                pass

            async def send_message(self, context: MessageContext, text: str, parse_mode: str = "markdown"):
                pass

            async def edit_message(self, context: MessageContext, text: str, parse_mode: str = "markdown"):
                pass

            async def send_inline_keyboard(self, context: MessageContext, text: str, keyboard):
                pass

            def register_callbacks(self, **kwargs):
                pass

            def should_use_thread_for_reply(self) -> bool:
                return False

        config = Mock(spec=BaseIMConfig)
        client = TestClient(config)

        assert client.config == config
        assert client.formatter is None

    def test_abstract_methods_defined(self):
        """Test that BaseIMClient defines required abstract methods"""
        from modules.im.base import BaseIMClient

        abstract_methods = [
            'send_message',
            'edit_message',
            'send_inline_keyboard',
            'register_callbacks',
            'should_use_thread_for_reply',
            'run',
        ]

        for method in abstract_methods:
            assert hasattr(BaseIMClient, method), f"Abstract method {method} not found"
