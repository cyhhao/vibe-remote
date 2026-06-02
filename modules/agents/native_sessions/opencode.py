from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from .base import NativeSessionProvider, build_tail_preview, dt_from_ts, normalize_title_text, parse_json_blob
from .types import BackendSessionTitle, NativeResumeSession

logger = logging.getLogger(__name__)


class OpenCodeNativeSessionProvider(NativeSessionProvider):
    agent_name = "opencode"
    _DEFAULT_TITLE_PREFIXES = ("New session - ", "Child session - ")

    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path or Path.home() / ".local" / "share" / "opencode" / "opencode.db")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)

    def list_metadata(self, working_path: str) -> list[NativeResumeSession]:
        if not self.db_path.exists():
            return []
        rows: list[NativeResumeSession] = []
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    SELECT id, title, time_created, time_updated
                    FROM session
                    WHERE directory = ?
                    ORDER BY time_updated DESC, id DESC
                    """,
                    (working_path,),
                )
                for session_id, title, created_ms, updated_ms in cursor.fetchall():
                    created_at = dt_from_ts(created_ms, millis=True)
                    updated_at = dt_from_ts(updated_ms, millis=True)
                    rows.append(
                        NativeResumeSession(
                            agent="opencode",
                            agent_prefix="oc",
                            native_session_id=session_id,
                            working_path=working_path,
                            created_at=created_at,
                            updated_at=updated_at,
                            sort_ts=(updated_at or created_at).timestamp() if (updated_at or created_at) else 0.0,
                            locator={"title": title or ""},
                        )
                    )
        except Exception as exc:
            logger.warning("Failed to list OpenCode sessions for %s: %s", working_path, exc)
        return rows

    def hydrate_preview(self, item: NativeResumeSession) -> NativeResumeSession:
        preview = ""
        if not self.db_path.exists():
            return item
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    SELECT m.data, p.data
                    FROM part p
                    JOIN message m ON m.id = p.message_id
                    WHERE p.session_id = ?
                    ORDER BY p.time_created DESC, p.id DESC
                    """,
                    (item.native_session_id,),
                )
                for message_blob, part_blob in cursor.fetchall():
                    message_data = parse_json_blob(message_blob)
                    if message_data.get("role") != "assistant":
                        continue
                    part_data = parse_json_blob(part_blob)
                    if part_data.get("type") != "text":
                        continue
                    text = str(part_data.get("text") or "").strip()
                    if text:
                        preview = text
                        break
        except Exception as exc:
            logger.warning("Failed to hydrate OpenCode session %s: %s", item.native_session_id, exc)
        item.last_agent_message = preview
        fallback = str(item.locator.get("title") or item.native_session_id)
        item.last_agent_tail = build_tail_preview(preview or fallback)
        return item

    @classmethod
    def is_default_title(cls, title: str) -> bool:
        return any(title.startswith(prefix) for prefix in cls._DEFAULT_TITLE_PREFIXES)

    def get_title(
        self,
        *,
        native_session_id: str,
        working_path: str,
        first_user_message: str = "",
    ) -> BackendSessionTitle | None:
        if not self.db_path.exists():
            return None
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT title
                    FROM session
                    WHERE id = ? AND directory = ?
                    LIMIT 1
                    """,
                    (native_session_id, working_path),
                ).fetchone()
        except Exception as exc:
            logger.warning("Failed to read OpenCode session title %s: %s", native_session_id, exc)
            return None
        title = normalize_title_text(str(row[0] or "")) if row else ""
        if not title or self.is_default_title(title):
            return None
        return BackendSessionTitle(title=title, source="backend", confidence="high")
