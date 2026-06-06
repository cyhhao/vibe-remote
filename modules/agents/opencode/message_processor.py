"""Message processing helpers for OpenCode agent."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Mapping

logger = logging.getLogger(__name__)


def extract_opencode_response_text(
    response: Mapping[str, Any],
    *,
    allow_non_text_fallback: bool = False,
) -> str:
    """Extract user-visible assistant text from an OpenCode message."""
    parts = response.get("parts", [])
    text_parts: list[str] = []
    fallback_parts: list[str] = []

    if not isinstance(parts, list):
        return ""

    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        cleaned = text.strip()
        if part.get("type") == "text":
            text_parts.append(cleaned)
        else:
            fallback_parts.append(cleaned)

    if text_parts:
        return "\n\n".join(text_parts).strip()
    if allow_non_text_fallback:
        return "\n\n".join(fallback_parts).strip()
    return ""


class OpenCodeMessageProcessorMixin:
    """Pure-ish helpers that depend only on instance config."""

    def _extract_response_text(self, response: Dict[str, Any]) -> str:
        text = extract_opencode_response_text(response)
        parts = response.get("parts", [])

        if not text and isinstance(parts, list) and parts:
            part_types = [p.get("type") for p in parts if isinstance(p, dict)]
            msg_id = response.get("info", {}).get("id", "unknown")
            logger.info(
                "OpenCode message %s has no extractable text; part types: %s",
                msg_id,
                part_types,
            )

        return text


    def _to_relative_path(self, abs_path: str, cwd: str) -> str:
        """Convert absolute file paths to relative paths under cwd."""

        try:
            abs_path = os.path.abspath(os.path.expanduser(abs_path))
            cwd = os.path.abspath(os.path.expanduser(cwd))
            rel_path = os.path.relpath(abs_path, cwd)
            if rel_path.startswith("../.."):  # outside workspace
                return abs_path
            if not rel_path.startswith(".") and rel_path != ".":
                rel_path = "./" + rel_path
            return rel_path
        except Exception:
            return abs_path
