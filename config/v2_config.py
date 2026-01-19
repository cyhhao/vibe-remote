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
        if not self.bot_token or not self.bot_token.startswith("xoxb-"):
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
class V2Config:
    mode: str
    version: str
    slack: SlackConfig
    runtime: RuntimeConfig
    agents: AgentsConfig
    gateway: Optional[GatewayConfig] = None
    ui: UiConfig = field(default_factory=UiConfig)
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
        slack_payload = payload.get("slack") or {}
        if "require_mention" not in slack_payload:
            slack_payload = dict(slack_payload)
            slack_payload["require_mention"] = False
        if "target_channels" in slack_payload:
            slack_payload = dict(slack_payload)
            slack_payload.pop("target_channels", None)
        slack = SlackConfig(**slack_payload)
        gateway_payload = payload.get("gateway")
        gateway = GatewayConfig(**gateway_payload) if gateway_payload else None
        runtime_payload = payload.get("runtime") or {}
        if "target_channels" in runtime_payload:
            runtime_payload = dict(runtime_payload)
            runtime_payload.pop("target_channels", None)
        runtime = RuntimeConfig(**runtime_payload)
        agents_payload = payload.get("agents") or {}
        opencode = OpenCodeConfig(**(agents_payload.get("opencode") or {}))
        claude = ClaudeConfig(**(agents_payload.get("claude") or {}))
        codex = CodexConfig(**(agents_payload.get("codex") or {}))
        agents = AgentsConfig(
            default_backend=agents_payload.get("default_backend", "opencode"),
            opencode=opencode,
            claude=claude,
            codex=codex,
        )
        ui = UiConfig(**(payload.get("ui") or {}))
        return cls(
            mode=payload.get("mode", "self_host"),
            version=payload.get("version", "v2"),
            slack=slack,
            runtime=runtime,
            agents=agents,
            gateway=gateway,
            ui=ui,
            ack_mode=payload.get("ack_mode", "reaction"),
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
            "ack_mode": self.ack_mode,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
