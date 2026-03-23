"""Consolidated agent message dispatcher.

Owns the main log/result/notify dispatch state machine that was previously
embedded in ``Controller.emit_agent_message``.
"""

from __future__ import annotations

import logging
import asyncio
from pathlib import Path
from typing import Optional

from modules.im import MessageContext
from core.reply_enhancer import process_reply

logger = logging.getLogger(__name__)


class ConsolidatedMessageDispatcher:
    """Dispatch agent messages while preserving existing product behavior."""

    def __init__(self, controller):
        self.controller = controller
        self._consolidated_message_ids: dict[str, str] = {}
        self._consolidated_message_buffers: dict[str, str] = {}
        self._consolidated_message_locks: dict[str, asyncio.Lock] = {}
        self._thread_current_message_id: dict[str, str] = {}

    def _get_settings_key(self, context: MessageContext) -> str:
        return self.controller._get_settings_key(context)

    def _get_session_key(self, context: MessageContext) -> str:
        return self.controller._get_session_key(context)

    def _get_im_client(self, context: MessageContext):
        getter = getattr(self.controller, "get_im_client_for_context", None)
        if callable(getter):
            return getter(context)
        return self.controller.im_client

    def _get_target_context(self, context: MessageContext) -> MessageContext:
        if self._get_im_client(context).should_use_thread_for_reply() and context.thread_id:
            return MessageContext(
                user_id=context.user_id,
                channel_id=context.channel_id,
                platform=context.platform,
                thread_id=context.thread_id,
                message_id=context.message_id,
                platform_specific=context.platform_specific,
            )
        return context

    def _get_consolidated_message_key(self, context: MessageContext) -> str:
        session_key = self._get_session_key(context)
        thread_key = context.thread_id or context.channel_id
        tracking_key = f"{session_key}:{thread_key}"
        trigger_id = self._thread_current_message_id.get(tracking_key) or context.message_id or ""
        return f"{session_key}:{thread_key}:{trigger_id}"

    def update_thread_message_id(self, context: MessageContext) -> None:
        if not context.message_id:
            return
        session_key = self._get_session_key(context)
        thread_key = context.thread_id or context.channel_id
        tracking_key = f"{session_key}:{thread_key}"
        self._thread_current_message_id[tracking_key] = context.message_id

    def _get_consolidated_message_lock(self, key: str) -> asyncio.Lock:
        if key not in self._consolidated_message_locks:
            self._consolidated_message_locks[key] = asyncio.Lock()
        return self._consolidated_message_locks[key]

    async def clear_consolidated_message_id(
        self,
        context: MessageContext,
        trigger_message_id: Optional[str] = None,
    ) -> None:
        session_key = self._get_session_key(context)
        thread_key = context.thread_id or context.channel_id
        msg_id = trigger_message_id if trigger_message_id else (context.message_id or "")
        key = f"{session_key}:{thread_key}:{msg_id}"

        lock = self._get_consolidated_message_lock(key)
        async with lock:
            self._consolidated_message_ids.pop(key, None)
            self._consolidated_message_buffers.pop(key, None)

    def _get_consolidated_max_bytes(self, context: MessageContext) -> int:
        if (
            context.platform or (context.platform_specific or {}).get("platform") or self.controller.config.platform
        ) == "discord":
            return 2000
        return 4000

    def _get_consolidated_split_threshold(self, context: MessageContext) -> int:
        if (
            context.platform or (context.platform_specific or {}).get("platform") or self.controller.config.platform
        ) == "discord":
            return 1800
        return 3600

    @staticmethod
    def _get_text_byte_length(text: str) -> int:
        return len(text.encode("utf-8"))

    def _get_result_max_chars(self, context: MessageContext) -> int:
        if (
            context.platform or (context.platform_specific or {}).get("platform") or self.controller.config.platform
        ) == "discord":
            return 1900
        return 30000

    def _supports_quick_replies(self, context: MessageContext) -> bool:
        return (
            context.platform or (context.platform_specific or {}).get("platform") or self.controller.config.platform
        ) != "wechat"

    @staticmethod
    def _is_video_path(path: str) -> bool:
        return Path(path).suffix.lower() in {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}

    @staticmethod
    def _build_result_summary(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        prefix = "Result too long; showing a summary.\n\n"
        suffix = "\n\n…(truncated; see result.md for full output)"
        keep = max(0, max_chars - len(prefix) - len(suffix))
        return f"{prefix}{text[:keep]}{suffix}"

    def _truncate_consolidated(self, text: str, max_bytes: int) -> str:
        if self._get_text_byte_length(text) <= max_bytes:
            return text
        ellipsis = "…"
        target_bytes = max_bytes - len(ellipsis.encode("utf-8"))
        encoded = text.encode("utf-8")
        truncated = encoded[:target_bytes].decode("utf-8", errors="ignore")
        return truncated.rstrip() + ellipsis

    async def emit_agent_message(
        self,
        context: MessageContext,
        message_type: str,
        text: str,
        parse_mode: Optional[str] = "markdown",
    ) -> None:
        """Centralized dispatch for agent messages.

        Message Types:
        - Log Messages (system/assistant/toolcall): consolidated into a single
          editable message per conversation round. Can be hidden by user settings.
        - Result Message: final output, always sent immediately, not hideable.
        - Notify Message: notifications, always sent immediately.
        """
        if not text or not text.strip():
            return

        settings_manager = self.controller.get_settings_manager_for_context(context)
        im_client = self._get_im_client(context)

        canonical_type = settings_manager._canonicalize_message_type(message_type or "")
        settings_key = self._get_settings_key(context)

        if canonical_type == "notify":
            target_context = self._get_target_context(context)
            try:
                await im_client.send_message(target_context, text, parse_mode=parse_mode)
            except Exception as err:
                logger.error("Failed to send notify message: %s", err)
            return

        if canonical_type == "result":
            target_context = self._get_target_context(context)

            # --- Reply enhancements: extract file links & quick-reply buttons ---
            reply_enhancements_on = getattr(self.controller.config, "reply_enhancements", True)
            if reply_enhancements_on:
                enhanced = process_reply(text)
                display_text = enhanced.text if enhanced.text.strip() else text
            else:
                enhanced = None
                display_text = text

            if len(display_text) <= self._get_result_max_chars(context):
                if enhanced and enhanced.buttons and self._supports_quick_replies(context):
                    try:
                        await self._send_with_quick_replies(
                            im_client,
                            target_context,
                            display_text,
                            enhanced.buttons,
                            parse_mode,
                        )
                    except Exception as err:
                        logger.warning("Failed to send result with quick replies, falling back: %s", err)
                        try:
                            await im_client.send_message(target_context, display_text, parse_mode=parse_mode)
                        except Exception as fallback_err:
                            logger.error("Failed to send fallback result message: %s", fallback_err)
                else:
                    try:
                        await im_client.send_message(target_context, display_text, parse_mode=parse_mode)
                    except Exception as err:
                        logger.error("Failed to send result message: %s", err)
            else:
                summary = self._build_result_summary(display_text, self._get_result_max_chars(context))
                try:
                    await im_client.send_message(target_context, summary, parse_mode=parse_mode)
                except Exception as err:
                    logger.error("Failed to send result summary: %s", err)

                if (
                    context.platform
                    or (context.platform_specific or {}).get("platform")
                    or self.controller.config.platform
                ) in {"slack", "discord", "lark"} and hasattr(im_client, "upload_markdown"):
                    try:
                        await im_client.upload_markdown(
                            target_context,
                            title="result.md",
                            content=display_text,
                            filetype="markdown",
                        )
                    except Exception as err:
                        logger.warning(f"Failed to upload result attachment: {err}")
                        await im_client.send_message(
                            target_context,
                            "Failed to upload attachment. Want me to split the result into multiple messages?",
                            parse_mode=parse_mode,
                        )

            # Upload extracted file attachments
            if enhanced and enhanced.files:
                await self._upload_file_links(im_client, target_context, enhanced.files)

            # Final result closes the current turn: clear consolidated
            # assistant/tool/system message state so the next user turn starts
            # a fresh log message instead of appending to the previous one.
            consolidated_key = self._get_consolidated_message_key(context)
            lock = self._get_consolidated_message_lock(consolidated_key)
            async with lock:
                self._consolidated_message_ids.pop(consolidated_key, None)
                self._consolidated_message_buffers.pop(consolidated_key, None)

            return

        if canonical_type not in {"system", "assistant", "toolcall"}:
            canonical_type = "assistant"

        if settings_manager.is_message_type_hidden(settings_key, canonical_type):
            preview = text if len(text) <= 500 else f"{text[:500]}…"
            logger.info(
                "Skipping %s message for settings %s (hidden). Preview: %s",
                canonical_type,
                settings_key,
                preview,
            )
            return

        consolidated_key = self._get_consolidated_message_key(context)
        lock = self._get_consolidated_message_lock(consolidated_key)

        async with lock:
            chunk = text.strip()
            max_bytes = self._get_consolidated_max_bytes(context)
            split_threshold = self._get_consolidated_split_threshold(context)
            existing = self._consolidated_message_buffers.get(consolidated_key, "")
            existing_message_id = self._consolidated_message_ids.get(consolidated_key)

            separator = "\n\n---\n\n" if existing else ""
            updated = f"{existing}{separator}{chunk}" if existing else chunk

            target_context = self._get_target_context(context)
            continuation_notice = "\n\n---\n\n_(continued below...)_"
            continuation_bytes = self._get_text_byte_length(continuation_notice)

            if existing_message_id and self._get_text_byte_length(updated) > split_threshold:
                old_text = existing + continuation_notice
                old_text = self._truncate_consolidated(old_text, max_bytes)

                try:
                    await im_client.edit_message(
                        target_context,
                        existing_message_id,
                        text=old_text,
                        parse_mode="markdown",
                    )
                except Exception as err:
                    logger.warning(f"Failed to finalize old Log Message: {err}")

                self._consolidated_message_buffers[consolidated_key] = chunk
                self._consolidated_message_ids.pop(consolidated_key, None)
                updated = chunk
                existing_message_id = None
                logger.info(
                    "Log Message exceeded %d bytes, starting new message",
                    split_threshold,
                )

            while self._get_text_byte_length(updated) > max_bytes:
                target_bytes = split_threshold - continuation_bytes
                first_part = self._truncate_consolidated(updated, target_bytes)
                first_part = first_part.rstrip("…") + continuation_notice

                send_ok = False
                if existing_message_id:
                    try:
                        await im_client.edit_message(
                            target_context,
                            existing_message_id,
                            text=first_part,
                            parse_mode="markdown",
                        )
                        send_ok = True
                    except Exception as err:
                        logger.warning(f"Failed to edit oversized Log Message: {err}")
                else:
                    try:
                        await im_client.send_message(target_context, first_part, parse_mode="markdown")
                        send_ok = True
                    except Exception as err:
                        logger.error(f"Failed to send oversized Log Message: {err}")

                if not send_ok:
                    logger.warning("Stopping split loop due to send failure, truncating remainder")
                    break

                sent_chars = len(first_part) - len(continuation_notice)
                updated = updated[sent_chars:]
                existing_message_id = None
                self._consolidated_message_ids.pop(consolidated_key, None)
                logger.info(
                    "Log Message chunk exceeded %d bytes, split and continuing",
                    max_bytes,
                )

            updated = self._truncate_consolidated(updated, max_bytes)
            self._consolidated_message_buffers[consolidated_key] = updated

            if existing_message_id:
                try:
                    ok = await im_client.edit_message(
                        target_context,
                        existing_message_id,
                        text=updated,
                        parse_mode="markdown",
                    )
                except Exception as err:
                    logger.warning(f"Failed to edit Log Message: {err}")
                    ok = False
                if ok:
                    return
                self._consolidated_message_ids.pop(consolidated_key, None)

            try:
                new_id = await im_client.send_message(target_context, updated, parse_mode="markdown")
                self._consolidated_message_ids[consolidated_key] = new_id
            except Exception as err:
                logger.error(f"Failed to send Log Message: {err}", exc_info=True)

    # ------------------------------------------------------------------
    # Reply-enhancement helpers
    # ------------------------------------------------------------------

    async def _send_with_quick_replies(
        self,
        im_client,
        context: MessageContext,
        text: str,
        buttons,
        parse_mode,
    ) -> None:
        """Send a message with quick-reply buttons appended."""
        from modules.im.base import InlineButton, InlineKeyboard

        row = []
        for btn in buttons:
            callback = f"quick_reply:{btn.text}"
            row.append(InlineButton(text=btn.text, callback_data=callback))

        keyboard = InlineKeyboard(buttons=[row])
        await im_client.send_message_with_buttons(
            context,
            text,
            keyboard,
            parse_mode=parse_mode,
        )

    async def _upload_file_links(
        self,
        im_client,
        context: MessageContext,
        files,
    ) -> None:
        """Upload local files referenced by ``file://`` links."""
        import os
        from pathlib import Path

        if not hasattr(im_client, "upload_file_from_path"):
            logger.debug("IM client does not support upload_file_from_path; skipping file uploads")
            return

        for fl in files:
            if not os.path.isfile(fl.path):
                logger.warning("File not found, skipping upload: %s", fl.path)
                continue

            try:
                resolved = Path(fl.path).resolve(strict=True)
            except (OSError, ValueError):
                logger.warning("Cannot resolve file path, skipping: %s", fl.path)
                continue

            # Use link label as title, but preserve file extension so users can
            # download/open files correctly on all platforms.
            upload_title = (fl.label or "").strip() or os.path.basename(fl.path)
            src_ext = resolved.suffix
            if src_ext and not Path(upload_title).suffix:
                upload_title = f"{upload_title}{src_ext}"

            try:
                if self._is_video_path(str(resolved)):
                    await im_client.upload_video_from_path(
                        context,
                        file_path=str(resolved),
                        title=upload_title,
                    )
                elif getattr(fl, "is_image", False):
                    try:
                        await im_client.upload_image_from_path(
                            context,
                            file_path=str(resolved),
                            title=upload_title,
                        )
                    except Exception as image_err:
                        logger.warning(
                            "Image upload failed for %s, fallback to file upload: %r",
                            fl.path,
                            image_err,
                        )
                        await im_client.upload_file_from_path(
                            context,
                            file_path=str(resolved),
                            title=upload_title,
                        )
                else:
                    await im_client.upload_file_from_path(
                        context,
                        file_path=str(resolved),
                        title=upload_title,
                    )
            except NotImplementedError:
                logger.debug("IM client does not implement file uploads; skipping")
                return
            except Exception as err:
                logger.warning("Failed to upload file %s: %r", fl.path, err)
