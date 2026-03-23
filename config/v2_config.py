import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import List, Optional, Union

from config import paths
from modules.im.base import BaseIMConfig
from vibe.i18n import normalize_language

logger = logging.getLogger(__name__)

CONFIG_LOCK = threading.RLock()


def _filter_dataclass_fields(dc_class, payload: dict) -> dict:
    """Filter payload to only include fields defined in dataclass."""
    valid_fields = {f.name for f in fields(dc_class)}
    return {k: v for k, v in payload.items() if k in valid_fields}


@dataclass
class SlackConfig(BaseIMConfig):
    bot_token: str = ""
    app_token: Optional[str] = None
    signing_secret: Optional[str] = None
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    app_id: Optional[str] = None
    require_mention: bool = False

    def validate(self) -> None:
        # Allow empty token for initial setup
        if self.bot_token and not self.bot_token.startswith("xoxb-"):
            raise ValueError("Invalid Slack bot token format (should start with xoxb-)")
        if self.app_token and not self.app_token.startswith("xapp-"):
            raise ValueError("Invalid Slack app token format (should start with xapp-)")


@dataclass
class DiscordConfig(BaseIMConfig):
    bot_token: str = ""
    application_id: Optional[str] = None
    guild_allowlist: Optional[List[str]] = None
    guild_denylist: Optional[List[str]] = None
    require_mention: bool = False

    def validate(self) -> None:
        # Allow empty token for initial setup
        if self.bot_token and len(self.bot_token.strip()) < 10:
            raise ValueError("Invalid Discord bot token format")


@dataclass
class LarkConfig(BaseIMConfig):
    app_id: str = ""
    app_secret: str = ""
    require_mention: bool = False
    domain: str = "feishu"  # "feishu" for domestic (open.feishu.cn), "lark" for international (open.larksuite.com)

    def validate(self) -> None:
        if self.domain not in ("feishu", "lark"):
            raise ValueError(f"Invalid lark domain: {self.domain!r}. Must be 'feishu' or 'lark'.")

    @property
    def api_base_url(self) -> str:
        """Return the base API URL for the configured domain."""
        if self.domain == "lark":
            return "https://open.larksuite.com"
        return "https://open.feishu.cn"


@dataclass
class WeChatConfig(BaseIMConfig):
    bot_token: str = ""
    base_url: str = "https://ilinkai.weixin.qq.com"
    cdn_base_url: str = "https://novac2c.cdn.weixin.qq.com/c2c"
    proxy_url: Optional[str] = None
    require_mention: bool = False  # unused for WeChat DM-only, kept for interface compat

    def validate(self) -> None:
        # bot_token can be empty during setup wizard (filled after QR login)
        pass


@dataclass
class GatewayConfig:
    relay_url: Optional[str] = None
    workspace_token: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    last_connected_at: Optional[str] = None


@dataclass
class RuntimeConfig:
    default_cwd: str
    log_level: str = "INFO"


@dataclass
class OpenCodeConfig:
    enabled: bool = True
    cli_path: str = "opencode"
    default_agent: Optional[str] = None
    default_model: Optional[str] = None
    default_reasoning_effort: Optional[str] = None
    error_retry_limit: int = 1  # Max retries on LLM stream errors (0 = no retry)


@dataclass
class ClaudeConfig:
    enabled: bool = True
    cli_path: str = "claude"
    default_model: Optional[str] = None


@dataclass
class CodexConfig:
    enabled: bool = True
    cli_path: str = "codex"
    default_model: Optional[str] = None


@dataclass
class AgentsConfig:
    default_backend: str = "opencode"
    opencode: OpenCodeConfig = field(default_factory=OpenCodeConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    codex: CodexConfig = field(default_factory=CodexConfig)


@dataclass
class UiConfig:
    setup_host: str = "127.0.0.1"
    setup_port: int = 5123
    open_browser: bool = True


@dataclass
class UpdateConfig:
    """Configuration for automatic update checking and installation."""

    auto_update: bool = True  # Auto-install updates when idle
    check_interval_minutes: int = 60  # How often to check for updates (0 = disable)
    idle_minutes: int = 30  # Minutes of inactivity before auto-update
    notify_admins: bool = True  # Send update notification to admins when update is available


@dataclass
class PlatformsConfig:
    """Multi-platform enablement metadata.

    ``primary`` remains the compatibility anchor for legacy single-platform
    code paths while ``enabled`` is the new source of truth.
    """

    enabled: list[str] = field(default_factory=lambda: ["slack"])
    primary: str = "slack"

    def validate(self) -> None:
        supported = {"slack", "discord", "lark", "wechat"}
        normalized: list[str] = []
        for platform in self.enabled:
            if platform not in supported:
                raise ValueError(f"Unsupported enabled platform: {platform}")
            if platform not in normalized:
                normalized.append(platform)
        if not normalized:
            raise ValueError("Config 'platforms.enabled' must contain at least one platform")
        if self.primary not in supported:
            raise ValueError("Config 'platforms.primary' must be 'slack', 'discord', 'lark', or 'wechat'")
        if self.primary not in normalized:
            normalized.insert(0, self.primary)
        self.enabled = normalized


@dataclass
class V2Config:
    mode: str
    version: str
    slack: SlackConfig
    runtime: RuntimeConfig
    agents: AgentsConfig
    platform: str = "slack"
    platforms: PlatformsConfig = field(default_factory=PlatformsConfig)
    discord: Optional[DiscordConfig] = None
    lark: Optional[LarkConfig] = None
    wechat: Optional[WeChatConfig] = None
    gateway: Optional[GatewayConfig] = None
    ui: UiConfig = field(default_factory=UiConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    ack_mode: str = "typing"
    show_duration: bool = True  # Show task duration in result messages
    include_user_info: bool = True  # Prepend user identity to agent messages
    reply_enhancements: bool = True  # Enable file sending & quick-reply buttons
    language: str = "en"  # Global language setting (see vibe/i18n)

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "V2Config":
        paths.ensure_data_dirs()
        path = config_path or paths.get_config_path()
        with CONFIG_LOCK:
            if not path.exists():
                raise FileNotFoundError(f"Config not found: {path}")
            payload = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: dict) -> "V2Config":
        if not isinstance(payload, dict):
            raise ValueError("Config payload must be an object")

        mode = payload.get("mode")
        if mode not in {"self_host", "saas"}:
            raise ValueError("Config 'mode' must be 'self_host' or 'saas'")

        platform = payload.get("platform") or "slack"
        if platform not in {"slack", "discord", "lark", "wechat"}:
            raise ValueError("Config 'platform' must be 'slack', 'discord', 'lark', or 'wechat'")

        platforms_payload = payload.get("platforms")
        if platforms_payload is not None and not isinstance(platforms_payload, dict):
            raise ValueError("Config 'platforms' must be an object")
        if platforms_payload:
            platforms = PlatformsConfig(
                enabled=list(platforms_payload.get("enabled") or []),
                primary=platforms_payload.get("primary") or platform,
            )
        else:
            platforms = PlatformsConfig(enabled=[platform], primary=platform)
        platforms.validate()
        platform = platforms.primary

        slack_payload = payload.get("slack") or {}
        if not isinstance(slack_payload, dict):
            raise ValueError("Config 'slack' must be an object")

        if "require_mention" not in slack_payload:
            slack_payload = dict(slack_payload)
            slack_payload["require_mention"] = False

        slack = SlackConfig(**_filter_dataclass_fields(SlackConfig, slack_payload))
        slack.validate()

        discord_payload = payload.get("discord")
        if discord_payload is not None and not isinstance(discord_payload, dict):
            raise ValueError("Config 'discord' must be an object")
        discord = None
        if discord_payload is not None:
            discord = DiscordConfig(**_filter_dataclass_fields(DiscordConfig, discord_payload))
            discord.validate()
        if platform == "discord" and discord is None:
            raise ValueError("Config 'discord' must be provided when platform is discord")

        lark_payload = payload.get("lark")
        if lark_payload is not None and not isinstance(lark_payload, dict):
            raise ValueError("Config 'lark' must be an object")
        lark = None
        if lark_payload is not None:
            lark = LarkConfig(**_filter_dataclass_fields(LarkConfig, lark_payload))
            lark.validate()
        if platform == "lark" and lark is None:
            raise ValueError("Config 'lark' must be provided when platform is lark")

        wechat_payload = payload.get("wechat")
        if wechat_payload is not None and not isinstance(wechat_payload, dict):
            raise ValueError("Config 'wechat' must be an object")
        wechat = None
        if wechat_payload is not None:
            wechat = WeChatConfig(**_filter_dataclass_fields(WeChatConfig, wechat_payload))
            wechat.validate()
        if platform == "wechat" and wechat is None:
            raise ValueError("Config 'wechat' must be provided when platform is wechat")

        gateway_payload = payload.get("gateway")
        if gateway_payload is not None and not isinstance(gateway_payload, dict):
            raise ValueError("Config 'gateway' must be an object")
        gateway = GatewayConfig(**_filter_dataclass_fields(GatewayConfig, gateway_payload)) if gateway_payload else None

        runtime_payload = payload.get("runtime")
        if not isinstance(runtime_payload, dict):
            raise ValueError("Config 'runtime' must be an object")
        runtime = RuntimeConfig(**_filter_dataclass_fields(RuntimeConfig, runtime_payload))

        agents_payload = payload.get("agents")
        if not isinstance(agents_payload, dict):
            raise ValueError("Config 'agents' must be an object")

        opencode_payload = agents_payload.get("opencode") or {}
        if not isinstance(opencode_payload, dict):
            raise ValueError("Config 'agents.opencode' must be an object")

        claude_payload = agents_payload.get("claude") or {}
        if not isinstance(claude_payload, dict):
            raise ValueError("Config 'agents.claude' must be an object")

        codex_payload = agents_payload.get("codex") or {}
        if not isinstance(codex_payload, dict):
            raise ValueError("Config 'agents.codex' must be an object")

        opencode = OpenCodeConfig(**_filter_dataclass_fields(OpenCodeConfig, opencode_payload))
        claude = ClaudeConfig(**_filter_dataclass_fields(ClaudeConfig, claude_payload))
        codex = CodexConfig(**_filter_dataclass_fields(CodexConfig, codex_payload))

        default_backend = agents_payload.get("default_backend", "opencode")
        if default_backend not in {"opencode", "claude", "codex"}:
            raise ValueError("Config 'agents.default_backend' must be 'opencode', 'claude', or 'codex'")

        agents = AgentsConfig(
            default_backend=default_backend,
            opencode=opencode,
            claude=claude,
            codex=codex,
        )

        ui_payload = payload.get("ui") or {}
        if not isinstance(ui_payload, dict):
            raise ValueError("Config 'ui' must be an object")
        ui = UiConfig(**_filter_dataclass_fields(UiConfig, ui_payload))

        update_payload = payload.get("update") or {}
        if not isinstance(update_payload, dict):
            raise ValueError("Config 'update' must be an object")
        # Backward compat: rename legacy "notify_slack" → "notify_admins"
        if "notify_slack" in update_payload and "notify_admins" not in update_payload:
            update_payload["notify_admins"] = update_payload.pop("notify_slack")
        update = UpdateConfig(**_filter_dataclass_fields(UpdateConfig, update_payload))

        ack_mode = payload.get("ack_mode", "typing")
        if ack_mode not in {"reaction", "message", "typing"}:
            raise ValueError("Config 'ack_mode' must be 'reaction', 'message', or 'typing'")

        show_duration = payload.get("show_duration", True)
        if not isinstance(show_duration, bool):
            show_duration = True

        include_user_info = payload.get("include_user_info", True)
        if not isinstance(include_user_info, bool):
            include_user_info = True

        reply_enhancements = payload.get("reply_enhancements", True)
        if not isinstance(reply_enhancements, bool):
            reply_enhancements = True

        language = normalize_language(payload.get("language"), default="en")

        return cls(
            platform=platform,
            platforms=platforms,
            mode=mode,
            version=payload.get("version", "v2"),
            slack=slack,
            discord=discord,
            lark=lark,
            wechat=wechat,
            runtime=runtime,
            agents=agents,
            gateway=gateway,
            ui=ui,
            update=update,
            ack_mode=ack_mode,
            show_duration=show_duration,
            include_user_info=include_user_info,
            reply_enhancements=reply_enhancements,
            language=language,
        )

    def save(self, config_path: Optional[Path] = None) -> None:
        paths.ensure_data_dirs()
        path = config_path or paths.get_config_path()
        self.platforms.validate()
        self.platform = self.platforms.primary
        payload = {
            "platform": self.platform,
            "platforms": {
                "enabled": self.platforms.enabled,
                "primary": self.platforms.primary,
            },
            "mode": self.mode,
            "version": self.version,
            "slack": self.slack.__dict__,
            "discord": self.discord.__dict__ if self.discord else None,
            "lark": self.lark.__dict__ if self.lark else None,
            "wechat": self.wechat.__dict__ if self.wechat else None,
            "runtime": {
                "default_cwd": self.runtime.default_cwd,
                "log_level": self.runtime.log_level,
            },
            "agents": {
                "default_backend": self.agents.default_backend,
                "opencode": self.agents.opencode.__dict__,
                "claude": self.agents.claude.__dict__,
                "codex": self.agents.codex.__dict__,
            },
            "gateway": self.gateway.__dict__ if self.gateway else None,
            "ui": self.ui.__dict__,
            "update": self.update.__dict__,
            "ack_mode": self.ack_mode,
            "show_duration": self.show_duration,
            "include_user_info": self.include_user_info,
            "reply_enhancements": self.reply_enhancements,
            "language": self.language,
        }
        content = json.dumps(payload, indent=2)
        path.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_LOCK:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
                temp_name = tmp.name
            os.replace(temp_name, path)

    def enabled_platforms(self) -> list[str]:
        return list(self.platforms.enabled)
