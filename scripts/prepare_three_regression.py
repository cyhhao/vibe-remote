#!/usr/bin/env python3
"""Prepare generated config/state for three-end regression containers."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path


SERVICE_DEFS = {
    "slack": {
        "platform": "slack",
        "channel_env": "THREE_REGRESSION_SLACK_CHANNEL",
        "required_envs": (
            "THREE_REGRESSION_SLACK_BOT_TOKEN",
            "THREE_REGRESSION_SLACK_APP_TOKEN",
        ),
    },
    "discord": {
        "platform": "discord",
        "channel_env": "THREE_REGRESSION_DISCORD_CHANNEL",
        "required_envs": ("THREE_REGRESSION_DISCORD_BOT_TOKEN",),
    },
    "feishu": {
        "platform": "lark",
        "channel_env": "THREE_REGRESSION_FEISHU_CHAT_ID",
        "required_envs": (
            "THREE_REGRESSION_FEISHU_APP_ID",
            "THREE_REGRESSION_FEISHU_APP_SECRET",
        ),
    },
}

SUPPORTED_BACKENDS = {"opencode", "claude", "codex"}


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


def _service_prefix(service_name: str) -> str:
    return f"THREE_REGRESSION_{service_name.upper()}"


def _build_routing(service_name: str) -> dict:
    prefix = _service_prefix(service_name)
    backend = _env(f"{prefix}_BACKEND")
    if backend not in SUPPORTED_BACKENDS:
        allowed = ", ".join(sorted(SUPPORTED_BACKENDS))
        raise SystemExit(f"{prefix}_BACKEND must be one of: {allowed}")

    return {
        "agent_backend": backend,
        "opencode_agent": _optional(f"{prefix}_OPENCODE_AGENT"),
        "opencode_model": _optional(f"{prefix}_OPENCODE_MODEL"),
        "opencode_reasoning_effort": _optional(f"{prefix}_OPENCODE_REASONING_EFFORT"),
        "claude_agent": _optional(f"{prefix}_CLAUDE_AGENT"),
        "claude_model": _optional(f"{prefix}_CLAUDE_MODEL"),
        "codex_model": _optional(f"{prefix}_CODEX_MODEL"),
        "codex_reasoning_effort": _optional(f"{prefix}_CODEX_REASONING_EFFORT"),
    }


def _service_default_cwd(service_name: str) -> str:
    prefix = _service_prefix(service_name)
    return _env(f"{prefix}_DEFAULT_CWD", _env("THREE_REGRESSION_DEFAULT_CWD", "/data/vibe_remote/workdir"))


def _base_slack_payload() -> dict:
    return {
        "bot_token": "",
        "app_token": None,
        "signing_secret": None,
        "team_id": None,
        "team_name": None,
        "app_id": None,
        "require_mention": False,
    }


def _build_config_payload(service_name: str) -> dict:
    service = SERVICE_DEFS[service_name]
    prefix = _service_prefix(service_name)
    routing = _build_routing(service_name)
    backend = routing["agent_backend"]
    require_mention = _parse_bool(_env(f"{prefix}_REQUIRE_MENTION"), default=False)

    slack_payload = _base_slack_payload()
    discord_payload = None
    lark_payload = None

    if service_name == "slack":
        slack_payload = {
            **slack_payload,
            "bot_token": _env("THREE_REGRESSION_SLACK_BOT_TOKEN"),
            "app_token": _env("THREE_REGRESSION_SLACK_APP_TOKEN"),
            "require_mention": require_mention,
        }
    elif service_name == "discord":
        discord_payload = {
            "bot_token": _env("THREE_REGRESSION_DISCORD_BOT_TOKEN"),
            "application_id": None,
            "guild_allowlist": _parse_csv(_optional("THREE_REGRESSION_DISCORD_GUILD_ALLOWLIST")),
            "guild_denylist": _parse_csv(_optional("THREE_REGRESSION_DISCORD_GUILD_DENYLIST")),
            "require_mention": require_mention,
        }
    elif service_name == "feishu":
        lark_payload = {
            "app_id": _env("THREE_REGRESSION_FEISHU_APP_ID"),
            "app_secret": _env("THREE_REGRESSION_FEISHU_APP_SECRET"),
            "require_mention": require_mention,
            "domain": _env("THREE_REGRESSION_FEISHU_DOMAIN", "feishu"),
        }

    return {
        "platform": service["platform"],
        "mode": "self_host",
        "version": "v2",
        "slack": slack_payload,
        "discord": discord_payload,
        "lark": lark_payload,
        "runtime": {
            "default_cwd": _service_default_cwd(service_name),
            "log_level": _env("THREE_REGRESSION_LOG_LEVEL", "INFO"),
        },
        "agents": {
            "default_backend": backend,
            "opencode": {
                "enabled": True,
                "cli_path": "opencode",
                "default_agent": routing["opencode_agent"],
                "default_model": routing["opencode_model"],
                "default_reasoning_effort": routing["opencode_reasoning_effort"],
                "error_retry_limit": 1,
            },
            "claude": {
                "enabled": True,
                "cli_path": "claude",
                "default_model": routing["claude_model"],
            },
            "codex": {
                "enabled": True,
                "cli_path": "codex",
                "default_model": routing["codex_model"],
            },
        },
        "gateway": None,
        "ui": {
            "setup_host": "127.0.0.1",
            "setup_port": 5123,
            "open_browser": False,
        },
        "update": {
            "auto_update": False,
            "check_interval_minutes": 0,
            "idle_minutes": 30,
            "notify_slack": False,
        },
        "ack_mode": "reaction",
        "show_duration": True,
        "include_user_info": True,
        "reply_enhancements": True,
        "language": _env("THREE_REGRESSION_LANGUAGE", "en"),
    }


def _build_settings_payload(service_name: str) -> dict:
    service = SERVICE_DEFS[service_name]
    channel_id = _env(service["channel_env"])
    routing = _build_routing(service_name)
    channel_scope = {}

    if channel_id:
        channel_scope[channel_id] = {
            "enabled": True,
            "show_message_types": [],
            "custom_cwd": _service_default_cwd(service_name),
            "routing": routing,
            "require_mention": _parse_bool(
                _env(f"{_service_prefix(service_name)}_REQUIRE_MENTION"),
                default=False,
            ),
        }

    return {
        "schema_version": 3,
        "scopes": {
            "channel": {service["platform"]: channel_scope},
            "user": {},
        },
        "bind_codes": [],
    }


def _summary_channel(service: dict) -> str:
    channel = _env(service["channel_env"])
    return channel or "(configure later in UI)"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _ensure_service_dir(service_dir: Path, reset_state: bool = False) -> None:
    if reset_state and service_dir.exists():
        shutil.rmtree(service_dir)
    for subdir in ("config", "state", "logs", "runtime", "attachments", "workdir"):
        (service_dir / subdir).mkdir(parents=True, exist_ok=True)


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
    shared_root = output_root / "shared-home"
    _write_json(shared_root / ".claude" / "settings.json", _build_claude_settings_payload())
    _write_text(shared_root / ".codex" / "config.toml", _build_codex_config_toml())
    _write_json(shared_root / ".codex" / "auth.json", _build_codex_auth_payload())
    _write_json(shared_root / ".config" / "opencode" / "opencode.json", _build_opencode_payload())


def prepare(output_root: Path, reset_state: bool = False) -> None:
    _require_envs(("ANTHROPIC_API_KEY", "OPENAI_API_KEY"))
    _write_shared_agent_configs(output_root)

    summary: list[str] = []
    for service_name, service in SERVICE_DEFS.items():
        _require_envs(service["required_envs"])
        service_dir = output_root / service_name
        _ensure_service_dir(service_dir, reset_state=reset_state)

        config_path = service_dir / "config" / "config.json"
        settings_path = service_dir / "state" / "settings.json"
        sessions_path = service_dir / "state" / "sessions.json"

        if reset_state or not config_path.exists():
            _write_json(config_path, _build_config_payload(service_name))
        if reset_state or not settings_path.exists():
            _write_json(settings_path, _build_settings_payload(service_name))
        if reset_state or not sessions_path.exists():
            _write_json(sessions_path, {})

        summary.append(
            "- {service}: platform={platform} channel={channel} backend={backend} state={state}".format(
                service=service_name,
                platform=service["platform"],
                channel=_summary_channel(service),
                backend=_env(f"{_service_prefix(service_name)}_BACKEND"),
                state="reset" if reset_state else "preserved",
            )
        )

    print(f"Prepared three-end regression state under {output_root}")
    print("\n".join(summary))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        default=str(Path("_tmp") / "three-regression"),
        help="Directory that will hold generated per-service state",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Reset service config/state directories before generating files",
    )
    args = parser.parse_args()

    try:
        prepare(Path(args.output_root).resolve(), reset_state=args.reset_state)
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
