import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Union
from pathlib import Path

from config import paths
from config.v2_sessions import SessionsStore
from config.v2_settings import SettingsStore, ChannelSettings, RoutingSettings
from config.v2_settings import UserSettings as BoundUserSettings
from modules.sessions_facade import SessionsFacade


logger = logging.getLogger(__name__)


DEFAULT_SHOW_MESSAGE_TYPES: List[str] = []


ChannelRouting = RoutingSettings


def _routing_to_dict(routing: Optional[RoutingSettings]) -> dict:
    if routing is None:
        return {}
    return asdict(routing)


def _routing_from_dict(payload: Optional[dict]) -> RoutingSettings:
    data = payload or {}
    return RoutingSettings(
        agent_backend=data.get("agent_backend"),
        opencode_agent=data.get("opencode_agent"),
        opencode_model=data.get("opencode_model"),
        opencode_reasoning_effort=data.get("opencode_reasoning_effort"),
        claude_agent=data.get("claude_agent"),
        claude_model=data.get("claude_model"),
        codex_model=data.get("codex_model"),
        codex_reasoning_effort=data.get("codex_reasoning_effort"),
    )


def _clone_routing(routing: Optional[RoutingSettings]) -> RoutingSettings:
    return _routing_from_dict(_routing_to_dict(routing))


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
            result["routing"] = _routing_to_dict(self.channel_routing)
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
            settings.channel_routing = _routing_from_dict(routing_data)
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
        self.channel_settings: Dict[str, UserSettings] = {}
        self.dm_user_settings: Dict[str, UserSettings] = {}
        self.store = SettingsStore.get_instance(self.settings_file)
        self.sessions_store = SessionsStore()
        self.sessions_store.load()
        self.sessions = SessionsFacade(self.sessions_store)
        self._last_seen_store_mtime: Optional[float] = None
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

    def get_store(self) -> SettingsStore:
        """Explicit access to underlying SettingsStore.

        Intended for shared auth pipeline and API integrations that require
        store-level operations.
        """
        return self.store

    def iter_bound_users(self):
        """Iterate over bound users from persisted settings."""
        self._reload_if_changed()
        return self.store.settings.users.items()

    def is_bound_user(self, user_id: Union[int, str]) -> bool:
        """Check whether a user is already bound."""
        return self.store.is_bound_user(str(user_id))

    def bind_user_with_code(
        self,
        user_id: Union[int, str],
        display_name: str,
        code: str,
        dm_chat_id: str = "",
    ) -> tuple[bool, bool]:
        """Atomically validate bind code and create user binding."""
        return self.store.bind_user_with_code(str(user_id), display_name, code, dm_chat_id=dm_chat_id)

    def _from_channel_settings(self, channel_settings: ChannelSettings) -> UserSettings:
        return UserSettings(
            show_message_types=self._normalize_show_message_types(channel_settings.show_message_types),
            custom_cwd=channel_settings.custom_cwd,
            channel_routing=_clone_routing(channel_settings.routing),
            enabled=channel_settings.enabled,
        )

    def _from_bound_user_settings(self, bound_user: BoundUserSettings) -> UserSettings:
        """Convert a bound user's settings (from v2_settings.UserSettings) to runtime UserSettings."""
        return UserSettings(
            show_message_types=self._normalize_show_message_types(bound_user.show_message_types),
            custom_cwd=bound_user.custom_cwd,
            channel_routing=_clone_routing(bound_user.routing),
            enabled=bound_user.enabled,
        )

    def _to_channel_settings(self, settings: UserSettings) -> ChannelSettings:
        routing = _clone_routing(settings.channel_routing)
        return ChannelSettings(
            enabled=settings.enabled,
            show_message_types=self._normalize_show_message_types(settings.show_message_types),
            custom_cwd=settings.custom_cwd,
            routing=routing,
        )

    def _sync_to_bound_user(self, user_id: str, settings: UserSettings) -> None:
        """Sync runtime UserSettings back to the bound-user record in store.settings.users."""
        bound = self.store.settings.users.get(user_id)
        if not bound:
            return
        bound.enabled = settings.enabled
        bound.show_message_types = self._normalize_show_message_types(settings.show_message_types)
        bound.custom_cwd = settings.custom_cwd
        bound.routing = _clone_routing(settings.channel_routing)

    def _load_settings(self):
        """Load settings from JSON file"""
        self.store = SettingsStore.get_instance(self.settings_file)
        self._rebuild_runtime_settings()
        self._last_seen_store_mtime = self.store._file_mtime

    def _reload_if_changed(self) -> None:
        """Reload runtime settings if the underlying store has changed on disk.

        Uses a locally tracked mtime so that same-process writes (e.g. from
        the UI API hitting the singleton store) are detected correctly.
        """
        self.store.maybe_reload()
        if self.store._file_mtime != self._last_seen_store_mtime:
            logger.info("Settings file changed on disk, rebuilding runtime settings")
            self._rebuild_runtime_settings()
            self._last_seen_store_mtime = self.store._file_mtime

    def _rebuild_runtime_settings(self) -> None:
        """Rebuild the in-memory settings dicts from the store."""
        self.channel_settings = {}
        self.dm_user_settings = {}
        for cid, cs in self.store.settings.channels.items():
            self.channel_settings[str(cid)] = self._from_channel_settings(cs)
        for uid, us in self.store.settings.users.items():
            self.dm_user_settings[str(uid)] = self._from_bound_user_settings(us)
        logger.info(
            f"Rebuilt runtime settings for {len(self.channel_settings)} channels, {len(self.dm_user_settings)} DM users"
        )

    def _save_settings(self):
        """Save settings to JSON file.

        Writes channel-keyed entries from ``self.channel_settings`` back to
        ``store.settings.channels``, and syncs DM user entries from
        ``self.dm_user_settings`` back to ``store.settings.users``.
        """
        try:
            channels: Dict[str, ChannelSettings] = {}
            for cid, s in self.channel_settings.items():
                existing = self.store.settings.channels.get(cid)
                cs = self._to_channel_settings(s)
                if existing is not None:
                    cs.enabled = existing.enabled
                    cs.require_mention = existing.require_mention
                channels[cid] = cs
            self.store.settings.channels = channels
            for uid, s in self.dm_user_settings.items():
                self._sync_to_bound_user(uid, s)
            self.store.save()
            # Keep local mtime in sync so _reload_if_changed doesn't
            # trigger an unnecessary rebuild right after our own save.
            self._last_seen_store_mtime = self.store._file_mtime
            logger.info("Settings saved successfully")
        except Exception as e:
            logger.error(f"Error saving settings: {e}")

    def get_user_settings(self, user_id: Union[int, str]) -> UserSettings:
        """Get settings for a specific user/channel context.

        Checks dm_user_settings first, then channel_settings, then falls back
        to the store, and finally creates a default in channel_settings.
        """
        normalized_id = self._normalize_user_id(user_id)

        self._reload_if_changed()

        if normalized_id in self.dm_user_settings:
            return self.dm_user_settings[normalized_id]
        if normalized_id in self.channel_settings:
            return self.channel_settings[normalized_id]

        # New key — check store
        if normalized_id in self.store.settings.users:
            settings = self._from_bound_user_settings(self.store.settings.users[normalized_id])
            self.dm_user_settings[normalized_id] = settings
            return settings
        if normalized_id in self.store.settings.channels:
            settings = self._from_channel_settings(self.store.settings.channels[normalized_id])
            self.channel_settings[normalized_id] = settings
            return settings

        # Truly new — create default in channels
        settings = UserSettings()
        self.channel_settings[normalized_id] = settings
        self._save_settings()
        return settings

    def update_user_settings(self, user_id: Union[int, str], settings: UserSettings):
        """Update settings for a specific user"""
        normalized_id = self._normalize_user_id(user_id)

        settings.show_message_types = self._normalize_show_message_types(settings.show_message_types)

        if normalized_id in self.dm_user_settings:
            self.dm_user_settings[normalized_id] = settings
        elif normalized_id in self.channel_settings:
            self.channel_settings[normalized_id] = settings
        elif normalized_id in self.store.settings.users:
            self.dm_user_settings[normalized_id] = settings
        else:
            self.channel_settings[normalized_id] = settings
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
