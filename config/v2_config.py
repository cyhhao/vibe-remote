import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

from config import paths
from modules.im.base import BaseIMConfig

logger = logging.getLogger(__name__)


@dataclass
class SlackConfig(BaseIMConfig):
    bot_token: str
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
    check_interval_minutes: int = 10  # How often to check for updates (0 = disable)
    idle_minutes: int = 30  # Minutes of inactivity before auto-update
    notify_slack: bool = True  # Send Slack notification when update is available


@dataclass
class V2Config:
    mode: str
    version: str
    slack: SlackConfig
    runtime: RuntimeConfig
    agents: AgentsConfig
    gateway: Optional[GatewayConfig] = None
    ui: UiConfig = field(default_factory=UiConfig)
    update: UpdateConfig = field(default_factory=UpdateConfig)
    ack_mode: str = "reaction"

    @classmethod
    def load(cls, config_path: Optional[Path] = None) -> "V2Config":
        paths.ensure_data_dirs()
        path = config_path or paths.get_config_path()
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

        slack_payload = payload.get("slack")
        if not isinstance(slack_payload, dict):
            raise ValueError("Config 'slack' must be an object")

        if "require_mention" not in slack_payload:
            slack_payload = dict(slack_payload)
            slack_payload["require_mention"] = False

        slack = SlackConfig(**slack_payload)
        slack.validate()
        gateway_payload = payload.get("gateway")
        if gateway_payload is not None and not isinstance(gateway_payload, dict):
            raise ValueError("Config 'gateway' must be an object")
        gateway = GatewayConfig(**gateway_payload) if gateway_payload else None

        runtime_payload = payload.get("runtime")
        if not isinstance(runtime_payload, dict):
            raise ValueError("Config 'runtime' must be an object")
        runtime = RuntimeConfig(**runtime_payload)

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

        opencode = OpenCodeConfig(**opencode_payload)
        claude = ClaudeConfig(**claude_payload)
        codex = CodexConfig(**codex_payload)

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
        ui = UiConfig(**ui_payload)

        update_payload = payload.get("update") or {}
        if not isinstance(update_payload, dict):
            raise ValueError("Config 'update' must be an object")
        update = UpdateConfig(**update_payload)

        ack_mode = payload.get("ack_mode", "reaction")
        if ack_mode not in {"reaction", "message"}:
            raise ValueError("Config 'ack_mode' must be 'reaction' or 'message'")

        return cls(
            mode=mode,
            version=payload.get("version", "v2"),
            slack=slack,
            runtime=runtime,
            agents=agents,
            gateway=gateway,
            ui=ui,
            update=update,
            ack_mode=ack_mode,
        )

    def save(self, config_path: Optional[Path] = None) -> None:
        paths.ensure_data_dirs()
        path = config_path or paths.get_config_path()
        payload = {
            "mode": self.mode,
            "version": self.version,
            "slack": self.slack.__dict__,
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
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
