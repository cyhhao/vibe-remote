from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from .base import (
    NativeSessionProvider,
    build_tail_preview,
    dt_from_ts,
    normalize_preview_text,
    read_json_lines,
)
from .types import NativeResumeSession

logger = logging.getLogger(__name__)


def encode_project_path(working_path: str) -> str:
    return working_path.replace("/", "-")


class ClaudeNativeSessionProvider(NativeSessionProvider):
    agent_name = "claude"

    def __init__(self, root: str | None = None, history_path: str | None = None):
        self.root = Path(root or Path.home() / ".claude" / "projects")
        self.history_path = Path(history_path or Path.home() / ".claude" / "history.jsonl")

    def _project_dir(self, working_path: str) -> Path:
        return self.root / encode_project_path(working_path)

    @staticmethod
    def _candidate_project_names(working_path: str) -> set[str]:
        collapsed = re.sub(r"[^A-Za-z0-9]+", "-", working_path).strip("-")
        names = {
            encode_project_path(working_path),
            encode_project_path(working_path).replace("_", "-"),
        }
        if collapsed:
            names.add(f"-{collapsed}")
        return names

    @staticmethod
    def _merge_session(
        results: dict[str, NativeResumeSession],
        item: NativeResumeSession,
    ) -> None:
        existing = results.get(item.native_session_id)
        if existing is None:
            results[item.native_session_id] = item
            return

        if item.created_at and (existing.created_at is None or item.created_at < existing.created_at):
            existing.created_at = item.created_at
        if item.updated_at and (existing.updated_at is None or item.updated_at > existing.updated_at):
            existing.updated_at = item.updated_at
        existing.sort_ts = max(existing.sort_ts, item.sort_ts)

        for key, value in item.locator.items():
            if value and not existing.locator.get(key):
                existing.locator[key] = value

    @staticmethod
    def _parse_iso(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        except Exception:
            return None

    def list_metadata(self, working_path: str) -> list[NativeResumeSession]:
        candidate_names = self._candidate_project_names(working_path)
        results: dict[str, NativeResumeSession] = {}

        project_dirs: list[Path] = []
        seen_dirs: set[Path] = set()
        for name in candidate_names:
            path = self.root / name
            if path.exists() and path.is_dir() and path not in seen_dirs:
                project_dirs.append(path)
                seen_dirs.add(path)
        for path in sorted(self.root.iterdir()) if self.root.exists() else []:
            if not path.is_dir() or path in seen_dirs:
                continue
            project_dirs.append(path)
            seen_dirs.add(path)

        for project_dir in project_dirs:
            index_path = project_dir / "sessions-index.json"
            if index_path.is_file():
                try:
                    payload = json.loads(index_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning("Failed to parse Claude sessions index %s: %s", index_path, exc)
                    payload = {}

                entries = payload.get("entries", []) if isinstance(payload, dict) else []
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    session_id = str(entry.get("sessionId") or "").strip()
                    project_path = str(entry.get("projectPath") or "").strip()
                    if not session_id or project_path != working_path:
                        continue
                    created_at = self._parse_iso(entry.get("created"))
                    updated_at = self._parse_iso(entry.get("modified"))
                    self._merge_session(
                        results,
                        NativeResumeSession(
                            agent="claude",
                            agent_prefix="cc",
                            native_session_id=session_id,
                            working_path=working_path,
                            created_at=created_at,
                            updated_at=updated_at,
                            sort_ts=(updated_at or created_at).timestamp() if (updated_at or created_at) else 0.0,
                            locator={
                                "full_path": entry.get("fullPath") or str(project_dir / f"{session_id}.jsonl"),
                                "first_prompt": str(entry.get("firstPrompt") or ""),
                            },
                        ),
                    )

            for jsonl_path in sorted(project_dir.glob("*.jsonl")):
                session_id = jsonl_path.stem
                rows = read_json_lines(jsonl_path)
                if not rows:
                    continue
                created_at = None
                updated_at = None
                first_prompt = ""
                matched_working_path = False
                inferred_working_path = ""
                for row in rows:
                    row_cwd = str(row.get("cwd") or row.get("projectPath") or "").strip()
                    if row_cwd:
                        inferred_working_path = row_cwd
                    if row_cwd == working_path:
                        matched_working_path = True
                    if row.get("type") == "user" and not first_prompt:
                        first_prompt = str((row.get("message") or {}).get("content") or "")
                    timestamp = self._parse_iso(row.get("timestamp"))
                    if timestamp and created_at is None:
                        created_at = timestamp
                    if timestamp:
                        updated_at = timestamp
                if not matched_working_path and inferred_working_path and inferred_working_path != working_path:
                    continue
                if not matched_working_path and not inferred_working_path and project_dir.name not in candidate_names:
                    continue
                if not (created_at or updated_at):
                    stat = jsonl_path.stat()
                    created_at = datetime.fromtimestamp(stat.st_ctime)
                    updated_at = datetime.fromtimestamp(stat.st_mtime)
                self._merge_session(
                    results,
                    NativeResumeSession(
                        agent="claude",
                        agent_prefix="cc",
                        native_session_id=session_id,
                        working_path=working_path,
                        created_at=created_at,
                        updated_at=updated_at,
                        sort_ts=(updated_at or created_at).timestamp() if (updated_at or created_at) else 0.0,
                        locator={"full_path": str(jsonl_path), "first_prompt": first_prompt},
                    ),
                )

        if self.history_path.exists():
            latest_history: dict[str, dict[str, str | float | datetime | None]] = {}
            for row in read_json_lines(self.history_path):
                project_path = str(row.get("project") or "").strip()
                session_id = str(row.get("sessionId") or "").strip()
                if project_path != working_path or not session_id:
                    continue
                updated_at = dt_from_ts(row.get("timestamp"), millis=True)
                sort_ts = updated_at.timestamp() if updated_at else 0.0
                current = latest_history.get(session_id)
                if current and sort_ts <= float(current.get("sort_ts") or 0.0):
                    continue
                latest_history[session_id] = {
                    "updated_at": updated_at,
                    "sort_ts": sort_ts,
                    "display": str(row.get("display") or "").strip(),
                }

            for session_id, payload in latest_history.items():
                updated_at = payload.get("updated_at")
                self._merge_session(
                    results,
                    NativeResumeSession(
                        agent="claude",
                        agent_prefix="cc",
                        native_session_id=session_id,
                        working_path=working_path,
                        created_at=updated_at if isinstance(updated_at, datetime) else None,
                        updated_at=updated_at if isinstance(updated_at, datetime) else None,
                        sort_ts=float(payload.get("sort_ts") or 0.0),
                        locator={"history_display": str(payload.get("display") or "")},
                    ),
                )

        items = list(results.values())
        items.sort(key=lambda item: (-item.sort_ts, item.native_session_id))
        return items

    def hydrate_preview(self, item: NativeResumeSession) -> NativeResumeSession:
        preview = ""
        full_path_raw = str(item.locator.get("full_path") or "").strip()
        full_path = Path(full_path_raw) if full_path_raw else None
        if full_path and full_path.exists():
            rows = read_json_lines(full_path)
            for row in reversed(rows):
                if row.get("type") != "assistant":
                    continue
                message = row.get("message") or {}
                content = message.get("content")
                if isinstance(content, str):
                    preview = content
                    break
                if isinstance(content, list):
                    parts: list[str] = []
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = str(part.get("text") or "").strip()
                            if text:
                                parts.append(text)
                    if parts:
                        preview = "\n".join(parts)
                        break
        if not preview:
            preview = str(
                item.locator.get("first_prompt") or item.locator.get("history_display") or ""
            )
        item.last_agent_message = normalize_preview_text(preview)
        item.last_agent_tail = build_tail_preview(item.last_agent_message or preview or item.native_session_id)
        return item
