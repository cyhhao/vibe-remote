from dataclasses import dataclass
from typing import Optional

from config.v2_config import V2Config, SlackConfig


@dataclass
class ClaudeCompatConfig:
    permission_mode: str
    cwd: str
    system_prompt: Optional[str] = None

    def __post_init__(self) -> None:
        self.permission_mode = str(self.permission_mode)
        self.cwd = str(self.cwd)


@dataclass
class CodexCompatConfig:
    binary: str
    extra_args: list[str]
    default_model: Optional[str] = None


@dataclass
class OpenCodeCompatConfig:
    binary: str
    port: int
    request_timeout_seconds: int


@dataclass
class AppCompatConfig:
    platform: str
    slack: SlackConfig
    claude: ClaudeCompatConfig
    codex: Optional[CodexCompatConfig]
    opencode: Optional[OpenCodeCompatConfig]
    log_level: str
    cleanup_enabled: bool
    ack_mode: str
    agent_route_file: Optional[str]


def to_app_config(v2: V2Config) -> AppCompatConfig:
    claude = ClaudeCompatConfig(
        permission_mode="default",
        cwd=v2.runtime.default_cwd,
        system_prompt=None,
    )
    codex = None
    if v2.agents.codex.enabled:
        codex = CodexCompatConfig(
            binary=v2.agents.codex.cli_path,
            extra_args=[],
            default_model=v2.agents.codex.default_model,
        )
    opencode = None
    if v2.agents.opencode.enabled:
        opencode = OpenCodeCompatConfig(
            binary=v2.agents.opencode.cli_path,
            port=4096,
            request_timeout_seconds=60,
        )
    return AppCompatConfig(
        platform="slack",
        slack=v2.slack,
        claude=claude,
        codex=codex,
        opencode=opencode,
        log_level=v2.runtime.log_level,
        cleanup_enabled=v2.cleanup_enabled,
        ack_mode=v2.ack_mode,
        agent_route_file=v2.agent_route_file,
    )
