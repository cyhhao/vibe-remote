import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Union
from pathlib import Path


logger = logging.getLogger(__name__)


@dataclass
class UserSettings:
    """User personalization settings"""

    hidden_message_types: List[str] = field(
        default_factory=list
    )  # Message types to hide
    custom_cwd: Optional[str] = None  # Custom working directory
    # Nested map: {base_session_id: {working_path: claude_session_id}}
    session_mappings: Dict[str, Dict[str, str]] = field(default_factory=dict)
    # Slack active threads: {channel_id: {thread_ts: last_active_timestamp}}
    active_slack_threads: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UserSettings":
        """Create from dictionary"""
        return cls(**data)


class SettingsManager:
    """Manages user personalization settings with JSON persistence"""

    def __init__(self, settings_file: str = "user_settings.json"):
        self.settings_file = Path(settings_file)
        self.settings: Dict[Union[int, str], UserSettings] = {}
        self._load_settings()

    # ---------------------------------------------
    # Internal helpers
    # ---------------------------------------------
    def _normalize_user_id(self, user_id: Union[int, str]) -> str:
        """Normalize user_id consistently to string.

        Rationale: JSON object keys are strings; Slack IDs are strings; unifying to
        string avoids mixed-type keys (e.g., 123 vs "123").
        """
        return str(user_id)

    def _load_settings(self):
        """Load settings from JSON file"""
        try:
            if self.settings_file.exists():
                with open(self.settings_file, "r") as f:
                    data = json.load(f)
                    for user_id_str, user_data in data.items():
                        # Clean up old format session mappings, only keep nested dict format
                        if "session_mappings" in user_data:
                            cleaned_mappings = {}
                            for key, value in user_data["session_mappings"].items():
                                # Only keep nested dictionary format
                                if isinstance(value, dict):
                                    cleaned_mappings[key] = value
                            user_data["session_mappings"] = cleaned_mappings

                        # Ensure active_slack_threads exists and is properly formatted
                        if "active_slack_threads" not in user_data:
                            user_data["active_slack_threads"] = {}

                        # Always keep user_id as string in memory
                        user_id = user_id_str
                        self.settings[user_id] = UserSettings.from_dict(user_data)
                logger.info(f"Loaded settings for {len(self.settings)} users")
            else:
                logger.info("No settings file found, starting with empty settings")
        except Exception as e:
            logger.error(f"Error loading settings: {e}")
            self.settings = {}

    def _save_settings(self):
        """Save settings to JSON file"""
        try:
            data = {
                str(user_id): settings.to_dict()
                for user_id, settings in self.settings.items()
            }
            with open(self.settings_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.info("Settings saved successfully")
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

    def get_user_settings(self, user_id: Union[int, str]) -> UserSettings:
        """Get settings for a specific user"""
        normalized_id = self._normalize_user_id(user_id)

        # Return existing or create new
        if normalized_id not in self.settings:
            self.settings[normalized_id] = UserSettings()
            self._save_settings()
        return self.settings[normalized_id]

    def update_user_settings(self, user_id: Union[int, str], settings: UserSettings):
        """Update settings for a specific user"""
        normalized_id = self._normalize_user_id(user_id)

        self.settings[normalized_id] = settings
        self._save_settings()

    def toggle_hidden_message_type(
        self, user_id: Union[int, str], message_type: str
    ) -> bool:
        """Toggle a message type in hidden list, returns new state"""
        settings = self.get_user_settings(user_id)

        if message_type in settings.hidden_message_types:
            settings.hidden_message_types.remove(message_type)
            is_hidden = False
        else:
            settings.hidden_message_types.append(message_type)
            is_hidden = True

        self.update_user_settings(user_id, settings)
        return is_hidden

    def set_custom_cwd(self, user_id: Union[int, str], cwd: str):
        """Set custom working directory for user"""
        settings = self.get_user_settings(user_id)
        settings.custom_cwd = cwd
        self.update_user_settings(user_id, settings)

    def get_custom_cwd(self, user_id: Union[int, str]) -> Optional[str]:
        """Get custom working directory for user"""
        settings = self.get_user_settings(user_id)
        return settings.custom_cwd

    def is_message_type_hidden(
        self, user_id: Union[int, str], message_type: str
    ) -> bool:
        """Check if a message type is hidden for user"""
        settings = self.get_user_settings(user_id)
        return message_type in settings.hidden_message_types

    def save_user_settings(self, user_id: Union[int, str], settings: UserSettings):
        """Save settings for a specific user (alias for update_user_settings)"""
        self.update_user_settings(user_id, settings)

    def get_available_message_types(self) -> List[str]:
        """Get list of available message types that can be hidden"""
        return ["system", "user", "assistant", "result"]

    def get_message_type_display_names(self) -> Dict[str, str]:
        """Get display names for message types"""
        return {
            "system": "System",
            "user": "Response",  # Renamed from 'user' for clarity
            "assistant": "Assistant",
            "result": "Result",
        }

    def set_session_mapping(
        self,
        user_id: Union[int, str],
        base_session_id: str,
        working_path: str,
        claude_session_id: str,
    ):
        """Store mapping between base session ID, working path, and Claude session ID"""
        settings = self.get_user_settings(user_id)
        if base_session_id not in settings.session_mappings:
            settings.session_mappings[base_session_id] = {}
        settings.session_mappings[base_session_id][working_path] = claude_session_id
        self.update_user_settings(user_id, settings)
        logger.info(
            f"Stored session mapping for user {user_id}: {base_session_id}[{working_path}] -> {claude_session_id}"
        )

    def get_claude_session_id(
        self, user_id: Union[int, str], base_session_id: str, working_path: str
    ) -> Optional[str]:
        """Get Claude session ID for given base session ID and working path"""
        settings = self.get_user_settings(user_id)
        if base_session_id in settings.session_mappings:
            return settings.session_mappings[base_session_id].get(working_path)
        return None

    def clear_session_mapping(
        self,
        user_id: Union[int, str],
        base_session_id: str,
        working_path: Optional[str] = None,
    ):
        """Clear session mapping for given base session ID and optionally working path"""
        settings = self.get_user_settings(user_id)
        if base_session_id in settings.session_mappings:
            if working_path:
                # Clear specific path mapping
                if working_path in settings.session_mappings[base_session_id]:
                    del settings.session_mappings[base_session_id][working_path]
                    logger.info(
                        f"Cleared session mapping for user {user_id}: {base_session_id}[{working_path}]"
                    )
            else:
                # Clear all mappings for this base session
                del settings.session_mappings[base_session_id]
                logger.info(
                    f"Cleared all session mappings for user {user_id}: {base_session_id}"
                )
            self.update_user_settings(user_id, settings)

    def clear_all_session_mappings(self, user_id: Union[int, str]):
        """Clear all session mappings for a user"""
        settings = self.get_user_settings(user_id)
        if settings.session_mappings:
            count = len(settings.session_mappings)
            settings.session_mappings.clear()
            logger.info(f"Cleared all {count} session mappings for user {user_id}")
            self.update_user_settings(user_id, settings)

    # ---------------------------------------------
    # Slack thread management
    # ---------------------------------------------
    def mark_thread_active(
        self, user_id: Union[int, str], channel_id: str, thread_ts: str
    ):
        """Mark a Slack thread as active with current timestamp"""
        settings = self.get_user_settings(user_id)

        if channel_id not in settings.active_slack_threads:
            settings.active_slack_threads[channel_id] = {}

        settings.active_slack_threads[channel_id][thread_ts] = time.time()
        self.update_user_settings(user_id, settings)
        logger.info(
            f"Marked thread active for user {user_id}: channel={channel_id}, thread={thread_ts}"
        )

    def is_thread_active(
        self, user_id: Union[int, str], channel_id: str, thread_ts: str
    ) -> bool:
        """Check if a Slack thread is active (within 24 hours)"""
        settings = self.get_user_settings(user_id)

        # First cleanup expired threads for this channel
        self._cleanup_expired_threads_for_channel(user_id, channel_id)

        # Then check if thread is active
        if channel_id in settings.active_slack_threads:
            if thread_ts in settings.active_slack_threads[channel_id]:
                return True

        return False

    def _cleanup_expired_threads_for_channel(
        self, user_id: Union[int, str], channel_id: str
    ):
        """Remove threads older than 24 hours for a specific channel"""
        settings = self.get_user_settings(user_id)

        if channel_id not in settings.active_slack_threads:
            return

        current_time = time.time()
        twenty_four_hours_ago = current_time - (24 * 60 * 60)

        # Find expired threads
        expired_threads = [
            thread_ts
            for thread_ts, last_active in settings.active_slack_threads[channel_id].items()
            if last_active < twenty_four_hours_ago
        ]

        # Remove expired threads
        if expired_threads:
            for thread_ts in expired_threads:
                del settings.active_slack_threads[channel_id][thread_ts]

            # Clean up empty channel dict
            if not settings.active_slack_threads[channel_id]:
                del settings.active_slack_threads[channel_id]

            self.update_user_settings(user_id, settings)
            logger.info(
                f"Cleaned up {len(expired_threads)} expired threads for channel {channel_id}"
            )

    def cleanup_all_expired_threads(self, user_id: Union[int, str]):
        """Remove all threads older than 24 hours for all channels"""
        settings = self.get_user_settings(user_id)

        if not settings.active_slack_threads:
            return

        channels_to_clean = list(settings.active_slack_threads.keys())
        for channel_id in channels_to_clean:
            self._cleanup_expired_threads_for_channel(user_id, channel_id)
