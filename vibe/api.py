import asyncio
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from config import paths
from config.v2_config import CONFIG_LOCK, V2Config
from config.v2_settings import (
    SettingsStore,
    ChannelSettings,
    UserSettings,
    RoutingSettings,
    normalize_show_message_types,
    _parse_routing,
    _routing_to_dict,
)
from config.v2_sessions import SessionsStore
from vibe.opencode_config import (
    get_opencode_config_paths,
    load_first_opencode_user_config,
    set_jsonc_top_level_string_property,
)
from vibe.upgrade import (
    build_upgrade_plan,
    get_latest_version_info,
    get_restart_shell_command,
    get_running_vibe_path,
    get_safe_cwd,
)


logger = logging.getLogger(__name__)

# Cache per cwd: { cwd: { "data": ..., "updated_at": ... } }
_OPENCODE_OPTIONS_CACHE: dict[str, dict] = {}
_OPENCODE_OPTIONS_TTL_SECONDS = 30.0


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
    return path or None


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


def save_config(payload: dict) -> V2Config:
    if not isinstance(payload, dict):
        raise ValueError("Config payload must be an object")

    with CONFIG_LOCK:
        base_payload: dict = {}
        try:
            base_payload = config_to_payload(load_config())
        except FileNotFoundError:
            base_payload = {}

        merged_payload = _deep_merge_dicts(base_payload, payload) if base_payload else payload
        config = V2Config.from_payload(merged_payload)
        config.save()
        return config


def config_to_payload(config: V2Config) -> dict:
    payload = {
        "platform": config.platform,
        "platforms": {
            "enabled": config.platforms.enabled,
            "primary": config.platforms.primary,
        },
        "mode": config.mode,
        "version": config.version,
        "slack": {
            **config.slack.__dict__,
            "require_mention": config.slack.require_mention,
        },
        "discord": config.discord.__dict__ if config.discord else None,
        "lark": config.lark.__dict__ if config.lark else None,
        "wechat": config.wechat.__dict__ if config.wechat else None,
        "runtime": {
            "default_cwd": config.runtime.default_cwd,
            "log_level": config.runtime.log_level,
        },
        "agents": {
            "default_backend": config.agents.default_backend,
            "opencode": config.agents.opencode.__dict__,
            "claude": config.agents.claude.__dict__,
            "codex": config.agents.codex.__dict__,
        },
        "gateway": config.gateway.__dict__ if config.gateway else None,
        "ui": config.ui.__dict__,
        "update": config.update.__dict__,
        "ack_mode": config.ack_mode,
        "language": config.language,
        "show_duration": config.show_duration,
        "include_user_info": config.include_user_info,
        "reply_enhancements": config.reply_enhancements,
    }
    return payload


def get_settings(platform: Optional[str] = None) -> dict:
    store = SettingsStore.get_instance()
    return _settings_to_payload(store, platform=platform or _current_platform())


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
    store.save()
    return _settings_to_payload(store, platform=platform)


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


def slack_auth_test(bot_token: str) -> dict:
    try:
        from slack_sdk.web import WebClient

        client = WebClient(token=bot_token)
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


def discord_auth_test(bot_token: str) -> dict:
    try:
        data = _discord_api_get(bot_token, "users/@me")
        return {"ok": True, "response": data}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


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


def _discord_api_get(bot_token: str, path: str) -> dict:
    import urllib.request

    if not bot_token:
        raise ValueError("bot_token is required")
    url = f"https://discord.com/api/v10/{path.lstrip('/')}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bot {bot_token}", "User-Agent": "vibe-remote"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = resp.read().decode("utf-8")
        return json.loads(payload)


async def opencode_options_async(cwd: str) -> dict:
    # Expand ~ to user home directory
    expanded_cwd = os.path.expanduser(cwd)
    cache_entry = _OPENCODE_OPTIONS_CACHE.get(expanded_cwd, {})
    cache_data = cache_entry.get("data")
    updated_at = cache_entry.get("updated_at", 0.0)
    cache_age = time.monotonic() - updated_at
    if cache_data and cache_age < _OPENCODE_OPTIONS_TTL_SECONDS:
        return {"ok": True, "data": cache_data, "cached": True}

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


def _current_platform() -> str:
    return load_config().platform


def _settings_to_payload(store: SettingsStore, platform: str) -> dict:
    payload: dict = {"channels": {}, "users": {}, "bind_codes": []}
    for channel_id, settings in store.get_channels_for_platform(platform).items():
        payload["channels"][channel_id] = {
            "enabled": settings.enabled,
            "show_message_types": normalize_show_message_types(settings.show_message_types),
            "custom_cwd": settings.custom_cwd,
            "require_mention": settings.require_mention,
            "routing": _routing_to_dict(settings.routing),
        }
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
                restart_cmd = f"sleep 2 && {get_restart_shell_command(vibe_path=current_vibe_path)}"
                subprocess.Popen(
                    restart_cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    cwd=safe_cwd,
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


def claude_models() -> dict:
    """Best-effort list of Claude Code model options.

    Claude Code does not expose a stable `list models` CLI subcommand.
    We derive suggestions from:
    - Common aliases mentioned by `claude --help` (opus/sonnet/haiku)
    - Common full model names (claude-opus-4, claude-sonnet-4, etc.)
    - ~/.claude/settings.json `model`
    - ~/.claude/settings.json `env` model variables (if present)
    """

    # Common full model names (latest versions)
    options: list[str] = [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-opus-4-5",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
        "claude-opus-4",
        "claude-sonnet-4",
        "claude-haiku-4",
    ]

    settings_path = Path.home() / ".claude" / "settings.json"
    try:
        if settings_path.exists() and settings_path.is_file():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                model = data.get("model")
                # Only add full model names (e.g., "claude-sonnet-4"), not aliases like "opus"
                if isinstance(model, str) and model.strip() and model.strip().startswith("claude-"):
                    options.append(model.strip())
                env = data.get("env")
                if isinstance(env, dict):
                    for key in (
                        "ANTHROPIC_MODEL",
                        "ANTHROPIC_SMALL_FAST_MODEL",
                    ):
                        value = env.get(key)
                        # Only add full model names
                        if isinstance(value, str) and value.strip() and value.strip().startswith("claude-"):
                            options.append(value.strip())
    except Exception as exc:
        logger.warning("Failed to read Claude settings.json: %s", exc, exc_info=True)

    from modules.agents.opencode.utils import build_claude_reasoning_options

    uniq = sorted({x for x in options if x})
    reasoning_options = {"": build_claude_reasoning_options(None)}
    for model in uniq:
        reasoning_options[model] = build_claude_reasoning_options(model)
    return {"ok": True, "models": uniq, "reasoning_options": reasoning_options}


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


def codex_models() -> dict:
    """Best-effort list of Codex model options.

    Codex CLI does not expose a stable `list models` command.
    We derive suggestions from:
    - Common OpenAI model names (gpt-5, etc.)
    - ~/.codex/config.toml (if present)
    """

    # Common OpenAI model names used with Codex
    options: list[str] = [
        # GPT series (newest first)
        "gpt-5.4",
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-5.2",
        "gpt-5.1",
        "gpt-5",
    ]
    config_path = Path.home() / ".codex" / "config.toml"

    try:
        if config_path.exists() and config_path.is_file():
            try:
                import tomllib  # py3.11+
            except Exception:  # pragma: no cover
                tomllib = None

            if tomllib is None:
                uniq = sorted({x for x in options if x})
                return {"ok": True, "models": uniq}

            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                model = data.get("model")
                if isinstance(model, str) and model.strip():
                    options.append(model.strip())
                notice = data.get("notice")
                if isinstance(notice, dict):
                    migrations = notice.get("model_migrations")
                    if isinstance(migrations, dict):
                        for k, v in migrations.items():
                            if isinstance(k, str) and k.strip():
                                options.append(k.strip())
                            if isinstance(v, str) and v.strip():
                                options.append(v.strip())
    except Exception as exc:
        logger.warning("Failed to read Codex config.toml: %s", exc, exc_info=True)

    uniq = sorted({x for x in options if x})
    return {"ok": True, "models": uniq}


def _lark_api_base(domain: str = "feishu") -> str:
    """Return the API base URL for the given Lark/Feishu domain."""
    if domain == "lark":
        return "https://open.larksuite.com"
    return "https://open.feishu.cn"


def _lark_tenant_token(app_id: str, app_secret: str, domain: str = "feishu") -> Optional[str]:
    """Get Lark tenant access token (internal helper, not exposed to frontend)."""
    import urllib.request

    url = f"{_lark_api_base(domain)}/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read().decode())
        if result.get("code") == 0:
            return result.get("tenant_access_token")
    return None


def lark_auth_test(app_id: str, app_secret: str, domain: str = "feishu") -> dict:
    """Test Lark/Feishu app credentials. Only returns ok/error, never exposes token."""
    try:
        token = _lark_tenant_token(app_id, app_secret, domain)
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
