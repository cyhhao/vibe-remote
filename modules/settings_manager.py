import logging
import hashlib
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

from config import paths
from config.v2_sessions import SessionsStore
from config.v2_settings import SettingsStore, ChannelSettings, RoutingSettings


logger = logging.getLogger(__name__)


DEFAULT_SHOW_MESSAGE_TYPES: List[str] = []


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
    show_message_types: List[str] = field(default_factory=lambda: DEFAULT_SHOW_MESSAGE_TYPES.copy())
    custom_cwd: Optional[str] = None
    channel_routing: Optional[ChannelRouting] = None
    enabled: bool = True

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        result = {
            "show_message_types": self.show_message_types,
            "custom_cwd": self.custom_cwd,
        }
        if self.channel_routing is not None:
            result["routing"] = self.channel_routing.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "UserSettings":
        """Create from dictionary"""
        if data is None:
            return cls()
        payload = dict(data)
        routing_data = payload.pop("routing", None)
        show_message_types = payload.get("show_message_types")
        settings = cls(
            show_message_types=(
                show_message_types if show_message_types is not None else DEFAULT_SHOW_MESSAGE_TYPES.copy()
            ),
            custom_cwd=payload.get("custom_cwd"),
        )
        if routing_data is not None:
            settings.channel_routing = ChannelRouting.from_dict(routing_data)
        return settings


class SettingsManager:
    """Manages user personalization settings with JSON persistence"""

    MESSAGE_TYPE_ALIASES = {
        "tool_call": "toolcall",
        "tool": "toolcall",
    }

    def __init__(self, settings_file: Optional[str] = None):
        paths.ensure_data_dirs()
        self.settings_file = Path(settings_file) if settings_file else paths.get_settings_path()
        self._settings_mtime_ns: Optional[int] = None
        self._settings_fingerprint: Optional[str] = None
        self.settings: Dict[Union[int, str], UserSettings] = {}
        self.store = SettingsStore(self.settings_file)
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

    def _from_channel_settings(self, channel_settings: ChannelSettings) -> UserSettings:
        routing = ChannelRouting(
            agent_backend=channel_settings.routing.agent_backend,
            opencode_agent=channel_settings.routing.opencode_agent,
            opencode_model=channel_settings.routing.opencode_model,
            opencode_reasoning_effort=channel_settings.routing.opencode_reasoning_effort,
        )
        return UserSettings(
            show_message_types=self._normalize_show_message_types(channel_settings.show_message_types),
            custom_cwd=channel_settings.custom_cwd,
            channel_routing=routing,
            enabled=channel_settings.enabled,
        )

    def _to_channel_settings(self, settings: UserSettings) -> ChannelSettings:
        routing = settings.channel_routing or ChannelRouting()
        return ChannelSettings(
            enabled=settings.enabled,
            show_message_types=self._normalize_show_message_types(settings.show_message_types),
            custom_cwd=settings.custom_cwd,
            routing=RoutingSettings(
                agent_backend=routing.agent_backend,
                opencode_agent=routing.opencode_agent,
                opencode_model=routing.opencode_model,
                opencode_reasoning_effort=routing.opencode_reasoning_effort,
            ),
        )

    def _load_settings(self):
        """Load settings from JSON file"""
        self.store = SettingsStore(self.settings_file)
        self.settings = {}

        if not self.store.settings.channels:
            logger.info("No settings file found, starting with empty settings")
            return

        for channel_id, channel_settings in self.store.settings.channels.items():
            self.settings[str(channel_id)] = self._from_channel_settings(channel_settings)

        try:
            self._settings_mtime_ns = self.settings_file.stat().st_mtime_ns
            self._settings_fingerprint = self._compute_settings_fingerprint()
        except FileNotFoundError:
            self._settings_mtime_ns = None
            self._settings_fingerprint = None

        logger.info(f"Loaded settings for {len(self.settings)} channels")

    def _compute_settings_fingerprint(self) -> Optional[str]:
        try:
            data = self.settings_file.read_bytes()
        except FileNotFoundError:
            return None
        return hashlib.sha256(data).hexdigest()

    def _reload_if_changed(self) -> None:
        if not self.settings_file.exists():
            return
        try:
            mtime_ns = self.settings_file.stat().st_mtime_ns
        except FileNotFoundError:
            return
        fingerprint = None
        if self._settings_mtime_ns is None or mtime_ns != self._settings_mtime_ns:
            fingerprint = self._compute_settings_fingerprint()
        elif self._settings_fingerprint is None:
            fingerprint = self._compute_settings_fingerprint()
        if fingerprint and fingerprint != self._settings_fingerprint:
            logger.info("Settings file changed on disk, reloading")
            self._load_settings()
        elif fingerprint:
            self._settings_fingerprint = fingerprint
            self._settings_mtime_ns = mtime_ns
        else:
            self._settings_mtime_ns = mtime_ns

    def _save_settings(self):
        """Save settings to JSON file"""
        try:
            channels: Dict[str, ChannelSettings] = {}
            for settings_key, settings in self.settings.items():
                existing = self.store.settings.channels.get(str(settings_key))
                channel_settings = self._to_channel_settings(settings)
                if existing is not None:
                    channel_settings.enabled = existing.enabled
                channels[str(settings_key)] = channel_settings
            self.store.settings.channels = channels
            self.store.save()
            try:
                self._settings_mtime_ns = self.settings_file.stat().st_mtime_ns
                self._settings_fingerprint = self._compute_settings_fingerprint()
            except FileNotFoundError:
                self._settings_mtime_ns = None
                self._settings_fingerprint = None
            logger.info("Settings saved successfully")
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

    def get_user_settings(self, user_id: Union[int, str]) -> UserSettings:
        """Get settings for a specific user"""
        normalized_id = self._normalize_user_id(user_id)

        self._reload_if_changed()

        # Return existing or create new
        if normalized_id not in self.settings:
            settings = UserSettings()
            if normalized_id in self.store.settings.channels:
                settings = self._from_channel_settings(self.store.settings.channels[normalized_id])
            self.settings[normalized_id] = settings
            self._save_settings()
        return self.settings[normalized_id]

    def update_user_settings(self, user_id: Union[int, str], settings: UserSettings):
        """Update settings for a specific user"""
        normalized_id = self._normalize_user_id(user_id)

        settings.show_message_types = self._normalize_show_message_types(settings.show_message_types)

        self.settings[normalized_id] = settings
        self._save_settings()

    def toggle_show_message_type(self, user_id: Union[int, str], message_type: str) -> bool:
        """Toggle a message type in show list, returns new state (True if now shown)"""
        message_type = self._canonicalize_message_type(message_type)
        settings = self.get_user_settings(user_id)

        if message_type in settings.show_message_types:
            settings.show_message_types.remove(message_type)
            is_shown = False
        else:
            settings.show_message_types.append(message_type)
            is_shown = True

        self.update_user_settings(user_id, settings)
        return is_shown

    def set_custom_cwd(self, user_id: Union[int, str], cwd: str):
        """Set custom working directory for user"""
        settings = self.get_user_settings(user_id)
        settings.custom_cwd = cwd
        self.update_user_settings(user_id, settings)

    def get_custom_cwd(self, user_id: Union[int, str]) -> Optional[str]:
        """Get custom working directory for user"""
        settings = self.get_user_settings(user_id)
        return settings.custom_cwd

    def get_channel_settings(self, channel_id: Union[int, str]) -> Optional[ChannelSettings]:
        """Get raw ChannelSettings for a channel without creating defaults."""
        self._reload_if_changed()
        key = str(channel_id)
        return self.store.settings.channels.get(key)

    def is_message_type_hidden(self, user_id: Union[int, str], message_type: str) -> bool:
        """Check if a message type is hidden for user (not in show_message_types)"""
        self._reload_if_changed()
        message_type = self._canonicalize_message_type(message_type)
        settings = self.get_user_settings(user_id)
        return message_type not in settings.show_message_types

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

    def _ensure_agent_namespace(self, user_id: Union[int, str], agent_name: str) -> Dict[str, str]:
        user_key = self._normalize_user_id(user_id)
        return self.sessions_store.get_agent_map(user_key, agent_name)

    def set_agent_session_mapping(
        self,
        user_id: Union[int, str],
        agent_name: str,
        thread_id: str,
        session_id: str,
    ):
        """Store mapping between thread ID and agent session ID"""
        agent_map = self._ensure_agent_namespace(user_id, agent_name)
        agent_map[thread_id] = session_id
        self.sessions_store.save()
        logger.info(f"Stored {agent_name} session mapping for {user_id}: {thread_id} -> {session_id}")

    def get_agent_session_id(
        self,
        user_id: Union[int, str],
        thread_id: str,
        agent_name: str,
    ) -> Optional[str]:
        """Get agent session ID for given thread ID"""
        user_key = self._normalize_user_id(user_id)
        agent_map = self.sessions_store.get_agent_map(user_key, agent_name)
        return agent_map.get(thread_id)

    def _canonicalize_message_type(self, message_type: str) -> str:
        """Normalize message type to canonical form to support aliases."""
        return self.MESSAGE_TYPE_ALIASES.get(message_type, message_type)

    def _normalize_show_message_types(self, show_message_types: Optional[List[str]]) -> List[str]:
        """Normalize and migrate show message types to current canonical schema."""
        allowed = {"system", "assistant", "toolcall"}
        if show_message_types is None:
            return DEFAULT_SHOW_MESSAGE_TYPES.copy()
        normalized: List[str] = []
        seen = set()

        for msg_type in show_message_types or []:
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
        thread_id: str,
    ):
        """Clear session mapping for given thread ID"""
        user_key = self._normalize_user_id(user_id)
        agent_map = self.sessions_store.get_agent_map(user_key, agent_name)
        if thread_id in agent_map:
            del agent_map[thread_id]
            logger.info(f"Cleared {agent_name} session mapping for user {user_id}: {thread_id}")
            self.sessions_store.save()

    def clear_agent_sessions(self, user_id: Union[int, str], agent_name: str):
        """Clear every session mapping for the specified agent."""
        user_key = self._normalize_user_id(user_id)
        agent_map = self.sessions_store.get_agent_map(user_key, agent_name)
        if agent_map:
            self.sessions_store.state.session_mappings[user_key][agent_name] = {}
            logger.info(f"Cleared all {agent_name} session namespaces for user {user_id}")
            self.sessions_store.save()

    def clear_all_session_mappings(self, user_id: Union[int, str]):
        """Clear all session mappings for a user across agents"""
        user_key = self._normalize_user_id(user_id)
        agent_maps = self.sessions_store.state.session_mappings.get(user_key, {})
        if agent_maps:
            count = sum(len(agent_map) for agent_map in agent_maps.values())
            self.sessions_store.state.session_mappings[user_key] = {}
            logger.info(f"Cleared all session mappings ({count} bases) for user {user_id}")
            self.sessions_store.save()

    def list_agent_sessions(self, user_id: Union[int, str], agent_name: str) -> Dict[str, str]:
        """Get copy of session mappings (thread_id -> session_id) for an agent."""
        user_key = self._normalize_user_id(user_id)
        agent_map = self.sessions_store.get_agent_map(user_key, agent_name)
        return dict(agent_map)

    def list_all_agent_sessions(self, user_id: Union[int, str]) -> Dict[str, Dict[str, str]]:
        """Return all stored session mappings for the user, grouped by agent.

        Structure: {agent_name: {thread_id: session_id}}
        """
        user_key = self._normalize_user_id(user_id)
        # Ensure namespaces exist
        self.sessions_store._ensure_user_namespace(user_key)
        agent_maps = self.sessions_store.state.session_mappings.get(user_key, {})
        # Shallow copies to avoid accidental mutation
        return {agent: dict(mapping) for agent, mapping in agent_maps.items()}

    # Backwards-compatible helpers for Claude-specific call sites
    def set_session_mapping(
        self,
        user_id: Union[int, str],
        thread_id: str,
        claude_session_id: str,
    ):
        self.set_agent_session_mapping(user_id, "claude", thread_id, claude_session_id)

    def get_claude_session_id(self, user_id: Union[int, str], thread_id: str) -> Optional[str]:
        return self.get_agent_session_id(user_id, thread_id, agent_name="claude")

    def clear_session_mapping(
        self,
        user_id: Union[int, str],
        thread_id: str,
    ):
        self.clear_agent_session_mapping(user_id, "claude", thread_id)

    # ---------------------------------------------
    # Slack thread management
    # ---------------------------------------------
    def mark_thread_active(self, user_id: Union[int, str], channel_id: str, thread_ts: str):
        """Mark a Slack thread as active with current timestamp"""
        user_key = self._normalize_user_id(user_id)
        channel_map = self.sessions_store.get_thread_map(user_key, channel_id)
        channel_map[thread_ts] = time.time()
        self.sessions_store.save()
        logger.info(f"Marked thread active for user {user_id}: channel={channel_id}, thread={thread_ts}")

    def is_thread_active(self, user_id: Union[int, str], channel_id: str, thread_ts: str) -> bool:
        """Check if a Slack thread is active (within 24 hours)"""
        user_key = self._normalize_user_id(user_id)

        # First cleanup expired threads for this channel
        self._cleanup_expired_threads_for_channel(user_id, channel_id)

        channel_map = self.sessions_store.get_thread_map(user_key, channel_id)
        return thread_ts in channel_map

    def _cleanup_expired_threads_for_channel(self, user_id: Union[int, str], channel_id: str):
        """Remove threads older than 24 hours for a specific channel"""
        user_key = self._normalize_user_id(user_id)
        channel_map = self.sessions_store.get_thread_map(user_key, channel_id)

        if not channel_map:
            return

        current_time = time.time()
        twenty_four_hours_ago = current_time - (24 * 60 * 60)

        expired_threads = [
            thread_ts for thread_ts, last_active in channel_map.items() if last_active < twenty_four_hours_ago
        ]

        if expired_threads:
            for thread_ts in expired_threads:
                del channel_map[thread_ts]

            if not channel_map:
                self.sessions_store.state.active_slack_threads[user_key].pop(channel_id, None)

            self.sessions_store.save()
            logger.info(f"Cleaned up {len(expired_threads)} expired threads for channel {channel_id}")

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
    # Message deduplication
    # ---------------------------------------------
    def is_message_already_processed(self, channel_id: str, thread_ts: str, message_ts: str) -> bool:
        """Check if a message has already been processed.

        Compares the message_ts with the last processed message ts for the thread.
        Returns True if message_ts <= last_processed_ts (i.e., already processed).
        """
        last_ts = self.sessions_store.get_last_processed_message_ts(channel_id, thread_ts)
        if not last_ts:
            return False
        # Slack ts format is "epoch.sequence", can be compared as strings
        return message_ts <= last_ts

    def record_processed_message(self, channel_id: str, thread_ts: str, message_ts: str) -> None:
        """Record that a message has been processed."""
        self.sessions_store.set_last_processed_message_ts(channel_id, thread_ts, message_ts)
        logger.debug(f"Recorded processed message: channel={channel_id}, thread={thread_ts}, message={message_ts}")

    # ---------------------------------------------
    # Channel routing management
    # ---------------------------------------------
    def get_channel_routing(self, settings_key: Union[int, str]) -> Optional[ChannelRouting]:
        """Get channel routing override for the given settings key."""
        self._reload_if_changed()
        settings = self.get_user_settings(settings_key)
        return settings.channel_routing

    def set_channel_routing(self, settings_key: Union[int, str], routing: ChannelRouting):
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
        """Clear channel routing override (fall back to default backend)."""
        settings = self.get_user_settings(settings_key)
        if settings.channel_routing:
            settings.channel_routing = None
            self.update_user_settings(settings_key, settings)
            logger.info(f"Cleared channel routing for {settings_key}")

    # ---------------------------------------------
    # Per-channel require_mention management
    # ---------------------------------------------
    def get_require_mention(self, channel_id: Union[int, str], global_default: bool = False) -> bool:
        """Get effective require_mention value for a channel.

        Args:
            channel_id: The channel to check
            global_default: The global require_mention setting from config

        Returns:
            True if mention is required, False otherwise.
            Uses per-channel setting if set, otherwise falls back to global_default.
        """
        self._reload_if_changed()
        key = str(channel_id)
        channel_settings = self.store.settings.channels.get(key)

        if channel_settings is not None and channel_settings.require_mention is not None:
            return channel_settings.require_mention

        return global_default

    def set_require_mention(self, channel_id: Union[int, str], value: Optional[bool]):
        """Set per-channel require_mention override.

        Args:
            channel_id: The channel to configure
            value: True=require mention, False=don't require, None=use global default
        """
        key = str(channel_id)
        channel_settings = self.store.get_channel(key)
        channel_settings.require_mention = value
        self.store.update_channel(key, channel_settings)
        logger.info(f"Updated require_mention for channel {key}: {value}")

    def get_require_mention_override(self, channel_id: Union[int, str]) -> Optional[bool]:
        """Get the raw per-channel require_mention override (may be None)."""
        self._reload_if_changed()
        key = str(channel_id)
        channel_settings = self.store.settings.channels.get(key)
        if channel_settings is not None:
            return channel_settings.require_mention
        return None

    # ---------------------------------------------
    # Active polls management (for poll restoration on restart)
    # ---------------------------------------------
    def add_active_poll(
        self,
        opencode_session_id: str,
        base_session_id: str,
        channel_id: str,
        thread_id: str,
        settings_key: str,
        working_path: str,
        baseline_message_ids: List[str],
        ack_reaction_message_id: Optional[str] = None,
        ack_reaction_emoji: Optional[str] = None,
    ) -> None:
        """Record an active poll for potential restoration on restart."""
        from config.v2_sessions import ActivePollInfo

        poll_info = ActivePollInfo(
            opencode_session_id=opencode_session_id,
            base_session_id=base_session_id,
            channel_id=channel_id,
            thread_id=thread_id,
            settings_key=settings_key,
            working_path=working_path,
            baseline_message_ids=baseline_message_ids,
            seen_tool_calls=[],
            emitted_assistant_messages=[],
            started_at=time.time(),
            ack_reaction_message_id=ack_reaction_message_id,
            ack_reaction_emoji=ack_reaction_emoji,
        )
        self.sessions_store.add_active_poll(poll_info)
        logger.debug(f"Added active poll: session={opencode_session_id}, thread={thread_id}")

    def remove_active_poll(self, opencode_session_id: str) -> None:
        """Remove an active poll record."""
        self.sessions_store.remove_active_poll(opencode_session_id)
        logger.debug(f"Removed active poll: session={opencode_session_id}")

    def update_active_poll_state(
        self,
        opencode_session_id: str,
        seen_tool_calls: Optional[List[str]] = None,
        emitted_assistant_messages: Optional[List[str]] = None,
    ) -> None:
        """Update the state of an active poll (for restoration accuracy)."""
        from config.v2_sessions import ActivePollInfo

        poll_info = self.sessions_store.get_active_poll(opencode_session_id)
        if poll_info:
            if seen_tool_calls is not None:
                poll_info.seen_tool_calls = seen_tool_calls
            if emitted_assistant_messages is not None:
                poll_info.emitted_assistant_messages = emitted_assistant_messages
            self.sessions_store.update_active_poll(poll_info)

    def get_all_active_polls(self) -> Dict[str, Any]:
        """Get all active polls for restoration."""
        from config.v2_sessions import ActivePollInfo

        return self.sessions_store.get_all_active_polls()
