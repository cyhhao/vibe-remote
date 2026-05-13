import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import paths
from config.v2_config import CONFIG_LOCK, V2Config
from config.v2_settings import (
    SettingsStore,
    ChannelSettings,
    GuildSettings,
    UserSettings,
    RoutingSettings,
    normalize_show_message_types,
    _parse_routing,
    _routing_to_dict,
)
from config.discovered_chats import DiscoveredChatsStore
from config.v2_sessions import SessionsStore
from vibe.opencode_config import (
    get_opencode_config_paths,
    load_first_opencode_user_config,
    set_jsonc_top_level_string_property,
)
from vibe.upgrade import (
    build_upgrade_plan,
    get_latest_version_info,
    get_restart_command,
    get_restart_environment,
    get_running_vibe_path,
    get_safe_cwd,
)
from vibe.claude_model_catalog import DEFAULT_CLAUDE_MODEL_ALIASES, load_catalog_models
from modules.agents.subagent_router import list_codex_subagents


logger = logging.getLogger(__name__)

# Cache per cwd: { cwd: { "data": ..., "updated_at": ... } }
_OPENCODE_OPTIONS_CACHE: dict[str, dict] = {}
_OPENCODE_OPTIONS_TTL_SECONDS = 30.0


def _delayed_restart_helper_command() -> list[str]:
    candidates: list[list[str]] = []
    current = sys.executable

    if current and os.path.isabs(current) and os.path.exists(current) and os.access(current, os.X_OK):
        candidates.append([current])

    if os.name == "nt":
        candidates.extend((["py", "-3"], ["python"], ["python3"]))
    else:
        candidates.extend((["python3"], ["python"]))

    for candidate in candidates:
        binary = candidate[0]
        if os.path.isabs(binary):
            if os.path.exists(binary) and os.access(binary, os.X_OK):
                return candidate
            continue
        resolved = shutil.which(binary)
        if resolved:
            return [resolved, *candidate[1:]]

    raise FileNotFoundError("No stable Python launcher available for delayed restart helper")


def _spawn_delayed_restart(
    command: list[str],
    cwd: str,
    delay_seconds: float = 2.0,
    env: dict[str, str] | None = None,
) -> None:
    helper_code = (
        "import subprocess, time\n"
        f"time.sleep({delay_seconds!r})\n"
        f"subprocess.Popen({command!r}, cwd={cwd!r}, env={env!r}, stdout=subprocess.DEVNULL, "
        "stderr=subprocess.DEVNULL, close_fds=True)\n"
    )
    helper_cmd = [*_delayed_restart_helper_command(), "-c", helper_code]
    subprocess.Popen(
        helper_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        cwd=cwd,
    )


def _is_executable_file(path: Path) -> bool:
    return path.exists() and path.is_file() and os.access(path, os.X_OK)


def _nvm_binary_candidates(binary: str) -> list[Path]:
    versions_dir = Path.home() / ".nvm" / "versions" / "node"
    if not versions_dir.exists():
        return []

    def _version_sort_key(path: Path) -> tuple:
        parts = str(path.name).lstrip("v").split(".")
        key: list[int | str] = []
        for part in parts:
            key.append(int(part) if part.isdigit() else part)
        return tuple(key)

    candidates: list[Path] = []
    for version_dir in sorted(versions_dir.glob("*"), key=_version_sort_key, reverse=True):
        candidate = version_dir / "bin" / binary
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def _npm_global_binary_candidates(binary: str) -> list[Path]:
    if not binary or binary == "npm":
        return []

    npm_paths: list[Path] = []
    for candidate in _candidate_cli_paths("npm"):
        if _is_executable_file(candidate) and candidate not in npm_paths:
            npm_paths.append(candidate)

    which_npm = shutil.which("npm")
    if which_npm:
        npm_candidate = Path(which_npm)
        if npm_candidate not in npm_paths:
            npm_paths.append(npm_candidate)

    candidates: list[Path] = []
    for npm_path in npm_paths:
        try:
            result = subprocess.run(
                [str(npm_path), "config", "get", "prefix"],
                capture_output=True,
                text=True,
                timeout=5,
                env=_command_env_for(str(npm_path)),
            )
        except Exception:
            continue

        if result.returncode != 0:
            continue

        prefix = (result.stdout or "").strip().splitlines()
        if not prefix:
            continue

        prefix_path = Path(os.path.expanduser(prefix[-1]))
        derived_candidates = [
            prefix_path / "bin" / binary,
            prefix_path / binary,
            prefix_path / "node_modules" / ".bin" / binary,
        ]
        if os.name == "nt":
            derived_candidates.extend(
                [
                    prefix_path / f"{binary}.cmd",
                    prefix_path / f"{binary}.exe",
                    prefix_path / "node_modules" / ".bin" / f"{binary}.cmd",
                ]
            )

        for candidate in derived_candidates:
            if candidate not in candidates:
                candidates.append(candidate)

    return candidates


def _candidate_cli_paths(binary: str) -> list[Path]:
    if not binary:
        return []

    expanded = Path(os.path.expanduser(binary))
    has_path_separator = os.sep in binary or (os.altsep is not None and os.altsep in binary)
    if expanded.is_absolute() or has_path_separator:
        return [expanded]

    home = Path.home()
    candidates: list[Path] = []
    if binary == "claude":
        candidates.append(home / ".claude" / "local" / "claude")
    elif binary == "opencode":
        candidates.extend(
            [
                home / ".opencode" / "bin" / "opencode",
                home / ".local" / "bin" / "opencode",
            ]
        )

    common_candidates = [
        home / ".local" / "bin" / binary,
        home / ".bun" / "bin" / binary,
        Path("/opt/homebrew/bin") / binary,
        Path("/usr/local/bin") / binary,
    ]
    for candidate in common_candidates + _nvm_binary_candidates(binary) + _npm_global_binary_candidates(binary):
        if candidate not in candidates:
            candidates.append(candidate)

    return candidates


def resolve_cli_path(binary: str) -> str | None:
    for candidate in _candidate_cli_paths(binary):
        if _is_executable_file(candidate):
            return str(candidate)

    path = shutil.which(os.path.expanduser(binary)) if binary else None
    if path:
        return path

    # The stored cli_path was an absolute path that no longer exists. Most
    # common cause: an upstream installer moved the binary out from under us.
    # Real-world example: Claude Code's official ``install.sh`` puts the
    # native binary at ``~/.local/bin/claude`` (via ``~/.local/share/claude/
    # versions/<ver>``), while the legacy ``npm install -g
    # @anthropic-ai/claude-code`` install used ``/usr/local/bin/claude``.
    # After clicking "Upgrade" in the UI, V2Config still points at the
    # /usr/local/bin path, so the runtime probe reports ``installed=false``
    # and the chip flips to "not installed". Fall back to discovery using
    # only the basename — if a binary with that name is on any of the
    # standard candidate paths (~/.local/bin, /opt/homebrew/bin, npm/nvm/bun
    # globals, etc.) we treat that as the live install. The basename
    # restriction means custom callers passing ``"/path/to/my-claude"``
    # don't get silently redirected to the system claude.
    if not binary:
        return None
    expanded = Path(os.path.expanduser(binary))
    has_path_separator = os.sep in binary or (os.altsep is not None and os.altsep in binary)
    if expanded.is_absolute() or has_path_separator:
        basename = expanded.name
        if basename and basename != binary:
            for candidate in _candidate_cli_paths(basename):
                if _is_executable_file(candidate):
                    logger.info(
                        "resolve_cli_path: stored path %s missing; falling back to %s",
                        binary,
                        candidate,
                    )
                    return str(candidate)
    return None


def _command_env_for(binary_path: str | None) -> dict[str, str]:
    env = {**os.environ, "PATH": os.environ.get("PATH", "")}
    if not binary_path:
        return env

    binary_dir = str(Path(binary_path).expanduser().resolve().parent)
    path_entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry and entry != binary_dir]
    env["PATH"] = os.pathsep.join([binary_dir, *path_entries])
    return env


def browse_directory(path: str, show_hidden: bool = False) -> dict:
    """List sub-directories of *path* for the directory browser UI.

    Symlinks are not followed when scanning entries.

    Returns ``{"ok": True, "path": <abs>, "parent": <abs|None>, "dirs": [...]}``
    where each entry in *dirs* is ``{"name": ..., "path": ...}``.
    """
    try:
        target = Path(os.path.expanduser(path or "~")).resolve()

        if not target.is_dir():
            return {"ok": False, "error": f"Not a directory: {target}"}

        abs_path = str(target)
        parent = str(target.parent) if target.parent != target else None

        entries: list[dict[str, str]] = []
        try:
            for entry in sorted(os.scandir(abs_path), key=lambda e: e.name.lower()):
                if not show_hidden and entry.name.startswith("."):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    entries.append({"name": entry.name, "path": str(target / entry.name)})
        except PermissionError:
            return {"ok": False, "error": "permission_denied"}

        return {"ok": True, "path": abs_path, "parent": parent, "dirs": entries}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def load_config() -> V2Config:
    return V2Config.load()


def _deep_merge_dicts(base: dict, patch: dict) -> dict:
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


_AGENT_AUTH_FIELDS = ("auth_mode", "api_key", "base_url")


def _strip_agent_auth_fields(payload: dict) -> dict:
    """Drop auth fields from a generic settings patch.

    The UI's Settings → Backends page round-trips the masked agent config
    on save (api_key arrives as ``None`` after masking), and naive
    deep-merge would clobber the real key. Auth state changes must go
    through ``/backend/<name>/auth`` exclusively; this helper enforces
    that contract on the generic settings POST.
    """
    if not isinstance(payload, dict):
        return payload
    agents = payload.get("agents")
    if not isinstance(agents, dict):
        return payload
    cleaned_agents = dict(agents)
    for backend in ("claude", "codex"):
        backend_payload = cleaned_agents.get(backend)
        if isinstance(backend_payload, dict):
            cleaned_backend = {
                k: v for k, v in backend_payload.items() if k not in _AGENT_AUTH_FIELDS
            }
            cleaned_agents[backend] = cleaned_backend
    return {**payload, "agents": cleaned_agents}


def save_config(payload: dict) -> V2Config:
    if not isinstance(payload, dict):
        raise ValueError("Config payload must be an object")

    payload = _strip_agent_auth_fields(payload)

    with CONFIG_LOCK:
        base_payload: dict = {}
        base_config: Optional[V2Config] = None
        try:
            base_config = load_config()
            base_payload = config_to_payload(base_config, include_secrets=True)
        except FileNotFoundError:
            base_payload = {}

        merged_payload = _deep_merge_dicts(base_payload, payload) if base_payload else payload
        merged_payload = _merge_legacy_discord_guild_scope_fields(merged_payload, payload, base_config)
        sanitized_payload, guild_scope_update = _extract_settings_scopes_from_config_payload(merged_payload)
        config = V2Config.from_payload(sanitized_payload)
        if guild_scope_update is not None:
            _save_discord_guild_scope_update(*guild_scope_update)
        elif base_config is not None:
            store = SettingsStore.get_instance()
            if not store.has_guild_scope_for_platform("discord"):
                existing_update = _discord_guild_scope_from_config(base_config)
                if existing_update is not None:
                    _save_discord_guild_scope_update(*existing_update, store=store)
        config.save()
        return config


def _vibe_cloud_payload(config: V2Config, include_secrets: bool) -> dict:
    payload = config.remote_access.vibe_cloud.__dict__.copy()
    if not include_secrets:
        for key in ("tunnel_token", "instance_secret", "session_secret"):
            payload.pop(key, None)
    return payload


def _agent_payload(raw: dict, *, include_secrets: bool) -> dict:
    """Project a Claude/Codex config dict for the UI, masking the api_key.

    The UI surfaces *whether* a key is configured (and its length, so the
    user can see ``****6c1f``-style hints), never the plaintext. Only the
    secrets-included path (used by the setup wizard's "load existing
    config" flow) sees the raw value.
    """
    payload = dict(raw)
    api_key = payload.get("api_key")
    if isinstance(api_key, str):
        payload["api_key_length"] = len(api_key)
        payload["has_api_key"] = bool(api_key)
    else:
        payload["api_key_length"] = 0
        payload["has_api_key"] = False
    if not include_secrets:
        payload["api_key"] = None
    return payload


def config_to_payload(config: V2Config, *, include_secrets: bool = False) -> dict:
    from config.platform_registry import platform_descriptors

    platform_payload = {}
    for descriptor in platform_descriptors():
        descriptor_config = descriptor.get_config(config)
        platform_payload[descriptor.config_key] = descriptor_config.__dict__.copy() if descriptor_config else None
    if isinstance(platform_payload.get("discord"), dict):
        platform_payload["discord"].pop("guild_allowlist", None)
        platform_payload["discord"].pop("guild_denylist", None)
    payload = {
        "platform": config.platform,
        "platforms": {
            "enabled": config.platforms.enabled,
            "primary": config.platforms.primary,
        },
        "platform_catalog": config.platform_catalog(),
        "setup_state": config.setup_state(),
        "mode": config.mode,
        "version": config.version,
        **platform_payload,
        "runtime": {
            "default_cwd": config.runtime.default_cwd,
            "log_level": config.runtime.log_level,
        },
        "agents": {
            "default_backend": config.agents.default_backend,
            "opencode": config.agents.opencode.__dict__,
            "claude": _agent_payload(config.agents.claude.__dict__, include_secrets=include_secrets),
            "codex": _agent_payload(config.agents.codex.__dict__, include_secrets=include_secrets),
        },
        "gateway": config.gateway.__dict__ if config.gateway else None,
        "ui": config.ui.__dict__,
        "remote_access": {
            "provider": config.remote_access.provider,
            "vibe_cloud": _vibe_cloud_payload(config, include_secrets),
        },
        "update": config.update.__dict__,
        "ack_mode": config.ack_mode,
        "language": config.language,
        "show_duration": config.show_duration,
        "include_user_info": config.include_user_info,
        "reply_enhancements": config.reply_enhancements,
    }
    return payload


def _merge_legacy_discord_guild_scope_fields(
    merged_payload: dict,
    request_payload: dict,
    base_config: Optional[V2Config],
) -> dict:
    """Complete partial legacy Discord guild updates before migration."""
    request_discord = request_payload.get("discord")
    if not isinstance(request_discord, dict):
        return merged_payload
    if "guild_allowlist" not in request_discord and "guild_denylist" not in request_discord:
        return merged_payload

    next_payload = dict(merged_payload)
    merged_discord = dict(next_payload.get("discord") or {})
    base_discord = getattr(base_config, "discord", None) if base_config is not None else None

    if "guild_allowlist" not in request_discord and base_discord is not None:
        merged_discord["guild_allowlist"] = getattr(base_discord, "guild_allowlist", None) or []
    if "guild_denylist" not in request_discord and base_discord is not None:
        merged_discord["guild_denylist"] = getattr(base_discord, "guild_denylist", None) or []

    next_payload["discord"] = merged_discord
    return next_payload


def get_platform_catalog() -> dict:
    from config.platform_registry import platform_catalog_payload

    return {"platforms": platform_catalog_payload()}


def get_settings(platform: Optional[str] = None) -> dict:
    store = SettingsStore.get_instance()
    target_platform = platform or _current_platform()
    if target_platform == "discord":
        _migrate_discord_guild_scope_from_config(store)
    return _settings_to_payload(store, platform=target_platform)


def save_settings(payload: dict) -> dict:
    store = SettingsStore.get_instance()
    platform = payload.get("platform") or _current_platform()

    def _normalize_routing_payload(routing_payload: dict) -> dict:
        from modules.agents.opencode.utils import normalize_claude_reasoning_effort

        routing_data = dict(routing_payload or {})
        routing_data["claude_reasoning_effort"] = normalize_claude_reasoning_effort(
            routing_data.get("claude_model"),
            routing_data.get("claude_reasoning_effort"),
        )
        return routing_data

    if "channels" in payload:
        channels = {}
        for channel_id, channel_payload in (payload.get("channels") or {}).items():
            channels[channel_id] = ChannelSettings(
                enabled=channel_payload.get("enabled", True),
                show_message_types=normalize_show_message_types(channel_payload.get("show_message_types")),
                custom_cwd=channel_payload.get("custom_cwd"),
                routing=_parse_routing(_normalize_routing_payload(channel_payload.get("routing") or {})),
                require_mention=channel_payload.get("require_mention"),
            )
        store.set_channels_for_platform(platform, channels)
    if "guilds" in payload or "guild_allowlist" in payload:
        guilds, default_enabled = _guild_scope_update_from_settings_payload(store, platform, payload)
        store.set_guilds_for_platform(platform, guilds, default_enabled=default_enabled)
    store.save()
    return _settings_to_payload(store, platform=platform)


def _guild_scope_update_from_settings_payload(
    store: SettingsStore,
    platform: str,
    payload: dict,
) -> tuple[dict[str, GuildSettings], bool]:
    next_guilds = _guild_settings_from_payload(payload)
    if "guild_default_enabled" in payload:
        return next_guilds, bool(payload.get("guild_default_enabled", False))

    default_enabled = store.get_guild_default_enabled_for_platform(platform)
    if default_enabled:
        for guild_id, settings in store.get_guilds_for_platform(platform).items():
            if not settings.enabled and guild_id not in next_guilds:
                next_guilds[guild_id] = settings
    return next_guilds, default_enabled


def _guild_settings_from_payload(payload: dict) -> dict[str, GuildSettings]:
    if "guilds" in payload:
        guild_payload = payload.get("guilds") or {}
        if not isinstance(guild_payload, dict):
            return {}
        return {
            str(guild_id): GuildSettings(enabled=(settings or {}).get("enabled", True))
            for guild_id, settings in guild_payload.items()
            if isinstance(settings, dict)
        }

    allowlist = payload.get("guild_allowlist") or []
    if not isinstance(allowlist, list):
        return {}
    return {str(guild_id): GuildSettings(enabled=True) for guild_id in allowlist if str(guild_id)}


def _migrate_discord_guild_scope_from_config(store: SettingsStore, config: Optional[V2Config] = None) -> None:
    if store.has_guild_scope_for_platform("discord"):
        return
    try:
        cfg = config or load_config()
    except FileNotFoundError:
        return
    discord_config = getattr(cfg, "discord", None)
    if not discord_config:
        return
    allowlist = getattr(discord_config, "guild_allowlist", None) or []
    denylist = getattr(discord_config, "guild_denylist", None) or []
    if not allowlist and not denylist:
        return
    _save_discord_guild_scope_update(*_discord_guild_scope_from_legacy_payload(allowlist, denylist), store=store)


def _discord_guild_scope_from_legacy_payload(
    allowlist: list | None,
    denylist: list | None,
) -> tuple[dict[str, GuildSettings], bool]:
    default_enabled = not bool(allowlist)
    guilds = {
        str(guild_id): GuildSettings(enabled=True)
        for guild_id in (allowlist or [])
        if str(guild_id)
    }
    for guild_id in denylist or []:
        guilds[str(guild_id)] = GuildSettings(enabled=False)
    return guilds, default_enabled


def _discord_guild_scope_from_config(config: V2Config) -> Optional[tuple[dict[str, GuildSettings], bool]]:
    discord_config = getattr(config, "discord", None)
    if not discord_config:
        return None
    allowlist = getattr(discord_config, "guild_allowlist", None) or []
    denylist = getattr(discord_config, "guild_denylist", None) or []
    if not allowlist and not denylist:
        return None
    return _discord_guild_scope_from_legacy_payload(allowlist, denylist)


def _save_discord_guild_scope_update(
    guilds: dict[str, GuildSettings],
    default_enabled: bool,
    store: Optional[SettingsStore] = None,
) -> None:
    target_store = store or SettingsStore.get_instance()
    target_store.set_guilds_for_platform("discord", guilds, default_enabled=default_enabled)
    target_store.save()


def _extract_settings_scopes_from_config_payload(
    payload: dict,
) -> tuple[dict, Optional[tuple[dict[str, GuildSettings], bool]]]:
    """Move legacy Discord server access fields from config updates to settings."""
    if not isinstance(payload, dict):
        return payload, None
    next_payload = dict(payload)
    discord_payload = next_payload.get("discord")
    if not isinstance(discord_payload, dict):
        return next_payload, None

    discord_next = dict(discord_payload)
    has_guild_scope = "guild_allowlist" in discord_next or "guild_denylist" in discord_next
    allowlist = discord_next.pop("guild_allowlist", None)
    denylist = discord_next.pop("guild_denylist", None)
    next_payload["discord"] = discord_next

    if has_guild_scope:
        return next_payload, _discord_guild_scope_from_legacy_payload(allowlist, denylist)

    return next_payload, None


def init_sessions() -> None:
    store = SessionsStore()
    if store.sessions_path.exists():
        return
    store.save()


def detect_cli(binary: str) -> dict:
    path = resolve_cli_path(binary)
    if not path:
        return {"found": False, "path": None}
    return {"found": True, "path": path}


def check_cli_exec(path: str) -> dict:
    if not path:
        return {"ok": False, "error": "path is empty"}
    if not os.path.exists(path):
        return {"ok": False, "error": "path does not exist"}
    if not os.access(path, os.X_OK):
        return {"ok": False, "error": "path is not executable"}
    return {"ok": True}


def slack_auth_test(bot_token: str, proxy_url: str | None = None) -> dict:
    try:
        from slack_sdk.web import WebClient
        from vibe.proxy import resolve_proxy

        proxy = resolve_proxy(proxy_url)
        client = WebClient(token=bot_token, proxy=proxy)
        response = client.auth_test()
        return {"ok": True, "response": response.data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def list_channels(bot_token: str, browse_all: bool = False) -> dict:
    """List Slack channels.

    When *browse_all* is False (default), only channels the bot has joined are
    returned via ``users_conversations``.  This is very fast and avoids hitting
    Slack rate-limits even in large workspaces.

    When *browse_all* is True, all visible channels in the workspace are
    returned via ``conversations_list``.  Rate-limit retries with exponential
    back-off are applied automatically.
    """
    import time

    from slack_sdk.errors import SlackApiError
    from slack_sdk.web import WebClient

    client = WebClient(token=bot_token)
    channels: list[dict] = []
    cursor = None

    try:
        while True:
            for attempt in range(5):
                try:
                    if browse_all:
                        response = client.conversations_list(
                            types="public_channel,private_channel",
                            exclude_archived=True,
                            limit=200,
                            cursor=cursor,
                        )
                    else:
                        response = client.users_conversations(
                            types="public_channel,private_channel",
                            exclude_archived=True,
                            limit=200,
                            cursor=cursor,
                        )
                    break  # success
                except SlackApiError as e:
                    if e.response.status_code == 429:
                        retry_after = int(e.response.headers.get("Retry-After", 1))
                        wait = max(retry_after, 2**attempt)
                        logger.warning(
                            "Slack rate-limited (429), retrying after %ds (attempt %d/5)",
                            wait,
                            attempt + 1,
                        )
                        time.sleep(wait)
                    else:
                        raise
            else:
                # Exhausted retries
                return {
                    "ok": False,
                    "error": "Slack rate-limit exceeded after 5 retries",
                }

            for channel in response.get("channels", []):
                channels.append(
                    {
                        "id": channel.get("id"),
                        "name": channel.get("name"),
                        "is_private": channel.get("is_private", False),
                    }
                )
            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        return {"ok": True, "channels": channels, "is_member_only": not browse_all}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def discord_auth_test(bot_token: str, proxy_url: str | None = None) -> dict:
    try:
        data = _discord_api_get(bot_token, "users/@me", proxy_url=proxy_url)
        return {"ok": True, "response": data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def telegram_auth_test(bot_token: str, proxy_url: str | None = None) -> dict:
    try:
        from vibe.proxy import resolve_proxy

        proxy = resolve_proxy(proxy_url)
        return {"ok": True, "response": asyncio.run(_telegram_get_me(bot_token, proxy))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def telegram_list_chats(include_private: bool = False) -> dict:
    store = DiscoveredChatsStore.get_instance()
    all_chats = store.list_chats("telegram", include_private=True)
    chats = all_chats if include_private else [chat for chat in all_chats if not chat.is_private]
    return {
        "ok": True,
        "channels": [
            {
                "id": chat.chat_id,
                "name": chat.name or chat.username or chat.chat_id,
                "username": chat.username,
                "type": chat.chat_type,
                "is_private": chat.is_private,
                "is_forum": chat.is_forum,
                "supports_topics": chat.supports_topics,
                "last_seen_at": chat.last_seen_at,
            }
            for chat in chats
        ],
        "summary": {
            "discovered_count": len(all_chats),
            "visible_count": len(chats),
            "hidden_private_count": sum(1 for chat in all_chats if chat.is_private) if not include_private else 0,
            "forum_count": sum(1 for chat in chats if chat.supports_topics),
        },
    }


def discord_list_guilds(bot_token: str) -> dict:
    try:
        data = _discord_api_get(bot_token, "users/@me/guilds")
        return {"ok": True, "guilds": data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def discord_list_channels(bot_token: str, guild_id: str) -> dict:
    try:
        data = _discord_api_get(bot_token, f"guilds/{guild_id}/channels")
        channels = []
        for channel in data:
            channels.append(
                {
                    "id": channel.get("id"),
                    "name": channel.get("name"),
                    "type": channel.get("type"),
                }
            )
        return {"ok": True, "channels": channels}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def opencode_options(cwd: str) -> dict:
    try:
        return asyncio.run(opencode_options_async(cwd))
    except Exception as exc:
        logger.warning("OpenCode options fetch failed: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc)}


def _discord_api_get(bot_token: str, path: str, proxy_url: str | None = None) -> dict:
    import urllib.request

    from vibe.proxy import is_socks_proxy, resolve_proxy

    if not bot_token:
        raise ValueError("bot_token is required")
    url = f"https://discord.com/api/v10/{path.lstrip('/')}"
    headers = {"Authorization": f"Bot {bot_token}", "User-Agent": "vibe-remote"}

    proxy = resolve_proxy(proxy_url)
    if proxy and is_socks_proxy(proxy):
        # urllib has no native SOCKS support; route via aiohttp + aiohttp_socks.
        return asyncio.run(_discord_api_get_via_aiohttp(url, headers, proxy))

    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    else:
        opener = urllib.request.build_opener()
    req = urllib.request.Request(url, headers=headers)
    with opener.open(req, timeout=10) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload)


async def _discord_api_get_via_aiohttp(url: str, headers: dict, proxy: str) -> dict:
    import aiohttp
    from aiohttp_socks import ProxyConnector

    connector = ProxyConnector.from_url(proxy, rdns=True)
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            # urllib.urlopen raises HTTPError on non-2xx; mirror that here so
            # callers like discord_auth_test correctly treat 401 as a failure
            # instead of returning Discord's error JSON as a successful payload.
            resp.raise_for_status()
            return await resp.json()


async def _telegram_get_me(bot_token: str, proxy_url: str | None = None) -> dict:
    from modules.im import telegram_api

    result = await telegram_api.get_me(bot_token, proxy_url=proxy_url)
    return result.get("result") or {}


async def opencode_options_async(cwd: str) -> dict:
    # Expand ~ to user home directory
    request_loop = asyncio.get_running_loop()
    expanded_cwd = os.path.expanduser(cwd)
    cache_entry = _OPENCODE_OPTIONS_CACHE.get(expanded_cwd, {})
    cache_data = cache_entry.get("data")
    updated_at = cache_entry.get("updated_at", 0.0)
    cache_age = time.monotonic() - updated_at
    if cache_data and cache_age < _OPENCODE_OPTIONS_TTL_SECONDS:
        return {"ok": True, "data": cache_data, "cached": True}

    server = None
    try:
        from config.v2_compat import to_app_config
        from modules.agents.opencode import (
            OpenCodeServerManager,
            build_reasoning_effort_options,
        )

        config = to_app_config(V2Config.load())
        if not config.opencode:
            return {"ok": False, "error": "opencode disabled"}
        opencode_config = config.opencode
        timeout_seconds = min(10.0, float(opencode_config.request_timeout_seconds or 10))

        def _build_reasoning_options(
            models: dict,
            builder,
        ) -> dict:
            options: dict = {}
            for provider in models.get("providers", []):
                provider_id = provider.get("id") or provider.get("provider_id") or provider.get("name")
                if not provider_id:
                    continue
                model_ids = []
                provider_models = provider.get("models", {})
                if isinstance(provider_models, dict):
                    model_ids = list(provider_models.keys())
                elif isinstance(provider_models, list):
                    model_ids = [
                        model.get("id") for model in provider_models if isinstance(model, dict) and model.get("id")
                    ]
                for model_id in model_ids:
                    model_key = f"{provider_id}/{model_id}"
                    options[model_key] = builder(models, model_key)
            return options

        server = await OpenCodeServerManager.get_instance(
            binary=opencode_config.binary,
            port=opencode_config.port,
            request_timeout_seconds=opencode_config.request_timeout_seconds,
        )
        await asyncio.wait_for(server.ensure_running(), timeout=timeout_seconds)
        agents = await asyncio.wait_for(server.get_available_agents(expanded_cwd), timeout=timeout_seconds)
        models = await asyncio.wait_for(server.get_available_models(expanded_cwd), timeout=timeout_seconds)
        defaults = await asyncio.wait_for(server.get_default_config(expanded_cwd), timeout=timeout_seconds)
        reasoning_options = _build_reasoning_options(models, build_reasoning_effort_options)
        data = {
            "agents": agents,
            "models": models,
            "defaults": defaults,
            "reasoning_options": reasoning_options,
        }
        _OPENCODE_OPTIONS_CACHE[expanded_cwd] = {
            "data": data,
            "updated_at": time.monotonic(),
        }
        return {"ok": True, "data": data}
    except Exception as exc:
        logger.warning("OpenCode options fetch failed: %s", exc, exc_info=True)
        if cache_data:
            return {"ok": True, "data": cache_data, "cached": True, "warning": str(exc)}
        return {"ok": False, "error": str(exc)}
    finally:
        if server is not None:
            await server.close_http_session(loop=request_loop)


def _current_platform() -> str:
    return load_config().platform


def _settings_to_payload(store: SettingsStore, platform: str) -> dict:
    payload: dict = {
        "channels": {},
        "guilds": {},
        "guild_allowlist": [],
        "guild_scope_configured": False,
        "guild_default_enabled": False,
        "users": {},
        "bind_codes": [],
    }
    for channel_id, settings in store.get_channels_for_platform(platform).items():
        payload["channels"][channel_id] = {
            "enabled": settings.enabled,
            "show_message_types": normalize_show_message_types(settings.show_message_types),
            "custom_cwd": settings.custom_cwd,
            "require_mention": settings.require_mention,
            "routing": _routing_to_dict(settings.routing),
        }
    payload["guild_scope_configured"] = store.has_guild_scope_for_platform(platform)
    payload["guild_default_enabled"] = store.get_guild_default_enabled_for_platform(platform)
    for guild_id, settings in store.get_guilds_for_platform(platform).items():
        payload["guilds"][guild_id] = {
            "enabled": settings.enabled,
        }
    payload["guild_allowlist"] = [
        guild_id for guild_id, settings in payload["guilds"].items() if settings.get("enabled")
    ]
    for user_id, u in store.get_users_for_platform(platform).items():
        payload["users"][user_id] = {
            "display_name": u.display_name,
            "is_admin": u.is_admin,
            "bound_at": u.bound_at,
            "enabled": u.enabled,
            "show_message_types": u.show_message_types,
            "custom_cwd": u.custom_cwd,
            "routing": _routing_to_dict(u.routing),
        }
    for bc in store.settings.bind_codes:
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
    return payload


def get_slack_manifest() -> dict:
    """Get Slack App Manifest template for self-host mode.

    Loads manifest from vibe/templates/slack_manifest.json.

    Returns:
        {"ok": True, "manifest": str, "manifest_compact": str} on success
        {"ok": False, "error": str} on failure
    """
    import json
    import importlib.resources

    try:
        manifest = None

        # Try to load from package resources (installed via pip/uv)
        try:
            if hasattr(importlib.resources, "files"):
                package_files = importlib.resources.files("vibe")
                template_path = package_files / "templates" / "slack_manifest.json"
                if hasattr(template_path, "read_text"):
                    manifest = json.loads(template_path.read_text(encoding="utf-8"))
        except (TypeError, FileNotFoundError, AttributeError, json.JSONDecodeError):
            pass

        # Fallback: load from file system (development mode)
        if manifest is None:
            this_dir = Path(__file__).parent
            template_file = this_dir / "templates" / "slack_manifest.json"
            if template_file.exists():
                manifest = json.loads(template_file.read_text(encoding="utf-8"))

        if manifest is None:
            return {"ok": False, "error": "Manifest template file not found"}

        # Pretty JSON for display, compact JSON for URL
        manifest_pretty = json.dumps(manifest, indent=2)
        manifest_compact = json.dumps(manifest, separators=(",", ":"))
        return {
            "ok": True,
            "manifest": manifest_pretty,
            "manifest_compact": manifest_compact,
        }
    except Exception as exc:
        logger.error("Failed to load Slack manifest: %s", exc)
        return {"ok": False, "error": str(exc)}


def get_version_info() -> dict:
    """Get current version and check for updates.

    Returns:
        {
            "current": str,
            "latest": str | None,
            "has_update": bool,
            "error": str | None
        }
    """
    from vibe import __version__

    return get_latest_version_info(__version__)


def do_upgrade(auto_restart: bool = True) -> dict:
    """Perform upgrade to latest version.

    Args:
        auto_restart: If True, restart vibe after successful upgrade

    Returns:
        {"ok": bool, "message": str, "output": str | None, "restarting": bool}
    """
    current_vibe_path = get_running_vibe_path()
    plan = build_upgrade_plan(vibe_path=current_vibe_path)

    # Use a stable directory as cwd to avoid "Current directory does not exist"
    # errors.  The vibe service process cwd may be inside the uv tool venv
    # directory, which uv deletes and recreates during upgrade.
    safe_cwd = get_safe_cwd()

    try:
        result = subprocess.run(
            plan.command,
            capture_output=True,
            text=True,
            timeout=120,
            env=plan.env,
            cwd=safe_cwd,
        )
        if result.returncode == 0:
            restarting = False
            if auto_restart:
                _spawn_delayed_restart(
                    get_restart_command(vibe_path=current_vibe_path),
                    safe_cwd,
                    env=get_restart_environment(vibe_path=current_vibe_path),
                )
                restarting = True

            return {
                "ok": True,
                "message": "Upgrade successful." + (" Restarting..." if restarting else " Please restart vibe."),
                "output": result.stdout,
                "restarting": restarting,
            }
        else:
            return {
                "ok": False,
                "message": "Upgrade failed",
                "output": result.stderr or result.stdout,
                "restarting": False,
            }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "message": "Upgrade timed out",
            "output": None,
            "restarting": False,
        }
    except Exception as e:
        return {"ok": False, "message": str(e), "output": None, "restarting": False}


def setup_opencode_permission() -> dict:
    """Set OpenCode permission to 'allow' in config file.

    Detection priority (aligned with _load_opencode_user_config):
    1. ~/.config/opencode/opencode.json - if exists and valid JSON/JSONC, update it
    2. ~/.opencode/opencode.json - if exists and valid JSON/JSONC, update it
    3. Create new file at ~/.config/opencode/opencode.json (XDG standard)

    Mirrors _load_opencode_user_config behavior: skips invalid files and tries next.
    If config files exist but none can be parsed, returns an error instead of
    overwriting the existing file contents.

    Returns:
        {"ok": bool, "message": str, "config_path": str}
    """
    config_paths = get_opencode_config_paths(Path.home())
    probe = load_first_opencode_user_config(home=Path.home(), logger_instance=logger)

    if probe.config is not None and probe.path is not None:
        if probe.config.get("permission") == "allow":
            return {
                "ok": True,
                "message": "Permission already set",
                "config_path": str(probe.path),
            }

        try:
            original_content = probe.content
            if original_content is None:
                original_content = probe.path.read_text(encoding="utf-8")

            updated_content = set_jsonc_top_level_string_property(original_content, "permission", "allow")
            probe.path.write_text(updated_content, encoding="utf-8")
            return {
                "ok": True,
                "message": "Permission set to 'allow'",
                "config_path": str(probe.path),
            }
        except Exception as e:
            logger.error(f"Failed to update OpenCode config at {probe.path}: {e}")
            return {"ok": False, "message": str(e), "config_path": str(probe.path)}

    if probe.existing_paths:
        error_path, error_message = (
            probe.errors[0] if probe.errors else (probe.existing_paths[0], "unknown parse error")
        )
        logger.error(f"Refusing to overwrite invalid OpenCode config at {error_path}: {error_message}")
        return {
            "ok": False,
            "message": f"Existing OpenCode config could not be parsed: {error_message}. File left unchanged.",
            "config_path": str(error_path),
        }

    # No existing valid config found, create at XDG path (first in list)
    config_path = config_paths[0]
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps({"permission": "allow"}, indent=2) + "\n", encoding="utf-8")
        return {
            "ok": True,
            "message": "Permission set to 'allow'",
            "config_path": str(config_path),
        }
    except Exception as e:
        logger.error(f"Failed to create OpenCode config: {e}")
        return {"ok": False, "message": str(e), "config_path": str(config_path)}


def parse_claude_agent_file(agent_path: str) -> Optional[dict]:
    """Parse a Claude agent markdown file and extract metadata.

    Agent files have YAML frontmatter and a markdown body:
    ---
    name: agent-name
    description: When to invoke this agent
    tools: Read, Bash, Edit  # Optional
    model: sonnet  # Optional: sonnet, opus, haiku, inherit
    ---
    System prompt content here...

    Returns:
        {
            "name": str,
            "description": str,
            "prompt": str,       # The markdown body (system prompt)
            "tools": list[str],  # Optional
            "model": str,        # Optional
        }
        or None on parse failure
    """
    try:
        content = Path(agent_path).read_text(encoding="utf-8")

        # Check for YAML frontmatter
        if not content.startswith("---"):
            # No frontmatter, use entire content as prompt
            return {
                "name": Path(agent_path).stem,
                "description": f"Agent from {Path(agent_path).name}",
                "prompt": content.strip(),
                "tools": None,
                "model": None,
            }

        # Find the closing ---
        lines = content.split("\n")
        end_idx = -1
        for i, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                end_idx = i
                break

        if end_idx == -1:
            # Malformed frontmatter, use entire content
            return {
                "name": Path(agent_path).stem,
                "description": f"Agent from {Path(agent_path).name}",
                "prompt": content.strip(),
                "tools": None,
                "model": None,
            }

        # Parse YAML frontmatter
        frontmatter_lines = lines[1:end_idx]
        frontmatter_text = "\n".join(frontmatter_lines)

        # Use yaml.safe_load for proper YAML parsing (handles lists, etc.)
        metadata: dict = {}
        try:
            import yaml

            parsed = yaml.safe_load(frontmatter_text)
            if isinstance(parsed, dict):
                metadata = parsed
        except Exception as yaml_err:
            logger.debug(f"YAML parse failed, falling back to simple parsing: {yaml_err}")
            # Fallback to simple key: value parsing
            for line in frontmatter_lines:
                if ":" in line:
                    key, _, value = line.partition(":")
                    key = key.strip()
                    value = value.strip()
                    if key and value:
                        metadata[key] = value

        # Extract body (system prompt)
        body_lines = lines[end_idx + 1 :]
        body = "\n".join(body_lines).strip()

        # Parse tools if present
        tools = None
        if "tools" in metadata:
            tools_val = metadata["tools"]
            if isinstance(tools_val, list):
                # YAML list format: tools:\n  - Read\n  - Bash
                tools = [str(t).strip() for t in tools_val if t]
            elif isinstance(tools_val, str):
                # Inline format: tools: Read, Bash, Edit
                if "," in tools_val:
                    tools = [t.strip() for t in tools_val.split(",") if t.strip()]
                else:
                    tools = [t.strip() for t in tools_val.split() if t.strip()]

        return {
            "name": metadata.get("name", Path(agent_path).stem),
            "description": metadata.get("description", f"Agent from {Path(agent_path).name}"),
            "prompt": body,
            "tools": tools,
            "model": metadata.get("model"),
        }
    except Exception as e:
        logger.warning(f"Failed to parse agent file {agent_path}: {e}")
        return None


def claude_agents(cwd: Optional[str] = None) -> dict:
    """List available Claude Code agents (global + project).

    Claude supports both:
    - Global agents: ~/.claude/agents/*.md
    - Project agents: <cwd>/.claude/agents/*.md (if cwd provided)

    Returns:
        {
            "ok": True,
            "agents": [
                {"id": "reviewer", "name": "reviewer", "path": "/path/to/reviewer.md"},
                ...
            ]
        }
        or {"ok": False, "error": str} on failure
    """
    global_dir = Path.home() / ".claude" / "agents"
    project_dir: Optional[Path] = None
    if cwd:
        try:
            project_dir = Path(cwd).expanduser().resolve() / ".claude" / "agents"
        except Exception:
            project_dir = None

    def _scan_agents(directory: Path, source: str) -> dict[str, dict]:
        if not directory.exists():
            return {}
        if not directory.is_dir():
            return {}
        found: dict[str, dict] = {}
        for agent_file in sorted(directory.glob("*.md")):
            if not agent_file.is_file():
                continue
            agent_id = agent_file.stem
            found[agent_id] = {
                "id": agent_id,
                "name": agent_id,
                "path": str(agent_file),
                "source": source,
            }
        return found

    try:
        # Project overrides global on name collision.
        merged = _scan_agents(global_dir, "global")
        if project_dir is not None:
            merged.update(_scan_agents(project_dir, "project"))
        agents = list(merged.values())
        agents.sort(key=lambda x: (0 if x.get("source") == "project" else 1, x.get("id", "")))
        return {"ok": True, "agents": agents}
    except Exception as e:
        logger.error(f"Failed to scan Claude agents directory: {e}")
        return {"ok": False, "error": str(e)}


def codex_agents(cwd: Optional[str] = None) -> dict:
    """List available Codex custom agents (global + project)."""
    try:
        project_root: Optional[Path] = None
        if cwd:
            try:
                project_root = Path(cwd).expanduser().resolve()
            except Exception:
                project_root = None

        definitions = list_codex_subagents(project_root=project_root)
        agents = [
            {
                "id": definition.name,
                "name": definition.name,
                "path": str(definition.path) if definition.path else "",
                "source": definition.source,
                "description": definition.description,
            }
            for definition in definitions.values()
        ]
        agents.sort(key=lambda item: (0 if item.get("source") == "project" else 1, item.get("id", "")))
        return {"ok": True, "agents": agents}
    except Exception as e:
        logger.error("Failed to scan Codex agents directory: %s", e)
        return {"ok": False, "error": str(e)}


def claude_models() -> dict:
    """Best-effort merged list of Claude Code model options.

    Claude Code does not expose a stable `list models` CLI subcommand.
    We merge suggestions from:
    - The repository-owned Claude model catalog
    - ~/.claude/settings.json model/env values
    """

    def _append_unique(options: list[str], seen: set[str], value: object) -> None:
        if not isinstance(value, str):
            return
        model = value.strip()
        if not model or model in seen:
            return
        seen.add(model)
        options.append(model)

    options: list[str] = []
    seen: set[str] = set()

    for model in load_catalog_models():
        _append_unique(options, seen, model)

    for model in DEFAULT_CLAUDE_MODEL_ALIASES:
        _append_unique(options, seen, model)

    settings_path = Path.home() / ".claude" / "settings.json"
    try:
        if settings_path.exists() and settings_path.is_file():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _append_unique(options, seen, data.get("model"))
                env = data.get("env")
                if isinstance(env, dict):
                    for key in (
                        "ANTHROPIC_MODEL",
                        "ANTHROPIC_SMALL_FAST_MODEL",
                    ):
                        _append_unique(options, seen, env.get(key))
    except Exception as exc:
        logger.warning("Failed to read Claude settings.json: %s", exc, exc_info=True)

    from modules.agents.opencode.utils import build_claude_reasoning_options

    reasoning_options = {"": build_claude_reasoning_options(None)}
    for model in options:
        reasoning_options[model] = build_claude_reasoning_options(model)
    return {"ok": True, "models": options, "reasoning_options": reasoning_options}


def install_agent(name: str) -> dict:
    """Install an agent CLI tool.

    Supported agents:
    - opencode: curl -fsSL https://opencode.ai/install | bash
    - claude: curl -fsSL https://claude.ai/install.sh | bash (macOS/Linux)
              irm https://claude.ai/install.ps1 | iex (Windows)
    - codex: npm install -g @openai/codex

    Returns:
        {"ok": bool, "message": str, "output": str | None}
    """
    import platform

    system = platform.system().lower()

    # Max output size to prevent UI slowdown (last N characters)
    MAX_OUTPUT_CHARS = 8192

    def _check_binary(binary: str) -> str | None:
        """Check if a binary exists in PATH. Returns error message if not found."""
        if resolve_cli_path(binary) is None:
            return f"{binary} is required but not found. Please install it first."
        return None

    def _truncate_output(output: str) -> str:
        """Truncate output to last MAX_OUTPUT_CHARS characters."""
        if len(output) <= MAX_OUTPUT_CHARS:
            return output
        return "...(truncated)\n" + output[-MAX_OUTPUT_CHARS:]

    if name == "opencode":
        # OpenCode: use curl installer (not supported on Windows)
        if system == "windows":
            return {
                "ok": False,
                "message": "OpenCode installer is not supported on Windows. Please use the manual installation method.",
                "output": None,
            }
        # Check prerequisites
        for binary in ["curl", "bash"]:
            error = _check_binary(binary)
            if error:
                return {"ok": False, "message": error, "output": None}
        # Use pipefail to ensure curl failures are detected
        cmd = ["bash", "-c", "set -euo pipefail; curl -fsSL https://opencode.ai/install | bash"]
    elif name == "claude":
        # Claude Code: platform-specific installer
        if system == "windows":
            # Windows: use PowerShell with error handling
            error = _check_binary("powershell")
            if error:
                return {"ok": False, "message": error, "output": None}
            cmd = ["powershell", "-NoProfile", "-Command", "irm https://claude.ai/install.ps1 -ErrorAction Stop | iex"]
        else:
            # macOS/Linux: use bash with pipefail
            for binary in ["curl", "bash"]:
                error = _check_binary(binary)
                if error:
                    return {"ok": False, "message": error, "output": None}
            cmd = ["bash", "-c", "set -euo pipefail; curl -fsSL https://claude.ai/install.sh | bash"]
    elif name == "codex":
        # Codex: prefer npm, fallback to brew on macOS
        npm_path = resolve_cli_path("npm")
        if npm_path:
            cmd = [npm_path, "install", "-g", "@openai/codex"]
        elif system == "darwin":
            # macOS: try brew cask
            brew_path = resolve_cli_path("brew")
            if brew_path:
                cmd = [brew_path, "install", "--cask", "codex"]
            else:
                return {
                    "ok": False,
                    "message": "npm or brew not found. Please install Node.js or Homebrew first.",
                    "output": None,
                }
        else:
            return {
                "ok": False,
                "message": "npm not found. Please install Node.js first.",
                "output": None,
            }
    else:
        return {"ok": False, "message": f"Unknown agent: {name}", "output": None}

    try:
        logger.info("Installing agent %s with command: %s", name, cmd)
        command_env = _command_env_for(cmd[0] if cmd and os.path.isabs(cmd[0]) else None)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout for installation
            env=command_env,
        )
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        output = _truncate_output(output.strip())
        if result.returncode == 0:
            installed_path = resolve_cli_path(name)
            if installed_path:
                logger.info("Agent %s installed successfully at %s", name, installed_path)
            else:
                logger.warning("Agent %s install command succeeded but CLI path was not detected", name)
            # The chip refreshes runtime immediately after upgrade; drop the
            # 30s version cache so it reads the new `--version` instead of the
            # pre-upgrade value.
            _invalidate_version_cache(name)

            # Persist the freshly-discovered install path to V2Config so the
            # next ``get_backend_runtime`` reads it directly instead of
            # relying on the resolver's stale-path fallback. Without this,
            # the stored cli_path drifts whenever an upstream installer
            # moves the binary (real-world example: Claude Code's official
            # ``install.sh`` migrates from ``/usr/local/bin/claude`` —
            # the npm-era path the Dockerfile bootstraps with — to
            # ``~/.local/bin/claude``).
            if installed_path:
                try:
                    with CONFIG_LOCK:
                        try:
                            cfg = load_config()
                        except FileNotFoundError:
                            cfg = V2Config()
                        target = getattr(getattr(cfg, "agents", None), name, None)
                        if target is not None:
                            previous = getattr(target, "cli_path", "") or ""
                            if previous != installed_path:
                                target.cli_path = installed_path
                                cfg.save()
                                logger.info(
                                    "install_agent: updated V2Config cli_path for %s: %s -> %s",
                                    name,
                                    previous or "<unset>",
                                    installed_path,
                                )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "install_agent: failed to persist cli_path for %s: %s",
                        name,
                        exc,
                    )

            return {
                "ok": True,
                "message": f"{name} installed successfully",
                "path": installed_path,
                "output": output,
            }
        else:
            logger.warning("Agent %s installation failed: %s", name, output)
            return {
                "ok": False,
                "message": f"Installation failed (exit code {result.returncode})",
                "output": output,
            }
    except subprocess.TimeoutExpired:
        logger.error("Agent %s installation timed out", name)
        return {"ok": False, "message": "Installation timed out", "output": None}
    except Exception as e:
        logger.error("Agent %s installation error: %s", name, e)
        return {"ok": False, "message": str(e), "output": None}


# =============================================================================
# Backend lifecycle (version probe, latest check, restart)
# =============================================================================

# In-memory caches keyed by (backend, cli_path) so version answers stay tied
# to the binary they came from. Trade freshness for fewer probes during rapid
# popover opens. Tuned for human pacing (seconds), not bots.
#
# The UI server handles requests on multiple threads, so reads, writes, and
# invalidation can race. A single lock serializes mutation — fast in practice
# (the cache holds at most a handful of entries), and avoids
# ``RuntimeError: dictionary changed size during iteration`` during the
# scan in ``_invalidate_version_cache``.
_BACKEND_CACHE_LOCK = __import__("threading").Lock()
_BACKEND_VERSION_CACHE: dict[tuple[str, str], tuple[float, str | None]] = {}
_BACKEND_LATEST_CACHE: dict[str, tuple[float, str | None]] = {}
_BACKEND_VERSION_TTL_SECONDS = 30.0
_BACKEND_LATEST_TTL_SECONDS = 3600.0
# Failed lookups (network down, registry hiccup) re-probe sooner so a
# transient outage doesn't pin "—" for the full hour.
_BACKEND_LATEST_FAILURE_TTL_SECONDS = 120.0
_BACKEND_RUNTIME_USER_AGENT = "vibe-remote/backend-runtime"

_BACKENDS_WITH_RESTART = {"opencode", "codex"}
_BACKEND_LATEST_PROBES = {
    "opencode": ("github", "sst/opencode"),
    "codex": ("npm", "@openai/codex"),
    "claude": ("npm", "@anthropic-ai/claude-code"),
}


def _parse_semver(text: str) -> str | None:
    """Extract the first dotted-numeric version token from *text*.

    Handles outputs like ``opencode 1.2.3``, ``codex-cli 0.77.1 (build ...)``
    and ``v1.0.0`` uniformly. Returns ``None`` if no version is found.
    """
    if not text:
        return None
    match = re.search(r"\d+(?:\.\d+){1,3}(?:[-+][\w.\-]+)?", text)
    return match.group(0) if match else None


def _probe_cli_version(cli_path: str | None) -> str | None:
    """Run ``<cli> --version`` with a short timeout and return the parsed version."""
    if not cli_path:
        return None
    try:
        result = subprocess.run(
            [cli_path, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
            env=_command_env_for(cli_path if os.path.isabs(cli_path) else None),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.debug("CLI version probe failed for %s: %s", cli_path, exc)
        return None
    output = (result.stdout or "") + " " + (result.stderr or "")
    return _parse_semver(output.strip())


def _fetch_latest_version(name: str) -> str | None:
    """Best-effort upstream lookup. Returns ``None`` on any failure."""
    probe = _BACKEND_LATEST_PROBES.get(name)
    if not probe:
        return None
    kind, ident = probe
    url = (
        f"https://api.github.com/repos/{ident}/releases/latest"
        if kind == "github"
        else f"https://registry.npmjs.org/{ident}/latest"
    )
    try:
        from vibe.proxy import resolve_proxy

        proxy = resolve_proxy(None)
    except Exception:
        proxy = None

    req = urllib.request.Request(url, headers={"User-Agent": _BACKEND_RUNTIME_USER_AGENT})
    if proxy and not proxy.lower().startswith("socks"):
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
    else:
        # SOCKS proxies need aiohttp_socks; latest-version probe is best-effort
        # so we silently fall back to direct urlopen rather than complicate the
        # cache path. Direct-connection failures are cached for a short TTL.
        opener = urllib.request.build_opener()

    try:
        with opener.open(req, timeout=5) as resp:  # noqa: S310 - trusted registries
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # pragma: no cover - network failure path
        logger.debug("Latest version probe failed for %s: %s", name, exc)
        return None
    raw = payload.get("tag_name") if kind == "github" else payload.get("version")
    if not isinstance(raw, str):
        return None
    return raw.lstrip("v").strip() or None


def _cached_version(name: str, cli_path: str | None) -> str | None:
    key = (name, cli_path or "")
    with _BACKEND_CACHE_LOCK:
        cached = _BACKEND_VERSION_CACHE.get(key)
    if cached and time.time() - cached[0] < _BACKEND_VERSION_TTL_SECONDS:
        return cached[1]
    # Probe outside the lock — CLI invocation can block on subprocess for
    # seconds, and we don't want unrelated lookups stuck behind it.
    version = _probe_cli_version(cli_path)
    with _BACKEND_CACHE_LOCK:
        _BACKEND_VERSION_CACHE[key] = (time.time(), version)
    return version


def _invalidate_version_cache(name: str) -> None:
    """Drop all cached version entries for *name* across cli paths."""
    with _BACKEND_CACHE_LOCK:
        # Snapshot keys under the lock so the subsequent ``pop`` calls can
        # never observe a partially mutated dict from a concurrent writer.
        stale = [k for k in _BACKEND_VERSION_CACHE if k[0] == name]
        for key in stale:
            _BACKEND_VERSION_CACHE.pop(key, None)


def _cached_latest(name: str) -> str | None:
    with _BACKEND_CACHE_LOCK:
        cached = _BACKEND_LATEST_CACHE.get(name)
    if cached:
        ttl = _BACKEND_LATEST_TTL_SECONDS if cached[1] else _BACKEND_LATEST_FAILURE_TTL_SECONDS
        if time.time() - cached[0] < ttl:
            return cached[1]
    # Network fetch outside the lock — same reasoning as ``_cached_version``.
    latest = _fetch_latest_version(name)
    with _BACKEND_CACHE_LOCK:
        _BACKEND_LATEST_CACHE[name] = (time.time(), latest)
    return latest


def _compare_versions(current: str | None, latest: str | None) -> bool:
    """Return True when *latest* is strictly greater than *current*.

    Honors PEP 440 / semver pre-release ordering when possible (e.g. ``0.77.1``
    is greater than ``0.77.1-beta.0``). Falls back to a conservative numeric
    tuple comparison; returns False on any parsing failure so we never nag the
    user with a phantom update.
    """
    if not current or not latest or current == latest:
        return False

    try:
        from packaging.version import InvalidVersion, Version

        try:
            return Version(latest) > Version(current)
        except InvalidVersion:
            pass
    except Exception:  # pragma: no cover - packaging is a transitive dep
        pass

    def _parts(value: str) -> tuple[tuple[int, ...], bool] | None:
        # Strip build metadata; keep pre-release tag to compare lexically.
        core, _, pre = value.split("+", 1)[0].partition("-")
        try:
            nums = tuple(int(part) for part in core.split("."))
        except ValueError:
            return None
        # A version with a pre-release suffix is "less than" the bare release.
        return nums, bool(pre)

    cur_parts = _parts(current)
    new_parts = _parts(latest)
    if cur_parts is None or new_parts is None:
        return False
    cur_nums, cur_is_pre = cur_parts
    new_nums, new_is_pre = new_parts
    if new_nums != cur_nums:
        return new_nums > cur_nums
    # Same numeric core: pre-release sorts before release.
    return cur_is_pre and not new_is_pre


def _opencode_server_pid() -> int | None:
    pid_path = paths.get_logs_dir() / "opencode_server.json"
    if not pid_path.exists():
        return None
    try:
        info = json.loads(pid_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    pid = info.get("pid") if isinstance(info, dict) else None
    return pid if isinstance(pid, int) and pid > 0 else None


def _opencode_process_status() -> str:
    from vibe import runtime

    pid = _opencode_server_pid()
    if not pid or not runtime.pid_alive(pid):
        return "stopped"
    cmd = runtime.get_process_command(pid) or ""
    return "running" if "opencode" in cmd and "serve" in cmd else "unknown"


def _process_matches_codex_binary(cmdline: list[str], resolved_binary: str | None) -> bool:
    """Decide whether ``cmdline`` belongs to *our* codex app-server.

    The original cmdline-substring check matched any ``codex`` mention in any
    argument (e.g. a user invoking ``codex app-server`` themselves from a
    shell, or another tool whose args happen to contain those tokens), and
    a follow-up tightening only inspected ``argv[0]`` — which missed the
    ``npm install -g @openai/codex`` shim, where the live process is
    ``node /path/.../bin/codex app-server`` (``argv[0] == "node"``).

    We now scan the first few argv tokens (``argv[0]`` and ``argv[1]``)
    looking for the codex binary itself, since the kernel preserves the
    script path as ``argv[1]`` whenever a ``#!/usr/bin/env node`` shim is
    exec'd. The match requires:

      1. one of ``argv[0]``/``argv[1]`` resolves to the same absolute path
         as the configured codex binary (or, when no resolved binary is
         known, has basename starting with ``codex``); and
      2. one of the early arguments is exactly ``app-server``.
    """
    if not cmdline:
        return False
    try:
        target = (
            str(Path(resolved_binary).expanduser().resolve())
            if resolved_binary
            else None
        )
    except Exception:
        target = resolved_binary

    def _matches(token: str) -> bool:
        try:
            resolved = str(Path(token).expanduser().resolve())
        except Exception:
            resolved = token
        if target is not None:
            # When we know the configured binary, require an exact match
            # against either the raw or the resolved token. A second codex
            # install elsewhere on the system is *not* ours to kill.
            return resolved == target or token == target
        # No resolved binary: best-effort basename match so the chip still
        # works when the configured CLI isn't on PATH right now.
        return os.path.basename(resolved).startswith("codex")

    # Check argv[0] and argv[1] — the latter is where ``node`` shebang shims
    # land the codex script path. We deliberately stop at argv[1] so an
    # unrelated tool with ``codex`` mentioned later in its args isn't swept up.
    if not any(_matches(tok) for tok in cmdline[:2] if tok):
        return False
    # ``codex app-server`` always passes ``app-server`` as an argv token; we
    # intentionally do NOT match it inside an arbitrary substring. Widen the
    # window slightly so the node-shim layout (``node script app-server``)
    # still hits.
    return "app-server" in cmdline[1:5]


def _codex_processes(resolved_binary: str | None) -> list[int]:
    """Find live ``codex app-server`` subprocesses owned by the current user.

    The match must hit our resolved codex binary so unrelated tools that
    happen to mention ``codex`` and ``app-server`` aren't swept up.
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover - psutil is a hard dep elsewhere
        return []

    # ``uids`` is a POSIX-only psutil attribute; requesting it on Windows
    # makes ``process_iter`` raise ``ValueError: invalid attr name 'uids'``
    # and the entire probe blows up. Gate it on ``getuid`` availability,
    # which is the same signal we use to decide whether to filter at all.
    current_uid = os.getuid() if hasattr(os, "getuid") else None
    attrs = ["pid", "name", "cmdline"]
    if current_uid is not None:
        attrs.append("uids")
    pids: list[int] = []
    for proc in psutil.process_iter(attrs=attrs):
        try:
            info = proc.info
            cmdline = info.get("cmdline") or []
            if not _process_matches_codex_binary(cmdline, resolved_binary):
                continue
            if current_uid is not None:
                uids = info.get("uids")
                proc_uid = getattr(uids, "real", None) if uids else None
                if proc_uid is not None and proc_uid != current_uid:
                    continue
            pids.append(info["pid"])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


def _codex_process_status(resolved_binary: str | None) -> str:
    return "running" if _codex_processes(resolved_binary) else "stopped"


def get_backend_runtime(name: str) -> dict:
    """Return live lifecycle info for one backend.

    Versions are cached for short windows so popovers and re-renders do not
    fan out into many CLI invocations or registry HTTP calls.
    """
    if name not in _BACKEND_LATEST_PROBES:
        return {"ok": False, "error": f"Unknown backend: {name}"}

    try:
        config = V2Config.load()
    except Exception as exc:
        logger.debug("Failed to load config for backend runtime: %s", exc)
        config = None

    backend_cfg = getattr(getattr(config, "agents", None), name, None) if config else None
    enabled = bool(getattr(backend_cfg, "enabled", False))
    configured_path = getattr(backend_cfg, "cli_path", "") or name

    resolved_path = resolve_cli_path(configured_path)
    installed = resolved_path is not None

    current_version = _cached_version(name, resolved_path) if installed else None
    latest_version = _cached_latest(name)
    has_update = _compare_versions(current_version, latest_version)

    if name == "opencode":
        process_status = _opencode_process_status() if installed else "stopped"
    elif name == "codex":
        process_status = _codex_process_status(resolved_path) if installed else "stopped"
    else:
        process_status = "unknown"

    return {
        "ok": True,
        "name": name,
        "enabled": enabled,
        "cli_path": configured_path,
        "resolved_path": resolved_path,
        "installed": installed,
        "current_version": current_version,
        "latest_version": latest_version,
        "has_update": has_update,
        "supports_restart": name in _BACKENDS_WITH_RESTART,
        "process_status": process_status,
    }


def _runtime_command_dir() -> Path:
    """Directory the controller watches for cross-process command markers."""
    base = paths.get_state_dir() / "runtime_commands"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _wait_for_controller_ack(marker: Path, timeout: float) -> tuple[bool, str | None]:
    """Poll for ``marker`` removal as a signal that the controller ran it.

    Returns ``(handled, error)``:

    - ``handled=True, error=None`` — controller picked up the marker and the
      handler returned cleanly.
    - ``handled=True, error="..."`` — controller picked up the marker but
      the handler raised; the controller wrote the message to a companion
      ``<marker>.err`` file before deleting the request marker.
    - ``handled=False, error=None`` — timed out; the controller never
      consumed the marker. Caller should fall back to a direct kill.

    The companion ``.err`` file is consumed (unlinked) before returning so
    later requests start clean.
    """
    err_marker = marker.with_name(marker.name + ".err")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not marker.exists():
            error: str | None = None
            if err_marker.exists():
                try:
                    error = err_marker.read_text(encoding="utf-8").strip() or "unknown error"
                except OSError:
                    error = "unknown error"
                try:
                    err_marker.unlink(missing_ok=True)
                except OSError:  # pragma: no cover - best-effort cleanup
                    pass
            return True, error
        time.sleep(0.1)
    return False, None


def _request_controller_restart(backend: str, timeout: float = 4.0) -> tuple[bool, str | None]:
    """Ask the controller to refresh a backend via the runtime-command marker.

    The controller's ``RuntimeCommandWatcher`` (see ``core/runtime_commands.py``)
    is the in-process owner of ``CodexAgent._transports`` / OpenCode server
    state. Killing those processes from the UI server would leave that cache
    stale, so the cleanest path is to ask the controller to call its existing
    ``_refresh_backend_runtime(backend)`` for us. We drop a marker file and
    wait briefly for the controller to delete it; the caller falls back to a
    direct process kill when the controller is unreachable (e.g. running
    detached, not yet started).

    Each request gets its own marker filename (``restart-<backend>.<reqid>.cmd``)
    so we can correlate failures back to *this* request. Without the reqid,
    a stale ``.err`` from a prior request that timed out caller-side — or an
    overlapping concurrent restart — could be mistaken for *our* failure and
    surface a phantom error toast.

    Returns ``(handled, error)`` — see ``_wait_for_controller_ack`` for the
    contract. ``handled=True`` does *not* imply success; check ``error`` too
    so the UI toast doesn't claim a restart when the controller's refresh
    actually raised.
    """
    reqid = uuid.uuid4().hex[:8]
    marker = _runtime_command_dir() / f"restart-{backend}.{reqid}.cmd"
    try:
        marker.write_text(
            json.dumps({"backend": backend, "ts": time.time(), "reqid": reqid}),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.debug("Failed to write controller restart marker for %s: %s", backend, exc)
        return False, None
    handled, error = _wait_for_controller_ack(marker, timeout)
    if handled:
        return True, error
    # Marker still present — controller didn't pick it up. Clean up the
    # request marker *and* any stray ``.err`` so the next attempt starts
    # from a clean slate.
    try:
        marker.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        marker.with_name(marker.name + ".err").unlink(missing_ok=True)
    except OSError:
        pass
    return False, None


def restart_backend(name: str) -> dict:
    """Refresh the backend so the next request picks up new config/env.

    Preferred path: drop a runtime-command marker that the controller
    observes and reacts to via ``_refresh_backend_runtime``. This keeps the
    controller's in-memory transport/session state consistent. If the
    controller isn't running (e.g. service not yet started), we fall back to
    killing the OS process directly — the controller's recovery logic will
    rebuild state when it next starts.

    Claude has no persistent process and is rejected at the route layer.
    """
    if name not in _BACKENDS_WITH_RESTART:
        return {"ok": False, "message": f"Restart is not supported for backend: {name}"}

    controller_handled, controller_error = _request_controller_restart(name)
    _invalidate_version_cache(name)

    if controller_handled:
        if controller_error:
            # Controller saw the request and ran the handler, but the handler
            # raised. Don't lie to the user — surface the failure so they can
            # retry or look at logs. (The next runtime probe will also reflect
            # the stale state, but the toast must already say so.)
            return {
                "ok": False,
                "message": f"Backend refresh failed: {controller_error}",
            }
        if name == "opencode":
            return {"ok": True, "message": "OpenCode server refreshed; it will respawn on next request."}
        return {"ok": True, "message": "Codex runtime refreshed; transports will respawn on next request."}

    if name == "opencode":
        from vibe import runtime
        from vibe.cli import _stop_opencode_server

        stopped = _stop_opencode_server()
        if stopped:
            return {"ok": True, "message": "OpenCode server stopped; it will respawn on next request."}
        pid = _opencode_server_pid()
        if not pid or not runtime.pid_alive(pid):
            return {"ok": True, "message": "OpenCode server is not running; next request will start a fresh one."}
        return {"ok": False, "message": "Failed to stop OpenCode server."}

    # codex fallback: kill app-server processes; controller recovery rebuilds.
    try:
        import psutil
    except ImportError:
        return {"ok": False, "message": "psutil unavailable; cannot manage Codex processes."}

    try:
        config = V2Config.load()
        backend_cfg = getattr(getattr(config, "agents", None), "codex", None)
        configured = getattr(backend_cfg, "cli_path", "") or "codex"
    except Exception:
        configured = "codex"
    resolved = resolve_cli_path(configured)

    pids = _codex_processes(resolved)
    if not pids:
        return {"ok": True, "message": "Codex app-server is not running; next request will start a fresh one."}

    failed: list[int] = []
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except psutil.TimeoutExpired:
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            logger.debug("Codex restart skip pid=%s: %s", pid, exc)
        except Exception as exc:
            logger.warning("Failed to stop codex pid=%s: %s", pid, exc)
            failed.append(pid)
    if failed:
        return {"ok": False, "message": f"Failed to stop Codex process(es): {failed}"}
    return {"ok": True, "message": f"Stopped {len(pids)} Codex process(es); they will respawn on next request."}


_VALID_AUTH_MODES = {"oauth", "api_key"}


def _mask_api_key(api_key: str | None) -> str | None:
    """Return a UI-safe preview of an API key.

    Pattern: keep the prefix up to (and including) the first ``-`` block
    (e.g. ``sk-proj-``, ``sk-ant-``) so the user can still recognize
    the key type, then dots, then the last 4 characters. Short keys
    (<= 12 chars) get a uniform 6-dots-plus-last-4 pattern so we never
    accidentally render plaintext for a malformed key.
    """
    if not isinstance(api_key, str) or not api_key.strip():
        return None
    key = api_key.strip()
    last4 = key[-4:]
    if len(key) > 12 and "-" in key:
        # Take the recognizable prefix up to and including the second dash
        # (handles both ``sk-...`` and ``sk-proj-...`` shapes).
        first_dash = key.find("-")
        second_dash = key.find("-", first_dash + 1)
        prefix_end = second_dash + 1 if second_dash != -1 else first_dash + 1
        prefix = key[:prefix_end]
        return f"{prefix}{'•' * 9}{last4}"
    return f"{'•' * 6}{last4}"


# ---------------------------------------------------------------------------
# Web Settings → Backends OAuth flow plumbing
#
# Mirrors the IM ``/setup`` flow but runs in the UI server's own process.
# OAuth subprocess + Claude SDK client require a long-lived event loop —
# Flask routes are sync, so we host one on a dedicated thread and bridge
# every call with ``run_coroutine_threadsafe``. On success we drop a
# ``restart-<backend>.cmd`` marker so the live controller refreshes its
# in-process agent state (mirroring what ``_refresh_backend_runtime`` does
# in-process for IM-driven flows).
# ---------------------------------------------------------------------------


class _WebControllerStub:
    """Minimal ``Controller``-shaped facade for the web OAuth flow service.

    ``AgentAuthService`` only touches ``controller.config`` (for
    ``cli_path``) and gracefully no-ops when ``agent_service`` /
    ``session_handler`` are absent. The stub re-reads V2Config from disk on
    every access so a freshly-saved ``cli_path`` is picked up on the next
    flow without restarting the UI server.
    """

    @property
    def config(self):
        return load_config()

    # The following attributes are inspected via ``getattr(..., None)`` in
    # ``AgentAuthService`` and gate platform-specific paths that web flows
    # never traverse (IM message dispatch, session lookup, agent refresh).
    agent_service = None
    session_handler = None
    im_client = None


_oauth_service_lock = threading.Lock()
_oauth_service: Any = None
_oauth_loop: Any = None
_oauth_loop_thread: Any = None


def _start_oauth_event_loop() -> Any:
    loop = asyncio.new_event_loop()

    def _runner() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    thread = threading.Thread(target=_runner, daemon=True, name="vibe-oauth-loop")
    thread.start()
    return loop, thread


def _on_web_auth_success(backend: str) -> None:
    """Tell the live controller to refresh its agent after web OAuth success."""
    try:
        handled, err = _request_controller_restart(backend, timeout=4.0)
        if handled and err:
            logger.warning("Controller refresh after web auth reported error: %s", err)
        elif not handled:
            logger.info("Controller did not pick up web-auth refresh marker for %s", backend)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to notify controller after web auth: %s", exc)


def _get_oauth_service() -> Any:
    """Lazily build the (singleton) AgentAuthService for web flows."""
    global _oauth_service, _oauth_loop, _oauth_loop_thread
    with _oauth_service_lock:
        if _oauth_service is not None:
            return _oauth_service
        from core.agent_auth_service import AgentAuthService

        _oauth_loop, _oauth_loop_thread = _start_oauth_event_loop()
        controller = _WebControllerStub()
        _oauth_service = AgentAuthService(controller)
        _oauth_service._post_web_success_hook = _on_web_auth_success
        return _oauth_service


def _submit_oauth_coro(coro, *, timeout: float = 30.0):
    service = _get_oauth_service()  # ensures loop  # noqa: F841
    future = asyncio.run_coroutine_threadsafe(coro, _oauth_loop)
    return future.result(timeout=timeout)


def _serialize_web_flow_status(payload: dict) -> dict:
    """Strip server-only keys before returning to the browser."""
    if not isinstance(payload, dict):
        return {"ok": False, "error": "invalid_payload"}
    return payload


_WEB_OAUTH_BACKENDS = {"claude", "codex", "opencode"}


def start_oauth_web(
    backend: str,
    force_reset: bool = True,
    provider_id: Optional[str] = None,
) -> dict:
    backend = (backend or "").strip().lower()
    if backend not in _WEB_OAUTH_BACKENDS:
        return {"ok": False, "error": "unsupported_backend"}
    if backend == "opencode" and not (isinstance(provider_id, str) and provider_id.strip()):
        return {"ok": False, "error": "opencode_provider_id_required"}
    service = _get_oauth_service()
    try:
        flow = _submit_oauth_coro(
            service.start_web_setup(
                backend,
                force_reset=force_reset,
                provider_id=(provider_id.strip() if isinstance(provider_id, str) else None),
            ),
            timeout=60.0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Web OAuth start failed for %s: %s", backend, exc, exc_info=True)
        return {"ok": False, "error": "start_failed", "detail": str(exc)}

    if flow.state == "failed":
        return {
            "ok": False,
            "error": flow.error or "start_failed",
            "flow_id": flow.flow_id,
        }
    return {
        "ok": True,
        "flow_id": flow.flow_id,
        "backend": flow.backend,
        "state": flow.state,
        "url": flow.url,
        "device_code": flow.device_code,
        "awaiting_code": flow.awaiting_code,
        "provider": flow.provider,
    }


def get_oauth_web_status(flow_id: str) -> dict:
    flow_id = (flow_id or "").strip()
    if not flow_id:
        return {"ok": False, "error": "missing_flow_id"}
    service = _get_oauth_service()
    return _serialize_web_flow_status(service.get_web_flow_status(flow_id))


def submit_oauth_web_code(flow_id: str, code: str) -> dict:
    flow_id = (flow_id or "").strip()
    if not flow_id:
        return {"ok": False, "error": "missing_flow_id"}
    service = _get_oauth_service()
    try:
        return _submit_oauth_coro(
            service.submit_web_code(flow_id, code or ""),
            timeout=30.0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Web OAuth code submit failed: %s", exc, exc_info=True)
        return {"ok": False, "error": "submit_failed", "detail": str(exc)}


def remove_backend_auth(backend: str) -> dict:
    """Clear stored credentials for Claude or Codex (web Settings)."""
    backend = (backend or "").strip().lower()
    if backend not in _WEB_OAUTH_BACKENDS:
        return {"ok": False, "error": "unsupported_backend"}
    service = _get_oauth_service()
    try:
        return _submit_oauth_coro(service.remove_web_auth(backend), timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        logger.error("Web auth remove failed for %s: %s", backend, exc, exc_info=True)
        return {"ok": False, "error": "remove_failed", "detail": str(exc)}


def remove_backend_api_key(backend: str) -> dict:
    """Clear the stored API key for Claude / Codex without touching OAuth.

    Mirrors OpenCode's "Remove key" vs "Sign out" split: Claude and
    Codex can both carry ``api_key`` *and* OAuth credentials at the
    same time, and the CLI picks api_key when both are present. Without
    a way to drop just the API key, a stale or rejected key keeps
    forcing 401s even after the user signed in via OAuth.

    - **Codex**: re-applies ``apply_codex_auth(auth_mode='oauth')``
      which pops ``OPENAI_API_KEY`` from ``~/.codex/auth.json`` and
      keeps any ``tokens`` blob intact. V2Config's
      ``agents.codex.api_key`` is also cleared and ``auth_mode`` is
      flipped to ``oauth``. Triggers ``restart_backend('codex')`` so
      the persistent daemon reloads.
    - **Claude**: V2Config is the sole writer; we clear
      ``agents.claude.api_key`` + ``base_url`` and flip ``auth_mode``
      to ``oauth``. ``~/.claude/credentials.json`` (the OAuth token
      file) is left alone. No daemon to restart — Claude relaunches
      per request.
    """
    backend = (backend or "").strip().lower()
    if backend not in {"claude", "codex"}:
        return {"ok": False, "error": "unsupported_backend"}

    notices: list = []
    if backend == "codex":
        from vibe.codex_config import apply_codex_auth

        try:
            result = apply_codex_auth(auth_mode="oauth", api_key=None, base_url=None)
            if isinstance(result, dict):
                raw_notices = result.get("notices")
                if isinstance(raw_notices, list):
                    notices = raw_notices
        except Exception as exc:  # noqa: BLE001
            logger.error("apply_codex_auth(oauth) during remove-key failed: %s", exc, exc_info=True)
            return {"ok": False, "error": "remove_failed", "detail": str(exc)}

    # Clear V2Config api_key for both backends.
    try:
        with CONFIG_LOCK:
            try:
                config = load_config()
            except FileNotFoundError:
                config = V2Config()
            target = getattr(getattr(config, "agents", None), backend, None)
            if target is not None:
                target.auth_mode = "oauth"
                target.api_key = None
                if backend == "codex":
                    target.base_url = None
                config.save()
    except Exception as exc:  # noqa: BLE001
        logger.warning("V2Config clear during remove-key failed for %s: %s", backend, exc)

    # Codex has a persistent daemon — refresh it so the cleared key
    # actually takes effect on the next request. Claude is one-shot per
    # request so a synthetic restart is enough.
    restart: dict
    if backend == "codex":
        try:
            restart = restart_backend("codex")
        except Exception as exc:  # noqa: BLE001
            restart = {"ok": False, "message": str(exc)}
    else:
        restart = {
            "ok": True,
            "message": "Claude relaunches per request; the next message uses the new auth.",
        }
    response: dict = {"ok": True, "restart": restart}
    if notices:
        response["notices"] = notices
    return response


def test_backend_auth(backend: str, model: Optional[str] = None) -> dict:
    """Send a single-token ``Hi`` probe through the backend CLI.

    ``model`` lets the caller override the CLI's configured default —
    important for Codex users whose ``config.toml`` selects a slow
    reasoning model, where even "Hi" can blow past the test timeout.
    """
    backend = (backend or "").strip().lower()
    if backend not in _WEB_OAUTH_BACKENDS:
        return {"ok": False, "error": "unsupported_backend"}
    service = _get_oauth_service()
    try:
        return _submit_oauth_coro(
            service.test_web_auth(backend, model=model),
            timeout=60.0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Web auth test failed for %s: %s", backend, exc, exc_info=True)
        return {"ok": False, "error": "test_failed", "detail": str(exc)}


def test_opencode_provider(provider_id: str, model: Optional[str] = None) -> dict:
    """Probe a single OpenCode provider over the live ``opencode serve`` HTTP API.

    OpenCode users typically wire up multiple providers (OpenAI, Poe,
    Anthropic, ...) but only a few will be active at any time. A single
    backend-wide button would either spuriously fail when one is broken
    or hide which one works. Per-provider probes echo the model's
    response so the user knows the round-trip actually returned text.
    """
    provider_id = (provider_id or "").strip()
    if not provider_id:
        return {"ok": False, "error": "missing_provider"}
    service = _get_oauth_service()
    try:
        return _submit_oauth_coro(
            service.test_opencode_provider(provider_id, model=model),
            timeout=90.0,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "OpenCode provider test failed for %s: %s",
            provider_id,
            exc,
            exc_info=True,
        )
        return {"ok": False, "error": "test_failed", "detail": str(exc)}


def cancel_oauth_web(flow_id: str) -> dict:
    flow_id = (flow_id or "").strip()
    if not flow_id:
        return {"ok": False, "error": "missing_flow_id"}
    service = _get_oauth_service()
    try:
        return _submit_oauth_coro(service.cancel_web_flow(flow_id), timeout=15.0)
    except Exception as exc:  # noqa: BLE001
        logger.error("Web OAuth cancel failed: %s", exc, exc_info=True)
        return {"ok": False, "error": "cancel_failed", "detail": str(exc)}


def get_codex_auth() -> dict:
    """Return the user-facing Codex auth state for the Settings UI.

    Merges two sources of truth:
    - on-disk ``~/.codex/{config.toml,auth.json}`` (what Codex actually
      reads at launch) — authoritative for ``has_api_key`` / ``base_url`` /
      ``has_chatgpt_tokens`` so the UI never lies when the user edited the
      files by hand.
    - ``V2Config.agents.codex`` — the mode we *intend* to be in, useful as
      a tiebreaker (e.g. user clicked OAuth but hasn't run ``codex login``
      yet, so disk has no tokens but our config says oauth).

    Secrets never leave the server; only length is returned.
    """
    from vibe.codex_config import read_codex_auth_state

    disk_state = read_codex_auth_state()
    try:
        config = load_config()
        cfg = getattr(getattr(config, "agents", None), "codex", None)
        configured_mode = getattr(cfg, "auth_mode", None)
    except Exception:
        configured_mode = None

    # Disk wins when it carries unambiguous evidence of API-key auth: an
    # ``OPENAI_API_KEY`` in ``~/.codex/auth.json`` is a concrete artefact
    # the user (or a prior ``codex login`` flow) placed there. ``V2Config``
    # may still default ``auth_mode`` to ``"oauth"`` on the upgrade path
    # (older configs lacked the field entirely), so trusting the config
    # alone would make the UI render OAuth and a subsequent save would
    # then wipe ``OPENAI_API_KEY``. Configured mode remains the source of
    # truth only when disk has no key — i.e., the user's stated intent
    # before they have signed in or pasted credentials.
    if disk_state.get("has_api_key"):
        auth_mode: str | None = "api_key"
    elif configured_mode in _VALID_AUTH_MODES:
        auth_mode = configured_mode
    else:
        auth_mode = disk_state.get("auth_mode")
    # The *active* auth source the running Codex CLI uses at launch is
    # determined entirely by ``~/.codex/auth.json``: a stored API key wins;
    # else ChatGPT tokens; else "not configured". This is what the user
    # cares about ("which one is actually working"), separate from the
    # ``auth_mode`` field above (which is the *intent* we'd save next).
    has_api_key_live = bool(disk_state.get("has_api_key"))
    has_chatgpt_live = bool(disk_state.get("has_chatgpt_tokens"))
    if has_api_key_live:
        active_auth_mode = "api_key"
    elif has_chatgpt_live:
        active_auth_mode = "oauth"
    else:
        active_auth_mode = "none"

    return {
        "ok": True,
        "auth_mode": auth_mode or "oauth",
        "active_auth_mode": active_auth_mode,
        "has_api_key": has_api_key_live,
        "api_key_length": int(disk_state.get("api_key_length") or 0),
        "api_key_masked": _mask_api_key(disk_state.get("api_key_raw")),
        "base_url": disk_state.get("base_url"),
        "has_chatgpt_tokens": has_chatgpt_live,
        "chatgpt_account": disk_state.get("chatgpt_account"),
        # Forward the live Codex credentials-store status so the UI can
        # warn when the user is about to switch storage backends
        # (Codex's documented default is ``auto`` → keyring-preferred).
        # Dropping these here was the bug: the React page would treat
        # ``file_store_active`` as undefined and surface a keyring
        # warning even when the store is already ``file``.
        "credentials_store": disk_state.get("credentials_store") or "auto",
        "file_store_active": bool(disk_state.get("file_store_active")),
        # Surface "we can't read your key — it may live in the OS
        # keychain" so the UI doesn't claim "no key configured" when
        # Codex is in keyring-preferred mode and we have no disk
        # evidence. We suppress the flag when V2Config has a stored
        # ``auth_mode`` (the user already saved through our flow), since
        # we then know the mode and the next save will pin file storage.
        "auth_mode_uncertain": (
            bool(disk_state.get("auth_mode_uncertain"))
            and configured_mode not in _VALID_AUTH_MODES
        ),
    }


def save_codex_auth(payload: dict) -> dict:
    """Persist Codex auth: V2Config + ``~/.codex/{config.toml,auth.json}``.

    The on-disk write is what Codex actually reads; the V2Config write
    records the user's intent so the UI can render a coherent state after
    restart. We treat the disk write as authoritative — if it fails, we
    surface the error instead of leaving V2Config out of sync.

    After writing, we trigger ``restart_backend('codex')`` so the persistent
    app-server reloads with the new credentials. The restart failure is
    surfaced but does not roll back the config write; the user can retry.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "message": "Payload must be an object"}

    auth_mode = payload.get("auth_mode")
    if auth_mode not in _VALID_AUTH_MODES:
        return {"ok": False, "message": f"auth_mode must be one of {sorted(_VALID_AUTH_MODES)}"}

    raw_api_key = payload.get("api_key")
    if raw_api_key is not None and not isinstance(raw_api_key, str):
        return {"ok": False, "message": "api_key must be a string"}
    api_key = raw_api_key.strip() if isinstance(raw_api_key, str) else None

    # Three-state ``base_url`` payload (matches the OpenCode provider save
    # handler and the new web Settings → OAuth flow): omitting the key
    # means "leave the stored value alone" so toggling auth_mode does not
    # accidentally clear a relay URL the user had set up in api_key mode.
    base_url_present = "base_url" in payload
    raw_base_url = payload.get("base_url") if base_url_present else None
    if base_url_present and raw_base_url is not None and not isinstance(raw_base_url, str):
        return {"ok": False, "message": "base_url must be a string"}
    base_url_change: Optional[str] = None
    if base_url_present:
        base_url_change = raw_base_url.strip() if isinstance(raw_base_url, str) else None
        if base_url_change == "":
            base_url_change = None

    if auth_mode == "api_key" and not api_key:
        # Allow callers to PATCH base_url alone by reusing the stored key.
        # ``auth.json`` is the live source Codex reads at launch, and it
        # captures keys rotated outside this flow (e.g. ``codex login
        # --with-api-key``). The V2Config cache can be stale relative to
        # disk, so trusting it first would silently revert a freshly
        # rotated key when we re-write ``auth.json`` below. Prefer disk;
        # fall back to V2Config only if disk has nothing (legacy installs
        # that never wrote ``auth.json``).
        try:
            from vibe.codex_config import read_codex_api_key

            api_key = read_codex_api_key()
        except Exception:
            api_key = None
        if not api_key:
            with CONFIG_LOCK:
                try:
                    existing = load_config()
                    stored = getattr(getattr(existing, "agents", None), "codex", None)
                    api_key = getattr(stored, "api_key", None) or None
                except Exception:
                    api_key = None
        if not api_key:
            return {"ok": False, "message": "api_key is required when auth_mode='api_key'"}

    # Resolve the effective base_url: explicit payload wins, otherwise
    # preserve whatever V2Config currently has.
    if base_url_present:
        effective_base_url = base_url_change
    else:
        with CONFIG_LOCK:
            try:
                existing_cfg = load_config()
                stored_codex = getattr(getattr(existing_cfg, "agents", None), "codex", None)
                effective_base_url = getattr(stored_codex, "base_url", None) or None
            except Exception:
                effective_base_url = None

    from vibe.codex_config import apply_codex_auth

    notices: list = []
    try:
        result = apply_codex_auth(
            auth_mode=auth_mode, api_key=api_key, base_url=effective_base_url
        )
        if isinstance(result, dict):
            raw_notices = result.get("notices")
            if isinstance(raw_notices, list):
                notices = raw_notices
    except ValueError as exc:
        return {"ok": False, "message": str(exc)}
    except OSError as exc:
        logger.error("Failed to write Codex auth files: %s", exc, exc_info=True)
        return {"ok": False, "message": f"Failed to write Codex config: {exc}"}

    with CONFIG_LOCK:
        try:
            config = load_config()
        except FileNotFoundError:
            config = V2Config()
        config.agents.codex.auth_mode = auth_mode
        config.agents.codex.api_key = api_key if auth_mode == "api_key" else None
        config.agents.codex.base_url = effective_base_url
        config.save()

    restart_result = restart_backend("codex")
    state = get_codex_auth()
    state["restart"] = restart_result
    if notices:
        # Surface non-fatal config-rewrite notices (e.g. "we cleared a
        # custom relay pointer because OAuth tokens won't validate
        # against ai-relay.chainbot.io") so the UI can show a one-time
        # banner. Without this the user sees a green "saved" toast then
        # hits a confusing 401 on their next request.
        state["notices"] = notices
    if not restart_result.get("ok", False):
        # Config written, restart failed — tell the UI both so the toast
        # can say "saved, but you may need to restart Codex manually".
        state["ok"] = True
        state["message"] = restart_result.get("message")
    return state


def get_claude_auth() -> dict:
    """Return the user-facing Claude auth state for the Settings UI.

    Claude differs from Codex in two structural ways:

    1. We never write to ``~/.claude/settings.json``. V2Config is the sole
       writer; the Claude CLI subprocess inherits ``ANTHROPIC_*`` vars
       from the env we set in ``session_handler.py`` at launch time.
    2. OAuth tokens minted by ``claude login`` live in the OS keychain,
       which we cannot portably inspect. The "OAuth signed in" signal is
       therefore inferred from "no API key is configured" — the UI shows
       a hint pointing users at ``claude login`` if they need to switch.

    We still inspect ``settings.json`` because the user (or their tooling)
    may have put ``ANTHROPIC_AUTH_TOKEN`` / ``ANTHROPIC_BASE_URL`` there.
    When that happens *and* V2Config also has a key, settings.json wins
    at launch (Claude Code applies its ``env`` block on top of inherited
    env). The UI surfaces a warning so users aren't confused by stale
    keys silently overriding what they just saved.
    """
    from vibe.claude_config import (
        read_claude_auth_state,
        read_claude_oauth_signed_in,
        read_claude_settings_env,
    )

    disk_state = read_claude_auth_state()
    oauth_signed_in = read_claude_oauth_signed_in()
    settings_env = read_claude_settings_env()
    settings_key = settings_env.get("ANTHROPIC_API_KEY") or settings_env.get("ANTHROPIC_AUTH_TOKEN") or ""
    settings_base = settings_env.get("ANTHROPIC_BASE_URL") or ""

    try:
        config = load_config()
        cfg = getattr(getattr(config, "agents", None), "claude", None)
        configured_mode = getattr(cfg, "auth_mode", None)
        configured_key = getattr(cfg, "api_key", None) or ""
        configured_base = getattr(cfg, "base_url", None) or ""
    except Exception:
        configured_mode = None
        configured_key = ""
        configured_base = ""

    configured_key = configured_key.strip() if isinstance(configured_key, str) else ""
    configured_base = configured_base.strip() if isinstance(configured_base, str) else ""

    # Effective values surface to the UI. V2Config wins when populated
    # (that's what we'd write next), else fall back to whatever the
    # running CLI actually inherits from ``settings.json``. This is the
    # difference between "no key configured" (truly empty) and "key lives
    # in settings.json from a hand-edit or older tool" (looks empty in
    # V2Config but actually drives the live CLI).
    effective_key = configured_key or settings_key
    effective_base = configured_base or settings_base
    has_api_key = bool(effective_key)

    if configured_mode in _VALID_AUTH_MODES:
        auth_mode = configured_mode
    elif effective_key:
        auth_mode = "api_key"
    else:
        auth_mode = "oauth"

    # ``settings_conflict`` keeps the original meaning: BOTH V2Config and
    # settings.json have a key, in which case settings.json wins at launch
    # (Claude Code layers ``env`` on top of inherited env). The new
    # "settings.json is sole source" case is not a conflict — it's just
    # the effective value and we now render it instead of blanking out.
    settings_conflict = bool(disk_state.get("settings_env_has_key")) and bool(configured_key)

    # ``active_auth_mode`` reflects what the running CLI is actually using
    # at launch.
    if effective_key and auth_mode == "api_key":
        active_auth_mode = "api_key"
    elif oauth_signed_in and auth_mode == "oauth":
        active_auth_mode = "oauth"
    elif effective_key:
        active_auth_mode = "api_key"
    elif oauth_signed_in:
        active_auth_mode = "oauth"
    else:
        active_auth_mode = "none"

    # Which storage the live API key came from — helps the UI explain the
    # state ("Key configured in settings.json"). Plaintext never leaves
    # the server; only the mask is forwarded.
    if configured_key:
        api_key_source = "v2config"
    elif settings_key:
        api_key_source = "settings_json"
    else:
        api_key_source = None

    return {
        "ok": True,
        "auth_mode": auth_mode,
        "active_auth_mode": active_auth_mode,
        "has_api_key": has_api_key,
        "api_key_length": len(effective_key),
        "api_key_masked": _mask_api_key(effective_key),
        "api_key_source": api_key_source,
        "has_oauth_credentials": oauth_signed_in,
        "base_url": effective_base or None,
        "settings_path": disk_state.get("settings_path"),
        "settings_exists": bool(disk_state.get("settings_exists")),
        "settings_env_has_key": bool(disk_state.get("settings_env_has_key")),
        "settings_env_key_length": int(disk_state.get("settings_env_key_length") or 0),
        "settings_env_key_var": disk_state.get("settings_env_key_var"),
        "settings_env_base_url": disk_state.get("settings_env_base_url"),
        "settings_conflict": settings_conflict,
    }


def save_claude_auth(payload: dict) -> dict:
    """Persist Claude auth into V2Config.

    No disk writes — V2Config is the source of truth and
    ``session_handler.py`` injects the resulting env vars at each
    one-shot CLI launch. Claude is a per-request subprocess so there is
    no daemon to restart; the *next* user message picks up the change.

    Empty ``api_key`` while in ``api_key`` mode is treated as "keep the
    stored key" — same UX promise as Codex — so callers can PATCH the
    base URL without re-typing the secret. An empty key with no stored
    fallback is rejected.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "message": "Payload must be an object"}

    auth_mode = payload.get("auth_mode")
    if auth_mode not in _VALID_AUTH_MODES:
        return {"ok": False, "message": f"auth_mode must be one of {sorted(_VALID_AUTH_MODES)}"}

    raw_api_key = payload.get("api_key")
    if raw_api_key is not None and not isinstance(raw_api_key, str):
        return {"ok": False, "message": "api_key must be a string"}
    api_key = raw_api_key.strip() if isinstance(raw_api_key, str) else None

    # Three-state ``base_url`` payload semantics (matches Codex/OpenCode):
    # absent key → keep stored value; null/blank → clear; non-blank → set.
    base_url_present = "base_url" in payload
    raw_base_url = payload.get("base_url") if base_url_present else None
    if base_url_present and raw_base_url is not None and not isinstance(raw_base_url, str):
        return {"ok": False, "message": "base_url must be a string"}
    base_url_change: Optional[str] = None
    if base_url_present:
        base_url_change = raw_base_url.strip() if isinstance(raw_base_url, str) else None
        if base_url_change == "":
            base_url_change = None

    if auth_mode == "api_key" and not api_key:
        # Reuse stored key for base-URL-only updates. Unlike Codex we
        # have no live-disk fallback (V2Config is sole writer), so we
        # only consult ``settings.json`` for legacy installs where the
        # user pre-configured the relay there and never re-typed the
        # secret into the Settings UI.
        with CONFIG_LOCK:
            try:
                existing = load_config()
                stored = getattr(getattr(existing, "agents", None), "claude", None)
                api_key = getattr(stored, "api_key", None) or None
            except Exception:
                api_key = None
        if not api_key:
            try:
                from vibe.claude_config import read_claude_api_key_from_settings

                api_key = read_claude_api_key_from_settings()
            except Exception:
                api_key = None
        if not api_key:
            return {"ok": False, "message": "api_key is required when auth_mode='api_key'"}

    with CONFIG_LOCK:
        try:
            config = load_config()
        except FileNotFoundError:
            config = V2Config()
        config.agents.claude.auth_mode = auth_mode
        config.agents.claude.api_key = api_key if auth_mode == "api_key" else None
        if base_url_present:
            config.agents.claude.base_url = base_url_change
        # else: keep whatever is already stored (omitted payload key).
        config.save()

    # Claude is one-shot per request — no daemon to restart. Return a
    # synthetic restart result so the UI handles the same response shape
    # as Codex / OpenCode and the toast wording can stay consistent.
    state = get_claude_auth()
    state["restart"] = {
        "ok": True,
        "message": "Claude relaunches per request; the next message uses the new auth.",
    }
    return state


# ---------------------------------------------------------------------------
# OpenCode provider configuration
# ---------------------------------------------------------------------------
#
# The OpenCode page in Settings → Backends is fully dynamic: we never ship
# a hard-coded provider list. Instead we fan out to the running OpenCode
# server (``GET /provider`` for the catalog, ``GET /provider/auth`` for
# the auth-method index, ``GET /config/providers`` for model lists) and
# merge the responses into a per-card view with ``configured`` /
# ``oauth_available`` / ``local`` flags.
#
# Writes go through OpenCode's own ``PUT /auth/<id>`` /
# ``DELETE /auth/<id>`` endpoints (already wrapped by
# ``OpenCodeServer.set_api_key_auth`` / ``remove_provider_auth``); we
# also persist ``default_provider`` into ``V2Config`` so the chip and
# routing layers stay in sync across restarts.


async def _opencode_get_server():
    """Spin up a transient OpenCodeServerManager instance for HTTP calls.

    Mirrors the pattern used by ``opencode_options_async``: pull the
    OpenCode config from V2Config, request a manager instance, ensure
    the daemon is reachable, and let the caller drive its HTTP methods.
    Returns ``None`` if OpenCode is disabled — callers translate that
    into a UI-friendly error.
    """
    from config.v2_compat import to_app_config
    from modules.agents.opencode import OpenCodeServerManager

    config = to_app_config(V2Config.load())
    if not config.opencode:
        return None
    opencode_config = config.opencode
    server = await OpenCodeServerManager.get_instance(
        binary=opencode_config.binary,
        port=opencode_config.port,
        request_timeout_seconds=opencode_config.request_timeout_seconds,
    )
    await server.ensure_running()
    return server


_LOCAL_PROVIDER_IDS = {"ollama", "lmstudio", "lm-studio"}


def _is_local_provider(provider_id: str, auth_methods: list) -> bool:
    """Whether the provider runs on localhost and needs no credentials.

    Earlier this also tagged ``no auth methods → local`` but OpenCode
    1.14's ``/provider/auth`` only enumerates providers that have OAuth
    or special prompts — bare API-key providers (minimax, openrouter,
    poe…) are simply absent from that map. Treating absence as "local"
    pushed them into a fallback that kept ``configured`` True even
    after the user removed their key. Narrow to a known-local
    whitelist; the auth-methods param is kept for symmetry / future use.
    """
    _ = auth_methods  # noqa: F841 — kept for callsite symmetry
    return isinstance(provider_id, str) and provider_id.lower() in _LOCAL_PROVIDER_IDS


def _coerce_opencode_provider_catalog(providers_raw) -> dict:
    """Normalize OpenCode ``/provider`` payloads into an id-keyed map.

    OpenCode 1.x returns ``{all: [Provider, ...], default: {...},
    connected: [...]}`` where ``all`` is a list. A pre-1.x prototype
    returned ``{all: {pid: Provider}}`` (dict). The original legacy shape
    was ``{providers: [...]}`` under a different top-level key. Tolerate
    all three so an OpenCode upgrade-in-place or a stale client cannot
    leave the Settings grid empty.
    """
    if not isinstance(providers_raw, dict):
        return {}
    out: dict = {}
    raw_all = providers_raw.get("all")
    if isinstance(raw_all, dict):
        return raw_all
    if isinstance(raw_all, list):
        for entry in raw_all:
            if isinstance(entry, dict):
                pid = entry.get("id")
                if pid:
                    out[pid] = entry
        return out
    legacy = providers_raw.get("providers")
    if isinstance(legacy, list):
        for entry in legacy:
            pid = entry.get("id") if isinstance(entry, dict) else None
            if pid:
                out[pid] = entry
    return out


async def _get_opencode_providers_async() -> dict:
    """Build the merged provider catalog reported to the Settings UI."""
    server = await _opencode_get_server()
    if server is None:
        return {"ok": False, "message": "OpenCode is disabled in V2Config"}

    request_loop = asyncio.get_running_loop()
    try:
        providers_raw, auth_raw, config_raw = await asyncio.gather(
            server.get_providers(),
            server.get_provider_auth(),
            server.get_available_models(os.path.expanduser("~")),
            return_exceptions=False,
        )
    finally:
        await server.close_http_session(loop=request_loop)

    all_providers = _coerce_opencode_provider_catalog(providers_raw)

    connected = providers_raw.get("connected") if isinstance(providers_raw, dict) else None
    connected_set = {pid for pid in connected if isinstance(pid, str)} if isinstance(connected, list) else set()

    model_index: dict = {}
    if isinstance(config_raw, dict):
        for entry in config_raw.get("providers", []) or []:
            pid = entry.get("id") if isinstance(entry, dict) else None
            if pid:
                model_index[pid] = entry

    auth_index = auth_raw if isinstance(auth_raw, dict) else {}

    # Resolve the user-configured default provider (V2Config wins because
    # the chip / lifecycle layer reads it from there; OpenCode's own
    # ``default`` block is a runtime hint we fall back to only when
    # V2Config has the schema default).
    default_provider = "anthropic"
    try:
        config = load_config()
        cfg = getattr(getattr(config, "agents", None), "opencode", None)
        configured_default = getattr(cfg, "default_provider", None)
        if isinstance(configured_default, str) and configured_default.strip():
            default_provider = configured_default.strip()
    except Exception:
        pass

    # Pre-load the user-config base-URL overrides once so we can attach
    # them to each row without re-parsing the JSON file per provider.
    try:
        from vibe.opencode_config import load_first_opencode_user_config

        opencode_probe = await asyncio.to_thread(
            load_first_opencode_user_config, logger_instance=logger
        )
    except Exception as exc:
        logger.debug("Could not read opencode.json for baseURL pre-population: %s", exc)
        opencode_probe = None
    base_url_index: dict = {}
    if opencode_probe is not None and isinstance(opencode_probe.config, dict):
        provider_block = opencode_probe.config.get("provider")
        if isinstance(provider_block, dict):
            for pid_key, pid_config in provider_block.items():
                if not isinstance(pid_config, dict):
                    continue
                options = pid_config.get("options")
                if not isinstance(options, dict):
                    continue
                candidate = options.get("baseURL")
                if isinstance(candidate, str) and candidate.strip():
                    base_url_index[pid_key] = candidate.strip()

    # Per-provider stored credentials, masked server-side so the
    # Settings UI can pre-fill the API Key input
    # ("sk-proj-•••H8mN") without leaking plaintext, and badge each
    # provider with the *active* auth source (``api`` / ``oauth`` /
    # absent). OpenCode 1.14 caches its in-memory ``connected`` list
    # at server startup so we treat auth.json as authoritative for
    # what the user has explicitly configured — both for save (cache
    # is stale → auth.json wins as "yes") and remove (cache is stale
    # → auth.json absence wins as "no, the user removed it").
    try:
        from vibe.opencode_config import read_opencode_provider_auth_entries

        auth_entries = await asyncio.to_thread(
            read_opencode_provider_auth_entries, logger_instance=logger
        )
    except Exception as exc:
        logger.debug("Could not read OpenCode auth.json for masked keys: %s", exc)
        auth_entries = {}
    api_key_mask_index: dict = {}
    active_auth_type_index: dict = {}
    for pid_key, entry in auth_entries.items():
        entry_type = entry.get("type") if isinstance(entry, dict) else None
        if entry_type == "api":
            raw_key = entry.get("key") if isinstance(entry, dict) else None
            masked = _mask_api_key(raw_key) if raw_key else None
            if masked:
                api_key_mask_index[pid_key] = masked
            active_auth_type_index[pid_key] = "api"
        elif entry_type == "oauth":
            active_auth_type_index[pid_key] = "oauth"
        elif entry_type:
            active_auth_type_index[pid_key] = entry_type
    auth_file_provider_set: set = set(auth_entries.keys())

    out_providers = []
    for pid, entry in all_providers.items():
        if not isinstance(entry, dict):
            continue
        auth_methods = auth_index.get(pid)
        auth_methods_list = auth_methods if isinstance(auth_methods, list) else []
        oauth_available = any(
            isinstance(method, dict) and method.get("type") == "oauth"
            for method in auth_methods_list
        )
        local = _is_local_provider(pid, auth_methods_list)
        # Authoritative source for the "configured" badge:
        # - If auth.json carries an entry → configured (user explicitly
        #   set it up, even if OpenCode's cache hasn't caught up yet).
        # - If auth.json is empty AND ``connected`` lists it → configured
        #   only when ``local`` (Ollama / LM Studio don't need keys).
        #   Otherwise treat ``connected`` as stale — the user just
        #   removed the key and the daemon hasn't restarted yet.
        if pid in auth_file_provider_set:
            configured = True
        elif local and pid in connected_set:
            configured = True
        else:
            configured = False
        models_for_provider = model_index.get(pid, {})
        provider_models = models_for_provider.get("models")
        if isinstance(provider_models, dict):
            model_ids = sorted(provider_models.keys())
        elif isinstance(provider_models, list):
            model_ids = [m.get("id") for m in provider_models if isinstance(m, dict) and m.get("id")]
        else:
            model_ids = []
        default_model = None
        defaults_block = config_raw.get("default") if isinstance(config_raw, dict) else None
        if isinstance(defaults_block, dict):
            raw_default = defaults_block.get(pid)
            if isinstance(raw_default, str):
                default_model = raw_default

        out_providers.append(
            {
                "id": pid,
                "name": entry.get("name") or pid,
                "description": entry.get("description") or "",
                "configured": configured,
                "oauth_available": oauth_available,
                "local": local,
                "models": model_ids,
                "default_model": default_model,
                "base_url": base_url_index.get(pid),
                "api_key_masked": api_key_mask_index.get(pid),
                # ``api`` / ``oauth`` / null — the type the daemon will
                # actually use at launch. Lets the UI badge the right
                # source for dual-mode providers (e.g. openai supports
                # both, but only one entry lives in auth.json at a time).
                "active_auth_type": active_auth_type_index.get(pid),
            }
        )

    out_providers.sort(key=lambda p: (not p["configured"], p["local"], p["id"]))

    # Surface the current ``permission`` setting from opencode.json so the
    # Settings page can hide the "Allow tool calls" affordance once it's
    # already ``allow`` — and strengthen the copy when it isn't, since a
    # missing/blocking setting silently makes every tool call wait for an
    # approval prompt that Vibe Remote can't reply to.
    permission_allowed = False
    if opencode_probe is not None and isinstance(opencode_probe.config, dict):
        permission_allowed = opencode_probe.config.get("permission") == "allow"

    return {
        "ok": True,
        "providers": out_providers,
        "default_provider": default_provider,
        "permission_allowed": permission_allowed,
    }


def get_opencode_providers() -> dict:
    """Sync wrapper for the OpenCode provider catalog."""
    try:
        return asyncio.run(_get_opencode_providers_async())
    except Exception as exc:
        logger.warning("OpenCode providers fetch failed: %s", exc, exc_info=True)
        return {"ok": False, "message": str(exc)}


# Sentinel used by ``save_opencode_provider_auth`` to distinguish three
# states of the optional ``base_url`` field:
#   * key absent from payload      → ``_BASE_URL_UNCHANGED`` (no-op)
#   * key present, value blank     → ``None``                (clear stored)
#   * key present, value non-blank → ``str``                 (upsert)
# Without this, a payload like ``{"api_key": "..."}`` (re-saving just
# the API key) would silently wipe the stored ``baseURL`` because the
# server cannot tell "omitted" from "explicitly empty".
_BASE_URL_UNCHANGED: object = object()


async def _save_opencode_provider_auth_async(
    provider_id: str,
    api_key: str,
    base_url: Any = _BASE_URL_UNCHANGED,
) -> dict:
    server = await _opencode_get_server()
    if server is None:
        return {"ok": False, "message": "OpenCode is disabled in V2Config"}
    request_loop = asyncio.get_running_loop()
    try:
        await server.set_api_key_auth(provider_id, api_key)
    finally:
        await server.close_http_session(loop=request_loop)

    # Two-source-of-truth pruning: drop the legacy ``opencode.json``
    # ``apiKey`` entry now that the daemon's auth store owns the key.
    # This is best-effort: a JSON-write failure here is non-fatal because
    # the daemon already has the key.
    from vibe.opencode_config import (
        remove_opencode_provider_api_key,
        remove_opencode_provider_base_url,
        upsert_opencode_provider_base_url,
    )

    try:
        await asyncio.to_thread(
            remove_opencode_provider_api_key, provider_id, logger_instance=logger
        )
    except Exception as exc:
        logger.debug("Legacy opencode.json apiKey cleanup skipped for %s: %s", provider_id, exc)

    # ``baseURL`` is different: OpenCode's auth endpoint has no field for
    # it, so this write is the *only* place it gets persisted. A silent
    # failure would surface as "save success, value lost on reload" — the
    # exact UX bug Codex flagged. Surface those errors to the caller so
    # the UI can show a useful message.
    if base_url is _BASE_URL_UNCHANGED:
        return {"ok": True}

    try:
        if base_url:
            await asyncio.to_thread(
                upsert_opencode_provider_base_url,
                provider_id,
                base_url,
                logger_instance=logger,
            )
        else:
            await asyncio.to_thread(
                remove_opencode_provider_base_url,
                provider_id,
                logger_instance=logger,
            )
    except Exception as exc:
        logger.warning(
            "OpenCode base_url persist failed for %s: %s", provider_id, exc, exc_info=True
        )
        return {
            "ok": False,
            "message": (
                "API key saved, but base URL persistence failed: "
                f"{exc}"
            ),
        }
    return {"ok": True}


def save_opencode_provider_auth(provider_id: str, payload: dict) -> dict:
    """Persist a single OpenCode provider's API key (and optional base URL).

    The api key is forwarded to OpenCode's own ``PUT /auth`` endpoint so
    the daemon's auth store remains the source of truth. The optional
    ``base_url`` override is persisted into ``opencode.json`` because
    OpenCode's auth endpoint has no field for it — without this fan-out
    the Settings UI's Base URL input would be a no-op.

    ``base_url`` field semantics in the payload:
      * absent              → leave the stored value untouched
      * empty / whitespace  → clear the stored value
      * non-empty string    → upsert (must start with http:// or https://)
    """
    if not isinstance(provider_id, str) or not provider_id.strip():
        return {"ok": False, "message": "provider_id is required"}
    if not isinstance(payload, dict):
        return {"ok": False, "message": "Payload must be an object"}
    raw_key = payload.get("api_key")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return {"ok": False, "message": "api_key is required"}
    api_key = raw_key.strip()

    base_url: Any = _BASE_URL_UNCHANGED
    if "base_url" in payload:
        raw_base_url = payload.get("base_url")
        if raw_base_url is None:
            base_url = None
        elif isinstance(raw_base_url, str):
            candidate = raw_base_url.strip()
            if not candidate:
                base_url = None
            else:
                if not candidate.lower().startswith(("http://", "https://")):
                    return {
                        "ok": False,
                        "message": "base_url must start with http:// or https://",
                    }
                base_url = candidate
        else:
            return {"ok": False, "message": "base_url must be a string"}

    try:
        result = asyncio.run(
            _save_opencode_provider_auth_async(provider_id.strip(), api_key, base_url)
        )
    except Exception as exc:
        logger.warning("OpenCode set-auth failed for %s: %s", provider_id, exc, exc_info=True)
        return {"ok": False, "message": str(exc)}

    # Ask the live controller to refresh the OpenCode server so the
    # daemon's in-memory ``connected`` cache picks up the new auth.
    # Without this, ``GET /provider`` keeps returning the pre-save
    # state until OpenCode restarts on its own (typically next idle
    # cleanup cycle). The restart is best-effort: we report it under a
    # separate ``restart`` key so the UI can show "saved, but daemon
    # refresh failed" when applicable.
    try:
        result["restart"] = restart_backend("opencode")
    except Exception as exc:
        logger.warning("OpenCode auto-restart after save failed for %s: %s", provider_id, exc)
        result["restart"] = {"ok": False, "message": str(exc)}
    return result


async def _delete_opencode_provider_auth_async(provider_id: str) -> dict:
    server = await _opencode_get_server()
    if server is None:
        return {"ok": False, "message": "OpenCode is disabled in V2Config"}
    request_loop = asyncio.get_running_loop()
    try:
        await server.remove_provider_auth(provider_id)
    finally:
        await server.close_http_session(loop=request_loop)
    return {"ok": True}


def delete_opencode_provider_auth(provider_id: str) -> dict:
    """Drop a single provider's stored credentials.

    Same restart pattern as save: the daemon caches ``connected`` at
    startup, so a fresh DELETE on ``/auth/<id>`` doesn't flip the
    runtime state until the daemon restarts. We trigger
    ``restart_backend("opencode")`` so the UI's next refresh reflects
    reality. The restart status comes back under ``restart`` so the
    page can warn on "removed, but daemon refresh failed".
    """
    if not isinstance(provider_id, str) or not provider_id.strip():
        return {"ok": False, "message": "provider_id is required"}
    try:
        result = asyncio.run(_delete_opencode_provider_auth_async(provider_id.strip()))
    except Exception as exc:
        logger.warning("OpenCode delete-auth failed for %s: %s", provider_id, exc, exc_info=True)
        return {"ok": False, "message": str(exc)}
    try:
        result["restart"] = restart_backend("opencode")
    except Exception as exc:
        logger.warning("OpenCode auto-restart after delete failed for %s: %s", provider_id, exc)
        result["restart"] = {"ok": False, "message": str(exc)}
    return result


def set_opencode_default_provider(payload: dict) -> dict:
    """Persist ``V2Config.agents.opencode.default_provider``.

    No daemon contact required — OpenCode itself accepts a per-request
    ``provider`` field on messages, so the "default" is purely our
    routing concern. Storing it in V2Config keeps the chip and the
    routing layer in sync across restarts.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "message": "Payload must be an object"}
    raw = payload.get("provider_id")
    if not isinstance(raw, str) or not raw.strip():
        return {"ok": False, "message": "provider_id is required"}
    provider_id = raw.strip()

    with CONFIG_LOCK:
        try:
            config = load_config()
        except FileNotFoundError:
            config = V2Config()
        config.agents.opencode.default_provider = provider_id
        config.save()
    return {"ok": True, "default_provider": provider_id}


def codex_models() -> dict:
    """Best-effort merged list of Codex model options.

    Codex CLI does not expose a stable `list models` command.
    We merge suggestions from:
    - Built-in known model ids
    - ~/.codex/models_cache.json (maintained by Codex CLI)
    - ~/.codex/config.toml (user-selected model and migration hints)
    """

    def _append_unique(options: list[str], seen: set[str], value: object) -> None:
        if not isinstance(value, str):
            return
        model = value.strip()
        if not model or model in seen:
            return
        seen.add(model)
        options.append(model)

    built_in_options: list[str] = [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.3-codex",
        "gpt-5.3-codex-spark",
        "gpt-5.2-codex",
        "gpt-5.2",
        "gpt-5.1-codex-max",
        "gpt-5.1-codex-mini",
        "gpt-5.1",
        "gpt-5",
    ]

    options: list[str] = []
    seen: set[str] = set()
    codex_home = Path.home() / ".codex"
    models_cache_path = codex_home / "models_cache.json"
    config_path = codex_home / "config.toml"

    for model in built_in_options:
        _append_unique(options, seen, model)

    try:
        if models_cache_path.exists() and models_cache_path.is_file():
            cache_data = json.loads(models_cache_path.read_text(encoding="utf-8"))
            models = cache_data.get("models")
            if isinstance(models, list):
                visible_models: list[tuple[int, int, str]] = []
                for index, item in enumerate(models):
                    if not isinstance(item, dict):
                        continue
                    slug = item.get("slug")
                    if not isinstance(slug, str) or not slug.strip():
                        continue
                    priority = item.get("priority")
                    if not isinstance(priority, int):
                        priority = 10**9
                    visible_models.append((priority, index, slug.strip()))

                for _, _, slug in sorted(visible_models):
                    _append_unique(options, seen, slug)
    except Exception as exc:
        logger.warning("Failed to read Codex models_cache.json: %s", exc, exc_info=True)

    try:
        if config_path.exists() and config_path.is_file():
            try:
                import tomllib  # py3.11+
            except Exception:  # pragma: no cover
                tomllib = None

            if tomllib is None:
                return {"ok": True, "models": options}

            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _append_unique(options, seen, data.get("model"))
                notice = data.get("notice")
                if isinstance(notice, dict):
                    migrations = notice.get("model_migrations")
                    if isinstance(migrations, dict):
                        for k, v in migrations.items():
                            _append_unique(options, seen, k)
                            _append_unique(options, seen, v)
    except Exception as exc:
        logger.warning("Failed to read Codex config.toml: %s", exc, exc_info=True)

    return {"ok": True, "models": options}


def _lark_api_base(domain: str = "feishu") -> str:
    """Return the API base URL for the given Lark/Feishu domain."""
    if domain == "lark":
        return "https://open.larksuite.com"
    return "https://open.feishu.cn"


def _lark_tenant_token(
    app_id: str,
    app_secret: str,
    domain: str = "feishu",
    proxy_url: str | None = None,
) -> Optional[str]:
    """Get Lark tenant access token (internal helper, not exposed to frontend).

    ``proxy_url`` is honored when set: SOCKS schemes route through
    ``aiohttp_socks``, HTTP schemes use ``urllib.ProxyHandler``. The runtime
    Feishu/Lark adapter still bypasses this because ``lark-oapi`` has no
    proxy hook — that gap is surfaced by the adapter, not here.
    """
    import urllib.request

    from vibe.proxy import is_socks_proxy

    url = f"{_lark_api_base(domain)}/open-apis/auth/v3/tenant_access_token/internal"
    body = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    headers = {"Content-Type": "application/json"}

    if proxy_url and is_socks_proxy(proxy_url):
        result = asyncio.run(_lark_tenant_token_via_aiohttp(url, body, headers, proxy_url))
    else:
        if proxy_url:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            )
        else:
            opener = urllib.request.build_opener()
        req = urllib.request.Request(url, data=body, headers=headers)
        with opener.open(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())

    if result.get("code") == 0:
        return result.get("tenant_access_token")
    return None


async def _lark_tenant_token_via_aiohttp(url: str, body: bytes, headers: dict, proxy: str) -> dict:
    import aiohttp
    from aiohttp_socks import ProxyConnector

    connector = ProxyConnector.from_url(proxy, rdns=True)
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        async with session.post(url, data=body, headers=headers) as resp:
            resp.raise_for_status()
            return await resp.json()


def lark_auth_test(
    app_id: str,
    app_secret: str,
    domain: str = "feishu",
    proxy_url: str | None = None,
) -> dict:
    """Test Lark/Feishu app credentials. Only returns ok/error, never exposes token.

    ``proxy_url`` is honored for the auth call itself; the runtime SDK
    (``lark-oapi``) has no proxy hook and bypasses it — that limitation is
    surfaced by ``modules/im/feishu.py`` once at adapter init.
    """
    from vibe.proxy import resolve_proxy

    proxy = resolve_proxy(proxy_url)
    try:
        token = _lark_tenant_token(app_id, app_secret, domain, proxy_url=proxy)
        if not token:
            return {"ok": False, "error": "Invalid credentials"}
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def lark_list_chats(app_id: str, app_secret: str, domain: str = "feishu") -> dict:
    """List Lark/Feishu group chats the bot has joined (with pagination)."""
    import urllib.request

    try:
        token = _lark_tenant_token(app_id, app_secret, domain)
        if not token:
            return {"ok": False, "error": "Failed to get access token"}

        base = _lark_api_base(domain)
        channels = []
        page_token = ""
        seen_page_tokens: set = set()
        max_pages = 50  # safety cap to prevent infinite loop
        page = 0
        while page < max_pages:
            url = f"{base}/open-apis/im/v1/chats?page_size=100"
            if page_token:
                url = f"{url}&page_token={page_token}"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
            if result.get("code") != 0:
                return {"ok": False, "error": result.get("msg", "Unknown error")}
            data = result.get("data", {})
            items = data.get("items", [])
            channels.extend(
                {
                    "id": c.get("chat_id"),
                    "name": c.get("name"),
                    "is_private": c.get("chat_type") == "private",
                }
                for c in items
            )
            page_token = data.get("page_token") or ""
            if not data.get("has_more") or not page_token:
                break
            if page_token in seen_page_tokens:
                break  # server returned the same token — avoid loop
            seen_page_tokens.add(page_token)
            page += 1
        truncated = page >= max_pages
        return {"ok": True, "channels": channels, "truncated": truncated}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# User and Bind Code management (for admin permission feature)
# ---------------------------------------------------------------------------


def get_users(platform: Optional[str] = None) -> dict:
    """Get all bound users."""
    store = SettingsStore.get_instance()
    platform = platform or _current_platform()
    users = {}
    for user_id, u in store.get_users_for_platform(platform).items():
        users[user_id] = {
            "display_name": u.display_name,
            "is_admin": u.is_admin,
            "bound_at": u.bound_at,
            "enabled": u.enabled,
            "show_message_types": u.show_message_types,
            "custom_cwd": u.custom_cwd,
            "routing": _routing_to_dict(u.routing),
        }
    return {"ok": True, "users": users}


def save_users(payload: dict) -> dict:
    """Save user settings (bulk update from UI)."""
    store = SettingsStore.get_instance()
    platform = payload.get("platform") or _current_platform()

    def _normalize_routing_payload(routing_payload: dict) -> dict:
        from modules.agents.opencode.utils import normalize_claude_reasoning_effort

        routing_data = dict(routing_payload or {})
        routing_data["claude_reasoning_effort"] = normalize_claude_reasoning_effort(
            routing_data.get("claude_model"),
            routing_data.get("claude_reasoning_effort"),
        )
        return routing_data

    users = {}
    for user_id, up in (payload.get("users") or {}).items():
        if not isinstance(up, dict):
            continue
        # Preserve dm_chat_id from existing user (not editable via UI)
        existing = store.get_user(user_id, platform=platform)
        users[user_id] = UserSettings(
            display_name=up.get("display_name", ""),
            is_admin=up.get("is_admin", False),
            bound_at=up.get("bound_at", ""),
            enabled=up.get("enabled", True),
            show_message_types=normalize_show_message_types(up.get("show_message_types")),
            custom_cwd=up.get("custom_cwd"),
            routing=_parse_routing(_normalize_routing_payload(up.get("routing") or {})),
            dm_chat_id=existing.dm_chat_id if existing else "",
        )

    # Merge instead of replace: update existing users and add new ones,
    # but preserve users not included in the payload (e.g. concurrently bound)
    current_users = store.get_users_for_platform(platform)
    for uid, user_settings in users.items():
        current_users[uid] = user_settings
    store.set_users_for_platform(platform, current_users)
    store.save()
    return get_users(platform)


def toggle_admin(user_id: str, is_admin: bool, platform: Optional[str] = None) -> dict:
    """Toggle admin status for a user."""
    store = SettingsStore.get_instance()
    platform = platform or _current_platform()
    if not store.set_admin(user_id, is_admin, platform=platform):
        if not store.is_bound_user(user_id, platform=platform):
            return {"ok": False, "error": "User not found"}
        return {"ok": False, "error": "Failed to update admin status"}
    return {"ok": True}


def remove_user(user_id: str, platform: Optional[str] = None) -> dict:
    """Remove a bound user."""
    store = SettingsStore.get_instance()
    platform = platform or _current_platform()
    user = store.get_user(user_id, platform=platform)
    if user is None:
        return {"ok": False, "error": "User not found"}
    store.remove_user(user_id, platform=platform)
    return {"ok": True}


def get_bind_codes() -> dict:
    """Get all bind codes."""
    store = SettingsStore.get_instance()
    codes = []
    for bc in store.get_bind_codes():
        codes.append(
            {
                "code": bc.code,
                "type": bc.type,
                "created_at": bc.created_at,
                "expires_at": bc.expires_at,
                "is_active": bc.is_active,
                "used_by": bc.used_by,
            }
        )
    return {"ok": True, "bind_codes": codes}


def create_bind_code(code_type: str = "one_time", expires_at: Optional[str] = None) -> dict:
    """Create a new bind code."""
    if code_type not in ("one_time", "expiring"):
        return {"ok": False, "error": "type must be 'one_time' or 'expiring'"}
    if code_type == "expiring" and not expires_at:
        return {"ok": False, "error": "expires_at is required for expiring bind codes"}
    store = SettingsStore.get_instance()
    bc = store.create_bind_code(code_type, expires_at)
    return {
        "ok": True,
        "bind_code": {
            "code": bc.code,
            "type": bc.type,
            "created_at": bc.created_at,
            "expires_at": bc.expires_at,
            "is_active": bc.is_active,
        },
    }


def delete_bind_code(code: str) -> dict:
    """Deactivate a bind code."""
    store = SettingsStore.get_instance()
    if store.deactivate_bind_code(code):
        return {"ok": True}
    return {"ok": False, "error": "Bind code not found"}


def get_first_bind_code() -> dict:
    """Get or create the initial bind code for setup wizard."""
    store = SettingsStore.get_instance()
    # If any valid (active + not expired) code exists, return it
    for bc in store.get_bind_codes():
        if bc.is_active and store.validate_bind_code(bc.code) is not None:
            return {"ok": True, "code": bc.code, "is_new": False}
    # Otherwise create a new one-time code
    bc = store.create_bind_code("one_time")
    return {"ok": True, "code": bc.code, "is_new": True}


def auto_bind_wechat_user(user_id: str) -> dict:
    """Auto-create a UserSettings entry for the WeChat user on QR login.

    WeChat is 1:1 DM only — no channels, no bind codes needed.
    The QR scan itself is the authentication, so we auto-bind the user
    as admin with default settings.
    """
    from config.v2_settings import _now_iso

    store = SettingsStore.get_instance()
    platform = "wechat"

    # Skip if already bound
    if store.is_bound_user(user_id, platform=platform):
        logger.info("WeChat user %s already bound, skipping auto-bind", user_id)
        return {"ok": True, "already_bound": True}

    config = load_config()
    user = UserSettings(
        display_name=user_id,
        is_admin=True,
        bound_at=_now_iso(),
        enabled=True,
        custom_cwd=config.runtime.default_cwd or None,
        routing=RoutingSettings(agent_backend=config.agents.default_backend or None),
    )

    current_users = store.get_users_for_platform(platform)
    current_users[user_id] = user
    store.set_users_for_platform(platform, current_users)
    store.save()

    logger.info("Auto-bound WeChat user %s as admin", user_id)
    return {"ok": True, "already_bound": False}


# ---------------------------------------------------------------------------
# Lark temporary WebSocket connection (for setup wizard)
# ---------------------------------------------------------------------------
# The Feishu console only shows the "Use Long Connection" option when an
# active WebSocket connection exists.  During the setup wizard we start a
# temporary WS client so the user can configure event subscriptions.

_temp_ws_lock = __import__("threading").Lock()
_temp_ws_client = None
_temp_ws_thread = None


def lark_temp_ws_start(app_id: str, app_secret: str, domain: str = "feishu") -> dict:
    """Start a temporary WebSocket connection so the Feishu console shows the long-connection option."""
    global _temp_ws_client, _temp_ws_thread

    with _temp_ws_lock:
        # Stop any existing temp connection first
        _stop_temp_ws_internal()

        try:
            import lark_oapi as lark

            sdk_domain = lark.LARK_DOMAIN if domain == "lark" else lark.FEISHU_DOMAIN

            # Minimal event handler (does nothing, just keeps the connection alive)
            handler = lark.EventDispatcherHandler.builder("", "").build()

            client = lark.ws.Client(
                app_id=app_id,
                app_secret=app_secret,
                event_handler=handler,
                log_level=lark.LogLevel.INFO,
                domain=sdk_domain,
            )

            import threading

            def _run():
                try:
                    client.start()
                except Exception:
                    pass  # Thread exits silently on stop

            t = threading.Thread(target=_run, daemon=True, name="lark-temp-ws")
            t.start()

            _temp_ws_client = client
            _temp_ws_thread = t

            return {"ok": True, "message": "Temporary WebSocket connection started"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


def lark_temp_ws_stop() -> dict:
    """Stop the temporary WebSocket connection."""
    with _temp_ws_lock:
        _stop_temp_ws_internal()
    return {"ok": True}


def _stop_temp_ws_internal():
    """Internal helper to stop temp WS (caller must hold _temp_ws_lock)."""
    global _temp_ws_client, _temp_ws_thread
    if _temp_ws_client is not None:
        try:
            # Prevent auto-reconnect and close the underlying connection
            _temp_ws_client._auto_reconnect = False
            from lark_oapi.ws.client import loop as ws_loop

            import asyncio

            asyncio.run_coroutine_threadsafe(_temp_ws_client._disconnect(), ws_loop)
        except Exception:
            pass
        _temp_ws_client = None
        _temp_ws_thread = None
