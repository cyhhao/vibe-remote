"""Codex agent — persistent app-server mode with JSON-RPC 2.0 transport."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from modules.agents.base import AgentRequest, BaseAgent
from modules.agents.codex.event_handler import CodexEventHandler
from modules.agents.codex.session import CodexSessionManager
from modules.agents.codex.transport import CodexTransport

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
        self._event_handler = CodexEventHandler(self)

        # base_session_id → active AgentRequest (for routing notifications)
        self._active_requests: Dict[str, AgentRequest] = {}
        # Track which base_sessions have emitted the init system message
        self._initialized_sessions: set[str] = set()

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

        # Store the active request so notifications can route to the right context
        self._active_requests[request.base_session_id] = request

        await self._delete_ack(request)

        try:
            # Get or create thread
            thread_id = self._session_mgr.get_thread_id(request.base_session_id)

            if not thread_id:
                thread_id = await self._start_thread(transport, request)

            # If a turn is active, interrupt it first
            active_turn = self._session_mgr.get_active_turn(request.base_session_id)
            if active_turn:
                await self.controller.emit_agent_message(
                    request.context,
                    "notify",
                    "⚠️ Interrupting previous Codex task...",
                )
                try:
                    await transport.send_request(
                        "turn/interrupt",
                        {"threadId": thread_id, "turnId": active_turn},
                    )
                    # Clear pending text for the interrupted turn
                    self._event_handler.clear_pending(active_turn)
                    self._session_mgr.clear_active_turn(request.base_session_id)
                except Exception as e:
                    logger.warning("Failed to interrupt turn %s: %s", active_turn, e)

            # Build input items
            input_items = self._build_input(request)

            # Read channel-level configuration overrides
            channel_settings = self.settings_manager.get_channel_settings(request.context.channel_id)
            routing = channel_settings.routing if channel_settings else None
            effective_model = (routing.codex_model if routing else None) or self.codex_config.default_model
            effective_effort = routing.codex_reasoning_effort if routing else None

            # Start a new turn
            turn_params: Dict[str, Any] = {
                "threadId": thread_id,
                "input": input_items,
            }
            if effective_model:
                turn_params["model"] = effective_model
            if effective_effort:
                turn_params["effort"] = effective_effort

            resp = await transport.send_request("turn/start", turn_params)
            turn_id = resp.get("turnId", "")
            if turn_id:
                self._session_mgr.set_active_turn(request.base_session_id, turn_id)
            logger.info(
                "Codex turn started: thread=%s turn=%s session=%s",
                thread_id,
                turn_id,
                request.composite_session_id,
            )

        except Exception as e:
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
        turn_id = self._session_mgr.get_active_turn(request.base_session_id)

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
            self._event_handler.clear_pending(turn_id)
            self._session_mgr.clear_active_turn(request.base_session_id)
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
        """Clear all sessions and optionally stop transports."""
        self.settings_manager.clear_agent_sessions(settings_key, self.name)

        count = self._session_mgr.clear_all()

        # Stop all transports — they'll be recreated on next message
        for cwd, transport in list(self._transports.items()):
            try:
                await transport.stop()
            except Exception as e:
                logger.warning("Error stopping transport for %s: %s", cwd, e)
        self._transports.clear()
        self._transport_locks.clear()
        self._active_requests.clear()
        self._initialized_sessions.clear()

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

        resp = await transport.send_request("thread/start", params)
        thread_id = resp.get("threadId", "")
        if not thread_id:
            raise RuntimeError("Codex thread/start returned no threadId")

        self._session_mgr.set_thread_id(request.base_session_id, thread_id)
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
        # Find the active request by threadId
        thread_id = params.get("threadId", "")
        request = self._find_request_for_thread(thread_id)
        if not request:
            logger.debug(
                "No active request for Codex notification %s (thread=%s)",
                method,
                thread_id,
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
        for base_id, request in self._active_requests.items():
            stored_thread = self._session_mgr.get_thread_id(base_id)
            if stored_thread == thread_id:
                return request
        return None

    async def _delete_ack(self, request: AgentRequest) -> None:
        ack_id = request.ack_message_id
        if ack_id and hasattr(self.im_client, "delete_message"):
            try:
                await self.im_client.delete_message(request.context.channel_id, ack_id)
            except Exception as err:
                logger.debug("Could not delete ack message: %s", err)
            finally:
                request.ack_message_id = None
