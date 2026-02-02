import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from config import paths

logger = logging.getLogger(__name__)

DEFAULT_SHOW_MESSAGE_TYPES: List[str] = []
ALLOWED_MESSAGE_TYPES = {"system", "assistant", "toolcall"}


def normalize_show_message_types(show_message_types: Optional[List[str]]) -> List[str]:
    if show_message_types is None:
        return DEFAULT_SHOW_MESSAGE_TYPES.copy()
    return [msg for msg in show_message_types if msg in ALLOWED_MESSAGE_TYPES]


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
    # Per-channel language setting: None=use system default, "en", "zh", etc.
    language: Optional[str] = None


@dataclass
class SettingsState:
    channels: Dict[str, ChannelSettings] = field(default_factory=dict)


class SettingsStore:
    def __init__(self, settings_path: Optional[Path] = None):
        self.settings_path = settings_path or paths.get_settings_path()
        self.settings: SettingsState = SettingsState()
        self._load()

    def _load(self) -> None:
        if not self.settings_path.exists():
            return
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to load settings: %s", exc)
            return
        raw_channels = payload.get("channels") if isinstance(payload, dict) else None
        if raw_channels is None:
            logger.error("Failed to load settings: invalid format")
            return
        if not isinstance(raw_channels, dict):
            logger.error("Failed to load settings: channels must be an object")
            return
        channels = {}
        for channel_id, channel_payload in raw_channels.items():
            if not isinstance(channel_payload, dict):
                continue
            routing_payload = channel_payload.get("routing") or {}
            routing = RoutingSettings(
                agent_backend=routing_payload.get("agent_backend"),
                opencode_agent=routing_payload.get("opencode_agent"),
                opencode_model=routing_payload.get("opencode_model"),
                opencode_reasoning_effort=routing_payload.get("opencode_reasoning_effort"),
                claude_agent=routing_payload.get("claude_agent"),
                claude_model=routing_payload.get("claude_model"),
                codex_model=routing_payload.get("codex_model"),
                codex_reasoning_effort=routing_payload.get("codex_reasoning_effort"),
            )
            channels[channel_id] = ChannelSettings(
                enabled=channel_payload.get("enabled", False),
                show_message_types=normalize_show_message_types(channel_payload.get("show_message_types")),
                custom_cwd=channel_payload.get("custom_cwd"),
                routing=routing,
                require_mention=channel_payload.get("require_mention"),
                language=channel_payload.get("language"),
            )
        self.settings = SettingsState(channels=channels)

    def save(self) -> None:
        paths.ensure_data_dirs()
        payload = {"channels": {}}
        for channel_id, settings in self.settings.channels.items():
            payload["channels"][channel_id] = {
                "enabled": settings.enabled,
                "show_message_types": settings.show_message_types,
                "custom_cwd": settings.custom_cwd,
                "routing": {
                    "agent_backend": settings.routing.agent_backend,
                    "opencode_agent": settings.routing.opencode_agent,
                    "opencode_model": settings.routing.opencode_model,
                    "opencode_reasoning_effort": settings.routing.opencode_reasoning_effort,
                    "claude_agent": settings.routing.claude_agent,
                    "claude_model": settings.routing.claude_model,
                    "codex_model": settings.routing.codex_model,
                    "codex_reasoning_effort": settings.routing.codex_reasoning_effort,
                },
                "require_mention": settings.require_mention,
                "language": settings.language,
            }
        self.settings_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def get_channel(self, channel_id: str) -> ChannelSettings:
        if channel_id not in self.settings.channels:
            self.settings.channels[channel_id] = ChannelSettings()
        return self.settings.channels[channel_id]

    def update_channel(self, channel_id: str, settings: ChannelSettings) -> None:
        self.settings.channels[channel_id] = settings
        self.save()
