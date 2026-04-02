"""Minimal Telegram Bot API helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import aiohttp


def _api_url(bot_token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}/{method}"


def _file_url(bot_token: str, file_path: str) -> str:
    return f"https://api.telegram.org/file/bot{bot_token}/{file_path.lstrip('/')}"


async def call_api(
    bot_token: str,
    method: str,
    payload: Optional[dict[str, Any]] = None,
    *,
    timeout_seconds: int = 60,
    form: Optional[aiohttp.FormData] = None,
) -> dict[str, Any]:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        if form is not None:
            async with session.post(_api_url(bot_token, method), data=form) as resp:
                data = await resp.json()
        else:
            async with session.post(_api_url(bot_token, method), json=payload or {}) as resp:
                data = await resp.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"Telegram API call failed: {method}")
    return data


async def get_me(bot_token: str) -> dict[str, Any]:
    return await call_api(bot_token, "getMe")


async def create_forum_topic(bot_token: str, chat_id: str, name: str) -> dict[str, Any]:
    return await call_api(bot_token, "createForumTopic", {"chat_id": chat_id, "name": name})


async def get_updates(bot_token: str, offset: Optional[int], timeout_seconds: int = 30) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "timeout": timeout_seconds,
        "allowed_updates": ["message", "callback_query", "my_chat_member", "chat_member"],
    }
    if offset is not None:
        payload["offset"] = offset
    return await call_api(bot_token, "getUpdates", payload, timeout_seconds=timeout_seconds + 10)


async def download_file(bot_token: str, file_path: str, *, timeout_seconds: int = 60) -> bytes:
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(_file_url(bot_token, file_path)) as resp:
            resp.raise_for_status()
            return await resp.read()


async def get_file(bot_token: str, file_id: str) -> dict[str, Any]:
    return await call_api(bot_token, "getFile", {"file_id": file_id})


async def delete_message(bot_token: str, chat_id: str, message_id: str) -> dict[str, Any]:
    return await call_api(bot_token, "deleteMessage", {"chat_id": chat_id, "message_id": int(message_id)})


async def set_message_reaction(
    bot_token: str,
    chat_id: str,
    message_id: str,
    emoji: str,
    *,
    is_big: bool = False,
) -> dict[str, Any]:
    payload = {
        "chat_id": chat_id,
        "message_id": int(message_id),
        "reaction": [{"type": "emoji", "emoji": emoji}],
        "is_big": is_big,
    }
    return await call_api(bot_token, "setMessageReaction", payload)


async def clear_message_reaction(bot_token: str, chat_id: str, message_id: str) -> dict[str, Any]:
    payload = {
        "chat_id": chat_id,
        "message_id": int(message_id),
        "reaction": [],
    }
    return await call_api(bot_token, "setMessageReaction", payload)


async def send_multipart_file(
    bot_token: str,
    method: str,
    payload: dict[str, Any],
    file_path: str,
    field_name: str,
) -> dict[str, Any]:
    form = aiohttp.FormData()
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            form.add_field(key, json.dumps(value))
        else:
            form.add_field(key, str(value))
    path = Path(file_path)
    form.add_field(field_name, path.read_bytes(), filename=path.name)
    return await call_api(bot_token, method, form=form)
