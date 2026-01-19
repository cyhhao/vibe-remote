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

_OPENCODE_OPTIONS_CACHE: dict[str, Optional[object]] = {
    "data": None,
    "updated_at": 0.0,
}
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
        "slack": {
            **config.slack.__dict__,
            "require_mention": config.slack.require_mention,
        },
        "agents": {
            "default_backend": config.agents.default_backend,
            "opencode": config.agents.opencode.__dict__,
            "claude": config.agents.claude.__dict__,
            "codex": config.agents.codex.__dict__,
        },
        "gateway": config.gateway.__dict__ if config.gateway else None,
        "ui": config.ui.__dict__,
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
        )
        channels[channel_id] = ChannelSettings(
            enabled=channel_payload.get("enabled", True),
            show_message_types=normalize_show_message_types(
                channel_payload.get("show_message_types")
            ),
            custom_cwd=channel_payload.get("custom_cwd"),
            routing=routing,
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
                channels.append({
                    "id": channel.get("id"),
                    "name": channel.get("name"),
                    "is_private": channel.get("is_private", False),
                })
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
    cache_data = _OPENCODE_OPTIONS_CACHE.get("data")
    updated_at = _OPENCODE_OPTIONS_CACHE.get("updated_at")
    updated_at_value = updated_at if isinstance(updated_at, float) else 0.0
    cache_age = time.monotonic() - updated_at_value
    if cache_data and cache_age < _OPENCODE_OPTIONS_TTL_SECONDS:
        return {"ok": True, "data": cache_data, "cached": True}

    try:
        from config.v2_compat import to_app_config
        from modules.agents.opencode_agent import (
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
        agents = await asyncio.wait_for(server.get_available_agents(cwd), timeout=timeout_seconds)
        models = await asyncio.wait_for(server.get_available_models(cwd), timeout=timeout_seconds)
        defaults = await asyncio.wait_for(server.get_default_config(cwd), timeout=timeout_seconds)
        reasoning_options = _build_reasoning_options(models, build_reasoning_effort_options)
        data = {
            "agents": agents,
            "models": models,
            "defaults": defaults,
            "reasoning_options": reasoning_options,
        }
        _OPENCODE_OPTIONS_CACHE["data"] = data
        _OPENCODE_OPTIONS_CACHE["updated_at"] = time.monotonic()
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
            "routing": {
                "agent_backend": settings.routing.agent_backend,
                "opencode_agent": settings.routing.opencode_agent,
                "opencode_model": settings.routing.opencode_model,
                "opencode_reasoning_effort": settings.routing.opencode_reasoning_effort,
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
            if hasattr(importlib.resources, 'files'):
                package_files = importlib.resources.files('vibe')
                template_path = package_files / 'templates' / 'slack_manifest.json'
                if hasattr(template_path, 'read_text'):
                    manifest = json.loads(template_path.read_text(encoding='utf-8'))
        except (TypeError, FileNotFoundError, AttributeError, json.JSONDecodeError):
            pass
        
        # Fallback: load from file system (development mode)
        if manifest is None:
            this_dir = Path(__file__).parent
            template_file = this_dir / 'templates' / 'slack_manifest.json'
            if template_file.exists():
                manifest = json.loads(template_file.read_text(encoding='utf-8'))
        
        if manifest is None:
            return {"ok": False, "error": "Manifest template file not found"}
        
        # Pretty JSON for display, compact JSON for URL
        manifest_pretty = json.dumps(manifest, indent=2)
        manifest_compact = json.dumps(manifest, separators=(',', ':'))
        return {"ok": True, "manifest": manifest_pretty, "manifest_compact": manifest_compact}
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
                    current_parts = [int(x) for x in current.split(".")[:3] if x.isdigit()]
                    latest_parts = [int(x) for x in latest.split(".")[:3] if x.isdigit()]
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
        cmd = [uv_path, "tool", "install", "vibe-remote", "--force"]
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
        return {"ok": False, "message": "Upgrade timed out", "output": None, "restarting": False}
    except Exception as e:
        return {"ok": False, "message": str(e), "output": None, "restarting": False}

