import json
import logging
import secrets
import string
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config import paths

logger = logging.getLogger(__name__)

DEFAULT_SHOW_MESSAGE_TYPES: List[str] = []
ALLOWED_MESSAGE_TYPES = {"system", "assistant", "toolcall"}
SCHEMA_VERSION = 3
SCOPED_KEY_SEP = "::"

# Bind code prefix and length
_BIND_CODE_PREFIX = "vr-"
_BIND_CODE_RANDOM_LENGTH = 6
_BIND_CODE_ALPHABET = string.ascii_lowercase + string.digits


def normalize_show_message_types(show_message_types: Optional[List[str]]) -> List[str]:
    if show_message_types is None:
        return DEFAULT_SHOW_MESSAGE_TYPES.copy()
    return [msg for msg in show_message_types if msg in ALLOWED_MESSAGE_TYPES]


def _generate_bind_code() -> str:
    """Generate a random bind code like 'vr-a3x9k2'."""
    random_part = "".join(secrets.choice(_BIND_CODE_ALPHABET) for _ in range(_BIND_CODE_RANDOM_LENGTH))
    return f"{_BIND_CODE_PREFIX}{random_part}"


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _make_scoped_key(platform: str, item_id: str) -> str:
    return f"{platform}{SCOPED_KEY_SEP}{item_id}"


def _split_scoped_key(scoped_key: str) -> Tuple[Optional[str], str]:
    if SCOPED_KEY_SEP in scoped_key:
        platform, raw_id = scoped_key.split(SCOPED_KEY_SEP, 1)
        if platform:
            return platform, raw_id
    return None, scoped_key


def _infer_channel_platform(channel_id: str) -> str:
    cid = str(channel_id)
    if cid.startswith("oc_"):
        return "lark"
    if cid and cid[0] in {"C", "G", "D"}:
        return "slack"
    if cid.isdigit() and len(cid) >= 15:
        return "discord"
    return "unknown"


def _infer_user_platform(user_id: str) -> str:
    uid = str(user_id)
    if uid.startswith("ou_"):
        return "lark"
    if uid and uid[0] in {"U", "W"}:
        return "slack"
    if uid.isdigit() and len(uid) >= 15:
        return "discord"
    return "unknown"


@dataclass
class RoutingSettings:
    agent_backend: Optional[str] = None
    # OpenCode settings
    opencode_agent: Optional[str] = None
    opencode_model: Optional[str] = None
    opencode_reasoning_effort: Optional[str] = None
    # Claude Code settings
    claude_agent: Optional[str] = None
    claude_model: Optional[str] = None
    claude_reasoning_effort: Optional[str] = None
    # Codex settings
    codex_model: Optional[str] = None
    codex_reasoning_effort: Optional[str] = None
    # Note: Codex subagent not supported yet


@dataclass
class ChannelSettings:
    enabled: bool = False
    show_message_types: List[str] = field(default_factory=lambda: DEFAULT_SHOW_MESSAGE_TYPES.copy())
    custom_cwd: Optional[str] = None
    routing: RoutingSettings = field(default_factory=RoutingSettings)
    # Per-channel require_mention override: None=use global default, True=require, False=don't require
    require_mention: Optional[bool] = None


@dataclass
class UserSettings:
    """Settings for a bound DM user."""

    display_name: str = ""
    is_admin: bool = False
    bound_at: str = ""  # ISO 8601 timestamp
    enabled: bool = True
    show_message_types: List[str] = field(default_factory=lambda: DEFAULT_SHOW_MESSAGE_TYPES.copy())
    custom_cwd: Optional[str] = None
    routing: RoutingSettings = field(default_factory=RoutingSettings)
    dm_chat_id: str = ""


@dataclass
class BindCode:
    """A bind code for authorizing DM access."""

    code: str
    type: str  # "one_time" or "expiring"
    created_at: str  # ISO 8601
    expires_at: Optional[str] = None  # ISO 8601, only for "expiring" type
    is_active: bool = True
    used_by: List[str] = field(default_factory=list)  # user_ids that used this code


@dataclass
class SettingsState:
    channels: Dict[str, ChannelSettings] = field(default_factory=dict)
    users: Dict[str, UserSettings] = field(default_factory=dict)
    bind_codes: List[BindCode] = field(default_factory=list)


def _parse_routing(payload: dict) -> RoutingSettings:
    """Parse a routing settings dict into a RoutingSettings dataclass."""
    return RoutingSettings(
        agent_backend=payload.get("agent_backend"),
        opencode_agent=payload.get("opencode_agent"),
        opencode_model=payload.get("opencode_model"),
        opencode_reasoning_effort=payload.get("opencode_reasoning_effort"),
        claude_agent=payload.get("claude_agent"),
        claude_model=payload.get("claude_model"),
        claude_reasoning_effort=payload.get("claude_reasoning_effort"),
        codex_model=payload.get("codex_model"),
        codex_reasoning_effort=payload.get("codex_reasoning_effort"),
    )


def _routing_to_dict(routing: RoutingSettings) -> dict:
    """Serialize a RoutingSettings to dict."""
    return {
        "agent_backend": routing.agent_backend,
        "opencode_agent": routing.opencode_agent,
        "opencode_model": routing.opencode_model,
        "opencode_reasoning_effort": routing.opencode_reasoning_effort,
        "claude_agent": routing.claude_agent,
        "claude_model": routing.claude_model,
        "claude_reasoning_effort": routing.claude_reasoning_effort,
        "codex_model": routing.codex_model,
        "codex_reasoning_effort": routing.codex_reasoning_effort,
    }


class SettingsStore:
    # ------------------------------------------------------------------
    # Singleton: one store shared by bot process AND UI API handlers.
    # ------------------------------------------------------------------
    _instance: Optional["SettingsStore"] = None
    _instance_lock = threading.Lock()

    @classmethod
    def get_instance(cls, settings_path: Optional[Path] = None) -> "SettingsStore":
        """Return the process-wide singleton, creating it on first call.

        Automatically reloads from disk if the file has changed.
        """
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls(settings_path)
            else:
                cls._instance.maybe_reload()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for tests only)."""
        with cls._instance_lock:
            cls._instance = None

    def __init__(self, settings_path: Optional[Path] = None):
        self.settings_path = settings_path or paths.get_settings_path()
        self.settings: SettingsState = SettingsState()
        self._bind_lock = threading.Lock()  # Guards atomic bind operations
        self._file_mtime: float = 0
        self._load()

    def maybe_reload(self) -> None:
        """Reload from disk if the file has been modified since last load."""
        try:
            mtime = self.settings_path.stat().st_mtime
            if mtime > self._file_mtime:
                self._load()
        except FileNotFoundError:
            pass

    def _load(self) -> None:
        if not self.settings_path.exists():
            return
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to load settings: %s", exc)
            return
        if not isinstance(payload, dict):
            logger.error("Failed to load settings: invalid format")
            return

        channels: Dict[str, ChannelSettings] = {}
        users: Dict[str, UserSettings] = {}
        migrated_legacy_channels = False

        # --- New schema (v3): payload["scopes"]["channel"|"user"][platform][id] ---
        scopes = payload.get("scopes")
        if isinstance(scopes, dict):
            raw_channel_scopes = scopes.get("channel") or {}
            if isinstance(raw_channel_scopes, dict):
                for platform, items in raw_channel_scopes.items():
                    if not isinstance(items, dict):
                        continue
                    for channel_id, cp in items.items():
                        if not isinstance(cp, dict):
                            continue
                        key = _make_scoped_key(str(platform), str(channel_id))
                        channels[key] = ChannelSettings(
                            enabled=cp.get("enabled", False),
                            show_message_types=normalize_show_message_types(cp.get("show_message_types")),
                            custom_cwd=cp.get("custom_cwd"),
                            routing=_parse_routing(cp.get("routing") or {}),
                            require_mention=cp.get("require_mention"),
                        )

            raw_user_scopes = scopes.get("user") or {}
            if isinstance(raw_user_scopes, dict):
                for platform, items in raw_user_scopes.items():
                    if not isinstance(items, dict):
                        continue
                    for user_id, up in items.items():
                        if not isinstance(up, dict):
                            continue
                        key = _make_scoped_key(str(platform), str(user_id))
                        users[key] = UserSettings(
                            display_name=up.get("display_name", ""),
                            is_admin=up.get("is_admin", False),
                            bound_at=up.get("bound_at", ""),
                            enabled=up.get("enabled", True),
                            show_message_types=normalize_show_message_types(up.get("show_message_types")),
                            custom_cwd=up.get("custom_cwd"),
                            routing=_parse_routing(up.get("routing") or {}),
                            dm_chat_id=up.get("dm_chat_id", ""),
                        )
        else:
            # --- Legacy schema: flat payload["channels"] / payload["users"] ---
            raw_channels = payload.get("channels") or {}
            if not isinstance(raw_channels, dict):
                logger.error("Failed to load settings: channels must be an object")
                raw_channels = {}
            for channel_id, cp in raw_channels.items():
                if not isinstance(cp, dict):
                    continue
                platform = _infer_channel_platform(str(channel_id))
                scoped_key = _make_scoped_key(platform, str(channel_id))
                channels[scoped_key] = ChannelSettings(
                    enabled=cp.get("enabled", False),
                    show_message_types=normalize_show_message_types(cp.get("show_message_types")),
                    custom_cwd=cp.get("custom_cwd"),
                    routing=_parse_routing(cp.get("routing") or {}),
                    require_mention=cp.get("require_mention"),
                )
                migrated_legacy_channels = True

            raw_users = payload.get("users") or {}
            if isinstance(raw_users, dict):
                for user_id, up in raw_users.items():
                    if not isinstance(up, dict):
                        continue
                    platform = _infer_user_platform(str(user_id))
                    scoped_key = _make_scoped_key(platform, str(user_id))
                    users[scoped_key] = UserSettings(
                        display_name=up.get("display_name", ""),
                        is_admin=up.get("is_admin", False),
                        bound_at=up.get("bound_at", ""),
                        enabled=up.get("enabled", True),
                        show_message_types=normalize_show_message_types(up.get("show_message_types")),
                        custom_cwd=up.get("custom_cwd"),
                        routing=_parse_routing(up.get("routing") or {}),
                        dm_chat_id=up.get("dm_chat_id", ""),
                    )

        # --- Bind Codes ---
        raw_codes = payload.get("bind_codes") or []
        bind_codes: List[BindCode] = []
        if isinstance(raw_codes, list):
            for bc in raw_codes:
                if not isinstance(bc, dict):
                    continue
                bind_codes.append(
                    BindCode(
                        code=bc.get("code", ""),
                        type=bc.get("type", "one_time"),
                        created_at=bc.get("created_at", ""),
                        expires_at=bc.get("expires_at"),
                        is_active=bc.get("is_active", True),
                        used_by=bc.get("used_by") or [],
                    )
                )

        self.settings = SettingsState(channels=channels, users=users, bind_codes=bind_codes)

        if migrated_legacy_channels:
            logger.info("Migrating legacy channel settings to platform-scoped schema")
            self.save()

        try:
            self._file_mtime = self.settings_path.stat().st_mtime
        except FileNotFoundError:
            self._file_mtime = 0

    def save(self) -> None:
        paths.ensure_data_dirs()
        payload: dict = {
            "schema_version": SCHEMA_VERSION,
            "scopes": {"channel": {}, "user": {}},
            "bind_codes": [],
        }

        # Channels (platform-scoped)
        for scoped_key, s in self.settings.channels.items():
            platform, channel_id = _split_scoped_key(scoped_key)
            if not platform:
                platform = _infer_channel_platform(channel_id)
            payload["scopes"]["channel"].setdefault(platform, {})[channel_id] = {
                "enabled": s.enabled,
                "show_message_types": s.show_message_types,
                "custom_cwd": s.custom_cwd,
                "routing": _routing_to_dict(s.routing),
                "require_mention": s.require_mention,
            }

        # Users (platform-scoped)
        for scoped_key, u in self.settings.users.items():
            platform, user_id = _split_scoped_key(scoped_key)
            if not platform:
                platform = _infer_user_platform(user_id)
            payload["scopes"]["user"].setdefault(platform, {})[user_id] = {
                "display_name": u.display_name,
                "is_admin": u.is_admin,
                "bound_at": u.bound_at,
                "enabled": u.enabled,
                "show_message_types": u.show_message_types,
                "custom_cwd": u.custom_cwd,
                "routing": _routing_to_dict(u.routing),
                "dm_chat_id": u.dm_chat_id,
            }

        # Bind codes
        for bc in self.settings.bind_codes:
            payload["bind_codes"].append(
                {
                    "code": bc.code,
                    "type": bc.type,
                    "created_at": bc.created_at,
                    "expires_at": bc.expires_at,
                    "is_active": bc.is_active,
                    "used_by": bc.used_by,
                }
            )

        self.settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            self._file_mtime = self.settings_path.stat().st_mtime
        except FileNotFoundError:
            self._file_mtime = 0

    # --- Channel helpers ---

    def _channel_key(self, channel_id: str, platform: Optional[str] = None) -> str:
        return _make_scoped_key(platform, channel_id) if platform else channel_id

    def _user_key(self, user_id: str, platform: Optional[str] = None) -> str:
        return _make_scoped_key(platform, user_id) if platform else user_id

    def get_channels_for_platform(self, platform: str) -> Dict[str, ChannelSettings]:
        result: Dict[str, ChannelSettings] = {}
        prefix = f"{platform}{SCOPED_KEY_SEP}"
        for key, settings in self.settings.channels.items():
            if key.startswith(prefix):
                result[key[len(prefix) :]] = settings
        return result

    def set_channels_for_platform(self, platform: str, channels: Dict[str, ChannelSettings]) -> None:
        prefix = f"{platform}{SCOPED_KEY_SEP}"
        self.settings.channels = {k: v for k, v in self.settings.channels.items() if not k.startswith(prefix)}
        for channel_id, settings in channels.items():
            self.settings.channels[self._channel_key(str(channel_id), platform)] = settings

    def get_users_for_platform(self, platform: str) -> Dict[str, UserSettings]:
        result: Dict[str, UserSettings] = {}
        prefix = f"{platform}{SCOPED_KEY_SEP}"
        for key, settings in self.settings.users.items():
            if key.startswith(prefix):
                result[key[len(prefix) :]] = settings
        return result

    def set_users_for_platform(self, platform: str, users: Dict[str, UserSettings]) -> None:
        prefix = f"{platform}{SCOPED_KEY_SEP}"
        self.settings.users = {k: v for k, v in self.settings.users.items() if not k.startswith(prefix)}
        for user_id, settings in users.items():
            self.settings.users[self._user_key(str(user_id), platform)] = settings

    def get_channel(self, channel_id: str, platform: Optional[str] = None) -> ChannelSettings:
        key = self._channel_key(channel_id, platform)
        if platform is None and key not in self.settings.channels:
            suffix = f"{SCOPED_KEY_SEP}{channel_id}"
            for scoped_key, settings in self.settings.channels.items():
                if scoped_key.endswith(suffix):
                    return settings
        if key not in self.settings.channels:
            self.settings.channels[key] = ChannelSettings()
        return self.settings.channels[key]

    def find_channel(self, channel_id: str, platform: Optional[str] = None) -> Optional[ChannelSettings]:
        key = self._channel_key(channel_id, platform)
        if key in self.settings.channels:
            return self.settings.channels[key]
        if platform is None:
            suffix = f"{SCOPED_KEY_SEP}{channel_id}"
            for scoped_key, settings in self.settings.channels.items():
                if scoped_key.endswith(suffix):
                    return settings
        return None

    def update_channel(self, channel_id: str, settings: ChannelSettings, platform: Optional[str] = None) -> None:
        key = self._channel_key(channel_id, platform)
        self.settings.channels[key] = settings
        self.save()

    # --- User helpers ---

    def get_user(self, user_id: str, platform: Optional[str] = None) -> Optional[UserSettings]:
        """Get user settings, or None if user is not bound."""
        key = self._user_key(user_id, platform)
        user = self.settings.users.get(key)
        if user is not None or platform:
            return user
        suffix = f"{SCOPED_KEY_SEP}{user_id}"
        for scoped_key, value in self.settings.users.items():
            if scoped_key.endswith(suffix):
                return value
        return None

    def is_bound_user(self, user_id: str, platform: Optional[str] = None) -> bool:
        if platform:
            return self._user_key(user_id, platform) in self.settings.users
        if user_id in self.settings.users:
            return True
        suffix = f"{SCOPED_KEY_SEP}{user_id}"
        return any(key.endswith(suffix) for key in self.settings.users.keys())

    def is_admin(self, user_id: str, platform: Optional[str] = None) -> bool:
        if platform:
            user = self.settings.users.get(self._user_key(user_id, platform))
            return user is not None and user.is_admin
        if user_id in self.settings.users:
            return self.settings.users[user_id].is_admin
        suffix = f"{SCOPED_KEY_SEP}{user_id}"
        for key, value in self.settings.users.items():
            if key.endswith(suffix):
                return value.is_admin
        return False

    def has_any_admin(self, platform: Optional[str] = None) -> bool:
        """Return True if at least one admin exists."""
        if platform:
            prefix = f"{platform}{SCOPED_KEY_SEP}"
            return any(u.is_admin for key, u in self.settings.users.items() if key.startswith(prefix))
        return any(u.is_admin for u in self.settings.users.values())

    def get_admins(self, platform: Optional[str] = None) -> Dict[str, UserSettings]:
        """Return all admin users."""
        if platform:
            prefix = f"{platform}{SCOPED_KEY_SEP}"
            return {uid: u for uid, u in self.settings.users.items() if uid.startswith(prefix) and u.is_admin}
        return {uid: u for uid, u in self.settings.users.items() if u.is_admin}

    def add_user(
        self, user_id: str, display_name: str, is_admin: bool = False, platform: Optional[str] = None
    ) -> UserSettings:
        """Add a new bound user. Returns the created UserSettings."""
        user = UserSettings(
            display_name=display_name,
            is_admin=is_admin,
            bound_at=_now_iso(),
            enabled=True,
        )
        self.settings.users[self._user_key(user_id, platform)] = user
        self.save()
        return user

    def bind_user_with_code(
        self, user_id: str, display_name: str, code: str, dm_chat_id: str = "", platform: Optional[str] = None
    ) -> Tuple[bool, bool]:
        """Atomically validate code, create user, and consume code.

        Returns (success, is_admin).
        Thread-safe: uses a lock to prevent concurrent bind races.
        """
        with self._bind_lock:
            # Ensure we have the latest data from disk (UI API may have
            # created bind codes via the same singleton after we last read).
            self.maybe_reload()

            # Check already bound
            if self.is_bound_user(user_id, platform=platform):
                return False, False

            # Validate code
            bc = self.validate_bind_code(code)
            if bc is None:
                return False, False

            # Auto-admin for first user
            is_admin = not self.has_any_admin(platform=platform)

            # Create user
            user = UserSettings(
                display_name=display_name,
                is_admin=is_admin,
                bound_at=_now_iso(),
                enabled=True,
                dm_chat_id=dm_chat_id,
            )
            scoped_user_id = self._user_key(user_id, platform)
            self.settings.users[scoped_user_id] = user

            # Consume code
            bc.used_by.append(scoped_user_id)
            if bc.type == "one_time":
                bc.is_active = False

            self.save()
            return True, is_admin

    def update_user(self, user_id: str, settings: UserSettings, platform: Optional[str] = None) -> None:
        self.settings.users[self._user_key(user_id, platform)] = settings
        self.save()

    def remove_user(self, user_id: str, platform: Optional[str] = None) -> bool:
        key = self._user_key(user_id, platform)
        if key in self.settings.users:
            del self.settings.users[key]
            self.save()
            return True
        return False

    def set_admin(self, user_id: str, is_admin: bool, platform: Optional[str] = None) -> bool:
        """Set admin flag for a user. Returns False if user not found."""
        key = self._user_key(user_id, platform)
        user = self.settings.users.get(key)
        if user is None:
            return False
        user.is_admin = is_admin
        self.save()
        return True

    # --- Bind code helpers ---

    def create_bind_code(self, code_type: str = "one_time", expires_at: Optional[str] = None) -> BindCode:
        """Create a new bind code."""
        code = _generate_bind_code()
        bc = BindCode(
            code=code,
            type=code_type,
            created_at=_now_iso(),
            expires_at=expires_at if code_type == "expiring" else None,
            is_active=True,
        )
        self.settings.bind_codes.append(bc)
        self.save()
        return bc

    def validate_bind_code(self, code: str) -> Optional[BindCode]:
        """Validate a bind code. Returns the BindCode if valid, None otherwise."""
        for bc in self.settings.bind_codes:
            if bc.code != code:
                continue
            if not bc.is_active:
                return None
            if bc.type == "expiring" and bc.expires_at:
                try:
                    expires = datetime.fromisoformat(bc.expires_at)
                    # If only a date was provided (no time component), treat as end-of-day
                    if expires.hour == 0 and expires.minute == 0 and expires.second == 0 and "T" not in bc.expires_at:
                        expires = expires.replace(hour=23, minute=59, second=59)
                    # Ensure timezone-aware comparison
                    if expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) > expires:
                        return None
                except (ValueError, TypeError):
                    # Fail closed: reject codes with unparseable expiration
                    logger.warning("Bind code %s has unparseable expires_at: %s", code, bc.expires_at)
                    return None
            return bc
        return None

    def use_bind_code(self, code: str, user_id: str) -> bool:
        """Mark a bind code as used by a user. Returns True on success."""
        bc = self.validate_bind_code(code)
        if bc is None:
            return False
        bc.used_by.append(user_id)
        if bc.type == "one_time":
            bc.is_active = False
        self.save()
        return True

    def deactivate_bind_code(self, code: str) -> bool:
        """Deactivate a bind code. Returns True if found and deactivated."""
        for bc in self.settings.bind_codes:
            if bc.code == code:
                bc.is_active = False
                self.save()
                return True
        return False

    def get_bind_codes(self) -> List[BindCode]:
        """Return all bind codes."""
        return list(self.settings.bind_codes)
