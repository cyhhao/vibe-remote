"""OpenCode session bookkeeping.

This module owns per-thread locks and mapping from Slack thread (base_session_id)
to OpenCode session IDs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Dict, Optional, Tuple

from modules.agents.base import AgentRequest

from .server import OpenCodeServerManager

logger = logging.getLogger(__name__)


RequestSessionTuple = Tuple[str, str, str]


class OpenCodeSessionManager:
    """Manage OpenCode session ids and concurrency guards."""

    def __init__(self, settings_manager, agent_name: str):
        self._settings_manager = settings_manager
        self._agent_name = agent_name

        self._request_sessions: Dict[str, RequestSessionTuple] = {}
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._initialized_sessions: set[str] = set()

    def get_request_session(self, base_session_id: str) -> Optional[RequestSessionTuple]:
        return self._request_sessions.get(base_session_id)

    def set_request_session(
        self,
        base_session_id: str,
        opencode_session_id: str,
        working_path: str,
        settings_key: str,
    ) -> None:
        self._request_sessions[base_session_id] = (
            opencode_session_id,
            working_path,
            settings_key,
        )

    def pop_request_session(self, base_session_id: str) -> Optional[RequestSessionTuple]:
        return self._request_sessions.pop(base_session_id, None)

    def pop_all_for_settings_key(self, settings_key: str) -> Dict[str, RequestSessionTuple]:
        matches: Dict[str, RequestSessionTuple] = {}
        for base_id, info in list(self._request_sessions.items()):
            if len(info) >= 3 and info[2] == settings_key:
                matches[base_id] = info
        return matches

    def mark_initialized(self, opencode_session_id: str) -> bool:
        """Return True if this session was newly marked initialized."""

        if opencode_session_id in self._initialized_sessions:
            return False
        self._initialized_sessions.add(opencode_session_id)
        return True

    def get_session_lock(self, base_session_id: str) -> asyncio.Lock:
        if base_session_id not in self._session_locks:
            self._session_locks[base_session_id] = asyncio.Lock()
        return self._session_locks[base_session_id]

    async def wait_for_session_idle(
        self,
        server: OpenCodeServerManager,
        session_id: str,
        directory: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                messages = await server.list_messages(session_id, directory)
            except Exception as err:
                logger.debug(
                    f"Failed to poll OpenCode session {session_id} for idle: {err}"
                )
                await asyncio.sleep(1.0)
                continue

            in_progress = False
            for message in messages:
                info = message.get("info", {})
                if info.get("role") != "assistant":
                    continue
                time_info = info.get("time") or {}
                if not time_info.get("completed"):
                    in_progress = True
                    break

            if not in_progress:
                return

            await asyncio.sleep(1.0)

        logger.warning(
            "OpenCode session %s did not reach idle state within %.1fs",
            session_id,
            timeout_seconds,
        )

    async def ensure_working_dir(self, working_path: str) -> None:
        if not os.path.exists(working_path):
            os.makedirs(working_path, exist_ok=True)

    async def get_or_create_session_id(
        self, request: AgentRequest, server: OpenCodeServerManager
    ) -> Optional[str]:
        """Get a cached OpenCode session id, or create a new session."""

        session_id = self._settings_manager.get_agent_session_id(
            request.settings_key,
            request.base_session_id,
            agent_name=self._agent_name,
        )

        if not session_id:
            try:
                session_data = await server.create_session(
                    directory=request.working_path,
                    title=f"vibe-remote:{request.base_session_id}",
                )
                session_id = session_data.get("id")
                if session_id:
                    self._settings_manager.set_agent_session_mapping(
                        request.settings_key,
                        self._agent_name,
                        request.base_session_id,
                        session_id,
                    )
                    logger.info(
                        f"Created OpenCode session {session_id} for {request.base_session_id}"
                    )
            except Exception as e:
                logger.error(f"Failed to create OpenCode session: {e}", exc_info=True)
                return None
            return session_id

        existing = await server.get_session(session_id, request.working_path)
        if existing:
            return session_id

        try:
            session_data = await server.create_session(
                directory=request.working_path,
                title=f"vibe-remote:{request.base_session_id}",
            )
            new_session_id = session_data.get("id")
            if new_session_id:
                self._settings_manager.set_agent_session_mapping(
                    request.settings_key,
                    self._agent_name,
                    request.base_session_id,
                    new_session_id,
                )
                logger.info(
                    f"Recreated OpenCode session {new_session_id} for {request.base_session_id}"
                )
                return new_session_id
        except Exception as e:
            logger.error(f"Failed to recreate session: {e}", exc_info=True)
            return None

        return None
