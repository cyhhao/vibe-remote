"""OpenCode agent implementation (coordinator).

Most heavy lifting lives in:
- server.py: OpenCodeServerManager
- question_handler.py: question UI + answer submission
- poll_loop.py: unified poll loop
- session.py: session mapping + concurrency guards
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Optional

from modules.agents.base import AgentRequest, BaseAgent

from .client_manager import OpenCodeClientManager
from .message_processor import OpenCodeMessageProcessorMixin
from .poll_loop import OpenCodePollLoop
from .question_handler import OpenCodeQuestionHandler
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

        self._question_handler = OpenCodeQuestionHandler(
            self.controller,
            self.im_client,
            self.settings_manager,
            get_server=self._get_server,
        )
        self._poll_loop = OpenCodePollLoop(self, self._question_handler)

        self._active_requests: Dict[str, asyncio.Task] = {}

    async def _get_server(self) -> OpenCodeServerManager:
        return await self._client_manager.get_server()

    async def handle_message(self, request: AgentRequest) -> None:
        lock = self._session_manager.get_session_lock(request.base_session_id)
        open_modal_task: Optional[asyncio.Task] = None
        task: Optional[asyncio.Task] = None

        async with lock:
            pending = self._question_handler.get_pending(request.base_session_id)
            is_modal_open = pending and request.message == "opencode_question:open_modal"
            is_answer_submission = pending and not is_modal_open

            existing_task = self._active_requests.get(request.base_session_id)
            if existing_task and not existing_task.done():
                if is_modal_open:
                    logger.info(
                        "OpenCode session %s running; opening modal without cancel",
                        request.base_session_id,
                    )
                elif is_answer_submission:
                    logger.info(
                        "OpenCode session %s running; submitting answer without cancel",
                        request.base_session_id,
                    )
                else:
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

            if is_modal_open:
                if hasattr(self.im_client, "open_opencode_question_modal"):
                    open_modal_task = asyncio.create_task(
                        self._question_handler.open_question_modal(request, pending)  # type: ignore[arg-type]
                    )
                    # Clean up reaction for modal open request
                    await self._remove_ack_reaction(request)
                else:
                    task = asyncio.create_task(self._process_message(request))
                    self._active_requests[request.base_session_id] = task
            elif is_answer_submission:
                server = await self._get_server()
                await self._question_handler.process_question_answer(
                    request,
                    pending,
                    server,  # type: ignore[arg-type]
                )
                # Clean up reaction for answer submission
                await self._remove_ack_reaction(request)
                return
            else:
                task = asyncio.create_task(self._process_message(request))
                self._active_requests[request.base_session_id] = task

        if open_modal_task:
            await open_modal_task
            return

        if not task:
            return

        try:
            await task
        except asyncio.CancelledError:
            logger.debug(f"OpenCode task cancelled for {request.base_session_id}")
            await self._question_handler.clear(request.base_session_id)
        finally:
            if self._active_requests.get(request.base_session_id) is task:
                self._active_requests.pop(request.base_session_id, None)
                self._session_manager.pop_request_session(request.base_session_id)

    async def _process_message(self, request: AgentRequest) -> None:
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
            await self._remove_ack_reaction(request)
            return

        self._session_manager.set_request_session(
            request.base_session_id,
            session_id,
            request.working_path,
            request.settings_key,
        )

        if self._session_manager.mark_initialized(session_id):
            system_text = self.im_client.formatter.format_system_message(request.working_path, "init", session_id)
            await self.controller.emit_agent_message(
                request.context,
                "system",
                system_text,
                parse_mode="markdown",
            )

        try:
            override_agent, override_model, override_reasoning = self.controller.get_opencode_overrides(request.context)

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

            await server.prompt_async(
                session_id=session_id,
                directory=request.working_path,
                text=prompt_text,
                agent=agent_to_use,
                model=model_dict,
                reasoning_effort=reasoning_effort,
            )

            logger.info(
                "Starting OpenCode poll loop for %s (thread=%s, cwd=%s)",
                session_id,
                request.base_session_id,
                request.working_path,
            )

            self.settings_manager.add_active_poll(
                opencode_session_id=session_id,
                base_session_id=request.base_session_id,
                channel_id=request.context.channel_id,
                thread_id=request.context.thread_id,
                settings_key=request.settings_key,
                working_path=request.working_path,
                baseline_message_ids=list(baseline_message_ids),
                ack_reaction_message_id=request.ack_reaction_message_id,
                ack_reaction_emoji=request.ack_reaction_emoji,
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
                # Clean up reaction even if we don't emit a result
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

            # Clean up answer reaction after result is sent
            await self._question_handler.clear(request.base_session_id)
            self.settings_manager.remove_active_poll(session_id)

        except asyncio.CancelledError:
            logger.info(f"OpenCode request cancelled for {request.base_session_id}")
            await self._question_handler.clear(request.base_session_id)
            await self._remove_ack_reaction(request)
            if session_id:
                self.settings_manager.remove_active_poll(session_id)
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

            # Clean up answer reaction on error
            await self._question_handler.clear(request.base_session_id)
            await self._remove_ack_reaction(request)
            if session_id:
                self.settings_manager.remove_active_poll(session_id)

            await self.controller.emit_agent_message(
                request.context,
                "notify",
                f"OpenCode request failed: {error_text}",
            )

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
            self.settings_manager.remove_active_poll(opencode_session_id)

        await self.controller.emit_agent_message(request.context, "notify", "Terminated OpenCode execution.")
        logger.info(f"OpenCode session {request.base_session_id} terminated via /stop")
        return True

    async def clear_sessions(self, settings_key: str) -> int:
        self.settings_manager.clear_agent_sessions(settings_key, self.name)
        terminated = 0
        for base_id, task in list(self._active_requests.items()):
            req_info = self._session_manager.get_request_session(base_id)
            if req_info and len(req_info) >= 3 and req_info[2] == settings_key:
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
                self.settings_manager.remove_active_poll(opencode_session_id)
        return terminated

    async def _delete_ack(self, request: AgentRequest) -> None:
        ack_id = request.ack_message_id
        if ack_id and hasattr(self.im_client, "delete_message"):
            try:
                await self.im_client.delete_message(request.context.channel_id, ack_id)
            except Exception as err:
                logger.debug(f"Could not delete ack message: {err}")
            finally:
                request.ack_message_id = None

    async def _remove_ack_reaction(self, request: AgentRequest) -> None:
        """Remove acknowledgement reaction on error paths."""
        if request.ack_reaction_message_id and request.ack_reaction_emoji:
            try:
                await self.im_client.remove_reaction(
                    request.context,
                    request.ack_reaction_message_id,
                    request.ack_reaction_emoji,
                )
            except Exception as err:
                logger.debug(f"Could not remove ack reaction: {err}")
            finally:
                request.ack_reaction_message_id = None
                request.ack_reaction_emoji = None

    async def restore_active_polls(self) -> int:
        """Restore active poll loops that were interrupted by vibe-remote restart."""

        active_polls = self.settings_manager.get_all_active_polls()
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
                stale_poll_ids.append(session_id)
                continue

            logger.info(
                f"Restoring poll loop for OpenCode session {session_id} "
                f"(thread={poll_info.base_session_id}, cwd={poll_info.working_path})"
            )

            task = asyncio.create_task(self._poll_loop.run_restored_poll_loop(poll_info))
            self._active_requests[poll_info.base_session_id] = task
            self._session_manager.set_request_session(
                poll_info.base_session_id,
                poll_info.opencode_session_id,
                poll_info.working_path,
                poll_info.settings_key,
            )
            restored_count += 1

        for session_id in stale_poll_ids:
            self.settings_manager.remove_active_poll(session_id)

        if restored_count > 0:
            logger.info(f"Restored {restored_count} active poll loop(s)")
        if stale_poll_ids:
            logger.info(f"Removed {len(stale_poll_ids)} stale active poll(s)")

        return restored_count

    def _prepare_message_with_files(self, request: AgentRequest) -> str:
        """Prepare message with file attachment information.

        If there are file attachments, prepend file info to the message
        so the agent knows what files are available to read.

        Args:
            request: The agent request containing message and files

        Returns:
            Message string, potentially with file info prepended
        """
        if not request.files:
            return request.message

        # Build file info section
        file_info_parts = []
        images = []
        other_files = []

        for attachment in request.files:
            if not attachment.local_path:
                continue

            is_image = attachment.mimetype.startswith("image/")
            if is_image:
                images.append(attachment)
            else:
                other_files.append(attachment)

        # Format file info
        if images:
            if len(images) == 1:
                img = images[0]
                file_info_parts.append(f"[User attached an image: {img.local_path}]")
            else:
                paths = ", ".join(img.local_path for img in images)
                file_info_parts.append(f"[User attached {len(images)} images: {paths}]")

        if other_files:
            if len(other_files) == 1:
                f = other_files[0]
                file_info_parts.append(f"[User attached a file: {f.local_path} ({f.mimetype})]")
            else:
                files_desc = ", ".join(f"{f.local_path} ({f.mimetype})" for f in other_files)
                file_info_parts.append(f"[User attached {len(other_files)} files: {files_desc}]")

        if not file_info_parts:
            return request.message

        # Combine file info with user message
        file_info = "\n".join(file_info_parts)

        # If there's no text message, just use file info
        if not request.message or not request.message.strip():
            return file_info

        # Prepend file info to message
        return f"{file_info}\n\n{request.message}"
