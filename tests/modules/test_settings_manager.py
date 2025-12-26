"""Tests for modules/settings_manager.py"""

import pytest
import tempfile
import os
from unittest.mock import Mock, patch
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSettingsManager:
    """Tests for SettingsManager class"""

    @pytest.fixture
    def temp_settings_file(self):
        """Create a temporary settings file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write('{}')
            temp_path = f.name
        yield temp_path
        # Cleanup
        if os.path.exists(temp_path):
            os.unlink(temp_path)

    @pytest.fixture
    def settings_manager(self, temp_settings_file):
        """Create a SettingsManager instance with temp file"""
        from modules.settings_manager import SettingsManager
        manager = SettingsManager(settings_file=temp_settings_file)
        return manager

    def test_init(self, settings_manager):
        """Test SettingsManager initialization"""
        from modules.settings_manager import SettingsManager

        manager = SettingsManager()
        assert hasattr(manager, 'settings')
        assert hasattr(manager, 'settings_file')

    def test_get_user_settings_new_user(self, settings_manager):
        """Test getting settings for new user creates default settings"""
        user_settings = settings_manager.get_user_settings("new_user_123")

        assert user_settings is not None
        assert hasattr(user_settings, 'hidden_message_types')
        assert user_settings.hidden_message_types == []

    def test_get_user_settings_existing_user(self, settings_manager):
        """Test getting settings for existing user"""
        # First create settings
        settings_manager.get_user_settings("existing_user")

        # Then retrieve them
        user_settings = settings_manager.get_user_settings("existing_user")

        assert user_settings is not None
        assert user_settings.hidden_message_types == []

    def test_update_user_settings(self, settings_manager):
        """Test updating user settings"""
        user_settings = settings_manager.get_user_settings("user_123")
        user_settings.hidden_message_types = ["tool_results"]

        settings_manager.update_user_settings("user_123", user_settings)

        # Verify update persisted
        retrieved_settings = settings_manager.get_user_settings("user_123")
        assert retrieved_settings.hidden_message_types == ["tool_results"]

    def test_get_custom_cwd_no_override(self, settings_manager):
        """Test getting custom CWD when not set"""
        custom_cwd = settings_manager.get_custom_cwd("user_123")
        assert custom_cwd is None

    def test_set_custom_cwd(self, settings_manager):
        """Test setting custom CWD"""
        settings_manager.set_custom_cwd("user_123", "/custom/path")

        custom_cwd = settings_manager.get_custom_cwd("user_123")
        assert custom_cwd == "/custom/path"

    def test_clear_custom_cwd(self, settings_manager):
        """Test clearing custom CWD"""
        settings_manager.set_custom_cwd("user_123", "/custom/path")
        # Re-set to None to clear
        settings_manager.set_custom_cwd("user_123", None)

        custom_cwd = settings_manager.get_custom_cwd("user_123")
        assert custom_cwd is None

    def test_is_message_type_hidden(self, settings_manager):
        """Test checking if message type is hidden"""
        user_settings = settings_manager.get_user_settings("user_123")
        user_settings.hidden_message_types = ["tool_results", "thought"]
        settings_manager.update_user_settings("user_123", user_settings)

        assert settings_manager.is_message_type_hidden("user_123", "tool_results") is True
        assert settings_manager.is_message_type_hidden("user_123", "user_message") is False

    def test_topic_worktree_operations(self, settings_manager):
        """Test topic-worktree mapping operations"""
        # Set mapping (requires: user_id, chat_id, topic_id, worktree_path)
        settings_manager.set_topic_worktree("user_123", "channel_1", "topic_1", "/path/to/worktree")

        # Get mapping (requires: user_id, chat_id, topic_id)
        worktree = settings_manager.get_topic_worktree("user_123", "channel_1", "topic_1")
        assert worktree == "/path/to/worktree"

        # Remove mapping (requires: user_id, chat_id, topic_id)
        settings_manager.remove_topic_worktree("user_123", "channel_1", "topic_1")
        worktree = settings_manager.get_topic_worktree("user_123", "channel_1", "topic_1")
        assert worktree is None

    def test_manager_topic_operations(self, settings_manager):
        """Test manager topic binding operations"""
        # Set manager topic
        settings_manager.set_manager_topic("user_123", "channel_1", "topic_1")

        # Get manager topic
        topic = settings_manager.get_manager_topic("user_123", "channel_1")
        assert topic == "topic_1"

        # Clear manager topic
        settings_manager.clear_manager_topic("user_123", "channel_1")
        topic = settings_manager.get_manager_topic("user_123", "channel_1")
        assert topic is None
