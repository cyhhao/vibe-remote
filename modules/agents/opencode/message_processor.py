"""Message processing helpers for OpenCode agent."""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

logger = logging.getLogger(__name__)


class OpenCodeMessageProcessorMixin:
    """Pure-ish helpers that depend only on instance config."""

    def _extract_response_text(self, response: Dict[str, Any]) -> str:
        parts = response.get("parts", [])
        text_parts = []

        for part in parts:
            part_type = part.get("type")
            if part_type == "text":
                text = part.get("text", "")
                if text:
                    text_parts.append(text)

        if not text_parts and parts:
            part_types = [p.get("type") for p in parts]
            logger.debug(
                f"OpenCode response has no text parts; part types: {part_types}"
            )

        return "\n\n".join(text_parts).strip()

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
