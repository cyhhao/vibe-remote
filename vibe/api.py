import json
import asyncio
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Dict, List, Optional

from config import paths
from config.v2_config import V2Config
from config.v2_settings import (
    SettingsStore,
    ChannelSettings,
    RoutingSettings,
    normalize_show_message_types,
)
from config.v2_sessions import SessionsStore


logger = logging.getLogger(__name__)

# Cache per cwd: { cwd: { "data": ..., "updated_at": ... } }
_OPENCODE_OPTIONS_CACHE: dict[str, dict] = {}
_OPENCODE_OPTIONS_TTL_SECONDS = 30.0


def load_config() -> V2Config:
    return V2Config.load()


def save_config(payload: dict) -> V2Config:
    config = V2Config.from_payload(payload)
    config.save()
    return config


def config_to_payload(config: V2Config) -> dict:
    payload = {
        "mode": config.mode,
        "version": config.version,
        "slack": {
            **config.slack.__dict__,
            "require_mention": config.slack.require_mention,
        },
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
    }
    return payload


def get_settings() -> dict:
    store = SettingsStore()
    return _settings_to_payload(store)


def save_settings(payload: dict) -> dict:
    store = SettingsStore()
    channels = {}
    for channel_id, channel_payload in (payload.get("channels") or {}).items():
        routing_payload = channel_payload.get("routing") or {}
        routing = RoutingSettings(
            agent_backend=routing_payload.get("agent_backend"),
            opencode_agent=routing_payload.get("opencode_agent"),
            opencode_model=routing_payload.get("opencode_model"),
            opencode_reasoning_effort=routing_payload.get("opencode_reasoning_effort"),
            claude_agent=routing_payload.get("claude_agent"),
            claude_model=routing_payload.get("claude_model"),
            codex_model=routing_payload.get("codex_model"),
            codex_reasoning_effort=routing_payload.get("codex_reasoning_effort"),
        )
        channels[channel_id] = ChannelSettings(
            enabled=channel_payload.get("enabled", True),
            show_message_types=normalize_show_message_types(
                channel_payload.get("show_message_types")
            ),
            custom_cwd=channel_payload.get("custom_cwd"),
            routing=routing,
            require_mention=channel_payload.get("require_mention"),
        )
    store.settings.channels = channels
    store.save()
    return _settings_to_payload(store)


def init_sessions() -> None:
    store = SessionsStore()
    store.save()


def detect_cli(binary: str) -> dict:
    if binary == "claude":
        preferred = Path.home() / ".claude" / "local" / "claude"
        if preferred.exists() and os.access(preferred, os.X_OK):
            return {"found": True, "path": str(preferred)}
    path = shutil.which(binary)
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


def list_channels(bot_token: str) -> dict:
    try:
        from slack_sdk.web import WebClient

        client = WebClient(token=bot_token)
        channels = []
        cursor = None
        while True:
            response = client.conversations_list(
                types="public_channel,private_channel",
                limit=200,
                cursor=cursor,
            )
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
        return {"ok": True, "channels": channels}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def opencode_options(cwd: str) -> dict:
    try:
        return asyncio.run(opencode_options_async(cwd))
    except Exception as exc:
        logger.warning("OpenCode options fetch failed: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc)}


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
        timeout_seconds = min(
            10.0, float(opencode_config.request_timeout_seconds or 10)
        )

        def _build_reasoning_options(
            models: dict,
            builder,
        ) -> dict:
            options: dict = {}
            for provider in models.get("providers", []):
                provider_id = (
                    provider.get("id")
                    or provider.get("provider_id")
                    or provider.get("name")
                )
                if not provider_id:
                    continue
                model_ids = []
                provider_models = provider.get("models", {})
                if isinstance(provider_models, dict):
                    model_ids = list(provider_models.keys())
                elif isinstance(provider_models, list):
                    model_ids = [
                        model.get("id")
                        for model in provider_models
                        if isinstance(model, dict) and model.get("id")
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
        agents = await asyncio.wait_for(
            server.get_available_agents(expanded_cwd), timeout=timeout_seconds
        )
        models = await asyncio.wait_for(
            server.get_available_models(expanded_cwd), timeout=timeout_seconds
        )
        defaults = await asyncio.wait_for(
            server.get_default_config(expanded_cwd), timeout=timeout_seconds
        )
        reasoning_options = _build_reasoning_options(
            models, build_reasoning_effort_options
        )
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


def _settings_to_payload(store: SettingsStore) -> dict:
    payload = {"channels": {}}
    for channel_id, settings in store.settings.channels.items():
        payload["channels"][channel_id] = {
            "enabled": settings.enabled,
            "show_message_types": normalize_show_message_types(
                settings.show_message_types
            ),
            "custom_cwd": settings.custom_cwd,
            "require_mention": settings.require_mention,
            "routing": {
                "agent_backend": settings.routing.agent_backend,
                "opencode_agent": settings.routing.opencode_agent,
                "opencode_model": settings.routing.opencode_model,
                "opencode_reasoning_effort": settings.routing.opencode_reasoning_effort,
                "claude_agent": settings.routing.claude_agent,
                "claude_model": settings.routing.claude_model,
                "codex_model": settings.routing.codex_model,
                "codex_reasoning_effort": settings.routing.codex_reasoning_effort,
            },
        }
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
    import urllib.request
    from vibe import __version__

    current = __version__
    result = {"current": current, "latest": None, "has_update": False, "error": None}

    try:
        url = "https://pypi.org/pypi/vibe-remote/json"
        req = urllib.request.Request(url, headers={"User-Agent": "vibe-remote"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            latest = data.get("info", {}).get("version", "")
            result["latest"] = latest

            # Simple version comparison (works for semver)
            if latest and latest != current:
                try:
                    current_parts = [
                        int(x) for x in current.split(".")[:3] if x.isdigit()
                    ]
                    latest_parts = [
                        int(x) for x in latest.split(".")[:3] if x.isdigit()
                    ]
                    result["has_update"] = latest_parts > current_parts
                except (ValueError, AttributeError):
                    result["has_update"] = latest != current
    except Exception as e:
        result["error"] = str(e)

    return result


def do_upgrade(auto_restart: bool = True) -> dict:
    """Perform upgrade to latest version.

    Args:
        auto_restart: If True, restart vibe after successful upgrade

    Returns:
        {"ok": bool, "message": str, "output": str | None, "restarting": bool}
    """
    import sys

    # Determine upgrade method based on how vibe was installed
    # Check if running from uv tool environment
    exe_path = sys.executable
    is_uv_tool = ".local/share/uv/tools/" in exe_path or "/uv/tools/" in exe_path

    uv_path = shutil.which("uv")

    if is_uv_tool and uv_path:
        # Installed via uv tool, upgrade with uv
        cmd = [uv_path, "tool", "install", "vibe-remote", "--upgrade"]
    else:
        # Installed via pip or other method, use current Python's pip
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "vibe-remote"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            restarting = False
            if auto_restart:
                # Schedule restart in background after response is sent
                # Use 'vibe' command which will restart both service and UI
                vibe_path = shutil.which("vibe")
                if vibe_path:
                    # Start restart process detached, with delay to allow response to be sent
                    restart_cmd = f"sleep 2 && {vibe_path}"
                    subprocess.Popen(
                        restart_cmd,
                        shell=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                    restarting = True

            return {
                "ok": True,
                "message": "Upgrade successful."
                + (" Restarting..." if restarting else " Please restart vibe."),
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
    1. ~/.config/opencode/opencode.json - if exists and valid, update it
    2. ~/.opencode/opencode.json - if exists and valid, update it
    3. Create new file at ~/.config/opencode/opencode.json (XDG standard)

    Mirrors _load_opencode_user_config behavior: skips invalid files and tries next.

    Returns:
        {"ok": bool, "message": str, "config_path": str}
    """
    from pathlib import Path

    config_paths = [
        Path.home() / ".config" / "opencode" / "opencode.json",
        Path.home() / ".opencode" / "opencode.json",
    ]

    # Try each path in priority order
    for config_path in config_paths:
        if not config_path.exists():
            continue

        try:
            content = config_path.read_text(encoding="utf-8").strip()
            if not content:
                # Empty file, treat as valid empty config
                config = {}
            else:
                config = json.loads(content)
                if not isinstance(config, dict):
                    logger.warning(f"{config_path}: not a JSON object, skipping")
                    continue

            # Found a valid config file, update it
            if config.get("permission") == "allow":
                return {
                    "ok": True,
                    "message": "Permission already set",
                    "config_path": str(config_path),
                }

            config["permission"] = "allow"
            config_path.write_text(
                json.dumps(config, indent=2) + "\n", encoding="utf-8"
            )
            return {
                "ok": True,
                "message": "Permission set to 'allow'",
                "config_path": str(config_path),
            }
        except json.JSONDecodeError as e:
            logger.warning(f"{config_path}: invalid JSON ({e}), skipping")
            continue
        except Exception as e:
            logger.warning(f"{config_path}: failed to read ({e}), skipping")
            continue

    # No existing valid config found, create at XDG path (first in list)
    config_path = config_paths[0]
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({"permission": "allow"}, indent=2) + "\n", encoding="utf-8"
        )
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

        # Simple YAML parsing (avoid external dependency)
        metadata: dict = {}
        for line in frontmatter_lines:
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip()
                value = value.strip()
                if key and value:
                    metadata[key] = value

        # Extract body (system prompt)
        body_lines = lines[end_idx + 1:]
        body = "\n".join(body_lines).strip()

        # Parse tools if present (comma or space separated)
        tools = None
        if "tools" in metadata:
            tools_str = metadata["tools"]
            # Handle both "Read, Bash, Edit" and "Read Bash Edit"
            if "," in tools_str:
                tools = [t.strip() for t in tools_str.split(",") if t.strip()]
            else:
                tools = [t.strip() for t in tools_str.split() if t.strip()]

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
            project_dir = (Path(cwd).expanduser().resolve() / ".claude" / "agents")
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
        "claude-opus-4",
        "claude-sonnet-4",
        "claude-haiku-4",
        "claude-opus-4-5",
        "claude-sonnet-4-5",
        "claude-haiku-4-5",
    ]

    settings_path = Path.home() / ".claude" / "settings.json"
    try:
        if settings_path.exists() and settings_path.is_file():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                model = data.get("model")
                if isinstance(model, str) and model.strip():
                    options.append(model.strip())
                env = data.get("env")
                if isinstance(env, dict):
                    for key in (
                        "ANTHROPIC_MODEL",
                        "ANTHROPIC_SMALL_FAST_MODEL",
                    ):
                        value = env.get(key)
                        if isinstance(value, str) and value.strip():
                            options.append(value.strip())
    except Exception as exc:
        logger.warning("Failed to read Claude settings.json: %s", exc, exc_info=True)

    uniq = sorted({x for x in options if x})
    return {"ok": True, "models": uniq}


def codex_models() -> dict:
    """Best-effort list of Codex model options.

    Codex CLI does not expose a stable `list models` command.
    We derive suggestions from:
    - Common OpenAI model names (gpt-5, etc.)
    - ~/.codex/config.toml (if present)
    """

    # Common OpenAI model names used with Codex
    options: list[str] = [
        # GPT series
        "gpt-5",
        "gpt-5.1",
        "gpt-5.2",
        "gpt-5.2-codex",
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
