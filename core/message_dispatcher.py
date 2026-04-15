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
from core.reply_enhancer import process_reply, strip_file_links
from vibe.i18n import t as i18n_t

logger = logging.getLogger(__name__)

_WECHAT_TEXT_LIMIT = 1900
_WECHAT_CONSOLIDATED_SPLIT_THRESHOLD = 1700


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

    def _t(self, key: str, **kwargs) -> str:
        translator = getattr(self.controller, "_t", None)
        if callable(translator):
            return translator(key, **kwargs)
        lang = getattr(getattr(self.controller, "config", None), "language", "en")
        return i18n_t(key, lang, **kwargs)

    def _get_target_context(self, context: MessageContext) -> MessageContext:
        payload = dict(context.platform_specific or {})
        delivery_override = payload.get("delivery_override")
        if isinstance(delivery_override, dict):
            next_payload = dict(payload)
            next_payload["is_dm"] = delivery_override.get("is_dm", next_payload.get("is_dm", False))
            return MessageContext(
                user_id=str(delivery_override.get("user_id") or context.user_id),
                channel_id=str(delivery_override.get("channel_id") or context.channel_id),
                platform=delivery_override.get("platform") or context.platform,
                thread_id=delivery_override.get("thread_id"),
                message_id=context.message_id,
                platform_specific=next_payload,
            )
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
        platform = (
            context.platform or (context.platform_specific or {}).get("platform") or self.controller.config.platform
        )
        if platform == "discord":
            return 2000
        if platform == "wechat":
            return _WECHAT_TEXT_LIMIT
        return 4000

    def _get_consolidated_split_threshold(self, context: MessageContext) -> int:
        platform = (
            context.platform or (context.platform_specific or {}).get("platform") or self.controller.config.platform
        )
        if platform == "discord":
            return 1800
        if platform == "wechat":
            return _WECHAT_CONSOLIDATED_SPLIT_THRESHOLD
        return 3600

    @staticmethod
    def _get_text_byte_length(text: str) -> int:
        return len(text.encode("utf-8"))

    def _get_result_max_chars(self, context: MessageContext) -> int:
        platform = (
            context.platform or (context.platform_specific or {}).get("platform") or self.controller.config.platform
        )
        if platform == "discord":
            return 1900
        return 30000

    def _get_result_max_bytes(self, context: MessageContext) -> Optional[int]:
        platform = (
            context.platform or (context.platform_specific or {}).get("platform") or self.controller.config.platform
        )
        if platform == "wechat":
            return _WECHAT_TEXT_LIMIT
        return None

    def _should_split_long_result(self, context: MessageContext) -> bool:
        return (
            context.platform or (context.platform_specific or {}).get("platform") or self.controller.config.platform
        ) in {"discord", "wechat"}

    def _result_within_limit(self, context: MessageContext, text: str) -> bool:
        max_bytes = self._get_result_max_bytes(context)
        if max_bytes is not None:
            return self._get_text_byte_length(text) <= max_bytes
        return len(text) <= self._get_result_max_chars(context)

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

    @staticmethod
    def _find_result_split_index(text: str, max_chars: int) -> int:
        minimum_boundary = max_chars // 2
        for separator in ("\n\n", "\n", " "):
            index = text.rfind(separator, 0, max_chars + 1)
            if index >= minimum_boundary:
                candidate = index + len(separator)
                return candidate if candidate <= max_chars else index
        return max_chars

    def _split_result_text(self, text: str, max_chars: int) -> list[str]:
        if len(text) <= max_chars:
            return [text]

        chunks: list[str] = []
        remaining = text

        while len(remaining) > max_chars:
            split_at = self._find_result_split_index(remaining, max_chars)
            if split_at <= 0:
                split_at = max_chars
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]

        if remaining:
            chunks.append(remaining)

        return chunks

    def _split_result_text_by_bytes(self, text: str, max_bytes: int) -> list[str]:
        if self._get_text_byte_length(text) <= max_bytes:
            return [text]

        chunks: list[str] = []
        remaining = text

        while self._get_text_byte_length(remaining) > max_bytes:
            prefix = remaining.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")
            minimum_boundary = max(1, len(prefix) // 2)
            split_at = len(prefix)
            for separator in ("\n\n", "\n", " "):
                index = prefix.rfind(separator)
                if index >= minimum_boundary:
                    candidate = index + len(separator)
                    if self._get_text_byte_length(remaining[:candidate]) <= max_bytes:
                        split_at = candidate
                        break
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]

        if remaining:
            chunks.append(remaining)

        return chunks

    def _split_result_text_for_context(self, context: MessageContext, text: str) -> list[str]:
        max_bytes = self._get_result_max_bytes(context)
        if max_bytes is not None:
            return self._split_result_text_by_bytes(text, max_bytes)
        return self._split_result_text(text, self._get_result_max_chars(context))

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
    ) -> Optional[str]:
        """Centralized dispatch for agent messages.

        Message Types:
        - Log Messages (system/assistant/toolcall): consolidated into a single
          editable message per conversation round. Can be hidden by user settings.
        - Result Message: final output, always sent immediately, not hideable.
        - Notify Message: notifications, always sent immediately.
        """
        if not text or not text.strip():
            return None

        settings_manager = self.controller.get_settings_manager_for_context(context)
        im_client = self._get_im_client(context)

        canonical_type = settings_manager._canonicalize_message_type(message_type or "")
        settings_key = self._get_settings_key(context)

        if canonical_type == "notify":
            target_context = self._get_target_context(context)
            try:
                return await im_client.send_message(target_context, text, parse_mode=parse_mode)
            except Exception as err:
                logger.error("Failed to send notify message: %s", err)
            return None

        if canonical_type == "result":
            target_context = self._get_target_context(context)
            primary_message_id: Optional[str] = None
            delivered_as_attachment = False

            # --- Reply enhancements: extract file links & quick-reply buttons ---
            reply_enhancements_on = getattr(self.controller.config, "reply_enhancements", True)
            if reply_enhancements_on:
                enhanced = process_reply(text)
                display_text = enhanced.text if enhanced.text.strip() else text
            else:
                enhanced = None
                display_text = text

            if self._result_within_limit(context, display_text):
                if enhanced and enhanced.buttons and self._supports_quick_replies(context):
                    try:
                        primary_message_id = await self._send_with_quick_replies(
                            im_client,
                            target_context,
                            display_text,
                            enhanced.buttons,
                            parse_mode,
                        )
                    except Exception as err:
                        logger.warning("Failed to send result with quick replies, falling back: %s", err)
                        try:
                            primary_message_id = await im_client.send_message(
                                target_context, display_text, parse_mode=parse_mode
                            )
                        except Exception as fallback_err:
                            logger.error("Failed to send fallback result message: %s", fallback_err)
                else:
                    try:
                        primary_message_id = await im_client.send_message(
                            target_context, display_text, parse_mode=parse_mode
                        )
                    except Exception as err:
                        logger.error("Failed to send result message: %s", err)
            elif self._should_split_long_result(context):
                try:
                    primary_message_id = await self._send_split_result_messages(
                        im_client,
                        target_context,
                        display_text,
                        enhanced.buttons if enhanced else [],
                        parse_mode,
                    )
                except Exception as err:
                    logger.error("Failed to send split result messages: %s", err)
            else:
                summary = self._build_result_summary(display_text, self._get_result_max_chars(context))
                try:
                    primary_message_id = await im_client.send_message(target_context, summary, parse_mode=parse_mode)
                except Exception as err:
                    logger.error("Failed to send result summary: %s", err)

                if (
                    context.platform
                    or (context.platform_specific or {}).get("platform")
                    or self.controller.config.platform
                ) in {"slack", "discord", "telegram", "lark"} and hasattr(im_client, "upload_markdown"):
                    try:
                        attachment_message_id = await im_client.upload_markdown(
                            target_context,
                            title="result.md",
                            content=display_text,
                            filetype="markdown",
                        )
                        if primary_message_id is None:
                            primary_message_id = attachment_message_id
                            delivered_as_attachment = True
                    except Exception as err:
                        logger.warning(f"Failed to upload result attachment: {err}")
                        await im_client.send_message(
                            target_context,
                            self._t("error.resultAttachmentUploadFailed"),
                            parse_mode=parse_mode,
                        )

            # --- Fallback: card content rejected (e.g. table over limit) ---
            if primary_message_id is None and display_text:
                logger.warning("All direct result sends failed; attempting fallback delivery")
                _file_uploaded = False

                # Fallback 1: upload full content as .md file
                if hasattr(im_client, "upload_markdown"):
                    try:
                        primary_message_id = await im_client.upload_markdown(
                            target_context,
                            title="result.md",
                            content=display_text,
                            filetype="markdown",
                        )
                        _file_uploaded = True
                        delivered_as_attachment = True
                        logger.info("Result delivered as .md file attachment (fallback)")
                    except Exception as _upload_err:
                        logger.warning("upload_markdown fallback failed: %s", _upload_err)

                # Fallback 2: split into multiple messages
                if not _file_uploaded:
                    try:
                        primary_message_id = await self._send_split_result_messages(
                            im_client,
                            target_context,
                            display_text,
                            enhanced.buttons if enhanced else [],
                            parse_mode,
                        )
                        logger.info("Result delivered via split messages (fallback)")
                    except Exception as _split_err:
                        logger.error("Split message fallback also failed: %s", _split_err)

            # Explain attachment-only delivery or total failure once all attempts settle.
            try:
                if delivered_as_attachment:
                    notice = self._t("info.resultDeliveredAsAttachment")
                elif primary_message_id is None and display_text:
                    notice = self._t("error.resultDeliveryFailed")
                else:
                    notice = None
                if notice:
                    await im_client.send_message(target_context, notice, parse_mode="markdown")
            except Exception:
                logger.error("Failed to send delivery status notification")

            # Upload extracted file attachments
            if enhanced and enhanced.files:
                await self._upload_file_links(im_client, target_context, enhanced.files)

            if primary_message_id:
                try:
                    self.controller.session_handler.finalize_scheduled_delivery(context, primary_message_id)
                except Exception as err:
                    logger.warning("Failed to finalize scheduled delivery anchor: %s", err)

            # Final result closes the current turn: clear consolidated
            # assistant/tool/system message state so the next user turn starts
            # a fresh log message instead of appending to the previous one.
            consolidated_key = self._get_consolidated_message_key(context)
            lock = self._get_consolidated_message_lock(consolidated_key)
            async with lock:
                self._consolidated_message_ids.pop(consolidated_key, None)
                self._consolidated_message_buffers.pop(consolidated_key, None)

            return primary_message_id

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
            return None

        reply_enhancements_on = getattr(self.controller.config, "reply_enhancements", True)
        if reply_enhancements_on:
            chunk = strip_file_links(text).strip()
        else:
            chunk = text.strip()

        if not chunk:
            return None

        consolidated_key = self._get_consolidated_message_key(context)
        lock = self._get_consolidated_message_lock(consolidated_key)

        async with lock:
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
                    return existing_message_id
                self._consolidated_message_ids.pop(consolidated_key, None)

            try:
                new_id = await im_client.send_message(target_context, updated, parse_mode="markdown")
                self._consolidated_message_ids[consolidated_key] = new_id
                return new_id
            except Exception as err:
                logger.error(f"Failed to send Log Message: {err}", exc_info=True)
                return None

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
    ) -> str:
        """Send a message with quick-reply buttons appended."""
        from modules.im.base import InlineButton, InlineKeyboard

        row = []
        for btn in buttons:
            callback = f"quick_reply:{btn.text}"
            row.append(InlineButton(text=btn.text, callback_data=callback))

        platform = (
            context.platform or (context.platform_specific or {}).get("platform") or self.controller.config.platform
        )
        rows = [[button] for button in row] if platform in {"lark", "telegram"} else [row]
        keyboard = InlineKeyboard(buttons=rows)
        return await im_client.send_message_with_buttons(
            context,
            text,
            keyboard,
            parse_mode=parse_mode,
        )

    async def _send_split_result_messages(
        self,
        im_client,
        context: MessageContext,
        text: str,
        buttons,
        parse_mode,
    ) -> Optional[str]:
        chunks = self._split_result_text_for_context(context, text)
        first_message_id: Optional[str] = None

        for index, chunk in enumerate(chunks):
            is_last_chunk = index == len(chunks) - 1
            message_id: Optional[str] = None

            if is_last_chunk and buttons and self._supports_quick_replies(context):
                try:
                    message_id = await self._send_with_quick_replies(
                        im_client,
                        context,
                        chunk,
                        buttons,
                        parse_mode,
                    )
                except Exception as err:
                    logger.warning("Failed to send split result chunk with quick replies, falling back: %s", err)

            if message_id is None:
                message_id = await im_client.send_message(context, chunk, parse_mode=parse_mode)

            if first_message_id is None:
                first_message_id = message_id

        return first_message_id

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
