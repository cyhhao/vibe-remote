"""Helpers for reading OpenCode user config files."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OpenCodeConfigProbeResult:
    config: Optional[Dict[str, Any]] = None
    path: Optional[Path] = None
    existing_paths: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)


def get_opencode_config_paths(home: Path | None = None) -> list[Path]:
    resolved_home = home or Path.home()
    return [
        resolved_home / ".config" / "opencode" / "opencode.json",
        resolved_home / ".opencode" / "opencode.json",
    ]


def _strip_jsonc_comments(source: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    in_line_comment = False
    in_block_comment = False

    i = 0
    while i < len(source):
        char = source[i]
        next_char = source[i + 1] if i + 1 < len(source) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                result.append(char)
            i += 1
            continue

        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                i += 2
                continue
            if char == "\n":
                result.append(char)
            i += 1
            continue

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            i += 1
            continue

        if char == "/" and next_char == "/":
            in_line_comment = True
            i += 2
            continue

        if char == "/" and next_char == "*":
            in_block_comment = True
            i += 2
            continue

        result.append(char)
        i += 1

    return "".join(result)


def _strip_trailing_commas(source: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False

    i = 0
    while i < len(source):
        char = source[i]

        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            i += 1
            continue

        if char == ",":
            j = i + 1
            while j < len(source) and source[j] in " \t\r\n":
                j += 1
            if j < len(source) and source[j] in "}]":
                i += 1
                continue

        result.append(char)
        i += 1

    return "".join(result)


def parse_jsonc_object(content: str) -> Dict[str, Any]:
    normalized = _strip_trailing_commas(_strip_jsonc_comments(content.lstrip("\ufeff"))).strip()
    if not normalized:
        raise ValueError("empty JSONC content")

    parsed = json.loads(normalized)
    if not isinstance(parsed, dict):
        raise ValueError("root is not a JSON object")
    return parsed


def load_first_opencode_user_config(
    *,
    home: Path | None = None,
    logger_instance: Optional[logging.Logger] = None,
) -> OpenCodeConfigProbeResult:
    active_logger = logger_instance or logger
    result = OpenCodeConfigProbeResult()

    for config_path in get_opencode_config_paths(home):
        if not config_path.exists():
            continue

        result.existing_paths.append(config_path)
        try:
            content = config_path.read_text(encoding="utf-8")
            result.config = parse_jsonc_object(content)
            result.path = config_path
            return result
        except Exception as exc:
            active_logger.warning(f"Failed to load {config_path}: {exc}")
            result.errors.append((config_path, str(exc)))
            continue

    return result
