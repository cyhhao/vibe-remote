import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List

from config import paths
from config.v2_config import V2Config
from config.v2_settings import SettingsStore, ChannelSettings, RoutingSettings
from config.v2_sessions import SessionsStore


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
        "slack": config.slack.__dict__,
        "runtime": {
            "default_cwd": config.runtime.default_cwd,
            "log_level": config.runtime.log_level,
            "require_mention": config.runtime.require_mention,
            "target_channels": config.runtime.target_channels,
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
        "cleanup_enabled": config.cleanup_enabled,
        "agent_route_file": config.agent_route_file,
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
            hidden_message_types=channel_payload.get(
                "hidden_message_types", ["system", "assistant", "toolcall"]
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
        import asyncio
        from config.v2_compat import to_app_config
        from modules.agents.opencode_agent import OpenCodeAgent

        config = to_app_config(V2Config.load())
        agent = OpenCodeAgent(None, config.opencode)

        async def _fetch() -> dict:
            server = await agent._get_server()
            await server.ensure_running()
            agents = await server.get_available_agents(cwd)
            models = await server.get_available_models(cwd)
            defaults = await server.get_default_config(cwd)
            return {"agents": agents, "models": models, "defaults": defaults}

        return {"ok": True, "data": asyncio.run(_fetch())}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _settings_to_payload(store: SettingsStore) -> dict:
    payload = {"channels": {}}
    for channel_id, settings in store.settings.channels.items():
        payload["channels"][channel_id] = {
            "enabled": settings.enabled,
            "hidden_message_types": settings.hidden_message_types,
            "custom_cwd": settings.custom_cwd,
            "routing": {
                "agent_backend": settings.routing.agent_backend,
                "opencode_agent": settings.routing.opencode_agent,
                "opencode_model": settings.routing.opencode_model,
                "opencode_reasoning_effort": settings.routing.opencode_reasoning_effort,
            },
        }
    return payload
