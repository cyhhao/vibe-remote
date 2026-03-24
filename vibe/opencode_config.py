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
    content: Optional[str] = None
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


@dataclass(slots=True)
class _JsoncTopLevelProperty:
    key: str
    key_start: int
    value_start: int
    value_end: int
    delimiter_index: int
    delimiter: str


def _consume_json_string(source: str, start: int) -> int:
    i = start + 1
    escaped = False
    while i < len(source):
        char = source[i]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            return i + 1
        i += 1
    raise ValueError("unterminated string")


def _consume_jsonc_comment(source: str, start: int) -> int:
    next_char = source[start + 1] if start + 1 < len(source) else ""
    if next_char == "/":
        i = start + 2
        while i < len(source) and source[i] != "\n":
            i += 1
        return i
    if next_char == "*":
        i = start + 2
        while i + 1 < len(source):
            if source[i] == "*" and source[i + 1] == "/":
                return i + 2
            i += 1
        raise ValueError("unterminated block comment")
    raise ValueError("expected JSONC comment")


def _skip_jsonc_whitespace_and_comments(source: str, start: int) -> int:
    i = start
    while i < len(source):
        char = source[i]
        if char in " \t\r\n":
            i += 1
            continue
        if char == "/" and i + 1 < len(source) and source[i + 1] in "/*":
            i = _consume_jsonc_comment(source, i)
            continue
        return i
    return i


def _find_matching_jsonc_delimiter(source: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    i = start
    while i < len(source):
        char = source[i]
        if char == '"':
            i = _consume_json_string(source, i)
            continue
        if char == "/" and i + 1 < len(source) and source[i + 1] in "/*":
            i = _consume_jsonc_comment(source, i)
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    raise ValueError(f"unterminated {opening}{closing} structure")


def _consume_jsonc_primitive(source: str, start: int) -> int:
    i = start
    while i < len(source):
        char = source[i]
        if char in " \t\r\n,}]":
            return i
        if char == "/" and i + 1 < len(source) and source[i + 1] in "/*":
            return i
        i += 1
    return i


def _consume_jsonc_value(source: str, start: int) -> int:
    if start >= len(source):
        raise ValueError("missing JSON value")

    char = source[start]
    if char == '"':
        return _consume_json_string(source, start)
    if char == "{":
        return _find_matching_jsonc_delimiter(source, start, "{", "}") + 1
    if char == "[":
        return _find_matching_jsonc_delimiter(source, start, "[", "]") + 1
    return _consume_jsonc_primitive(source, start)


def _line_start(source: str, index: int) -> int:
    return source.rfind("\n", 0, index) + 1


def _detect_newline(source: str) -> str:
    return "\r\n" if "\r\n" in source else "\n"


def _scan_jsonc_top_level_properties(source: str) -> tuple[int, int, list[_JsoncTopLevelProperty]]:
    root_start = 1 if source.startswith("\ufeff") else 0
    root_start = _skip_jsonc_whitespace_and_comments(source, root_start)
    if root_start >= len(source) or source[root_start] != "{":
        raise ValueError("root is not a JSON object")

    root_end = _find_matching_jsonc_delimiter(source, root_start, "{", "}")
    properties: list[_JsoncTopLevelProperty] = []

    i = root_start + 1
    while True:
        i = _skip_jsonc_whitespace_and_comments(source, i)
        if i >= root_end:
            break
        if source[i] != '"':
            raise ValueError("expected object property")

        key_end = _consume_json_string(source, i)
        key = json.loads(source[i:key_end])

        colon_index = _skip_jsonc_whitespace_and_comments(source, key_end)
        if colon_index >= len(source) or source[colon_index] != ":":
            raise ValueError("expected ':' after object property name")

        value_start = _skip_jsonc_whitespace_and_comments(source, colon_index + 1)
        value_end = _consume_jsonc_value(source, value_start)
        delimiter_index = _skip_jsonc_whitespace_and_comments(source, value_end)
        delimiter = source[delimiter_index] if delimiter_index < len(source) else ""

        if delimiter not in {",", "}"}:
            raise ValueError("expected ',' or '}' after object property")

        properties.append(
            _JsoncTopLevelProperty(
                key=key,
                key_start=i,
                value_start=value_start,
                value_end=value_end,
                delimiter_index=delimiter_index,
                delimiter=delimiter,
            )
        )

        if delimiter == "}":
            break
        i = delimiter_index + 1

    return root_start, root_end, properties


def set_jsonc_top_level_string_property(source: str, key: str, value: str) -> str:
    parse_jsonc_object(source)

    root_start, root_end, properties = _scan_jsonc_top_level_properties(source)
    serialized_value = json.dumps(value)

    matching_property = next((prop for prop in reversed(properties) if prop.key == key), None)
    if matching_property is not None:
        return (
            source[: matching_property.value_start]
            + serialized_value
            + source[matching_property.value_end :]
        )

    newline = _detect_newline(source)
    root_line_start = _line_start(source, root_start)
    closing_line_start = _line_start(source, root_end)
    root_indent = source[root_line_start:root_start]
    first_property_indent = None
    if properties:
        first_property = properties[0]
        first_property_line_start = _line_start(source, first_property.key_start)
        candidate_indent = source[first_property_line_start:first_property.key_start]
        if candidate_indent.strip() == "":
            first_property_indent = candidate_indent

    child_indent = first_property_indent or (root_indent + "  ")
    property_text = f'{json.dumps(key)}: {serialized_value}'
    has_multiline_layout = "\n" in source[root_start:root_end]
    closing_brace_on_own_line = source[closing_line_start:root_end].strip() == ""

    if not properties:
        if has_multiline_layout:
            insertion = f"{child_indent}{property_text}{newline}"
            return source[:closing_line_start] + insertion + source[closing_line_start:]
        return source[: root_start + 1] + property_text + source[root_end:]

    last_property = properties[-1]
    updated_source = source
    trailing_comma = last_property.delimiter == ","

    if not has_multiline_layout:
        insertion_point = root_end
        if not trailing_comma:
            updated_source = updated_source[: last_property.value_end] + "," + updated_source[last_property.value_end :]
            insertion_point += 1
        prefix = " " if updated_source[insertion_point - 1] not in "{[ \t\r\n" else ""
        suffix = "," if trailing_comma else ""
        return updated_source[:insertion_point] + f"{prefix}{property_text}{suffix}" + updated_source[insertion_point:]

    insertion_point = closing_line_start
    if not trailing_comma:
        updated_source = updated_source[: last_property.value_end] + "," + updated_source[last_property.value_end :]
        if insertion_point > last_property.value_end:
            insertion_point += 1

    if has_multiline_layout and closing_brace_on_own_line:
        suffix = "," if trailing_comma else ""
        insertion = f"{child_indent}{property_text}{suffix}{newline}"
        return updated_source[:insertion_point] + insertion + updated_source[insertion_point:]

    if has_multiline_layout:
        insertion_point = root_end
        if not trailing_comma:
            insertion_point += 1
        suffix = "," if trailing_comma else ""
        insertion = f"{newline}{child_indent}{property_text}{suffix}{newline}{root_indent}"
        return updated_source[:insertion_point] + insertion + updated_source[insertion_point:]


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
            result.content = content
            result.path = config_path
            return result
        except Exception as exc:
            active_logger.warning(f"Failed to load {config_path}: {exc}")
            result.errors.append((config_path, str(exc)))
            continue

    return result
