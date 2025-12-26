"""Tests for modules/session_manager.py"""

import pytest
from unittest.mock import Mock, AsyncMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestSessionManager:
    """Tests for SessionManager class"""

    @pytest.fixture
    def session_manager(self):
        """Create a SessionManager instance"""
        from modules.session_manager import SessionManager
        return SessionManager()

    def test_init(self, session_manager):
        """Test SessionManager initialization"""
        from modules.session_manager import SessionManager

        manager = SessionManager()
        assert hasattr(manager, 'sessions')
        assert hasattr(manager, 'receivers')

    def test_get_session_not_found(self, session_manager):
        """Test getting non-existent session returns None"""
        result = session_manager.get_session("non_existent_session")
        assert result is None

    def test_create_session(self, session_manager):
        """Test creating a new session"""
        session_id = session_manager.create_session(
            claude_client=Mock(),
            user_id="12345",
            channel_id="67890",
        )

        assert session_id is not None
        assert session_manager.get_session(session_id) is not None

    def test_remove_session(self, session_manager):
        """Test removing a session"""
        session_id = session_manager.create_session(
            claude_client=Mock(),
            user_id="12345",
            channel_id="67890",
        )

        session_manager.remove_session(session_id)

        assert session_manager.get_session(session_id) is None

    def test_get_session_attributes(self, session_manager):
        """Test that session has correct attributes"""
        mock_client = Mock()
        session_id = session_manager.create_session(
            claude_client=mock_client,
            user_id="12345",
            channel_id="67890",
        )

        session = session_manager.get_session(session_id)
        assert session.claude_client == mock_client
        assert session.user_id == "12345"
        assert session.channel_id == "67890"

    def test_multiple_sessions(self, session_manager):
        """Test managing multiple sessions"""
        session_id_1 = session_manager.create_session(
            claude_client=Mock(),
            user_id="12345",
            channel_id="67890",
        )
        session_id_2 = session_manager.create_session(
            claude_client=Mock(),
            user_id="11111",
            channel_id="22222",
        )

        assert session_id_1 != session_id_2
        assert session_manager.get_session(session_id_1) is not None
        assert session_manager.get_session(session_id_2) is not None

    def test_receiver_task_management(self, session_manager):
        """Test managing receiver tasks"""
        session_id = session_manager.create_session(
            claude_client=Mock(),
            user_id="12345",
            channel_id="67890",
        )

        # Add a receiver task
        mock_task = Mock()
        session_manager.add_receiver(session_id, mock_task)

        assert session_manager.receivers.get(session_id) == mock_task

        # Remove the receiver
        session_manager.remove_receiver(session_id)
        assert session_id not in session_manager.receivers

    def test_get_session_id_mapping(self, session_manager):
        """Test getting session ID for a channel/user"""
        session_id = session_manager.create_session(
            claude_client=Mock(),
            user_id="12345",
            channel_id="67890",
        )

        # Get the session_id using the mapping method
        retrieved_id = session_manager.get_session_id("67890", "12345")
        assert retrieved_id == session_id
