from dataclasses import dataclass
from typing import Optional

from config.v2_config import V2Config, SlackConfig


@dataclass
class ClaudeCompatConfig:
    permission_mode: str
    cwd: str
    system_prompt: Optional[str] = None
    default_model: Optional[str] = None

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
    error_retry_limit: int = 1  # Max retries on LLM stream errors (0 = no retry)


@dataclass
class AppCompatConfig:
    platform: str
    slack: SlackConfig
    claude: ClaudeCompatConfig
    codex: Optional[CodexCompatConfig]
    opencode: Optional[OpenCodeCompatConfig]
    log_level: str
    ack_mode: str


def to_app_config(v2: V2Config) -> AppCompatConfig:
    claude = ClaudeCompatConfig(
        permission_mode="bypassPermissions",
        cwd=v2.runtime.default_cwd,
        system_prompt=None,
        default_model=v2.agents.claude.default_model,
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
            error_retry_limit=v2.agents.opencode.error_retry_limit,
        )
    slack = SlackConfig(**v2.slack.__dict__)
    return AppCompatConfig(
        platform="slack",
        slack=slack,
        claude=claude,
        codex=codex,
        opencode=opencode,
        log_level=v2.runtime.log_level,
        ack_mode=v2.ack_mode,
    )
