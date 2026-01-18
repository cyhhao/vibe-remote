import json
import logging
import os
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

from config import paths
from config.v2_sessions import SessionsStore


logger = logging.getLogger(__name__)


DEFAULT_HIDDEN_MESSAGE_TYPES = ["system", "assistant", "toolcall"]


@dataclass
class ChannelRouting:
    """Per-channel agent routing configuration."""

    agent_backend: Optional[str] = None  # "claude" | "codex" | "opencode" | None
    opencode_agent: Optional[str] = None  # "build" | "plan" | ... | None
    opencode_model: Optional[str] = None  # "provider/model" | None
    opencode_reasoning_effort: Optional[str] = None  # "low" | "medium" | "high" | "xhigh" | None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ChannelRouting":
        """Create from dictionary"""
        if data is None:
            return None
        return cls(
            agent_backend=data.get("agent_backend"),
            opencode_agent=data.get("opencode_agent"),
            opencode_model=data.get("opencode_model"),
            opencode_reasoning_effort=data.get("opencode_reasoning_effort"),
        )


@dataclass
class UserSettings:
    hidden_message_types: List[str] = field(
        default_factory=lambda: DEFAULT_HIDDEN_MESSAGE_TYPES.copy()
    )
    custom_cwd: Optional[str] = None
    channel_routing: Optional[ChannelRouting] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        result = asdict(self)
        # Handle ChannelRouting serialization
        if self.channel_routing is not None:
            result["channel_routing"] = self.channel_routing.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "UserSettings":
        """Create from dictionary"""
        # Handle channel_routing deserialization
        routing_data = data.pop("channel_routing", None)
        settings = cls(**data)
        if routing_data:
            settings.channel_routing = ChannelRouting.from_dict(routing_data)
        return settings


class SettingsManager:
    """Manages user personalization settings with JSON persistence"""

    MESSAGE_TYPE_ALIASES = {
        # Legacy/compat aliases
        "response": "toolcall",
        "user": "toolcall",
        # Normalize common variants
        "tool_call": "toolcall",
        "tool": "toolcall",
    }

    def __init__(self, settings_file: Optional[str] = None):
        paths.ensure_data_dirs()
        self.settings_file = Path(settings_file) if settings_file else paths.get_settings_path()
        self.settings: Dict[Union[int, str], UserSettings] = {}
        self.sessions_store = SessionsStore()
        self.sessions_store.load()
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
                        user_id = user_id_str
                        settings = UserSettings.from_dict(user_data)
                        settings.hidden_message_types = self._normalize_hidden_message_types(
                            settings.hidden_message_types
                        )
                        self.settings[user_id] = settings

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

        settings.hidden_message_types = self._normalize_hidden_message_types(
            settings.hidden_message_types
        )

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
        return ["system", "assistant", "toolcall"]

    def get_message_type_display_names(self) -> Dict[str, str]:
        """Get display names for message types"""
        return {
            "system": "System",
            "assistant": "Assistant",
            "toolcall": "Toolcall",
        }

    def _ensure_agent_namespace(self, user_id: Union[int, str], agent_name: str) -> Dict[str, Dict[str, str]]:
        user_key = self._normalize_user_id(user_id)
        return self.sessions_store.get_agent_map(user_key, agent_name)

    def set_agent_session_mapping(
        self,
        user_id: Union[int, str],
        agent_name: str,
        base_session_id: str,
        working_path: str,
        session_id: str,
    ):
        """Store mapping between base session ID, working path, and agent session ID"""
        agent_map = self._ensure_agent_namespace(user_id, agent_name)
        if base_session_id not in agent_map:
            agent_map[base_session_id] = {}
        agent_map[base_session_id][working_path] = session_id
        self.sessions_store.save()
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
        user_key = self._normalize_user_id(user_id)
        agent_map = self.sessions_store.get_agent_map(user_key, agent_name)
        if base_session_id in agent_map:
            return agent_map[base_session_id].get(working_path)
        return None

    def _canonicalize_message_type(self, message_type: str) -> str:
        """Normalize message type to canonical form to support aliases."""
        return self.MESSAGE_TYPE_ALIASES.get(message_type, message_type)

    def _normalize_hidden_message_types(self, hidden_message_types: List[str]) -> List[str]:
        """Normalize and migrate hidden message types to current canonical schema."""
        allowed = {"system", "assistant", "toolcall"}
        normalized: List[str] = []
        seen = set()

        for msg_type in hidden_message_types or []:
            canonical = self._canonicalize_message_type(msg_type)
            if canonical not in allowed:
                continue
            if canonical in seen:
                continue
            seen.add(canonical)
            normalized.append(canonical)

        return normalized

    def clear_agent_session_mapping(
        self,
        user_id: Union[int, str],
        agent_name: str,
        base_session_id: str,
        working_path: Optional[str] = None,
    ):
        """Clear session mapping for given base session ID and optionally working path"""
        user_key = self._normalize_user_id(user_id)
        agent_map = self.sessions_store.get_agent_map(user_key, agent_name)
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
            self.sessions_store.save()

    def clear_agent_sessions(self, user_id: Union[int, str], agent_name: str):
        """Clear every session mapping for the specified agent."""
        user_key = self._normalize_user_id(user_id)
        agent_map = self.sessions_store.get_agent_map(user_key, agent_name)
        if agent_map:
            self.sessions_store.state.session_mappings[user_key][agent_name] = {}
            logger.info(
                f"Cleared all {agent_name} session namespaces for user {user_id}"
            )
            self.sessions_store.save()

    def clear_all_session_mappings(self, user_id: Union[int, str]):
        """Clear all session mappings for a user across agents"""
        user_key = self._normalize_user_id(user_id)
        agent_maps = self.sessions_store.state.session_mappings.get(user_key, {})
        if agent_maps:
            count = sum(len(agent_map) for agent_map in agent_maps.values())
            self.sessions_store.state.session_mappings[user_key] = {}
            logger.info(
                f"Cleared all session mappings ({count} bases) for user {user_id}"
            )
            self.sessions_store.save()

    def list_agent_session_bases(
        self, user_id: Union[int, str], agent_name: str
    ) -> Dict[str, Dict[str, str]]:
        """Get copy of session mappings for an agent."""
        user_key = self._normalize_user_id(user_id)
        agent_map = self.sessions_store.get_agent_map(user_key, agent_name)
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
        user_key = self._normalize_user_id(user_id)
        channel_map = self.sessions_store.get_thread_map(user_key, channel_id)
        channel_map[thread_ts] = time.time()
        self.sessions_store.save()
        logger.info(
            f"Marked thread active for user {user_id}: channel={channel_id}, thread={thread_ts}"
        )

    def is_thread_active(
        self, user_id: Union[int, str], channel_id: str, thread_ts: str
    ) -> bool:
        """Check if a Slack thread is active (within 24 hours)"""
        user_key = self._normalize_user_id(user_id)

        # First cleanup expired threads for this channel
        self._cleanup_expired_threads_for_channel(user_id, channel_id)

        channel_map = self.sessions_store.get_thread_map(user_key, channel_id)
        return thread_ts in channel_map

    def _cleanup_expired_threads_for_channel(
        self, user_id: Union[int, str], channel_id: str
    ):
        """Remove threads older than 24 hours for a specific channel"""
        user_key = self._normalize_user_id(user_id)
        channel_map = self.sessions_store.get_thread_map(user_key, channel_id)

        if not channel_map:
            return

        current_time = time.time()
        twenty_four_hours_ago = current_time - (24 * 60 * 60)

        expired_threads = [
            thread_ts
            for thread_ts, last_active in channel_map.items()
            if last_active < twenty_four_hours_ago
        ]

        if expired_threads:
            for thread_ts in expired_threads:
                del channel_map[thread_ts]

            if not channel_map:
                self.sessions_store.state.active_slack_threads[user_key].pop(channel_id, None)

            self.sessions_store.save()
            logger.info(
                f"Cleaned up {len(expired_threads)} expired threads for channel {channel_id}"
            )

    def cleanup_all_expired_threads(self, user_id: Union[int, str]):
        """Remove all threads older than 24 hours for all channels"""
        user_key = self._normalize_user_id(user_id)
        channel_map = self.sessions_store.state.active_slack_threads.get(user_key, {})

        if not channel_map:
            return

        channels_to_clean = list(channel_map.keys())
        for channel_id in channels_to_clean:
            self._cleanup_expired_threads_for_channel(user_id, channel_id)

    # ---------------------------------------------
    # Channel routing management
    # ---------------------------------------------
    def get_channel_routing(
        self, settings_key: Union[int, str]
    ) -> Optional[ChannelRouting]:
        """Get channel routing override for the given settings key."""
        settings = self.get_user_settings(settings_key)
        return settings.channel_routing

    def set_channel_routing(
        self, settings_key: Union[int, str], routing: ChannelRouting
    ):
        """Set channel routing override."""
        settings = self.get_user_settings(settings_key)
        settings.channel_routing = routing
        self.update_user_settings(settings_key, settings)
        logger.info(
            f"Updated channel routing for {settings_key}: "
            f"backend={routing.agent_backend}, "
            f"opencode_agent={routing.opencode_agent}, "
            f"opencode_model={routing.opencode_model}"
        )

    def clear_channel_routing(self, settings_key: Union[int, str]):
        """Clear channel routing override (fall back to agent_routes.yaml)."""
        settings = self.get_user_settings(settings_key)
        if settings.channel_routing:
            settings.channel_routing = None
            self.update_user_settings(settings_key, settings)
            logger.info(f"Cleared channel routing for {settings_key}")
