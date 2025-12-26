"""Tests for modules/im/factory.py"""

import pytest
from unittest.mock import Mock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestIMFactory:
    """Tests for IMFactory class"""

    def test_get_supported_platforms(self):
        """Test that supported platforms are correctly reported"""
        from modules.im.factory import IMFactory

        platforms = IMFactory.get_supported_platforms()

        assert "telegram" in platforms
        assert "slack" in platforms

    def test_factory_creates_telegram_client(self):
        """Test that IMFactory creates Telegram client for telegram platform"""
        from modules.im.factory import IMFactory
        from config.settings import TelegramConfig, AppConfig

        # Create mock config
        mock_config = Mock(spec=AppConfig)
        mock_config.platform = "telegram"
        mock_config.telegram = Mock(spec=TelegramConfig)
        mock_config.telegram.bot_token = "test_token"
        mock_config.slack = None
        mock_config.claude = Mock()
        mock_config.claude.cwd = "/tmp/test"

        with patch('modules.im.factory.IMFactory._create_telegram_client') as mock_create:
            mock_client = Mock()
            mock_create.return_value = mock_client

            client = IMFactory.create_client(mock_config)

            mock_create.assert_called_once_with(mock_config)
            assert client == mock_client

    def test_factory_creates_slack_client(self):
        """Test that IMFactory creates Slack client for slack platform"""
        from modules.im.factory import IMFactory
        from config.settings import SlackConfig, AppConfig

        # Create mock config
        mock_config = Mock(spec=AppConfig)
        mock_config.platform = "slack"
        mock_config.telegram = None
        mock_config.slack = Mock(spec=SlackConfig)
        mock_config.slack.bot_token = "xoxb-test"
        mock_config.claude = Mock()
        mock_config.claude.cwd = "/tmp/test"

        with patch('modules.im.factory.IMFactory._create_slack_client') as mock_create:
            mock_client = Mock()
            mock_create.return_value = mock_client

            client = IMFactory.create_client(mock_config)

            mock_create.assert_called_once_with(mock_config)
            assert client == mock_client

    def test_factory_raises_for_unknown_platform(self):
        """Test that IMFactory raises error for unknown platform"""
        from modules.im.factory import IMFactory
        from config.settings import AppConfig

        # Create mock config with unknown platform
        mock_config = Mock(spec=AppConfig)
        mock_config.platform = "unknown"

        with pytest.raises(ValueError, match="Unknown IM platform: unknown"):
            IMFactory.create_client(mock_config)

    def test_factory_requires_platform_config(self):
        """Test that IMFactory validates platform-specific config"""
        from modules.im.factory import IMFactory

        mock_config = Mock()
        mock_config.platform = "telegram"
        mock_config.telegram = None  # Missing telegram config

        with pytest.raises(ValueError, match="TelegramConfig is required"):
            IMFactory.create_client(mock_config)
