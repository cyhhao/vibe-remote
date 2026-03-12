"""Maps vibe-remote session keys to Codex thread/turn state."""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class CodexSessionManager:
    """Track Codex thread and turn IDs per vibe-remote base session.

    A *base_session_id* corresponds to a Slack thread (channel + thread_ts).
    Each base session maps to exactly one Codex ``threadId``.  At most one
    ``turnId`` may be active per base session at a time.
    """

    def __init__(self) -> None:
        # base_session_id → Codex threadId
        self._threads: dict[str, str] = {}
        # base_session_id → currently active Codex turnId
        self._active_turns: dict[str, str] = {}
        # base_session_id → settings_key (for scoped clear)
        self._settings_keys: dict[str, str] = {}

    # -- Thread mapping ---------------------------------------------------

    def get_thread_id(self, base_session_id: str) -> Optional[str]:
        return self._threads.get(base_session_id)

    def set_thread_id(self, base_session_id: str, thread_id: str) -> None:
        self._threads[base_session_id] = thread_id
        logger.info("Session %s → Codex thread %s", base_session_id, thread_id)

    # -- Turn tracking ----------------------------------------------------

    def get_active_turn(self, base_session_id: str) -> Optional[str]:
        return self._active_turns.get(base_session_id)

    def set_active_turn(self, base_session_id: str, turn_id: str) -> None:
        self._active_turns[base_session_id] = turn_id

    def clear_active_turn(self, base_session_id: str) -> None:
        self._active_turns.pop(base_session_id, None)

    # -- Settings-key tracking --------------------------------------------

    def set_settings_key(self, base_session_id: str, settings_key: str) -> None:
        self._settings_keys[base_session_id] = settings_key

    def clear_by_settings_key(self, settings_key: str) -> int:
        """Remove all sessions associated with a given settings_key. Returns count cleared."""
        to_remove = [bid for bid, sk in self._settings_keys.items() if sk == settings_key]
        for bid in to_remove:
            self._threads.pop(bid, None)
            self._active_turns.pop(bid, None)
            self._settings_keys.pop(bid, None)
        return len(to_remove)

    # -- Cleanup ----------------------------------------------------------

    def clear(self, base_session_id: str) -> None:
        """Remove all state for a session."""
        self._threads.pop(base_session_id, None)
        self._active_turns.pop(base_session_id, None)
        self._settings_keys.pop(base_session_id, None)

    def clear_all(self) -> int:
        """Remove all tracked sessions. Returns count cleared."""
        count = len(self._threads)
        self._threads.clear()
        self._active_turns.clear()
        self._settings_keys.clear()
        return count

    def all_thread_ids(self) -> list[str]:
        """Return all known Codex thread IDs (for archiving on shutdown)."""
        return list(self._threads.values())

    def all_base_sessions(self) -> list[str]:
        """Return all base session IDs being tracked."""
        return list(self._threads.keys())
