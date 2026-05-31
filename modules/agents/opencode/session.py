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

from modules.agents.base import AgentRequest, BaseAgent

from .server import OpenCodeServerManager


class OpenCodeResumeUnavailableError(RuntimeError):
    """The OpenCode session associated with this conversation can no longer be
    validated on the server. Raised instead of silently creating a fresh session,
    so the user is told the context is gone (product decision: no silent
    fallbacks)."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(
            f"Could not resume the previous OpenCode session ({session_id}); it may have expired. "
            "Not creating a new one to avoid silently losing context — start a new session to continue."
        )

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

    def _set_request_agent_session_id(self, request: AgentRequest, agent_session_id: Optional[str]) -> None:
        if not agent_session_id:
            return
        payload = dict(request.context.platform_specific or {})
        payload["agent_session_id"] = agent_session_id
        request.context.platform_specific = payload

    def _reserved_agent_session_id(self, request: AgentRequest) -> Optional[str]:
        payload = request.context.platform_specific or {}
        session_target = payload.get("agent_session_target")
        if isinstance(session_target, dict):
            target_id = str(session_target.get("id") or "").strip()
            if target_id:
                return target_id
        return None

    def ensure_agent_session_id(self, request: AgentRequest, composite_session_key: str) -> Optional[str]:
        reserved_id = self._reserved_agent_session_id(request)
        if reserved_id:
            self._set_request_agent_session_id(request, reserved_id)
            return reserved_id
        sessions = getattr(self._settings_manager, "sessions", self._settings_manager)
        ensure = getattr(sessions, "ensure_agent_session_id", None)
        if callable(ensure):
            agent_session_id = ensure(request.session_key, self._agent_name, composite_session_key)
        else:
            getter = getattr(sessions, "get_agent_session_row_id", None)
            agent_session_id = (
                getter(request.session_key, composite_session_key, self._agent_name)
                if callable(getter)
                else None
            )
        self._set_request_agent_session_id(request, agent_session_id)
        return agent_session_id

    def bind_agent_session_id(
        self,
        request: AgentRequest,
        composite_session_key: str,
        opencode_session_id: str,
    ) -> Optional[str]:
        sessions = getattr(self._settings_manager, "sessions", self._settings_manager)
        reserved_id = self._reserved_agent_session_id(request)
        if reserved_id:
            bind_by_id = getattr(sessions, "bind_agent_session_by_id", None)
            if callable(bind_by_id):
                agent_session_id = bind_by_id(
                    reserved_id,
                    opencode_session_id,
                    workdir=request.working_path,
                    vibe_agent_id=request.vibe_agent_id,
                    vibe_agent_name=request.vibe_agent_name,
                )
                if agent_session_id:
                    self._set_request_agent_session_id(request, agent_session_id)
                    return agent_session_id
            self._set_request_agent_session_id(request, reserved_id)
            return reserved_id
        binder = getattr(sessions, "bind_agent_session", None)
        if callable(binder):
            agent_session_id = binder(
                request.session_key,
                self._agent_name,
                composite_session_key,
                opencode_session_id,
            )
        else:
            sessions.set_agent_session_mapping(
                request.session_key,
                self._agent_name,
                composite_session_key,
                opencode_session_id,
            )
            agent_session_id = None
        if not agent_session_id:
            agent_session_id = self.ensure_agent_session_id(request, composite_session_key)
        else:
            self._set_request_agent_session_id(request, agent_session_id)
        return agent_session_id

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

        # Include working_path in the mapping key so cwd changes create new sessions
        composite_session_key = f"{request.base_session_id}:{request.working_path}"
        self.ensure_agent_session_id(request, composite_session_key)

        # Prefer the native session bound to the RESERVED workbench row (by PK):
        # the by-PK bind WRITE and this resume READ must agree, else avibe forks a
        # fresh session after a restart (context loss). The server-validation below
        # still handles a reserved native that no longer exists on the server.
        # IM/CLI turns (no reserved target) fall back to the projection.
        session_id = BaseAgent._reserved_native_session_id(request.context, self._agent_name) or sessions.get_agent_session_id(
            request.session_key,
            composite_session_key,
            agent_name=self._agent_name,
        )

        # Legacy fallback: migrate sessions stored under the old key format
        # (base_session_id only, without working_path) so existing users
        # don't lose session continuity on upgrade.
        if not session_id:
            legacy_id = sessions.get_agent_session_id(
                request.session_key,
                request.base_session_id,
                agent_name=self._agent_name,
            )
            if legacy_id:
                if await server.get_session(legacy_id, request.working_path):
                    self.bind_agent_session_id(request, composite_session_key, legacy_id)
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
                    title=f"vibe-remote:{request.base_session_id}",
                )
                session_id = session_data.get("id")
                if session_id:
                    self.bind_agent_session_id(request, composite_session_key, session_id)
                    logger.info(f"Created OpenCode session {session_id} for {request.base_session_id}")
            except Exception as e:
                logger.error(f"Failed to create OpenCode session: {e}", exc_info=True)
                return None
            return session_id

        # raise_on_error=True so a transport/connection failure propagates as a
        # transient server error (handled by the normal error path) rather than
        # being mislabeled as expiry — only a genuine "not found" (None) is
        # treated as context loss below.
        existing = await server.get_session(session_id, request.working_path, raise_on_error=True)
        if existing:
            self.bind_agent_session_id(request, composite_session_key, session_id)
            return session_id

        # FAIL LOUD: an existing mapped session the server says is gone is context
        # loss — surface it rather than silently creating a fresh session (product
        # decision: no silent fallbacks). A fresh session is only created when
        # there was NO prior mapping (handled above).
        raise OpenCodeResumeUnavailableError(session_id)
