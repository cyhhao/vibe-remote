#!/usr/bin/env python3
"""Prepare generated config/state for the unified regression container.

Generates a single config.json with all four IM platforms enabled and all
three agent backends configured, plus per-channel routing in settings.json.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


PLATFORM_DEFS = {
    "slack": {
        "platform": "slack",
        "channel_env": "THREE_REGRESSION_SLACK_CHANNEL",
        "backend_env": "THREE_REGRESSION_SLACK_BACKEND",
        "required_envs": (
            "THREE_REGRESSION_SLACK_BOT_TOKEN",
            "THREE_REGRESSION_SLACK_APP_TOKEN",
        ),
    },
    "discord": {
        "platform": "discord",
        "channel_env": "THREE_REGRESSION_DISCORD_CHANNEL",
        "backend_env": "THREE_REGRESSION_DISCORD_BACKEND",
        "required_envs": ("THREE_REGRESSION_DISCORD_BOT_TOKEN",),
    },
    "feishu": {
        "platform": "lark",
        "channel_env": "THREE_REGRESSION_FEISHU_CHAT_ID",
        "backend_env": "THREE_REGRESSION_FEISHU_BACKEND",
        "required_envs": (
            "THREE_REGRESSION_FEISHU_APP_ID",
            "THREE_REGRESSION_FEISHU_APP_SECRET",
        ),
    },
    "wechat": {
        "platform": "wechat",
        "channel_env": "THREE_REGRESSION_WECHAT_CHANNEL",
        "backend_env": "THREE_REGRESSION_WECHAT_BACKEND",
        "required_envs": (),  # bot_token obtained via QR login, not env
    },
}

SUPPORTED_BACKENDS = {"opencode", "claude", "codex"}
RESET_MODES = {"none", "config", "all"}


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _optional(key: str) -> str | None:
    value = _env(key)
    return value or None


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv(value: str | None) -> list[str] | None:
    if value is None:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def _require_envs(keys: tuple[str, ...]) -> None:
    missing = [key for key in keys if not _env(key)]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"Missing required environment variables: {joined}")


def _platform_prefix(name: str) -> str:
    return f"THREE_REGRESSION_{name.upper()}"


def _build_routing(name: str) -> dict:
    prefix = _platform_prefix(name)
    backend = _env(f"{prefix}_BACKEND")
    if backend and backend not in SUPPORTED_BACKENDS:
        allowed = ", ".join(sorted(SUPPORTED_BACKENDS))
        raise SystemExit(f"{prefix}_BACKEND must be one of: {allowed}")

    return {
        "agent_backend": backend or None,
        "opencode_agent": _optional(f"{prefix}_OPENCODE_AGENT"),
        "opencode_model": _optional(f"{prefix}_OPENCODE_MODEL"),
        "opencode_reasoning_effort": _optional(f"{prefix}_OPENCODE_REASONING_EFFORT"),
        "claude_agent": _optional(f"{prefix}_CLAUDE_AGENT"),
        "claude_model": _optional(f"{prefix}_CLAUDE_MODEL"),
        "codex_model": _optional(f"{prefix}_CODEX_MODEL"),
        "codex_reasoning_effort": _optional(f"{prefix}_CODEX_REASONING_EFFORT"),
    }


def _default_cwd() -> str:
    return _env("THREE_REGRESSION_DEFAULT_CWD", "/data/vibe_remote/workdir")


def _ui_host() -> str:
    return _env("THREE_REGRESSION_UI_HOST", "127.0.0.1")


def _build_slack_payload() -> dict:
    prefix = _platform_prefix("slack")
    require_mention = _parse_bool(_env(f"{prefix}_REQUIRE_MENTION"), default=False)
    return {
        "bot_token": _env("THREE_REGRESSION_SLACK_BOT_TOKEN"),
        "app_token": _env("THREE_REGRESSION_SLACK_APP_TOKEN") or None,
        "signing_secret": None,
        "team_id": None,
        "team_name": None,
        "app_id": None,
        "require_mention": require_mention,
    }


def _build_discord_payload() -> dict:
    prefix = _platform_prefix("discord")
    require_mention = _parse_bool(_env(f"{prefix}_REQUIRE_MENTION"), default=False)
    return {
        "bot_token": _env("THREE_REGRESSION_DISCORD_BOT_TOKEN"),
        "application_id": None,
        "guild_allowlist": _parse_csv(_optional("THREE_REGRESSION_DISCORD_GUILD_ALLOWLIST")),
        "guild_denylist": _parse_csv(_optional("THREE_REGRESSION_DISCORD_GUILD_DENYLIST")),
        "require_mention": require_mention,
    }


def _build_lark_payload() -> dict:
    prefix = _platform_prefix("feishu")
    require_mention = _parse_bool(_env(f"{prefix}_REQUIRE_MENTION"), default=False)
    return {
        "app_id": _env("THREE_REGRESSION_FEISHU_APP_ID"),
        "app_secret": _env("THREE_REGRESSION_FEISHU_APP_SECRET"),
        "require_mention": require_mention,
        "domain": _env("THREE_REGRESSION_FEISHU_DOMAIN", "feishu"),
    }


def _build_wechat_payload() -> dict:
    prefix = _platform_prefix("wechat")
    require_mention = _parse_bool(_env(f"{prefix}_REQUIRE_MENTION"), default=False)
    return {
        "bot_token": _env("THREE_REGRESSION_WECHAT_BOT_TOKEN"),
        "base_url": _env("THREE_REGRESSION_WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com"),
        "cdn_base_url": _env("THREE_REGRESSION_WECHAT_CDN_BASE_URL", "https://novac2c.cdn.weixin.qq.com/c2c"),
        "require_mention": require_mention,
    }


def _default_backend() -> str:
    backend = _env("THREE_REGRESSION_DEFAULT_BACKEND", "opencode")
    if backend not in SUPPORTED_BACKENDS:
        allowed = ", ".join(sorted(SUPPORTED_BACKENDS))
        raise SystemExit(f"THREE_REGRESSION_DEFAULT_BACKEND must be one of: {allowed}")
    return backend


def _build_config_payload() -> dict:
    """Build a unified config.json with all four platforms and all three backends."""
    return {
        "platforms": {
            "enabled": ["slack", "discord", "lark", "wechat"],
            "primary": "slack",
        },
        "platform": "slack",
        "mode": "self_host",
        "version": "v2",
        "slack": _build_slack_payload(),
        "discord": _build_discord_payload(),
        "lark": _build_lark_payload(),
        "wechat": _build_wechat_payload(),
        "runtime": {
            "default_cwd": _default_cwd(),
            "log_level": _env("THREE_REGRESSION_LOG_LEVEL", "INFO"),
        },
        "agents": {
            "default_backend": _default_backend(),
            "opencode": {
                "enabled": True,
                "cli_path": "opencode",
                "default_agent": _optional("THREE_REGRESSION_OPENCODE_AGENT"),
                "default_model": _optional("THREE_REGRESSION_OPENCODE_MODEL"),
                "default_reasoning_effort": _optional("THREE_REGRESSION_OPENCODE_REASONING_EFFORT"),
                "error_retry_limit": 1,
            },
            "claude": {
                "enabled": True,
                "cli_path": "claude",
                "default_model": _optional("THREE_REGRESSION_CLAUDE_MODEL"),
            },
            "codex": {
                "enabled": True,
                "cli_path": "codex",
                "default_model": _optional("THREE_REGRESSION_CODEX_MODEL"),
            },
        },
        "gateway": None,
        "ui": {
            "setup_host": _ui_host(),
            "setup_port": 5123,
            "open_browser": False,
        },
        "update": {
            "auto_update": False,
            "check_interval_minutes": 0,
            "idle_minutes": 30,
            "notify_admins": False,
        },
        "ack_mode": "reaction",
        "show_duration": True,
        "include_user_info": True,
        "reply_enhancements": True,
        "language": _env("THREE_REGRESSION_LANGUAGE", "en"),
    }


def _build_settings_payload() -> dict:
    """Build a unified settings.json with per-channel routing for every platform."""
    channel_scopes: dict[str, dict] = {}

    for name, pdef in PLATFORM_DEFS.items():
        platform_key = pdef["platform"]
        channel_id = _env(pdef["channel_env"])
        routing = _build_routing(name)
        scope: dict = {}

        if channel_id and routing["agent_backend"]:
            prefix = _platform_prefix(name)
            scope[channel_id] = {
                "enabled": True,
                "show_message_types": ["assistant"],
                "custom_cwd": _default_cwd(),
                "routing": routing,
                "require_mention": _parse_bool(
                    _env(f"{prefix}_REQUIRE_MENTION"),
                    default=False,
                ),
            }

        channel_scopes[platform_key] = scope

    return {
        "schema_version": 3,
        "scopes": {
            "channel": channel_scopes,
            "user": {},
        },
        "bind_codes": [],
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _ensure_shared_home(output_root: Path, reset_mode: str = "none") -> Path:
    if reset_mode == "all":
        shared_root = output_root / "shared-home"
        if shared_root.exists():
            shutil.rmtree(shared_root)

    shared_root = output_root / "shared-home"
    for subdir in (
        ".claude",
        ".codex",
        ".config/opencode",
        ".local/share/opencode",
    ):
        (shared_root / subdir).mkdir(parents=True, exist_ok=True)
    return shared_root


def _ensure_vibe_dir(vibe_dir: Path, reset_mode: str = "none") -> None:
    if reset_mode not in RESET_MODES:
        allowed = ", ".join(sorted(RESET_MODES))
        raise SystemExit(f"reset_mode must be one of: {allowed}")

    if reset_mode == "all" and vibe_dir.exists():
        shutil.rmtree(vibe_dir)
    elif reset_mode == "config" and vibe_dir.exists():
        for subdir in ("config", "state", "runtime"):
            target = vibe_dir / subdir
            if target.exists():
                shutil.rmtree(target)

    for subdir in ("config", "state", "logs", "runtime", "attachments", "workdir"):
        (vibe_dir / subdir).mkdir(parents=True, exist_ok=True)


def _build_claude_settings_payload() -> dict:
    auth_token = _optional("THREE_REGRESSION_CLAUDE_AUTH_TOKEN") or _env("ANTHROPIC_API_KEY")
    payload = {
        "env": {
            "ANTHROPIC_BASE_URL": _optional("THREE_REGRESSION_CLAUDE_BASE_URL")
            or _optional("ANTHROPIC_BASE_URL")
            or "",
            "ANTHROPIC_AUTH_TOKEN": auth_token,
            "CLAUDE_CODE_ATTRIBUTION_HEADER": _env("THREE_REGRESSION_CLAUDE_ATTRIBUTION_HEADER", "0"),
        }
    }
    return payload


def _build_codex_config_toml() -> str:
    model_provider = _env("THREE_REGRESSION_CODEX_MODEL_PROVIDER", "OpenAI")
    model = _env("THREE_REGRESSION_CODEX_MODEL", "gpt-5.4")
    review_model = _env("THREE_REGRESSION_CODEX_REVIEW_MODEL", model)
    reasoning_effort = _env("THREE_REGRESSION_CODEX_REASONING_EFFORT", "xhigh")
    base_url = _optional("THREE_REGRESSION_CODEX_BASE_URL") or _optional("OPENAI_BASE_URL") or ""
    disable_storage = str(
        _parse_bool(_optional("THREE_REGRESSION_CODEX_DISABLE_RESPONSE_STORAGE"), default=True)
    ).lower()
    responses_websockets_v2 = str(
        _parse_bool(_optional("THREE_REGRESSION_CODEX_RESPONSES_WEBSOCKETS_V2"), default=False)
    ).lower()
    suppress_unstable_warning = str(
        _parse_bool(_optional("THREE_REGRESSION_CODEX_SUPPRESS_UNSTABLE_WARNING"), default=True)
    ).lower()
    return (
        f'model_provider = "{model_provider}"\n'
        f'model = "{model}"\n'
        f'review_model = "{review_model}"\n'
        f'model_reasoning_effort = "{reasoning_effort}"\n'
        f"disable_response_storage = {disable_storage}\n"
        f"suppress_unstable_features_warning = {suppress_unstable_warning}\n"
        'network_access = "enabled"\n'
        "windows_wsl_setup_acknowledged = true\n"
        f"model_context_window = {_env('THREE_REGRESSION_CODEX_CONTEXT_WINDOW', '1000000')}\n"
        f"model_auto_compact_token_limit = {_env('THREE_REGRESSION_CODEX_AUTO_COMPACT_TOKEN_LIMIT', '900000')}\n\n"
        "[model_providers.OpenAI]\n"
        'name = "OpenAI"\n'
        f'base_url = "{base_url}"\n'
        'wire_api = "responses"\n'
        "supports_websockets = true\n"
        "requires_openai_auth = true\n\n"
        "[features]\n"
        f"responses_websockets_v2 = {responses_websockets_v2}\n"
    )


def _build_codex_auth_payload() -> dict:
    return {
        "OPENAI_API_KEY": _optional("THREE_REGRESSION_CODEX_OPENAI_API_KEY") or _env("OPENAI_API_KEY"),
    }


def _build_opencode_payload() -> dict:
    openai_base = _optional("THREE_REGRESSION_OPENCODE_OPENAI_BASE_URL")
    if not openai_base:
        openai_base = _optional("OPENAI_API_BASE") or _optional("OPENAI_BASE_URL") or ""
    anthropic_base = _optional("THREE_REGRESSION_OPENCODE_ANTHROPIC_BASE_URL")
    if not anthropic_base:
        anthropic_base = _optional("ANTHROPIC_BASE_URL") or ""
    openai_key = _optional("THREE_REGRESSION_OPENCODE_OPENAI_API_KEY") or _env("OPENAI_API_KEY")
    anthropic_key = _optional("THREE_REGRESSION_OPENCODE_ANTHROPIC_API_KEY") or _env("ANTHROPIC_API_KEY")

    return {
        "permission": "allow",
        "provider": {
            "openai": {
                "options": {
                    "baseURL": openai_base,
                    "apiKey": openai_key,
                },
                "models": {
                    "gpt-5.4": {
                        "name": "GPT-5.4",
                        "options": {"store": False},
                        "variants": {"low": {}, "medium": {}, "high": {}, "xhigh": {}},
                    },
                    "gpt-5.3-codex-spark": {
                        "name": "GPT-5.3 Codex Spark",
                        "options": {"store": False},
                        "variants": {"low": {}, "medium": {}, "high": {}, "xhigh": {}},
                    },
                    "gpt-5.3-codex": {
                        "name": "GPT-5.3 Codex",
                        "options": {"store": False},
                        "variants": {"low": {}, "medium": {}, "high": {}, "xhigh": {}},
                    },
                },
            },
            "anthropic": {
                "options": {
                    "baseURL": anthropic_base,
                    "apiKey": anthropic_key,
                },
                "npm": "@ai-sdk/anthropic",
            },
        },
        "agent": {
            "build": {"options": {"store": False}},
            "plan": {"options": {"store": False}},
        },
        "$schema": "https://opencode.ai/config.json",
    }


def _write_shared_agent_configs(output_root: Path) -> None:
    shared_root = _ensure_shared_home(output_root)
    _write_json(shared_root / ".claude" / "settings.json", _build_claude_settings_payload())
    claude_state_path = shared_root / ".claude.json"
    if not claude_state_path.exists():
        _write_text(claude_state_path, "{}\n")
    _write_text(shared_root / ".codex" / "config.toml", _build_codex_config_toml())
    _write_json(shared_root / ".codex" / "auth.json", _build_codex_auth_payload())
    _write_json(shared_root / ".config" / "opencode" / "opencode.json", _build_opencode_payload())


def prepare(output_root: Path, reset_mode: str = "none") -> None:
    _require_envs(("ANTHROPIC_API_KEY", "OPENAI_API_KEY"))

    # Validate platform-specific required env vars
    for name, pdef in PLATFORM_DEFS.items():
        _require_envs(pdef["required_envs"])

    _ensure_shared_home(output_root, reset_mode=reset_mode)
    _write_shared_agent_configs(output_root)

    vibe_dir = output_root / "vibe"
    _ensure_vibe_dir(vibe_dir, reset_mode=reset_mode)

    config_path = vibe_dir / "config" / "config.json"
    settings_path = vibe_dir / "state" / "settings.json"
    sessions_path = vibe_dir / "state" / "sessions.json"

    if reset_mode in {"config", "all"} or not config_path.exists():
        _write_json(config_path, _build_config_payload())
    if reset_mode in {"config", "all"} or not settings_path.exists():
        _write_json(settings_path, _build_settings_payload())
    if reset_mode in {"config", "all"} or not sessions_path.exists():
        _write_json(sessions_path, {})

    summary_lines: list[str] = []
    for name, pdef in PLATFORM_DEFS.items():
        channel = _env(pdef["channel_env"]) or "(configure later in UI)"
        backend = _env(pdef["backend_env"]) or _default_backend()
        summary_lines.append(f"  {name}: platform={pdef['platform']} channel={channel} backend={backend}")

    print(f"Prepared unified regression state under {output_root / 'vibe'}")
    print("Platform routing:")
    print("\n".join(summary_lines))
    print(f"State: {reset_mode if reset_mode != 'none' else 'preserved'}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        default=str(Path("_tmp") / "three-regression"),
        help="Directory that will hold generated state",
    )
    parser.add_argument(
        "--reset-mode",
        choices=sorted(RESET_MODES),
        default="none",
        help="Reset scope before generating files: none, config, or all",
    )
    args = parser.parse_args()

    try:
        prepare(Path(args.output_root).resolve(), reset_mode=args.reset_mode)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
