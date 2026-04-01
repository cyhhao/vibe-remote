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
        session_key: str,
    ) -> None:
        self._request_sessions[base_session_id] = (
            opencode_session_id,
            working_path,
            session_key,
        )

    def pop_request_session(self, base_session_id: str) -> Optional[RequestSessionTuple]:
        return self._request_sessions.pop(base_session_id, None)

    def pop_all_for_session_key(self, session_key: str) -> Dict[str, RequestSessionTuple]:
        matches: Dict[str, RequestSessionTuple] = {}
        for base_id, info in list(self._request_sessions.items()):
            if len(info) >= 3 and info[2] == session_key:
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
                logger.debug(f"Failed to poll OpenCode session {session_id} for idle: {err}")
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

    async def get_or_create_session_id(self, request: AgentRequest, server: OpenCodeServerManager) -> Optional[str]:
        """Get a cached OpenCode session id, or create a new session.

        The session mapping key includes working_path so that changing the
        working directory (e.g. via the Web UI) automatically creates a new
        session instead of reusing the old one anchored to a stale directory.
        This mirrors how Claude sessions use composite_key = base:working_path.
        """

        sessions = getattr(self._settings_manager, "sessions", self._settings_manager)
        payload = request.context.platform_specific or {}
        legacy_session_key = None
        if bool(payload.get("is_dm", False)) and request.context.user_id and request.context.channel_id:
            if request.context.channel_id != request.context.user_id:
                platform = request.context.platform or payload.get("platform") or ""
                if platform:
                    legacy_session_key = f"{platform}::{request.context.user_id}"

        # Include working_path in the mapping key so cwd changes create new sessions
        composite_session_key = f"{request.base_session_id}:{request.working_path}"

        get_with_fallback = getattr(sessions, "get_agent_session_id_with_fallback", None)
        if callable(get_with_fallback):
            session_id = get_with_fallback(
                request.session_key,
                legacy_session_key,
                composite_session_key,
                agent_name=self._agent_name,
            )
        else:
            session_id = sessions.get_agent_session_id(
                request.session_key,
                composite_session_key,
                agent_name=self._agent_name,
            )

        # Legacy fallback: migrate sessions stored under the old key format
        # (base_session_id only, without working_path) so existing users
        # don't lose session continuity on upgrade.
        if not session_id:
            if callable(get_with_fallback):
                legacy_id = get_with_fallback(
                    request.session_key,
                    legacy_session_key,
                    request.base_session_id,
                    agent_name=self._agent_name,
                )
            else:
                legacy_id = sessions.get_agent_session_id(
                    request.session_key,
                    request.base_session_id,
                    agent_name=self._agent_name,
                )
            if legacy_id:
                if await server.get_session(legacy_id, request.working_path):
                    sessions.set_agent_session_mapping(
                        request.session_key,
                        self._agent_name,
                        composite_session_key,
                        legacy_id,
                    )
                    logger.info(
                        "Migrated legacy OpenCode session %s to composite key for %s",
                        legacy_id,
                        request.base_session_id,
                    )
                    return legacy_id
                else:
                    logger.info(
                        "Legacy OpenCode session %s no longer valid, will create new session",
                        legacy_id,
                    )

        if not session_id:
            try:
                session_data = await server.create_session(
                    directory=request.working_path,
                )
                session_id = session_data.get("id")
                if session_id:
                    sessions.set_agent_session_mapping(
                        request.session_key,
                        self._agent_name,
                        composite_session_key,
                        session_id,
                    )
                    logger.info(f"Created OpenCode session {session_id} for {request.base_session_id}")
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
            )
            new_session_id = session_data.get("id")
            if new_session_id:
                sessions.set_agent_session_mapping(
                    request.session_key,
                    self._agent_name,
                    composite_session_key,
                    new_session_id,
                )
                logger.info(f"Recreated OpenCode session {new_session_id} for {request.base_session_id}")
                return new_session_id
        except Exception as e:
            logger.error(f"Failed to recreate session: {e}", exc_info=True)
            return None

        return None
