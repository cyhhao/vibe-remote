"""Tests for config/settings.py"""

import os
import pytest
from unittest.mock import patch, Mock
from dataclasses import dataclass


class TestTelegramConfig:
    """Tests for TelegramConfig class"""

    def test_from_env_missing_token(self):
        """Test that missing TELEGRAM_BOT_TOKEN raises ValueError"""
        from config.settings import TelegramConfig

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN environment variable is required"):
                TelegramConfig.from_env()

    def test_from_env_valid_token(self):
        """Test creating TelegramConfig with valid environment variables"""
        from config.settings import TelegramConfig

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123456789:ABCdefGhIJKlmnOPQrst-uvwXYZ",
            "TELEGRAM_TARGET_CHAT_ID": ""
        }):
            config = TelegramConfig.from_env()
            assert config.bot_token == "123456789:ABCdefGhIJKlmnOPQrst-uvwXYZ"
            assert config.target_chat_id is None

    def test_from_env_with_target_chat_id_list(self):
        """Test parsing comma-separated chat IDs"""
        from config.settings import TelegramConfig

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123456789:ABCdefGhIJKlmnOPQrst-uvwXYZ",
            "TELEGRAM_TARGET_CHAT_ID": "[12345, 67890, 11111]"
        }):
            config = TelegramConfig.from_env()
            assert config.target_chat_id == [12345, 67890, 11111]

    def test_from_env_with_target_chat_id_null(self):
        """Test parsing null target chat ID"""
        from config.settings import TelegramConfig

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123456789:ABCdefGhIJKlmnOPQrst-uvwXYZ",
            "TELEGRAM_TARGET_CHAT_ID": "null"
        }):
            config = TelegramConfig.from_env()
            assert config.target_chat_id is None

    def test_from_env_with_empty_target_chat_id(self):
        """Test parsing empty target chat ID"""
        from config.settings import TelegramConfig

        with patch.dict(os.environ, {
            "TELEGRAM_BOT_TOKEN": "123456789:ABCdefGhIJKlmnOPQrst-uvwXYZ",
            "TELEGRAM_TARGET_CHAT_ID": "[]"
        }):
            config = TelegramConfig.from_env()
            assert config.target_chat_id == []

    def test_validate_valid_token(self):
        """Test validation with valid bot token format"""
        from config.settings import TelegramConfig

        config = TelegramConfig(bot_token="123456789:valid_token")
        assert config.validate() is True

    def test_validate_empty_token(self):
        """Test validation with empty token raises error"""
        from config.settings import TelegramConfig

        config = TelegramConfig(bot_token="")
        with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN is required"):
            config.validate()


class TestSlackConfig:
    """Tests for SlackConfig class"""

    def test_from_env_missing_bot_token(self):
        """Test that missing SLACK_BOT_TOKEN raises ValueError"""
        from config.settings import SlackConfig

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SLACK_BOT_TOKEN", None)
            with pytest.raises(ValueError, match="SLACK_BOT_TOKEN environment variable is required"):
                SlackConfig.from_env()

    def test_from_env_valid_config(self):
        """Test creating SlackConfig with valid environment variables"""
        from config.settings import SlackConfig

        with patch.dict(os.environ, {
            "SLACK_BOT_TOKEN": "xoxb-test-1234567890-abcdefghijklmnop",
            "SLACK_APP_TOKEN": "xapp-test-1234567890-abcdefghijklmnop",
            "SLACK_SIGNING_SECRET": "test_signing_secret"
        }):
            config = SlackConfig.from_env()
            assert config.bot_token == "xoxb-test-1234567890-abcdefghijklmnop"
            assert config.app_token == "xapp-test-1234567890-abcdefghijklmnop"
            assert config.signing_secret == "test_signing_secret"

    def test_from_env_with_target_channel(self):
        """Test parsing comma-separated channel IDs"""
        from config.settings import SlackConfig

        with patch.dict(os.environ, {
            "SLACK_BOT_TOKEN": "xoxb-test-1234567890-abcdefghijklmnop",
            "SLACK_TARGET_CHANNEL": "[C12345, C67890]"
        }):
            config = SlackConfig.from_env()
            assert config.target_channel == ["C12345", "C67890"]

    def test_from_env_require_mention(self):
        """Test parsing require mention flag"""
        from config.settings import SlackConfig

        with patch.dict(os.environ, {
            "SLACK_BOT_TOKEN": "xoxb-test-1234567890-abcdefghijklmnop",
            "SLACK_REQUIRE_MENTION": "true"
        }):
            config = SlackConfig.from_env()
            assert config.require_mention is True

    def test_validate_valid_token(self):
        """Test validation with valid bot token format"""
        from config.settings import SlackConfig

        config = SlackConfig(bot_token="xoxb-test-1234567890-abcdefghijklmnop")
        assert config.validate() is True

    def test_validate_invalid_bot_token_format(self):
        """Test validation with invalid bot token format"""
        from config.settings import SlackConfig

        config = SlackConfig(bot_token="invalid-token")
        with pytest.raises(ValueError, match="Invalid Slack bot token format"):
            config.validate()

    def test_validate_invalid_app_token_format(self):
        """Test validation with invalid app token format"""
        from config.settings import SlackConfig

        config = SlackConfig(
            bot_token="xoxb-test-1234567890-abcdefghijklmnop",
            app_token="invalid-app-token"
        )
        with pytest.raises(ValueError, match="Invalid Slack app token format"):
            config.validate()


class TestClaudeConfig:
    """Tests for ClaudeConfig class"""

    def test_from_env_missing_permission_mode(self):
        """Test that missing CLAUDE_PERMISSION_MODE raises ValueError"""
        from config.settings import ClaudeConfig

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("CLAUDE_PERMISSION_MODE", None)
            os.environ.pop("CLAUDE_DEFAULT_CWD", None)
            with pytest.raises(ValueError, match="CLAUDE_PERMISSION_MODE environment variable is required"):
                ClaudeConfig.from_env()

    def test_from_env_missing_cwd(self):
        """Test that missing CLAUDE_DEFAULT_CWD raises ValueError"""
        from config.settings import ClaudeConfig

        with patch.dict(os.environ, {"CLAUDE_PERMISSION_MODE": "all"}):
            os.environ.pop("CLAUDE_DEFAULT_CWD", None)
            with pytest.raises(ValueError, match="CLAUDE_DEFAULT_CWD environment variable is required"):
                ClaudeConfig.from_env()

    def test_from_env_valid_config(self):
        """Test creating ClaudeConfig with valid environment variables"""
        from config.settings import ClaudeConfig

        with patch.dict(os.environ, {
            "CLAUDE_PERMISSION_MODE": "all",
            "CLAUDE_DEFAULT_CWD": "/tmp/test",
            "CLAUDE_SYSTEM_PROMPT": ""  # Clear any existing system prompt
        }):
            config = ClaudeConfig.from_env()
            assert config.permission_mode == "all"
            assert config.cwd == "/tmp/test"
            assert config.system_prompt is None or config.system_prompt == ""

    def test_from_env_with_system_prompt(self):
        """Test creating ClaudeConfig with custom system prompt"""
        from config.settings import ClaudeConfig

        with patch.dict(os.environ, {
            "CLAUDE_PERMISSION_MODE": "all",
            "CLAUDE_DEFAULT_CWD": "/tmp/test",
            "CLAUDE_SYSTEM_PROMPT": "You are a helpful assistant"
        }):
            config = ClaudeConfig.from_env()
            assert config.system_prompt == "You are a helpful assistant"


class TestAppConfig:
    """Tests for AppConfig class"""

    def test_from_env_missing_platform(self):
        """Test that missing IM_PLATFORM raises ValueError"""
        from config.settings import AppConfig

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("IM_PLATFORM", None)
            with pytest.raises(ValueError, match="IM_PLATFORM environment variable is required"):
                AppConfig.from_env()

    def test_from_env_invalid_platform(self):
        """Test that invalid platform raises ValueError"""
        from config.settings import AppConfig

        with patch.dict(os.environ, {"IM_PLATFORM": "invalid"}):
            with pytest.raises(ValueError, match="Invalid IM_PLATFORM"):
                AppConfig.from_env()

    def test_from_env_telegram_platform(self):
        """Test creating AppConfig for Telegram platform"""
        from config.settings import AppConfig

        env = {
            "IM_PLATFORM": "telegram",
            "TELEGRAM_BOT_TOKEN": "123456789:ABCdefGhIJKlmnOPQrst-uvwXYZ",
            "CLAUDE_PERMISSION_MODE": "all",
            "CLAUDE_DEFAULT_CWD": "/tmp/test",
        }
        with patch.dict(os.environ, env, clear=True):
            config = AppConfig.from_env()
            assert config.platform == "telegram"
            assert config.telegram is not None
            assert config.slack is None

    def test_from_env_slack_platform(self):
        """Test creating AppConfig for Slack platform"""
        from config.settings import AppConfig

        env = {
            "IM_PLATFORM": "slack",
            "SLACK_BOT_TOKEN": "xoxb-test-1234567890-abcdefghijklmnop",
            "CLAUDE_PERMISSION_MODE": "all",
            "CLAUDE_DEFAULT_CWD": "/tmp/test",
        }
        with patch.dict(os.environ, env, clear=True):
            config = AppConfig.from_env()
            assert config.platform == "slack"
            assert config.slack is not None
            assert config.telegram is None

    def test_from_env_with_log_level(self):
        """Test custom log level parsing"""
        from config.settings import AppConfig

        env = {
            "IM_PLATFORM": "telegram",
            "TELEGRAM_BOT_TOKEN": "123456789:ABCdefGhIJKlmnOPQrst-uvwXYZ",
            "CLAUDE_PERMISSION_MODE": "all",
            "CLAUDE_DEFAULT_CWD": "/tmp/test",
            "LOG_LEVEL": "DEBUG",
        }
        with patch.dict(os.environ, env, clear=True):
            config = AppConfig.from_env()
            assert config.log_level == "DEBUG"

    def test_from_env_with_cleanup_enabled(self):
        """Test cleanup enabled flag parsing"""
        from config.settings import AppConfig

        env = {
            "IM_PLATFORM": "telegram",
            "TELEGRAM_BOT_TOKEN": "123456789:ABCdefGhIJKlmnOPQrst-uvwXYZ",
            "CLAUDE_PERMISSION_MODE": "all",
            "CLAUDE_DEFAULT_CWD": "/tmp/test",
            "CLEANUP_ENABLED": "true",
        }
        with patch.dict(os.environ, env, clear=True):
            config = AppConfig.from_env()
            assert config.cleanup_enabled is True
