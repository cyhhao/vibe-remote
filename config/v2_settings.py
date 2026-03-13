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
    # Note: Claude Code has no CLI parameter for reasoning effort (Extended Thinking)
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

        # --- Channels ---
        raw_channels = payload.get("channels")
        if raw_channels is None:
            # Legacy format or empty — tolerate missing channels key
            raw_channels = {}
        if not isinstance(raw_channels, dict):
            logger.error("Failed to load settings: channels must be an object")
            raw_channels = {}
        channels: Dict[str, ChannelSettings] = {}
        for channel_id, cp in raw_channels.items():
            if not isinstance(cp, dict):
                continue
            channels[channel_id] = ChannelSettings(
                enabled=cp.get("enabled", False),
                show_message_types=normalize_show_message_types(cp.get("show_message_types")),
                custom_cwd=cp.get("custom_cwd"),
                routing=_parse_routing(cp.get("routing") or {}),
                require_mention=cp.get("require_mention"),
            )

        # --- Users ---
        raw_users = payload.get("users") or {}
        users: Dict[str, UserSettings] = {}
        if isinstance(raw_users, dict):
            for user_id, up in raw_users.items():
                if not isinstance(up, dict):
                    continue
                users[user_id] = UserSettings(
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
        try:
            self._file_mtime = self.settings_path.stat().st_mtime
        except FileNotFoundError:
            self._file_mtime = 0

    def save(self) -> None:
        paths.ensure_data_dirs()
        payload: dict = {"channels": {}, "users": {}, "bind_codes": []}

        # Channels
        for channel_id, s in self.settings.channels.items():
            payload["channels"][channel_id] = {
                "enabled": s.enabled,
                "show_message_types": s.show_message_types,
                "custom_cwd": s.custom_cwd,
                "routing": _routing_to_dict(s.routing),
                "require_mention": s.require_mention,
            }

        # Users
        for user_id, u in self.settings.users.items():
            payload["users"][user_id] = {
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

    def get_channel(self, channel_id: str) -> ChannelSettings:
        if channel_id not in self.settings.channels:
            self.settings.channels[channel_id] = ChannelSettings()
        return self.settings.channels[channel_id]

    def update_channel(self, channel_id: str, settings: ChannelSettings) -> None:
        self.settings.channels[channel_id] = settings
        self.save()

    # --- User helpers ---

    def get_user(self, user_id: str) -> Optional[UserSettings]:
        """Get user settings, or None if user is not bound."""
        return self.settings.users.get(user_id)

    def is_bound_user(self, user_id: str) -> bool:
        return user_id in self.settings.users

    def is_admin(self, user_id: str) -> bool:
        user = self.settings.users.get(user_id)
        return user is not None and user.is_admin

    def has_any_admin(self) -> bool:
        """Return True if at least one admin exists."""
        return any(u.is_admin for u in self.settings.users.values())

    def get_admins(self) -> Dict[str, UserSettings]:
        """Return all admin users."""
        return {uid: u for uid, u in self.settings.users.items() if u.is_admin}

    def add_user(self, user_id: str, display_name: str, is_admin: bool = False) -> UserSettings:
        """Add a new bound user. Returns the created UserSettings."""
        user = UserSettings(
            display_name=display_name,
            is_admin=is_admin,
            bound_at=_now_iso(),
            enabled=True,
        )
        self.settings.users[user_id] = user
        self.save()
        return user

    def bind_user_with_code(
        self, user_id: str, display_name: str, code: str, dm_chat_id: str = ""
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
            if self.is_bound_user(user_id):
                return False, False

            # Validate code
            bc = self.validate_bind_code(code)
            if bc is None:
                return False, False

            # Auto-admin for first user
            is_admin = not self.has_any_admin()

            # Create user
            user = UserSettings(
                display_name=display_name,
                is_admin=is_admin,
                bound_at=_now_iso(),
                enabled=True,
                dm_chat_id=dm_chat_id,
            )
            self.settings.users[user_id] = user

            # Consume code
            bc.used_by.append(user_id)
            if bc.type == "one_time":
                bc.is_active = False

            self.save()
            return True, is_admin

    def update_user(self, user_id: str, settings: UserSettings) -> None:
        self.settings.users[user_id] = settings
        self.save()

    def remove_user(self, user_id: str) -> bool:
        if user_id in self.settings.users:
            del self.settings.users[user_id]
            self.save()
            return True
        return False

    def set_admin(self, user_id: str, is_admin: bool) -> bool:
        """Set admin flag for a user. Returns False if user not found or trying to remove last admin."""
        user = self.settings.users.get(user_id)
        if user is None:
            return False
        if not is_admin:
            # Prevent removing the last admin
            admin_count = sum(1 for u in self.settings.users.values() if u.is_admin)
            if admin_count <= 1 and user.is_admin:
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
