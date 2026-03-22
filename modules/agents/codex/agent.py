"""Codex agent — persistent app-server mode with JSON-RPC 2.0 transport."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from modules.agents.base import AgentRequest, BaseAgent
from modules.agents.codex.event_handler import CodexEventHandler
from modules.agents.codex.session import CodexSessionManager
from modules.agents.codex.transport import CodexTransport
from modules.agents.codex.turn_state import CodexTurnRegistry

logger = logging.getLogger(__name__)


class CodexAgent(BaseAgent):
    """Codex CLI integration via persistent ``codex app-server`` subprocess.

    One transport (subprocess) is maintained per unique working directory.
    Multiple Slack threads in the same channel share a transport but each
    gets its own Codex thread.
    """

    name = "codex"

    def __init__(self, controller: Any, codex_config: Any) -> None:
        super().__init__(controller)
        self.codex_config = codex_config

        # cwd → CodexTransport (one persistent process per working dir)
        self._transports: Dict[str, CodexTransport] = {}
        self._transport_locks: Dict[str, asyncio.Lock] = {}

        self._session_mgr = CodexSessionManager()
        self._turn_registry = CodexTurnRegistry()
        self._event_handler = CodexEventHandler(self)

        # base_session_id → asyncio.Lock (serialize turn lifecycle per session)
        self._session_locks: Dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # BaseAgent interface
    # ------------------------------------------------------------------

    async def handle_message(self, request: AgentRequest) -> None:
        """Process a user message by routing it through app-server.

        Flow:
        1. Get or create transport for the working directory
        2. Get or create a Codex thread for this Slack thread
        3. If a turn is active → interrupt it first
        4. Start a new turn with the user's message
        """
        try:
            transport = await self._get_or_create_transport(request.working_path)
        except FileNotFoundError:
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "❌ Codex CLI not found. Please install it or set CODEX_CLI_PATH.",
            )
            await self._remove_ack_reaction(request)
            return
        except Exception as e:
            logger.error("Failed to start Codex transport: %s", e, exc_info=True)
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                f"❌ Failed to start Codex CLI: {e}",
            )
            await self._remove_ack_reaction(request)
            return

        # Track settings_key and cwd for scoped invalidation
        self._session_mgr.set_settings_key(request.base_session_id, request.settings_key)
        self._session_mgr.set_cwd(request.base_session_id, request.working_path)

        await self._delete_ack(request)

        # Serialize turn lifecycle per session
        if request.base_session_id not in self._session_locks:
            self._session_locks[request.base_session_id] = asyncio.Lock()

        async with self._session_locks[request.base_session_id]:
            self._turn_registry.remember_request(request)
            try:
                # Get or create thread (with resume support)
                thread_id = self._session_mgr.get_thread_id(request.base_session_id)

                if not thread_id:
                    thread_id = await self._start_or_resume_thread(transport, request)

                # If a turn is active, interrupt it first
                active_turn = self._turn_registry.get_active_turn(request.base_session_id)
                if active_turn:
                    try:
                        await transport.send_request(
                            "turn/interrupt",
                            {"threadId": thread_id, "turnId": active_turn},
                        )
                    except Exception as e:
                        logger.warning("Failed to interrupt turn %s: %s", active_turn, e)
                        await self.controller.emit_agent_message(
                            request.context,
                            "notify",
                            f"❌ Failed to interrupt previous Codex turn: {e}",
                        )
                        await self._remove_ack_reaction(request)
                        return
                    interrupted_request = self._event_handler.clear_pending(active_turn)
                    if interrupted_request:
                        await self._remove_ack_reaction(interrupted_request)

                thread_id = await self._start_turn(transport, request, thread_id)

            except Exception as e:
                # Safety net: if the thread is stale (e.g. Codex server-side
                # expiry, or the proactive invalidation in _get_or_create_transport
                # was bypassed by a race), invalidate and retry once.
                if "thread not found" in str(e).lower():
                    logger.warning(
                        "Stale Codex thread for session %s, clearing and retrying: %s",
                        request.base_session_id,
                        e,
                    )
                    self._session_mgr.invalidate_thread(request.base_session_id)
                    self._turn_registry.clear_session(request.base_session_id)
                    self.sessions.clear_agent_session_mapping(
                        request.settings_key,
                        self.name,
                        request.base_session_id,
                    )
                    try:
                        thread_id = await self._start_or_resume_thread(transport, request)
                        await self._start_turn(transport, request, thread_id)
                        return  # retry succeeded
                    except Exception as retry_err:
                        e = retry_err  # fall through to normal error handling

                self._turn_registry.clear_pending_turn_start(request.base_session_id, request)
                logger.error("Error in Codex handle_message: %s", e, exc_info=True)
                await self.controller.emit_agent_message(
                    request.context,
                    "notify",
                    f"❌ Codex error: {e}",
                )
                await self._remove_ack_reaction(request)

    async def handle_stop(self, request: AgentRequest) -> bool:
        """Gracefully interrupt the active turn."""
        thread_id = self._session_mgr.get_thread_id(request.base_session_id)
        turn_id = self._turn_registry.get_active_turn(request.base_session_id)

        if not thread_id or not turn_id:
            return False

        transport = self._transports.get(request.working_path)
        if not transport or not transport.is_alive:
            return False

        try:
            await transport.send_request(
                "turn/interrupt",
                {"threadId": thread_id, "turnId": turn_id},
            )
            interrupted_request = self._event_handler.clear_pending(turn_id)
            if interrupted_request:
                await self._remove_ack_reaction(interrupted_request)
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "🛑 Terminated Codex execution.",
            )
            logger.info("Codex turn %s interrupted via /stop", turn_id)
            return True
        except Exception as e:
            logger.error("Failed to interrupt Codex turn: %s", e)
            return False

    async def clear_sessions(self, settings_key: str) -> int:
        """Clear sessions scoped to a specific settings_key."""
        self.sessions.clear_agent_sessions(settings_key, self.name)

        # Use settings_key index (not _threads) so sessions with
        # invalidated threads are still cleaned up properly.
        to_clear = self._session_mgr.get_sessions_by_settings_key(settings_key)

        count = self._session_mgr.clear_by_settings_key(settings_key)

        # Clean up in-memory turn state and session locks for cleared sessions
        for bid in to_clear:
            self._turn_registry.clear_session(bid)
            self._session_locks.pop(bid, None)

        return count

    # ------------------------------------------------------------------
    # Transport management
    # ------------------------------------------------------------------

    async def _get_or_create_transport(self, cwd: str) -> CodexTransport:
        """Return an initialized transport for the given working directory."""
        existing = self._transports.get(cwd)
        if existing and existing.is_initialized:
            return existing

        # Serialize creation per cwd
        if cwd not in self._transport_locks:
            self._transport_locks[cwd] = asyncio.Lock()

        async with self._transport_locks[cwd]:
            # Double-check after acquiring lock
            existing = self._transports.get(cwd)
            if existing and existing.is_initialized:
                return existing

            # Stop stale transport if any
            if existing:
                await existing.stop()
                # The new app-server process won't know about threads/turns
                # from the old process.  Invalidate only sessions bound to
                # this cwd so healthy sessions on other cwds are unaffected.
                affected = self._session_mgr.sessions_for_cwd(cwd)
                for bid in affected:
                    self._session_mgr.invalidate_thread(bid)
                    self._turn_registry.clear_session(bid)
                if affected:
                    logger.info(
                        "Invalidated %d stale Codex session(s) after transport restart for cwd=%s",
                        len(affected),
                        cwd,
                    )

            transport = CodexTransport(
                binary=self.codex_config.binary,
                cwd=cwd,
                extra_args=list(self.codex_config.extra_args),
            )

            # Wire up callbacks
            transport.on_notification(self._on_notification)
            transport.on_server_request(self._on_server_request)

            await transport.start()
            self._transports[cwd] = transport
            return transport

    # ------------------------------------------------------------------
    # Thread management
    # ------------------------------------------------------------------

    async def _start_thread(
        self,
        transport: CodexTransport,
        request: AgentRequest,
    ) -> str:
        """Create a new Codex thread and return its threadId."""
        params: Dict[str, Any] = {
            "cwd": request.working_path,
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
        }

        if getattr(self.controller.config, "reply_enhancements", True):
            from core.reply_enhancer import build_reply_enhancements_prompt

            params["developerInstructions"] = build_reply_enhancements_prompt(
                include_quick_replies=self.controller.config.platform != "wechat"
            )

        resp = await transport.send_request("thread/start", params)
        # thread/start returns Thread directly OR may nest under "thread"
        thread_id = resp.get("id", "")
        if not thread_id:
            thread_obj = resp.get("thread")
            if isinstance(thread_obj, dict):
                thread_id = thread_obj.get("id", "")
        if not thread_id:
            raise RuntimeError("Codex thread/start returned no thread id")

        self._session_mgr.set_thread_id(request.base_session_id, thread_id)
        # Also persist for resume support
        self.sessions.set_agent_session_mapping(
            request.settings_key,
            self.name,
            request.base_session_id,
            thread_id,
        )
        return thread_id

    async def _start_or_resume_thread(
        self,
        transport: CodexTransport,
        request: AgentRequest,
    ) -> str:
        """Try to resume a persisted thread, fall back to creating a new one."""
        # Check if we have a persisted Codex thread_id from settings_manager
        persisted = self.sessions.get_agent_session_id(
            request.settings_key,
            request.base_session_id,
            self.name,
        )
        if persisted:
            try:
                resp = await transport.send_request(
                    "thread/resume",
                    {"threadId": persisted},
                )
                # thread/resume returns Thread directly OR may nest under "thread"
                thread_id = resp.get("id", "")
                if not thread_id:
                    thread_obj = resp.get("thread")
                    if isinstance(thread_obj, dict):
                        thread_id = thread_obj.get("id", "")
                if thread_id:
                    self._session_mgr.set_thread_id(request.base_session_id, thread_id)
                    logger.info("Resumed Codex thread %s for session %s", thread_id, request.base_session_id)
                    return thread_id
            except Exception as e:
                logger.warning("Failed to resume Codex thread %s: %s, starting new", persisted, e)

        return await self._start_thread(transport, request)

    async def _start_turn(
        self,
        transport: CodexTransport,
        request: AgentRequest,
        thread_id: str,
    ) -> str:
        """Build input, configure overrides, and send turn/start to Codex."""
        input_items = self._build_input(request)

        channel_settings = self.settings_manager.get_channel_settings(request.settings_key)
        routing = channel_settings.routing if channel_settings else None
        effective_model = (routing.codex_model if routing else None) or self.codex_config.default_model
        effective_effort = routing.codex_reasoning_effort if routing else None

        turn_params: Dict[str, Any] = {
            "threadId": thread_id,
            "input": input_items,
            "approvalPolicy": "never",
            "sandbox": "danger-full-access",
        }
        if effective_model:
            turn_params["model"] = effective_model
        if effective_effort:
            turn_params["effort"] = effective_effort

        self._turn_registry.begin_turn_start(request, thread_id)
        resp = await transport.send_request("turn/start", turn_params)

        turn_id = resp.get("id", "")
        if not turn_id:
            turn_obj = resp.get("turn")
            if isinstance(turn_obj, dict):
                turn_id = turn_obj.get("id", "")
        if not turn_id:
            turn_id = self._turn_registry.get_bootstrapped_turn_id(request.base_session_id, request) or ""
        if not turn_id:
            raise RuntimeError("Codex turn/start returned no turn id")

        turn_state = self._turn_registry.finalize_turn_start_response(turn_id, request)
        logger.info(
            "Codex turn started: thread=%s turn=%s session=%s state=%s",
            thread_id,
            turn_id,
            request.composite_session_id,
            "registered" if turn_state else "already-finished",
        )
        return thread_id

    # ------------------------------------------------------------------
    # Input building
    # ------------------------------------------------------------------

    def _build_input(self, request: AgentRequest) -> list[Dict[str, Any]]:
        """Convert AgentRequest into Codex UserInput items."""
        items: list[Dict[str, Any]] = []

        # Text input
        message = request.message
        if request.files:
            # Append file info like Claude agent does
            file_lines = ["", "[User Attachments]"]
            for attachment in request.files:
                if not attachment.local_path:
                    continue
                is_image = (attachment.mimetype or "").startswith("image/")
                if is_image:
                    # Send as localImage input
                    items.append(
                        {
                            "type": "localImage",
                            "path": attachment.local_path,
                        }
                    )
                else:
                    size_str = f", {attachment.size} bytes" if attachment.size else ""
                    file_lines.append(f"- File: {attachment.local_path} ({attachment.mimetype}{size_str})")
            if len(file_lines) > 2:
                message = f"{message}\n" + "\n".join(file_lines)

        if message:
            items.insert(0, {"type": "text", "text": message})

        return items

    # ------------------------------------------------------------------
    # Callback handlers (wired to transport)
    # ------------------------------------------------------------------

    async def _on_notification(self, method: str, params: Dict[str, Any]) -> None:
        """Route a server notification to the event handler."""
        request = self._find_request_for_notification(method, params)
        if not request:
            thread_id = self._extract_thread_id(params)
            turn_id = self._extract_turn_id(params)
            logger.debug(
                "No active request for Codex notification %s (thread=%s turn=%s)",
                method,
                thread_id,
                turn_id,
            )
            return

        await self._event_handler.handle_notification(method, params, request)

    async def _on_server_request(
        self,
        req_id: int | str,
        method: str,
        params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Handle server requests — auto-approve all."""
        if method in (
            "item/commandExecution/requestApproval",
            "item/fileChange/requestApproval",
        ):
            logger.info("Auto-approving Codex %s (item=%s)", method, params.get("itemId"))
            return {"approved": True}

        logger.warning("Unknown Codex server request: %s", method)
        return {"approved": True}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_request_for_thread(self, thread_id: str) -> Optional[AgentRequest]:
        """Look up the active AgentRequest for a given Codex threadId."""
        base_session_id = self._session_mgr.find_base_session_id_for_thread(thread_id)
        if not base_session_id:
            return None
        return self._turn_registry.get_latest_request(base_session_id)

    def _find_request_for_notification(self, method: str, params: Dict[str, Any]) -> Optional[AgentRequest]:
        turn_id = self._extract_turn_id(params)
        if turn_id:
            request = self._turn_registry.get_request_for_turn(turn_id)
            if request:
                return request

            thread_id = self._extract_thread_id(params)
            if not thread_id:
                return None
            if method != "turn/started":
                return None
            base_session_id = self._session_mgr.find_base_session_id_for_thread(thread_id)
            if not base_session_id:
                return None

            bootstrap_state = self._turn_registry.bootstrap_turn(turn_id, base_session_id, thread_id)
            if bootstrap_state:
                logger.info(
                    "Bootstrapped Codex turn %s for notification %s on session %s",
                    turn_id,
                    method,
                    base_session_id,
                )
                return bootstrap_state.request
            return None

        thread_id = self._extract_thread_id(params)
        if thread_id:
            return self._find_request_for_thread(thread_id)
        return None

    def _extract_thread_id(self, params: Dict[str, Any]) -> str:
        thread_id = params.get("threadId", "")
        if not thread_id:
            thread_obj = params.get("thread")
            if isinstance(thread_obj, dict):
                thread_id = thread_obj.get("id", "")
        return thread_id

    def _extract_turn_id(self, params: Dict[str, Any]) -> str:
        turn_id = params.get("turnId", "")
        if not turn_id:
            turn_obj = params.get("turn")
            if isinstance(turn_obj, dict):
                turn_id = turn_obj.get("id", "")
        return turn_id

    async def _delete_ack(self, request: AgentRequest) -> None:
        ack_id = request.ack_message_id
        if ack_id and hasattr(self.im_client, "delete_message"):
            try:
                await self.im_client.delete_message(request.context.channel_id, ack_id)
            except Exception as err:
                logger.debug("Could not delete ack message: %s", err)
            finally:
                request.ack_message_id = None
