"""OpenCode agent implementation (coordinator).

Most heavy lifting lives in:
- server.py: OpenCodeServerManager
- poll_loop.py: unified poll loop
- session.py: session mapping + concurrency guards
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional

from core.show_pages import avibe_cloud_url_available
from core.system_prompt_injection import build_system_prompt_injection
from modules.agents.base import AgentRequest, BaseAgent

from .client_manager import OpenCodeClientManager
from .message_processor import OpenCodeMessageProcessorMixin
from .poll_loop import OpenCodePollLoop
from .server import OpenCodeServerManager
from .session import OpenCodeSessionManager

logger = logging.getLogger(__name__)


class OpenCodeAgent(OpenCodeMessageProcessorMixin, BaseAgent):
    """OpenCode Server API integration via HTTP."""

    name = "opencode"

    def __init__(self, controller, opencode_config):
        super().__init__(controller)
        self.opencode_config = opencode_config

        self._client_manager = OpenCodeClientManager(opencode_config)
        self._session_manager = OpenCodeSessionManager(self.settings_manager, self.name)

        self._poll_loop = OpenCodePollLoop(self)

        self._active_requests: Dict[str, asyncio.Task] = {}

    async def _get_server(self) -> OpenCodeServerManager:
        return await self._client_manager.get_server()

    async def refresh_runtime_config(self, opencode_config) -> None:
        """Reload persisted runtime config before restarting the shared server."""
        previous_server = await self._client_manager.reset_config(opencode_config)
        self.opencode_config = opencode_config
        self.controller.config.opencode = opencode_config
        if previous_server is not None:
            detach = getattr(previous_server, "detach_after_deferred_refresh", None)
            if callable(detach):
                await detach()
            elif hasattr(previous_server, "restart_for_auth_refresh"):
                await previous_server.restart_for_auth_refresh()
            reload_config = getattr(previous_server, "reload_runtime_config", None)
            if callable(reload_config):
                await reload_config(
                    binary=opencode_config.binary,
                    port=opencode_config.port,
                    request_timeout_seconds=opencode_config.request_timeout_seconds,
                )

    async def handle_message(self, request: AgentRequest) -> None:
        lock = self._session_manager.get_session_lock(request.base_session_id)
        task: Optional[asyncio.Task] = None

        async with lock:
            existing_task = self._active_requests.get(request.base_session_id)
            if existing_task and not existing_task.done():
                logger.info(
                    "OpenCode session %s already running; cancelling before new request",
                    request.base_session_id,
                )
                req_info = self._session_manager.get_request_session(request.base_session_id)
                if req_info:
                    server = await self._get_server()
                    await server.abort_session(req_info[0], req_info[1])
                    await self._session_manager.wait_for_session_idle(server, req_info[0], req_info[1])

                existing_task.cancel()
                try:
                    await existing_task
                except asyncio.CancelledError:
                    pass

                logger.info(
                    "OpenCode session %s cancelled; continuing with new request",
                    request.base_session_id,
                )

            task = asyncio.create_task(self._process_message(request))
            self._active_requests[request.base_session_id] = task

        if not task:
            return

        try:
            await task
        except asyncio.CancelledError:
            logger.debug(f"OpenCode task cancelled for {request.base_session_id}")
        finally:
            if self._active_requests.get(request.base_session_id) is task:
                self._active_requests.pop(request.base_session_id, None)
                self._session_manager.pop_request_session(request.base_session_id)
            # The poll loop ran to completion above (handle_message awaits the
            # task), so the turn is fully settled here. Release any web-Chat
            # stream waiter: a no-result failure (only a notify was emitted)
            # ends the spinner now instead of waiting out the safety timeout.
            # Token-guarded + no-op for IM/CLI; success already released via the
            # result emit during the poll. Defensive: tolerate controllers
            # without streaming completion support.
            _mark = getattr(self.controller, "mark_turn_complete", None)
            if callable(_mark):
                _mark(request.context)

    async def _process_message(self, request: AgentRequest) -> None:
        run_registered = False
        try:
            server = await self._get_server()
            await server.ensure_running()
        except Exception as e:
            logger.error(f"Failed to start OpenCode server: {e}", exc_info=True)
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                f"Failed to start OpenCode server: {e}",
            )
            self._note_turn_failed(request.context)  # terminal failure → red dot
            await self._remove_ack_reaction(request)
            return

        await self._delete_ack(request)
        await self._session_manager.ensure_working_dir(request.working_path)

        session_id = await self._session_manager.get_or_create_session_id(request, server)
        if not session_id:
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "Failed to obtain OpenCode session ID",
            )
            self._note_turn_failed(request.context)  # terminal failure → red dot
            await self._remove_ack_reaction(request)
            return

        self._session_manager.set_request_session(
            request.base_session_id,
            session_id,
            request.working_path,
            request.session_key,
        )

        if self._session_manager.mark_initialized(session_id):
            system_text = self._get_formatter(request.context).format_system_message(
                request.working_path, "init", session_id
            )
            await self.controller.emit_agent_message(
                request.context,
                "system",
                system_text,
                parse_mode="markdown",
            )

        try:
            override_agent, override_model, override_reasoning = self.controller.get_opencode_overrides(request.context)
            override_model = request.vibe_agent_model or override_model
            override_reasoning = request.vibe_agent_reasoning_effort or override_reasoning

            override_agent = request.subagent_name or override_agent
            if request.subagent_name:
                override_model = request.subagent_model
                override_reasoning = request.subagent_reasoning_effort

            if request.subagent_name and not override_model:
                override_model = server.get_agent_model_from_config(request.subagent_name)
            if request.subagent_name and not override_reasoning:
                override_reasoning = server.get_agent_reasoning_effort_from_config(request.subagent_name)

            agent_to_use = override_agent
            if not agent_to_use:
                agent_to_use = server.get_default_agent_from_config()

            model_dict = None
            model_str = override_model
            if not model_str:
                model_str = server.get_agent_model_from_config(agent_to_use)
            if model_str:
                parts = model_str.split("/", 1)
                if len(parts) == 2:
                    model_dict = {"providerID": parts[0], "modelID": parts[1]}
                else:
                    # Bare model id (no ``provider/`` prefix): only inject
                    # ``providerID`` when the user has explicitly chosen a
                    # default provider in Settings → Backends → OpenCode.
                    # Otherwise leave ``model_dict`` unset so OpenCode keeps
                    # using its own routing — silently forcing every legacy
                    # install onto Anthropic on upgrade breaks Ollama/OpenAI
                    # users who never visited the new settings page.
                    opencode_cfg = getattr(self.controller.config, "opencode", None)
                    default_provider = getattr(opencode_cfg, "default_provider", None)
                    if isinstance(default_provider, str) and default_provider.strip():
                        model_dict = {"providerID": default_provider.strip(), "modelID": model_str}

            reasoning_effort = override_reasoning
            if not reasoning_effort:
                reasoning_effort = server.get_agent_reasoning_effort_from_config(agent_to_use)

            baseline_message_ids: set[str] = set()
            try:
                baseline_messages = await server.list_messages(
                    session_id=session_id,
                    directory=request.working_path,
                )
                for message in baseline_messages:
                    message_id = message.get("info", {}).get("id")
                    if message_id:
                        baseline_message_ids.add(message_id)
            except Exception as err:
                logger.debug(f"Failed to snapshot OpenCode messages before prompt: {err}")

            # Prepare message with file attachment info if present
            prompt_text = self._prepare_message_with_files(request)
            platform = (
                request.context.platform
                or (request.context.platform_specific or {}).get("platform")
                or self.controller.config.platform
            )

            system_prompt_injection = build_system_prompt_injection(
                include_quick_replies=getattr(self.controller.config, "reply_enhancements", True)
                and platform != "wechat",
                include_show_pages=getattr(self.controller.config, "show_pages_prompt", True),
                avibe_cloud_connected=avibe_cloud_url_available(self.controller.config),
                context=request.context,
                fallback_platform=platform,
            )
            if request.vibe_agent_system_prompt:
                system_prompt_injection = f"{request.vibe_agent_system_prompt}\n\n{system_prompt_injection}"

            await server.prompt_async(
                session_id=session_id,
                directory=request.working_path,
                text=prompt_text,
                agent=agent_to_use,
                model=model_dict,
                reasoning_effort=reasoning_effort,
                system=system_prompt_injection,
                tools={"question": False},
            )
            await server.mark_run_active(session_id)
            run_registered = True

            logger.info(
                "Starting OpenCode poll loop for %s (thread=%s, cwd=%s)",
                session_id,
                request.base_session_id,
                request.working_path,
            )

            # ActivePollInfo stores raw settings_key + separate platform field;
            # request.session_key is the scoped session key (platform::raw_id),
            # so strip the prefix before persisting.
            raw_settings_key = request.session_key
            if "::" in raw_settings_key:
                raw_settings_key = raw_settings_key.split("::", 1)[1]
            platform_payload = request.context.platform_specific or {}

            self.sessions.add_active_poll(
                opencode_session_id=session_id,
                base_session_id=request.base_session_id,
                channel_id=request.context.channel_id,
                thread_id=request.context.thread_id,
                settings_key=raw_settings_key,
                working_path=request.working_path,
                baseline_message_ids=list(baseline_message_ids),
                ack_reaction_message_id=request.ack_reaction_message_id,
                ack_reaction_emoji=request.ack_reaction_emoji,
                typing_indicator_active=request.typing_indicator_active,
                context_token=str(platform_payload.get("context_token") or ""),
                processing_indicator=self.controller.processing_indicator.snapshot_request(request),
                user_id=request.context.user_id or "",
                platform=request.context.platform or platform_payload.get("platform") or "",
            )

            final_text, should_emit = await self._poll_loop.run_prompt_poll(
                request,
                server,
                session_id,
                agent_to_use=agent_to_use,
                model_dict=model_dict,
                reasoning_effort=reasoning_effort,
                baseline_message_ids=baseline_message_ids,
            )

            if not should_emit:
                self.sessions.remove_active_poll(session_id)
                await self._remove_ack_reaction(request)
                return

            if final_text:
                await self.emit_result_message(
                    request.context,
                    final_text,
                    subtype="success",
                    started_at=request.started_at,
                    parse_mode="markdown",
                    request=request,
                )
            else:
                await self.emit_result_message(
                    request.context,
                    "(No response from OpenCode)",
                    subtype="warning",
                    started_at=request.started_at,
                    request=request,
                )

            self.sessions.remove_active_poll(session_id)

        except asyncio.CancelledError:
            logger.info(f"OpenCode request cancelled for {request.base_session_id}")
            await self._remove_ack_reaction(request)
            if session_id:
                self.sessions.remove_active_poll(session_id)
            raise
        except Exception as e:
            error_name = type(e).__name__
            error_details = str(e).strip()
            error_text = f"{error_name}: {error_details}" if error_details else error_name

            logger.error(f"OpenCode request failed: {error_text}", exc_info=True)
            try:
                await server.abort_session(session_id, request.working_path)
            except Exception as abort_err:
                logger.warning(f"Failed to abort OpenCode session after error: {abort_err}")

            await self._remove_ack_reaction(request)
            if session_id:
                self.sessions.remove_active_poll(session_id)

            message = f"OpenCode request failed: {error_text}"
            handled = await self.controller.agent_auth_service.maybe_emit_auth_recovery_message(
                request.context,
                "opencode",
                message,
            )
            if not handled:
                await self.controller.emit_agent_message(
                    request.context,
                    "notify",
                    message,
                )
            # handled == True persists the durable recovery notify centrally in
            # ``maybe_emit_auth_recovery_message`` (which also latches the turn
            # failure for the workbench dot — auth AND non-auth); the not-handled
            # branch persists via ``emit_agent_message`` above.
        finally:
            if run_registered:
                await server.mark_run_inactive(session_id)

    async def handle_stop(self, request: AgentRequest) -> bool:
        task = self._active_requests.get(request.base_session_id)
        if not task or task.done():
            return False

        req_info = self._session_manager.get_request_session(request.base_session_id)
        opencode_session_id = None
        if req_info:
            opencode_session_id = req_info[0]
            try:
                server = await self._get_server()
                await server.abort_session(req_info[0], req_info[1])
            except Exception as e:
                logger.warning(f"Failed to abort OpenCode session: {e}")

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        if opencode_session_id:
            self.sessions.remove_active_poll(opencode_session_id)

        await self.controller.emit_agent_message(request.context, "notify", "Terminated OpenCode execution.")
        logger.info(f"OpenCode session {request.base_session_id} terminated via /stop")
        return True

    async def clear_sessions(self, session_key: str) -> int:
        self.sessions.clear_agent_sessions(session_key, self.name)
        terminated = 0
        for base_id, task in list(self._active_requests.items()):
            req_info = self._session_manager.get_request_session(base_id)
            if req_info and len(req_info) >= 3 and req_info[2] == session_key:
                opencode_session_id = req_info[0]
                if not task.done():
                    try:
                        server = await self._get_server()
                        await server.abort_session(req_info[0], req_info[1])
                    except Exception:
                        pass
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    terminated += 1
                self.sessions.remove_active_poll(opencode_session_id)
        return terminated

    async def _delete_ack(self, request: AgentRequest) -> None:
        service = getattr(self.controller, "processing_indicator", None)
        if service is not None:
            await service.delete_ack_message(request)
            return
        ack_id = request.ack_message_id
        if ack_id and hasattr(self.im_client, "delete_message"):
            try:
                await self.im_client.delete_message(request.context.channel_id, ack_id)
            except Exception as err:
                logger.debug("Could not delete ack message: %s", err)
            finally:
                request.ack_message_id = None

    # _remove_ack_reaction is inherited from BaseAgent

    async def restore_active_polls(self) -> int:
        """Restore active poll loops that were interrupted by vibe-remote restart."""

        active_polls = self.sessions.get_all_active_polls()
        if not active_polls:
            logger.debug("No active polls to restore")
            return 0

        restored_count = 0
        stale_poll_ids = []

        for session_id, poll_info in active_polls.items():
            try:
                server = await self._get_server()
                messages = await server.list_messages(
                    session_id=poll_info.opencode_session_id,
                    directory=poll_info.working_path,
                )
            except Exception as err:
                logger.warning(f"Failed to verify OpenCode session {session_id} for restoration: {err}")
                stale_poll_ids.append(session_id)
                continue

            has_in_progress = False
            last_assistant_finish = None
            for message in messages:
                info = message.get("info", {})
                if info.get("role") != "assistant":
                    continue
                time_info = info.get("time") or {}
                if not time_info.get("completed"):
                    has_in_progress = True
                    break
                last_assistant_finish = info.get("finish")

            session_still_active = has_in_progress or last_assistant_finish == "tool-calls"
            if not session_still_active:
                logger.info(f"OpenCode session {session_id} has completed, removing from active polls")
                await self._poll_loop.remove_restored_ack(poll_info)
                stale_poll_ids.append(session_id)
                continue

            logger.info(
                f"Restoring poll loop for OpenCode session {session_id} "
                f"(thread={poll_info.base_session_id}, cwd={poll_info.working_path})"
            )

            task = asyncio.create_task(self._run_restored_poll_loop_with_tracking(poll_info))
            self._active_requests[poll_info.base_session_id] = task
            self._session_manager.set_request_session(
                poll_info.base_session_id,
                poll_info.opencode_session_id,
                poll_info.working_path,
                f"{poll_info.platform}::{poll_info.settings_key}" if poll_info.platform else poll_info.settings_key,
            )
            restored_count += 1

        for session_id in stale_poll_ids:
            self.sessions.remove_active_poll(session_id)

        if restored_count > 0:
            logger.info(f"Restored {restored_count} active poll loop(s)")
        if stale_poll_ids:
            logger.info(f"Removed {len(stale_poll_ids)} stale active poll(s)")

        return restored_count

    async def _run_restored_poll_loop_with_tracking(self, poll_info) -> None:
        server = await self._get_server()
        await server.mark_run_active(poll_info.opencode_session_id)
        current_task = asyncio.current_task()
        try:
            await self._poll_loop.run_restored_poll_loop(poll_info)
        finally:
            await server.mark_run_inactive(poll_info.opencode_session_id)
            if self._active_requests.get(poll_info.base_session_id) is current_task:
                self._active_requests.pop(poll_info.base_session_id, None)
                self._session_manager.pop_request_session(poll_info.base_session_id)

    def _prepare_message_with_files(self, request: AgentRequest) -> str:
        """Prepare message with file attachment information.

        If there are file attachments, append file info to the message
        so the agent knows what files are available to read.
        Files are stored in ~/.vibe_remote/attachments/{channel_id}/.

        Args:
            request: The agent request containing message and files

        Returns:
            Message string, potentially with file info appended
        """
        if not request.files:
            return request.message

        # Build file info section
        images = []
        other_files = []

        for attachment in request.files:
            if not attachment.local_path:
                continue

            is_image = (attachment.mimetype or "").startswith("image/")
            if is_image:
                images.append(attachment)
            else:
                other_files.append(attachment)

        if not images and not other_files:
            return request.message

        # Format file info as a clear block at the end
        file_lines = ["", "[User Attachments]"]

        for img in images:
            size_str = f", {img.size} bytes" if img.size else ""
            file_lines.append(f"- Image: {img.local_path} ({img.mimetype}{size_str})")

        for f in other_files:
            size_str = f", {f.size} bytes" if f.size else ""
            file_lines.append(f"- File: {f.local_path} ({f.mimetype}{size_str})")

        file_info = "\n".join(file_lines)

        # If there's no text message, just use file info (without leading newline)
        if not request.message or not request.message.strip():
            return file_info.lstrip()

        # Append file info to message
        return f"{request.message}{file_info}"
