import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Union
from pathlib import Path


logger = logging.getLogger(__name__)


@dataclass
class UserSettings:
    """User personalization settings"""

    hidden_message_types: List[str] = field(
        default_factory=list
    )  # Message types to hide
    custom_cwd: Optional[str] = None  # Custom working directory
    # Nested map: {agent_name: {base_session_id: {working_path: session_id}}}
    session_mappings: Dict[str, Dict[str, Dict[str, str]]] = field(
        default_factory=dict
    )
    # Slack active threads: {channel_id: {thread_ts: last_active_timestamp}}
    active_slack_threads: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # Telegram Topics support: {chat_id: {topic_id: worktree_path}}
    topic_worktrees: Dict[str, Dict[str, str]] = field(default_factory=dict)

    # Manager topic ID for each chat: {chat_id: topic_id}
    manager_topic_ids: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UserSettings":
        """Create from dictionary"""
        return cls(**data)


class SettingsManager:
    """Manages user personalization settings with JSON persistence"""

    MESSAGE_TYPE_ALIASES = {
        "response": "user",
    }

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
                        # Normalize session mappings to agent-aware structure
                        if "session_mappings" in user_data:
                            user_data["session_mappings"] = (
                                self._normalize_session_mappings(
                                    user_data["session_mappings"]
                                )
                            )

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

    def _normalize_session_mappings(
        self, mappings: Dict[str, Any]
    ) -> Dict[str, Dict[str, Dict[str, str]]]:
        """Normalize legacy session mapping schema into agent-aware structure."""
        normalized: Dict[str, Dict[str, Dict[str, str]]] = {}

        if not isinstance(mappings, dict):
            return normalized

        def is_path_map(value) -> bool:
            return isinstance(value, dict) and all(
                isinstance(v, str) for v in value.values()
            )

        # Detect new-format structure: {agent: {base_session_id: {path: session_id}}}
        is_new_format = all(
            isinstance(agent_map, dict) and all(is_path_map(path_map) for path_map in agent_map.values())
            for agent_map in mappings.values()
        )
        if is_new_format:
            return mappings

        # Legacy structure: {base_session_id: {path: session_id}}
        legacy_entries = {}
        for base_session_id, path_map in mappings.items():
            if is_path_map(path_map):
                legacy_entries[base_session_id] = path_map

        if legacy_entries:
            normalized["claude"] = legacy_entries

        return normalized

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
        message_type = self._canonicalize_message_type(message_type)
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
        message_type = self._canonicalize_message_type(message_type)
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

    def _ensure_agent_namespace(
        self, settings: UserSettings, agent_name: str
    ) -> Dict[str, Dict[str, str]]:
        """Ensure nested dict for an agent exists."""
        if agent_name not in settings.session_mappings:
            settings.session_mappings[agent_name] = {}
        return settings.session_mappings[agent_name]

    def set_agent_session_mapping(
        self,
        user_id: Union[int, str],
        agent_name: str,
        base_session_id: str,
        working_path: str,
        session_id: str,
    ):
        """Store mapping between base session ID, working path, and agent session ID"""
        settings = self.get_user_settings(user_id)
        agent_map = self._ensure_agent_namespace(settings, agent_name)
        if base_session_id not in agent_map:
            agent_map[base_session_id] = {}
        agent_map[base_session_id][working_path] = session_id
        self.update_user_settings(user_id, settings)
        logger.info(
            f"Stored {agent_name} session mapping for user {user_id}: "
            f"{base_session_id}[{working_path}] -> {session_id}"
        )

    def get_agent_session_id(
        self,
        user_id: Union[int, str],
        base_session_id: str,
        working_path: str,
        agent_name: str,
    ) -> Optional[str]:
        """Get agent session ID for given base session ID and working path"""
        settings = self.get_user_settings(user_id)
        agent_map = settings.session_mappings.get(agent_name, {})
        if base_session_id in agent_map:
            return agent_map[base_session_id].get(working_path)
        return None

    def _canonicalize_message_type(self, message_type: str) -> str:
        """Normalize message type to canonical form to support aliases."""
        return self.MESSAGE_TYPE_ALIASES.get(message_type, message_type)

    def clear_agent_session_mapping(
        self,
        user_id: Union[int, str],
        agent_name: str,
        base_session_id: str,
        working_path: Optional[str] = None,
    ):
        """Clear session mapping for given base session ID and optionally working path"""
        settings = self.get_user_settings(user_id)
        agent_map = settings.session_mappings.get(agent_name, {})
        if base_session_id in agent_map:
            if working_path:
                if working_path in agent_map[base_session_id]:
                    del agent_map[base_session_id][working_path]
                    logger.info(
                        f"Cleared {agent_name} session mapping for user {user_id}: "
                        f"{base_session_id}[{working_path}]"
                    )
            else:
                del agent_map[base_session_id]
                logger.info(
                    f"Cleared all {agent_name} session mappings for user {user_id}: {base_session_id}"
                )
            self.update_user_settings(user_id, settings)

    def clear_agent_sessions(self, user_id: Union[int, str], agent_name: str):
        """Clear every session mapping for the specified agent."""
        settings = self.get_user_settings(user_id)
        if agent_name in settings.session_mappings:
            del settings.session_mappings[agent_name]
            logger.info(
                f"Cleared all {agent_name} session namespaces for user {user_id}"
            )
            self.update_user_settings(user_id, settings)

    def clear_all_session_mappings(self, user_id: Union[int, str]):
        """Clear all session mappings for a user across agents"""
        settings = self.get_user_settings(user_id)
        if settings.session_mappings:
            count = sum(len(agent_map) for agent_map in settings.session_mappings.values())
            settings.session_mappings.clear()
            logger.info(
                f"Cleared all session mappings ({count} bases) for user {user_id}"
            )
            self.update_user_settings(user_id, settings)

    def list_agent_session_bases(
        self, user_id: Union[int, str], agent_name: str
    ) -> Dict[str, Dict[str, str]]:
        """Get copy of session mappings for an agent."""
        settings = self.get_user_settings(user_id)
        agent_map = settings.session_mappings.get(agent_name, {})
        return {base: paths.copy() for base, paths in agent_map.items()}

    # Backwards-compatible helpers for Claude-specific call sites
    def set_session_mapping(
        self,
        user_id: Union[int, str],
        base_session_id: str,
        working_path: str,
        claude_session_id: str,
    ):
        self.set_agent_session_mapping(
            user_id, "claude", base_session_id, working_path, claude_session_id
        )

    def get_claude_session_id(
        self, user_id: Union[int, str], base_session_id: str, working_path: str
    ) -> Optional[str]:
        return self.get_agent_session_id(
            user_id, base_session_id, working_path, agent_name="claude"
        )

    def clear_session_mapping(
        self,
        user_id: Union[int, str],
        base_session_id: str,
        working_path: Optional[str] = None,
    ):
        self.clear_agent_session_mapping(
            user_id, "claude", base_session_id, working_path
        )

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

    # ---------------------------------------------
    # Topic-Worktree management (Telegram Topics)
    # ---------------------------------------------

    def set_topic_worktree(
        self,
        user_id: Union[int, str],
        chat_id: Union[int, str],
        topic_id: Union[int, str],
        worktree_path: str,
    ):
        """Associate a topic with a worktree path"""
        settings = self.get_user_settings(user_id)
        chat_key = self._normalize_user_id(chat_id)
        topic_key = str(topic_id)

        if chat_key not in settings.topic_worktrees:
            settings.topic_worktrees[chat_key] = {}

        settings.topic_worktrees[chat_key][topic_key] = worktree_path
        self.update_user_settings(user_id, settings)
        logger.info(
            f"Set worktree for user {user_id}, chat {chat_key}, topic {topic_key}: {worktree_path}"
        )

    def get_topic_worktree(
        self,
        user_id: Union[int, str],
        chat_id: Union[int, str],
        topic_id: Union[int, str],
    ) -> Optional[str]:
        """Get worktree path for a topic"""
        settings = self.get_user_settings(user_id)
        chat_key = self._normalize_user_id(chat_id)
        topic_key = str(topic_id)

        return settings.topic_worktrees.get(chat_key, {}).get(topic_key)

    def list_topics_for_chat(
        self, user_id: Union[int, str], chat_id: Union[int, str]
    ) -> Dict[str, str]:
        """List all topics and their worktrees for a chat"""
        settings = self.get_user_settings(user_id)
        chat_key = self._normalize_user_id(chat_id)

        return settings.topic_worktrees.get(chat_key, {}).copy()

    def remove_topic_worktree(
        self,
        user_id: Union[int, str],
        chat_id: Union[int, str],
        topic_id: Union[int, str],
    ):
        """Remove topic-worktree association"""
        settings = self.get_user_settings(user_id)
        chat_key = self._normalize_user_id(chat_id)
        topic_key = str(topic_id)

        if chat_key in settings.topic_worktrees:
            if topic_key in settings.topic_worktrees[chat_key]:
                del settings.topic_worktrees[chat_key][topic_key]
                logger.info(
                    f"Removed worktree for user {user_id}, chat {chat_key}, topic {topic_key}"
                )

                # Clean up empty chat entry
                if not settings.topic_worktrees[chat_key]:
                    del settings.topic_worktrees[chat_key]

                self.update_user_settings(user_id, settings)

    def set_manager_topic(
        self,
        user_id: Union[int, str],
        chat_id: Union[int, str],
        topic_id: Union[int, str],
    ):
        """Set manager topic ID for a chat"""
        settings = self.get_user_settings(user_id)
        chat_key = self._normalize_user_id(chat_id)
        topic_key = str(topic_id)

        settings.manager_topic_ids[chat_key] = topic_key
        self.update_user_settings(user_id, settings)
        logger.info(
            f"Set manager topic for user {user_id}, chat {chat_key}: {topic_key}"
        )

    def get_manager_topic(
        self, user_id: Union[int, str], chat_id: Union[int, str]
    ) -> Optional[str]:
        """Get manager topic ID for a chat"""
        settings = self.get_user_settings(user_id)
        chat_key = self._normalize_user_id(chat_id)

        return settings.manager_topic_ids.get(chat_key)

    def is_manager_topic(
        self,
        user_id: Union[int, str],
        chat_id: Union[int, str],
        topic_id: Union[int, str],
    ) -> bool:
        """Check if a topic is the manager topic"""
        settings = self.get_user_settings(user_id)
        chat_key = self._normalize_user_id(chat_id)
        topic_key = str(topic_id)

        manager_topic = settings.manager_topic_ids.get(chat_key)
        return manager_topic == topic_key
