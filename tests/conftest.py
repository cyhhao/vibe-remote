"""Pytest configuration and fixtures for vibe-remote tests"""

import sys
import os
import pytest
from unittest.mock import Mock, MagicMock, AsyncMock, patch
from dataclasses import dataclass
from typing import Optional, Dict, Any

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def mock_config():
    """Create a mock AppConfig for testing"""
    from config.settings import AppConfig, ClaudeConfig, TelegramConfig

    config = Mock(spec=AppConfig)
    config.platform = "telegram"
    config.log_level = "DEBUG"
    config.cleanup_enabled = False
    config.agent_route_file = None

    # Mock platform-specific configs
    config.telegram = Mock(spec=TelegramConfig)
    config.telegram.bot_token = "test_bot_token"
    config.telegram.target_chat_id = None

    # Mock Claude config
    config.claude = Mock(spec=ClaudeConfig)
    config.claude.permission_mode = "all"
    config.claude.cwd = "/tmp/test_cwd"
    config.claude.system_prompt = None

    config.codex = None

    return config


@pytest.fixture
def mock_message_context():
    """Create a mock MessageContext for testing"""
    from modules.im import MessageContext

    return MessageContext(
        user_id="12345",
        channel_id="67890",
        thread_id=None,
        message_id="msg_001",
        platform_specific={},
    )


@pytest.fixture
def mock_im_client():
    """Create a mock IM client"""
    mock = Mock()
    mock.send_message = AsyncMock()
    mock.edit_message = AsyncMock()
    mock.send_inline_keyboard = AsyncMock()
    mock.should_use_thread_for_reply = Mock(return_value=False)
    mock.register_callbacks = Mock()
    mock.run = Mock()
    mock.formatter = None
    return mock


@pytest.fixture
def mock_settings_manager():
    """Create a mock SettingsManager"""
    mock = Mock()
    mock.get_user_settings = Mock(return_value=Mock(hidden_message_types=[]))
    mock.update_user_settings = Mock()
    mock.get_custom_cwd = Mock(return_value=None)
    mock.is_message_type_hidden = Mock(return_value=False)
    return mock


@pytest.fixture
def mock_session_manager():
    """Create a mock SessionManager"""
    mock = Mock()
    mock.get_session = Mock(return_value=None)
    mock.create_session = Mock(return_value="test_session_id")
    mock.remove_session = Mock()
    return mock
