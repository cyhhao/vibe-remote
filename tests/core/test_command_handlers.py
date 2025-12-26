"""Tests for core/handlers/command_handlers.py"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestCommandHandlers:
    """Tests for CommandHandlers class"""

    @pytest.fixture
    def mock_controller(self):
        """Create a mock controller for testing"""
        controller = Mock()
        controller.config = Mock()
        controller.config.platform = "telegram"
        controller.im_client = Mock()
        controller.im_client.send_message = AsyncMock()
        controller.im_client.send_inline_keyboard = AsyncMock()
        controller.im_client.get_user_info = AsyncMock(return_value={"id": "12345", "name": "Test User"})
        controller.im_client.get_channel_info = AsyncMock(return_value={"id": "67890", "name": "Test Channel"})
        controller.session_manager = Mock()
        controller.settings_manager = Mock()
        controller.settings_manager.get_user_settings = Mock(return_value=Mock(hidden_message_types=[]))
        controller.message_handler = Mock()
        controller.session_handler = Mock()
        controller.session_handler.topic_manager = Mock()
        controller.agent_service = Mock()
        controller.agent_service.default_agent = "claude"
        controller.agent_router = Mock()
        controller.agent_router.resolve = Mock(return_value="claude")
        controller._get_settings_key = Mock(return_value="12345")

        return controller

    def test_init(self, mock_controller):
        """Test CommandHandlers initialization"""
        from core.handlers.command_handlers import CommandHandlers

        handler = CommandHandlers(mock_controller)

        assert handler.controller == mock_controller
        assert handler.config == mock_controller.config
        assert handler.im_client == mock_controller.im_client

    def test_escape_md_v2(self, mock_controller):
        """Test Markdown escaping for Telegram"""
        from core.handlers.command_handlers import CommandHandlers

        handler = CommandHandlers(mock_controller)

        # Test basic escaping
        result = handler._escape_md_v2("Hello *world*")
        assert "_" in result  # Special chars should be escaped

    def test_get_channel_context_slack(self, mock_controller):
        """Test _get_channel_context for Slack platform"""
        from core.handlers.command_handlers import CommandHandlers
        from modules.im import MessageContext

        mock_controller.config.platform = "slack"
        handler = CommandHandlers(mock_controller)

        context = MessageContext(
            user_id="123",
            channel_id="C12345",
            thread_id="T12345",
            message_id="msg_001",
            platform_specific={},
        )

        result = handler._get_channel_context(context)

        # For Slack, thread_id should be None for command responses
        assert result.thread_id is None
        assert result.channel_id == "C12345"

    def test_get_channel_context_telegram(self, mock_controller):
        """Test _get_channel_context for Telegram platform"""
        from core.handlers.command_handlers import CommandHandlers
        from modules.im import MessageContext

        mock_controller.config.platform = "telegram"
        handler = CommandHandlers(mock_controller)

        context = MessageContext(
            user_id="123",
            channel_id="67890",
            thread_id="12345",
            message_id="msg_001",
            platform_specific={},
        )

        result = handler._get_channel_context(context)

        # For Telegram, keep original context
        assert result.thread_id == "12345"


class TestCommandRegistration:
    """Tests for command method registration"""

    @pytest.fixture
    def mock_controller(self):
        """Create a minimal mock controller"""
        controller = Mock()
        controller.config = Mock()
        controller.config.platform = "telegram"
        controller.im_client = Mock()
        controller.session_manager = Mock()
        controller.settings_manager = Mock()
        controller.message_handler = Mock()
        controller.session_handler = Mock()
        controller.session_handler.topic_manager = Mock()
        controller.agent_service = Mock()
        controller.agent_router = Mock()

        return controller

    def test_topic_commands_exist(self, mock_controller):
        """Test that all topic management commands are registered"""
        from core.handlers.command_handlers import CommandHandlers

        handler = CommandHandlers(mock_controller)

        topic_commands = [
            'handle_create_topic',
            'handle_clone',
            'handle_list_topics',
            'handle_show_topic',
            'handle_set_manager_topic',
            'handle_delete_topic',
            'handle_project_info',
            'handle_git_status',
        ]

        for cmd in topic_commands:
            assert hasattr(handler, cmd), f"Command {cmd} not found"

    def test_git_commands_exist(self, mock_controller):
        """Test that git commands are registered"""
        from core.handlers.command_handlers import CommandHandlers

        handler = CommandHandlers(mock_controller)

        git_commands = [
            'handle_git_log',
            'handle_git_diff',
            'handle_branch',
            'handle_switch',
        ]

        for cmd in git_commands:
            assert hasattr(handler, cmd), f"Command {cmd} not found"

    def test_utility_commands_exist(self, mock_controller):
        """Test that utility commands are registered"""
        from core.handlers.command_handlers import CommandHandlers

        handler = CommandHandlers(mock_controller)

        utility_commands = [
            'handle_cwd',
            'handle_set_cwd',
            'handle_clear',
            'handle_start',
            'handle_stop',
            'handle_ls',
            'handle_tree',
            'handle_find',
            'handle_grep',
            'handle_exec',
        ]

        for cmd in utility_commands:
            assert hasattr(handler, cmd), f"Command {cmd} not found"
