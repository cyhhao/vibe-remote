#!/usr/bin/env python3
"""Shared helpers for GitHub polling waiters."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import urllib.request
from typing import Any


def get_token() -> str | None:
    for env_name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = os.environ.get(env_name)
        if value:
            return value

    gh_path = shutil.which("gh")
    if not gh_path:
        return None

    try:
        result = subprocess.run(
            [gh_path, "auth", "token"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    token = result.stdout.strip()
    return token or None


def min_interval_for_unauthenticated(
    requests_per_poll: int,
    *,
    bootstrap_requests: int = 0,
) -> float:
    recurring_requests = max(requests_per_poll, 1)
    hourly_budget = max(1, 60 - max(bootstrap_requests, 0))
    return float(max(60, math.ceil((3600 * recurring_requests) / hourly_budget)))


def github_get(url: str, token: str | None) -> Any:
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("User-Agent", "background-watch-hook/0.1.0")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def get_authenticated_login(token: str | None) -> str | None:
    if not token:
        return None

    try:
        payload = github_get("https://api.github.com/user", token)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    login = payload.get("login")
    return str(login) if isinstance(login, str) and login else None


def list_paginated(base_url: str, token: str | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        separator = "&" if "?" in base_url else "?"
        url = f"{base_url}{separator}per_page=100&page={page}"
        payload = github_get(url, token)
        if not isinstance(payload, list):
            raise RuntimeError(f"Expected a JSON list from {url}")
        if not payload:
            break
        items.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < 100:
            break
        page += 1
    return items


def squash(text: str | None, *, limit: int = 140) -> str:
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def max_id(items: list[dict[str, Any]]) -> int:
    values = [int(item["id"]) for item in items if isinstance(item.get("id"), int)]
    return max(values, default=0)


def filter_new(items: list[dict[str, Any]], since_id: int) -> list[dict[str, Any]]:
    return sorted(
        [item for item in items if isinstance(item.get("id"), int) and int(item["id"]) > since_id],
        key=lambda item: (str(item.get("created_at") or ""), int(item["id"])),
    )


def requests_per_poll(*collections: list[dict[str, Any]]) -> int:
    requests = 0
    for items in collections:
        requests += max(1, (len(items) // 100) + 1)
    return requests
