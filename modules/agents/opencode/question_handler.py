"""Question/answer coordination for the OpenCode agent.

This encapsulates:
- Detecting question toolcalls and presenting UI to the user
- Waiting for an answer (event-based)
- Submitting answers back to OpenCode
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

from modules.agents.base import AgentRequest

from .server import OpenCodeServerManager
from .types import PendingQuestionPayload

logger = logging.getLogger(__name__)


class OpenCodeQuestionHandler:
    # Timeout for waiting on user to answer a question (30 minutes)
    QUESTION_WAIT_TIMEOUT_SECONDS = 30 * 60

    def __init__(self, controller, im_client, settings_manager, get_server=None):
        self._controller = controller
        self._im_client = im_client
        self._settings_manager = settings_manager
        self._get_server = get_server  # Callback to get server instance for abort

        self._pending_questions: Dict[str, PendingQuestionPayload] = {}
        self._question_answer_events: Dict[str, asyncio.Event] = {}
        self._timed_out_questions: set[str] = set()

    def get_pending(self, base_session_id: str) -> Optional[PendingQuestionPayload]:
        return self._pending_questions.get(base_session_id)

    def set_pending(self, base_session_id: str, payload: PendingQuestionPayload) -> None:
        self._pending_questions[base_session_id] = payload

    def pop_pending(self, base_session_id: str) -> Optional[PendingQuestionPayload]:
        return self._pending_questions.pop(base_session_id, None)

    def clear(self, base_session_id: str) -> None:
        self._pending_questions.pop(base_session_id, None)
        self._question_answer_events.pop(base_session_id, None)
        self._timed_out_questions.discard(base_session_id)

    def _get_or_create_question_event(self, base_session_id: str) -> asyncio.Event:
        """Get or create an event for question answer coordination.

        Clears the event if it already exists (important for nested questions).
        Also clears any stale timeout marker from previous runs.
        """

        # Clear stale timeout marker - this is a new question, not a late answer
        self._timed_out_questions.discard(base_session_id)

        evt = self._question_answer_events.get(base_session_id)
        if evt is None:
            evt = asyncio.Event()
            self._question_answer_events[base_session_id] = evt
        else:
            evt.clear()
        return evt

    def _clear_question_event(self, base_session_id: str, evt: asyncio.Event) -> None:
        """Clear question event, but only if it's still the same object (avoid races)."""

        current = self._question_answer_events.get(base_session_id)
        if current is evt:
            self._question_answer_events.pop(base_session_id, None)

    async def wait_for_question_answer(
        self,
        request: AgentRequest,
        session_id: str,
        pending_payload: PendingQuestionPayload,
    ) -> bool:
        """Wait for user to answer the question.

        Returns:
            True if answer was submitted successfully, False if timed out
        """

        evt = self._get_or_create_question_event(request.base_session_id)

        try:
            await asyncio.wait_for(evt.wait(), timeout=self.QUESTION_WAIT_TIMEOUT_SECONDS)

            # If a previous timed-out run left residue, clear it.
            if request.base_session_id in self._timed_out_questions:
                self._timed_out_questions.discard(request.base_session_id)
                logger.warning(
                    "Answer received after timeout for %s, ignoring",
                    request.base_session_id,
                )
                return False

            logger.info(
                "Answer received for %s, resuming poll loop", request.base_session_id
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout waiting for answer for %s after %d seconds",
                request.base_session_id,
                self.QUESTION_WAIT_TIMEOUT_SECONDS,
            )
            self._timed_out_questions.add(request.base_session_id)
            try:
                await self._handle_question_timeout(request, session_id, pending_payload)
            except Exception as e:
                logger.error(f"Error in timeout handler: {e}", exc_info=True)
            return False
        finally:
            self._clear_question_event(request.base_session_id, evt)

    async def _handle_question_timeout(
        self,
        request: AgentRequest,
        session_id: str,
        pending: PendingQuestionPayload,
    ) -> None:
        """Handle timeout when user doesn't answer a question within timeout period."""

        msg_id = pending.get("prompt_message_id")
        if msg_id and hasattr(self._im_client, "remove_inline_keyboard"):
            try:
                await self._im_client.remove_inline_keyboard(
                    request.context,
                    msg_id,
                    text=(pending.get("prompt_text") or "")
                    + "\n\n_(Timed out waiting for answer)_",
                    parse_mode="markdown",
                )
            except Exception:
                pass

        # Clear in-memory state
        self._pending_questions.pop(request.base_session_id, None)

        # Stop restoring this on restart (since we're abandoning the run)
        self._settings_manager.remove_active_poll(session_id)

        # Abort the OpenCode session so rerun starts fresh
        directory = pending.get("directory")
        if self._get_server and session_id and directory:
            try:
                server = await self._get_server()
                await server.abort_session(session_id, directory)
                logger.info(
                    "Aborted OpenCode session %s after question timeout", session_id
                )
            except Exception as e:
                logger.warning(f"Failed to abort session on timeout: {e}")

        await self._controller.emit_agent_message(
            request.context,
            "notify",
            "Timed out waiting for your answer (30 minutes). Please re-run your request.",
        )

    def _build_question_selection_note(self, answers_payload: List[List[str]]) -> str:
        if not answers_payload:
            return ""

        if len(answers_payload) == 1:
            joined = ", ".join([value for value in answers_payload[0] if value])
            return f"已选择：{joined}" if joined else ""

        lines = []
        for idx, answers in enumerate(answers_payload, start=1):
            joined = ", ".join([value for value in answers if value])
            if joined:
                lines.append(f"Q{idx}: {joined}")
        if not lines:
            return ""
        return "已选择：\n" + "\n".join(lines)

    async def open_question_modal(
        self, request: AgentRequest, pending: PendingQuestionPayload
    ) -> None:
        trigger_id = None
        if request.context.platform_specific:
            trigger_id = request.context.platform_specific.get("trigger_id")
        if not trigger_id:
            await self._im_client.send_message(
                request.context,
                "Slack did not provide a trigger_id for the modal. Please reply with a custom message.",
            )
            return

        if not hasattr(self._im_client, "open_opencode_question_modal"):
            await self._im_client.send_message(
                request.context,
                "Modal UI is not available. Please reply with a custom message.",
            )
            return

        try:
            await self._im_client.open_opencode_question_modal(
                trigger_id=trigger_id,
                context=request.context,
                pending=pending,
            )
        except Exception as err:
            logger.error(f"Failed to open OpenCode question modal: {err}", exc_info=True)
            await self._im_client.send_message(
                request.context,
                f"Failed to open modal: {err}. Please reply with a custom message.",
            )

    async def process_question_answer(
        self,
        request: AgentRequest,
        pending: PendingQuestionPayload,
        server: OpenCodeServerManager,
    ) -> None:
        session_id = pending.get("session_id")
        directory = pending.get("directory")
        question_id = pending.get("question_id")
        option_labels = pending.get("option_labels")
        option_labels = option_labels if isinstance(option_labels, list) else []
        question_count = pending.get("question_count")
        pending_thread_id = pending.get("thread_id")
        question_message_id = pending.get("prompt_message_id")

        if pending_thread_id and not request.context.thread_id:
            request.context.thread_id = pending_thread_id

        try:
            question_count_int = int(question_count) if question_count is not None else 1
        except Exception:
            question_count_int = 1
        question_count_int = max(1, question_count_int)

        if not session_id or not directory:
            await self._controller.emit_agent_message(
                request.context,
                "notify",
                "OpenCode question context is missing; please reply with a custom message.",
            )
            return

        answer_text = None
        if request.message.startswith("opencode_question:choose:"):
            try:
                choice_idx = int(request.message.rsplit(":", 1)[-1]) - 1
                if 0 <= choice_idx < len(option_labels):
                    answer_text = str(option_labels[choice_idx]).strip()
            except Exception:
                pass

        is_modal_payload = False
        answers_payload: Optional[List[List[str]]] = None
        if request.message.startswith("opencode_question:modal:"):
            is_modal_payload = True
            try:
                payload = json.loads(request.message.split(":", 2)[-1])
                answers = payload.get("answers") if isinstance(payload, dict) else None
                if isinstance(answers, list) and answers:
                    normalized: List[List[str]] = []
                    for answer in answers:
                        if isinstance(answer, list):
                            normalized.append([str(x) for x in answer if x])
                        elif answer:
                            normalized.append([str(answer)])
                        else:
                            normalized.append([])
                    answers_payload = normalized
                    if normalized:
                        answer_text = " ".join(normalized[0])
            except Exception:
                logger.debug("Failed to parse modal answers payload")

        if answer_text is None and request.message.startswith("opencode_question:"):
            raw_payload = request.message.split(":", 2)[-1]
            answer_text = raw_payload.strip() if raw_payload else ""

        if not answer_text:
            answer_text = (request.message or "").strip()

        if not answer_text:
            await self._controller.emit_agent_message(
                request.context,
                "notify",
                "Please reply with an answer.",
            )
            return

        # Consume pending state now; we'll put it back on failure.
        self._pending_questions.pop(request.base_session_id, None)

        if not question_id:
            call_id = pending.get("call_id")
            message_id = pending.get("message_id")
            try:
                questions = await server.list_questions(directory)
                if not questions:
                    questions = await server.list_questions()
                for item in questions:
                    tool = item.get("tool") or {}
                    item_session_id = (
                        item.get("sessionID")
                        or item.get("sessionId")
                        or item.get("session_id")
                    )
                    if item_session_id != session_id:
                        continue
                    if call_id and tool.get("callID") != call_id:
                        continue
                    if message_id and tool.get("messageID") != message_id:
                        continue
                    question_id = item.get("id")
                    questions_obj = item.get("questions")
                    if isinstance(questions_obj, list):
                        question_count_int = max(1, len(questions_obj))
                    break
            except Exception as err:
                logger.warning(f"Failed to resolve OpenCode question id: {err}")

        if not question_id:
            self._pending_questions[request.base_session_id] = pending
            await self._controller.emit_agent_message(
                request.context,
                "notify",
                "OpenCode is waiting for input, but the question id could not be resolved. Please retry.",
            )
            return

        if is_modal_payload and answers_payload is not None:
            padded = answers_payload[:question_count_int]
            if len(padded) < question_count_int:
                padded.extend([[] for _ in range(question_count_int - len(padded))])
            answers_payload = padded
        else:
            answers_payload = [[answer_text] for _ in range(question_count_int)]

        if question_message_id and hasattr(self._im_client, "remove_inline_keyboard"):
            note = self._build_question_selection_note(answers_payload)
            fallback_text = pending.get("prompt_text") if isinstance(pending, dict) else None
            if note:
                try:
                    updated_text = f"{fallback_text}\n\n{note}" if fallback_text else note
                    await self._im_client.remove_inline_keyboard(
                        request.context,
                        question_message_id,
                        text=updated_text,
                        parse_mode="markdown",
                    )
                except Exception as err:
                    logger.debug(f"Failed to update question message: {err}")
            else:
                try:
                    await self._im_client.remove_inline_keyboard(
                        request.context,
                        question_message_id,
                        text=fallback_text,
                        parse_mode="markdown",
                    )
                except Exception as err:
                    logger.debug(f"Failed to remove question buttons: {err}")

        try:
            ok = await server.reply_question(question_id, directory, answers_payload)
        except Exception as err:
            logger.warning(f"Failed to reply OpenCode question: {err}")
            self._pending_questions[request.base_session_id] = pending
            # Do NOT set event here - let user retry, poll loop keeps waiting
            await self._controller.emit_agent_message(
                request.context,
                "notify",
                f"Failed to submit answer to OpenCode: {err}. Please retry.",
            )
            return

        if not ok:
            self._pending_questions[request.base_session_id] = pending
            # Do NOT set event here - let user retry, poll loop keeps waiting
            await self._controller.emit_agent_message(
                request.context,
                "notify",
                "OpenCode did not accept the answer. Please retry.",
            )
            return

        # Add 👀 reaction to question message to indicate answer received
        if question_message_id and hasattr(self._im_client, "add_reaction"):
            try:
                await self._im_client.add_reaction(
                    request.context, question_message_id, "eyes"
                )
            except Exception as err:
                logger.debug(f"Failed to add reaction to question message: {err}")

        evt = self._question_answer_events.get(request.base_session_id)
        if evt:
            evt.set()
            logger.info(
                "Answer submitted for %s, signaling main poll loop to resume", session_id
            )
        else:
            logger.warning(
                "No wait event found for %s; cannot resume poll", request.base_session_id
            )
            await self._controller.emit_agent_message(
                request.context,
                "notify",
                "Answer submitted, but I no longer have an active poll loop for this session. "
                "Please send a new message to continue.",
            )

    async def handle_question_toolcall(
        self,
        request: AgentRequest,
        server: OpenCodeServerManager,
        opencode_session_id: str,
        message_id: str,
        tool_part: Dict[str, Any],
        tool_input: Dict[str, Any],
        call_key: str,
        seen_tool_calls: set[str],
    ) -> bool:
        """Render question UI and wait for answer.

        Returns:
            True if answer received (caller should restart poll), False if timed out.
        """

        logger.info(
            "Detected question toolcall for %s message=%s callID=%s",
            opencode_session_id,
            message_id,
            tool_part.get("callID"),
        )

        qlist = tool_input.get("questions") if isinstance(tool_input, dict) else None
        qlist = qlist if isinstance(qlist, list) else []

        question_fetch_deadline = time.monotonic() + 60.0
        question_fetch_delays = [1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0]
        max_question_attempts = 10

        def _question_delay(attempt_index: int) -> float:
            if attempt_index >= len(question_fetch_delays):
                return 0.0
            remaining = question_fetch_deadline - time.monotonic()
            if remaining <= 0:
                return 0.0
            return min(question_fetch_delays[attempt_index], remaining)

        question_id = None
        questions_listing: List[Dict[str, Any]] = []
        list_attempts = 0
        last_list_err: Optional[Exception] = None
        for attempt in range(max_question_attempts):
            list_attempts = attempt + 1
            try:
                questions_listing = await server.list_questions(request.working_path)
                if not questions_listing:
                    questions_listing = await server.list_questions()
                last_list_err = None
            except Exception as err:
                last_list_err = err
                questions_listing = []
            if questions_listing:
                break
            if attempt < max_question_attempts - 1:
                delay = _question_delay(attempt)
                if delay <= 0:
                    break
                await asyncio.sleep(delay)

        if last_list_err and not questions_listing:
            logger.warning(
                f"Failed to fetch questions listing for prompt fallback: {last_list_err}"
            )

        logger.info(
            "Question list fetch for %s: dir=%s attempts=%s items=%s",
            opencode_session_id,
            request.working_path,
            list_attempts,
            len(questions_listing),
        )

        if questions_listing:
            try:
                item_sessions = [
                    (
                        item.get("sessionID")
                        or item.get("sessionId")
                        or item.get("session_id")
                    )
                    for item in questions_listing
                ]
                logger.info(
                    "Question list sessions for %s: %s", opencode_session_id, item_sessions
                )
            except Exception:
                pass

        if questions_listing and not qlist:
            try:
                first_questions = questions_listing[0].get("questions")
                if not isinstance(first_questions, list):
                    first_questions = []
                listing_preview = {
                    "id": questions_listing[0].get("id"),
                    "sessionID": questions_listing[0].get("sessionID"),
                    "tool": questions_listing[0].get("tool"),
                    "questions_len": len(first_questions),
                }
                logger.info("Question list preview for %s: %s", opencode_session_id, listing_preview)
            except Exception:
                pass

        matched_item = None
        if questions_listing:
            session_items: List[Dict[str, Any]] = []
            for item in questions_listing:
                item_session_id = (
                    item.get("sessionID")
                    or item.get("sessionId")
                    or item.get("session_id")
                )
                if item_session_id == opencode_session_id:
                    session_items.append(item)

            for item in session_items:
                tool_meta = item.get("tool") or {}
                if tool_part.get("callID") and tool_meta.get("callID") == tool_part.get("callID"):
                    matched_item = item
                    break
                if message_id and tool_meta.get("messageID") == message_id:
                    matched_item = item
                    break

            if matched_item is None and session_items:
                matched_item = session_items[0]

            if matched_item is None and tool_part.get("callID"):
                for item in questions_listing:
                    tool_meta = item.get("tool") or {}
                    if tool_meta.get("callID") == tool_part.get("callID"):
                        matched_item = item
                        break

            if matched_item is None and message_id:
                for item in questions_listing:
                    tool_meta = item.get("tool") or {}
                    if tool_meta.get("messageID") == message_id:
                        matched_item = item
                        break

            if matched_item is None and len(questions_listing) == 1:
                matched_item = questions_listing[0]

        if matched_item:
            question_id = matched_item.get("id")
            if not qlist:
                q_obj = matched_item.get("questions")
                if isinstance(q_obj, list):
                    qlist = q_obj

        if not qlist and message_id:
            msg_attempts = 0
            last_msg_err: Optional[Exception] = None
            full_message: Optional[Dict[str, Any]] = None
            for attempt in range(max_question_attempts):
                msg_attempts = attempt + 1
                try:
                    full_message = await server.get_message(
                        session_id=opencode_session_id,
                        message_id=message_id,
                        directory=request.working_path,
                    )
                    last_msg_err = None
                except Exception as err:
                    last_msg_err = err
                    full_message = None

                if full_message:
                    for msg_part in full_message.get("parts", []) or []:
                        if msg_part.get("type") != "tool":
                            continue
                        if msg_part.get("tool") != "question":
                            continue
                        msg_call_id = msg_part.get("callID") or msg_part.get("id")
                        if call_key and msg_call_id and msg_call_id != call_key:
                            continue
                        msg_state = msg_part.get("state") or {}
                        msg_input = msg_state.get("input") or {}
                        msg_questions = (
                            msg_input.get("questions") if isinstance(msg_input, dict) else None
                        )
                        if isinstance(msg_questions, list):
                            qlist = msg_questions
                            break
                if qlist:
                    break
                if attempt < max_question_attempts - 1:
                    delay = _question_delay(attempt)
                    if delay <= 0:
                        break
                    await asyncio.sleep(delay)

            if last_msg_err and not qlist:
                logger.warning(
                    f"Failed to fetch full question input from message {message_id}: {last_msg_err}"
                )
            if full_message is not None:
                parts = full_message.get("parts", []) or []
                tool_parts = [p for p in parts if p.get("type") == "tool"]
                logger.info(
                    "Question message fetch for %s: attempts=%s parts=%s tool_parts=%s",
                    opencode_session_id,
                    msg_attempts,
                    len(parts),
                    len(tool_parts),
                )

        option_labels: list[str] = []
        lines: list[str] = []
        for q_idx, q in enumerate(qlist or []):
            if not isinstance(q, dict):
                continue
            title = (q.get("header") or f"Question {q_idx + 1}").strip()
            prompt = (q.get("question") or "").strip()
            options_raw = q.get("options")
            options: List[Dict[str, Any]] = options_raw if isinstance(options_raw, list) else []

            lines.append(f"**{title}**")
            if prompt:
                lines.append(prompt)

            for idx, opt in enumerate(options, start=1):
                if not isinstance(opt, dict):
                    continue
                label = (opt.get("label") or f"Option {idx}").strip()
                desc = (opt.get("description") or "").strip()
                if q_idx == 0:
                    option_labels.append(label)
                if desc:
                    lines.append(f"{idx}. *{label}* - {desc}")
                else:
                    lines.append(f"{idx}. *{label}*")

            if q_idx < len(qlist) - 1:
                lines.append("")

        first_q = qlist[0] if qlist and isinstance(qlist[0], dict) else {}
        text = "\n".join(lines)
        logger.info(
            "Question prompt built for %s: len=%s preview=%r",
            opencode_session_id,
            len(text),
            text[:200],
        )

        logger.info(
            "Question prompt data for %s: qlist=%s options=%s question_id=%s call_id=%s",
            opencode_session_id,
            len(qlist),
            len(option_labels),
            question_id,
            tool_part.get("callID"),
        )

        if not option_labels:
            logger.warning(
                "Question toolcall had no options in tool_input; session=%s question_id=%s",
                opencode_session_id,
                question_id,
            )

        question_count = len(qlist) if qlist else 1
        multiple = bool(first_q.get("multiple"))

        pending_payload: PendingQuestionPayload = {
            "session_id": opencode_session_id,
            "directory": request.working_path,
            "question_id": question_id,
            "call_id": tool_part.get("callID"),
            "message_id": message_id,
            "prompt_message_id": None,
            "prompt_text": text,
            "option_labels": option_labels,
            "question_count": question_count,
            "multiple": multiple,
            "questions": qlist,
            "thread_id": request.context.thread_id,
        }
        self._pending_questions[request.base_session_id] = pending_payload

        # Multi-select or multi-question: show full text + modal button
        if multiple or question_count != 1 or len(option_labels) > 10:
            modal_keyboard = None
            if hasattr(self._im_client, "send_message_with_buttons"):
                from modules.im import InlineButton, InlineKeyboard

                modal_keyboard = InlineKeyboard(
                    buttons=[[InlineButton(text="Choose…", callback_data="opencode_question:open_modal")]]
                )

            if modal_keyboard:
                try:
                    logger.info(
                        "Sending modal open button for %s (multiple=%s questions=%s)",
                        opencode_session_id,
                        multiple,
                        question_count,
                    )
                    question_message_id = await self._im_client.send_message_with_buttons(
                        request.context,
                        text,
                        modal_keyboard,
                        parse_mode="markdown",
                    )
                    if question_message_id:
                        pending_payload["prompt_message_id"] = question_message_id

                    seen_tool_calls.add(call_key)
                    if await self.wait_for_question_answer(
                        request, opencode_session_id, pending_payload
                    ):
                        return True
                    return False
                except Exception as err:
                    logger.warning(
                        f"Failed to send modal button, falling back to text: {err}",
                        exc_info=True,
                    )

            await self._im_client.send_message(
                request.context, text, parse_mode="markdown"
            )
            seen_tool_calls.add(call_key)
            if await self.wait_for_question_answer(request, opencode_session_id, pending_payload):
                return True
            return False

        # single question + single select + <=10 options -> buttons
        if (
            question_count == 1
            and isinstance(first_q, dict)
            and not multiple
            and len(option_labels) <= 10
            and hasattr(self._im_client, "send_message_with_buttons")
        ):
            from modules.im import InlineButton, InlineKeyboard

            buttons: list[list[InlineButton]] = []
            row: list[InlineButton] = []
            for idx, label in enumerate(option_labels, start=1):
                callback = f"opencode_question:choose:{idx}"
                row.append(InlineButton(text=label, callback_data=callback))
                if len(row) == 5:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

            keyboard = InlineKeyboard(buttons=buttons)
            try:
                logger.info(
                    "Sending single-select buttons for %s (options=%s)",
                    opencode_session_id,
                    len(option_labels),
                )
                question_message_id = await self._im_client.send_message_with_buttons(
                    request.context,
                    text,
                    keyboard,
                    parse_mode="markdown",
                )
                if question_message_id:
                    pending_payload["prompt_message_id"] = question_message_id
                seen_tool_calls.add(call_key)
                if await self.wait_for_question_answer(
                    request, opencode_session_id, pending_payload
                ):
                    return True
                return False
            except Exception as err:
                logger.warning(
                    f"Failed to send Slack buttons, falling back to text: {err}",
                    exc_info=True,
                )

        # fallback: text-only
        try:
            await self._im_client.send_message(
                request.context,
                text,
                parse_mode="markdown",
            )
        except Exception as err:
            logger.error(f"Failed to send question prompt to Slack: {err}", exc_info=True)

        seen_tool_calls.add(call_key)
        if await self.wait_for_question_answer(request, opencode_session_id, pending_payload):
            return True
        return False
