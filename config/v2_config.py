import json
import logging
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import List, Optional, Union

from config import paths
from config.platform_registry import (
    get_platform_descriptor,
    platform_catalog_payload,
    platform_descriptors,
    supported_platform_ids,
    supported_platform_set,
)
from modules.im.base import BaseIMConfig
from vibe.i18n import normalize_language

logger = logging.getLogger(__name__)

CONFIG_LOCK = threading.RLock()

DEFAULT_AGENT_BACKEND = "opencode"
DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS = 600
DEFAULT_OPENCODE_ERROR_RETRY_LIMIT = 1


def _filter_dataclass_fields(dc_class, payload: dict) -> dict:
    """Filter payload to only include fields defined in dataclass."""
    valid_fields = {f.name for f in fields(dc_class)}
    return {k: v for k, v in payload.items() if k in valid_fields}


def _deep_merge_dicts(base: dict, patch: dict) -> dict:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _remote_access_payload(payload: dict) -> dict:
    remote_payload = payload.get("remote_access")
    legacy_payload = payload.get("admin_access")
    if remote_payload is None:
        remote_payload = {}
    if legacy_payload is None:
        legacy_payload = {}
    if not isinstance(remote_payload, dict):
        raise ValueError("Config 'remote_access' must be an object")
    if not isinstance(legacy_payload, dict):
        raise ValueError("Config 'admin_access' must be an object")
    if legacy_payload:
        return _deep_merge_dicts(remote_payload, legacy_payload)
    return remote_payload


def _validate_cloudflare_remote_access_payload(payload: dict) -> None:
    for field_name in ("allowed_emails", "allowed_email_domains"):
        value = payload.get(field_name)
        if value is not None and not isinstance(value, list):
            raise ValueError(f"Config 'remote_access.cloudflare.{field_name}' must be a list")


@dataclass
class SlackConfig(BaseIMConfig):
    bot_token: str = ""
    app_token: Optional[str] = None
    signing_secret: Optional[str] = None
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    app_id: Optional[str] = None
    require_mention: bool = False
    disable_link_unfurl: bool = False

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
    # Legacy input fields. Runtime server access is stored in settings.json
    # under scopes.guild.discord so it stays with channel/user scope settings.
    guild_allowlist: Optional[List[str]] = None
    guild_denylist: Optional[List[str]] = None
    require_mention: bool = False
    # Auto-archive duration (minutes) for threads created by vibe-remote.
    # Discord only accepts 60, 1440, 4320, or 10080 (1h / 1d / 3d / 7d).
    # Defaults to 10080 (7d) to match Discord's longest native inactivity window
    # rather than aggressively archiving idle sessions after 1 hour.
    thread_auto_archive_minutes: int = 10080

    def validate(self) -> None:
        # Allow empty token for initial setup
        if self.bot_token and len(self.bot_token.strip()) < 10:
            raise ValueError("Invalid Discord bot token format")
        allowed_archive = {60, 1440, 4320, 10080}
        if self.thread_auto_archive_minutes not in allowed_archive:
            raise ValueError(
                "Invalid Discord thread_auto_archive_minutes "
                f"{self.thread_auto_archive_minutes!r}; must be one of "
                f"{sorted(allowed_archive)}"
            )


@dataclass
class TelegramConfig(BaseIMConfig):
    bot_token: str = ""
    require_mention: bool = True
    forum_auto_topic: bool = True
    use_webhook: bool = False
    webhook_url: Optional[str] = None
    webhook_secret_token: Optional[str] = None
    allowed_chat_ids: Optional[List[str]] = None
    allowed_user_ids: Optional[List[str]] = None

    def validate(self) -> None:
        # Allow empty token for initial setup
        if self.bot_token and ":" not in self.bot_token:
            raise ValueError("Invalid Telegram bot token format")


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
    error_retry_limit: int = DEFAULT_OPENCODE_ERROR_RETRY_LIMIT  # Max retries on LLM stream errors (0 = no retry)


@dataclass
class ClaudeConfig:
    enabled: bool = True
    cli_path: str = "claude"
    default_model: Optional[str] = None
    idle_timeout_seconds: int = DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS


@dataclass
class CodexConfig:
    enabled: bool = True
    cli_path: str = "codex"
    default_model: Optional[str] = None
    idle_timeout_seconds: int = DEFAULT_AGENT_IDLE_TIMEOUT_SECONDS


@dataclass
class AgentsConfig:
    default_backend: str = DEFAULT_AGENT_BACKEND
    opencode: OpenCodeConfig = field(default_factory=OpenCodeConfig)
    claude: ClaudeConfig = field(default_factory=ClaudeConfig)
    codex: CodexConfig = field(default_factory=CodexConfig)


@dataclass
class UiConfig:
    setup_host: str = "127.0.0.1"
    setup_port: int = 5123
    open_browser: bool = True


@dataclass
class CloudflareRemoteAccessConfig:
    enabled: bool = False
    hostname: str = ""
    account_id: str = ""
    zone_id: str = ""
    tunnel_id: str = ""
    tunnel_token: str = ""
    cloudflared_path: str = ""
    access_app_id: str = ""
    access_app_aud: str = ""
    allowed_emails: List[str] = field(default_factory=list)
    allowed_email_domains: List[str] = field(default_factory=list)
    confirmed_access_policy: bool = False
    confirmed_tunnel_route: bool = False


@dataclass
class RemoteAccessConfig:
    provider: str = "cloudflare"
    cloudflare: CloudflareRemoteAccessConfig = field(default_factory=CloudflareRemoteAccessConfig)


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
        supported = supported_platform_set()
        normalized: list[str] = []
        for platform in self.enabled:
            if platform not in supported:
                raise ValueError(f"Unsupported enabled platform: {platform}")
            if platform not in normalized:
                normalized.append(platform)
        if not normalized:
            raise ValueError("Config 'platforms.enabled' must contain at least one platform")
        if self.primary not in supported:
            supported_text = "', '".join(supported_platform_ids())
            raise ValueError(f"Config 'platforms.primary' must be one of: '{supported_text}'")
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
    telegram: Optional[TelegramConfig] = None
    lark: Optional[LarkConfig] = None
    wechat: Optional[WeChatConfig] = None
    platform_configs: dict[str, BaseIMConfig] = field(default_factory=dict)
    gateway: Optional[GatewayConfig] = None
    ui: UiConfig = field(default_factory=UiConfig)
    remote_access: RemoteAccessConfig = field(default_factory=RemoteAccessConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    ack_mode: str = "typing"
    show_duration: bool = False  # Show task duration in result messages
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
        try:
            get_platform_descriptor(platform)
        except ValueError as err:
            supported_text = "', '".join(supported_platform_ids())
            raise ValueError(f"Config 'platform' must be one of: '{supported_text}'") from err

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
        # When the caller explicitly set 'platform' but did not provide
        # 'platforms', treat it as a legacy single-platform update and
        # sync the new structure so that the old field is not silently
        # overridden by a stale 'platforms' value from a prior merge.
        if "platform" in payload and "platforms" not in payload:
            platforms = PlatformsConfig(enabled=[platform], primary=platform)
        platforms.validate()
        platform = platforms.primary

        platform_configs: dict[str, Optional[BaseIMConfig]] = {}
        for descriptor in platform_descriptors():
            platform_payload = payload.get(descriptor.config_key)
            if descriptor.id == "slack":
                platform_payload = platform_payload or {}
                if isinstance(platform_payload, dict) and "require_mention" not in platform_payload:
                    platform_payload = dict(platform_payload)
                    platform_payload["require_mention"] = False
            if platform_payload is not None and not isinstance(platform_payload, dict):
                raise ValueError(f"Config '{descriptor.config_key}' must be an object")
            if platform_payload is None:
                platform_configs[descriptor.id] = None
                continue

            platform_configs[descriptor.id] = descriptor.create_config(platform_payload)

        # Validate that every enabled platform has its config section present.
        for _ep in platforms.enabled:
            descriptor = get_platform_descriptor(_ep)
            if platform_configs[descriptor.id] is None:
                raise ValueError(f"Config '{descriptor.config_key}' must be provided when {_ep} is enabled")

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

        default_backend = agents_payload.get("default_backend", DEFAULT_AGENT_BACKEND)
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

        remote_access_payload = _remote_access_payload(payload)
        remote_access_provider = remote_access_payload.get("provider") or "cloudflare"
        if remote_access_provider != "cloudflare":
            raise ValueError("Config 'remote_access.provider' must be 'cloudflare'")
        cloudflare_payload = remote_access_payload.get("cloudflare")
        if cloudflare_payload is None:
            cloudflare_payload = {}
        if not isinstance(cloudflare_payload, dict):
            raise ValueError("Config 'remote_access.cloudflare' must be an object")
        _validate_cloudflare_remote_access_payload(cloudflare_payload)
        remote_access = RemoteAccessConfig(
            provider=remote_access_provider,
            cloudflare=CloudflareRemoteAccessConfig(
                **_filter_dataclass_fields(CloudflareRemoteAccessConfig, cloudflare_payload)
            ),
        )

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

        show_duration = payload.get("show_duration", False)
        if not isinstance(show_duration, bool):
            show_duration = False

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
            slack=platform_configs["slack"],
            discord=platform_configs["discord"],
            telegram=platform_configs["telegram"],
            lark=platform_configs["lark"],
            wechat=platform_configs["wechat"],
            platform_configs={key: value for key, value in platform_configs.items() if value is not None},
            runtime=runtime,
            agents=agents,
            gateway=gateway,
            ui=ui,
            remote_access=remote_access,
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
        platform_payload = {}
        for descriptor in platform_descriptors():
            descriptor_config = descriptor.get_config(self)
            config_payload = descriptor_config.__dict__.copy() if descriptor_config else None
            if descriptor.id == "discord" and isinstance(config_payload, dict):
                if not config_payload.get("guild_allowlist") and not config_payload.get("guild_denylist"):
                    config_payload.pop("guild_allowlist", None)
                    config_payload.pop("guild_denylist", None)
            platform_payload[descriptor.config_key] = config_payload
        payload = {
            "platform": self.platform,
            "platforms": {
                "enabled": self.platforms.enabled,
                "primary": self.platforms.primary,
            },
            "mode": self.mode,
            "version": self.version,
            **platform_payload,
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
            "remote_access": asdict(self.remote_access),
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

    def platform_has_credentials(self, platform: str) -> bool:
        return get_platform_descriptor(platform).has_credentials(self)

    def configured_platforms(self) -> list[str]:
        return [platform for platform in self.enabled_platforms() if self.platform_has_credentials(platform)]

    def missing_platform_credentials(self) -> list[str]:
        return [platform for platform in self.enabled_platforms() if not self.platform_has_credentials(platform)]

    def has_configured_platform_credentials(self) -> bool:
        return bool(self.configured_platforms())

    def platform_catalog(self) -> list[dict]:
        return platform_catalog_payload()

    def setup_state(self) -> dict:
        configured = self.configured_platforms()
        missing = self.missing_platform_credentials()
        return {
            "needs_setup": not bool(self.mode) or not bool(configured),
            "configured_platforms": configured,
            "missing_credentials": missing,
        }
