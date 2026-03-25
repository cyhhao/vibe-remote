from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from config.discovered_chats import DiscoveredChatsStore
from config.v2_config import TelegramConfig
from vibe.i18n import t as i18n_t

from .base import BaseIMClient, FileAttachment, MessageContext, InlineButton, InlineKeyboard
from .formatters import TelegramFormatter
from . import telegram_api

logger = logging.getLogger(__name__)


class TelegramBot(BaseIMClient):
    """Telegram adapter using Bot API long polling."""

    def __init__(self, config: TelegramConfig):
        super().__init__(config)
        self.config = config
        self.formatter = TelegramFormatter()
        self.settings_manager = None
        self.sessions = None
        self._controller = None
        self._stop_event = threading.Event()
        self._offset: Optional[int] = None
        self._bot_user: Optional[dict[str, Any]] = None
        self._on_ready: Optional[Callable] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_settings_manager(self, settings_manager):
        self.settings_manager = settings_manager
        self.sessions = getattr(settings_manager, "sessions", None)

    def set_controller(self, controller):
        self._controller = controller

    def register_callbacks(
        self,
        on_message: Optional[Callable] = None,
        on_command: Optional[Dict[str, Callable]] = None,
        on_callback_query: Optional[Callable] = None,
        **kwargs,
    ):
        super().register_callbacks(on_message, on_command, on_callback_query, **kwargs)
        if "on_ready" in kwargs:
            self._on_ready = kwargs["on_ready"]

    def _t(self, key: str, **kwargs) -> str:
        lang = "en"
        if self._controller and hasattr(self._controller, "_get_lang"):
            lang = self._controller._get_lang()
        return i18n_t(key, lang, **kwargs)

    def get_default_parse_mode(self) -> Optional[str]:
        return None

    def should_use_thread_for_reply(self) -> bool:
        return True

    def should_use_message_id_for_channel_session(self, context: Optional[MessageContext] = None) -> bool:
        return False

    def format_markdown(self, text: str) -> str:
        return text

    def register_handlers(self):
        return None

    def run(self):
        if not self.config.bot_token:
            raise ValueError("Telegram bot token is required")
        self._stop_event.clear()
        asyncio.run(self._run())

    def stop(self):
        self._stop_event.set()

    async def shutdown(self) -> None:
        self._stop_event.set()

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._bot_user = (await telegram_api.get_me(self.config.bot_token)).get("result")
        logger.info("Telegram bot connected as @%s", self._bot_user.get("username") if self._bot_user else "unknown")
        if self._on_ready:
            await self._on_ready()

        while not self._stop_event.is_set():
            try:
                updates = await telegram_api.get_updates(self.config.bot_token, self._offset)
                for update in updates.get("result", []):
                    self._offset = int(update["update_id"]) + 1
                    await self._handle_update(update)
            except Exception as err:
                logger.warning("Telegram poll loop error: %s", err, exc_info=True)
                await asyncio.sleep(2)

    async def _handle_update(self, update: dict[str, Any]) -> None:
        if update.get("callback_query"):
            await self._handle_callback_query(update["callback_query"])
            return
        message = update.get("message")
        if message:
            await self._handle_message(message)

    async def _handle_message(self, message: dict[str, Any]) -> None:
        context = self._build_message_context(message)
        if context is None:
            return

        text = message.get("text") or message.get("caption") or ""
        text = self._normalize_command_text(text)

        if self.config.require_mention and not context.platform_specific.get("is_dm", False):
            if not self._is_explicitly_addressed(message, text):
                return

        denial = self.check_authorization(
            user_id=context.user_id,
            channel_id=context.channel_id,
            is_dm=bool(context.platform_specific.get("is_dm")),
            text=text,
            settings_manager=self.settings_manager,
        )
        if not denial.allowed:
            denial_text = self.build_auth_denial_text(denial.denial, context.channel_id)
            if denial_text:
                await self.send_message(context, denial_text)
            return

        allow_plain_bind = self.should_allow_plain_bind(
            user_id=context.user_id,
            is_dm=bool(context.platform_specific.get("is_dm")),
            settings_manager=self.settings_manager,
        )
        if await self.dispatch_text_command(context, text, allow_plain_bind=allow_plain_bind):
            return

        context = await self._maybe_route_to_forum_topic(context, message, text)

        if self.on_message_callback:
            await self.on_message_callback(context, text)

    async def _maybe_route_to_forum_topic(
        self,
        context: MessageContext,
        message: dict[str, Any],
        text: str,
    ) -> MessageContext:
        if not self._should_auto_create_topic(context, message, text):
            return context

        try:
            new_context = await self.start_new_topic_session(context, seed_text=text, message=message)
            if new_context is not None:
                return new_context
        except Exception as err:
            logger.warning("Telegram forum auto-topic failed, falling back to current topic: %s", err, exc_info=True)
        return context

    def _should_auto_create_topic(self, context: MessageContext, message: dict[str, Any], text: str) -> bool:
        if not self.config.forum_auto_topic:
            return False
        if (context.platform_specific or {}).get("chat_type") != "supergroup":
            return False
        if not bool(message.get("is_topic_message")):
            return False
        if str(context.thread_id or "") != "1":
            return False
        if message.get("reply_to_message"):
            return False
        if text.startswith("/"):
            return False
        return True

    def _derive_topic_title(self, text: str, message: dict[str, Any]) -> str:
        first_line = ""
        if text:
            first_line = text.strip().splitlines()[0].strip()
        if first_line.startswith("/"):
            first_line = ""
        if first_line:
            if len(first_line) > 60:
                return first_line[:57].rstrip() + "..."
            return first_line
        sender = (message.get("from") or {}).get("first_name") or "Session"
        return f"{sender} {datetime.now().strftime('%m-%d %H:%M')}"

    async def start_new_topic_session(
        self,
        context: MessageContext,
        *,
        seed_text: str = "",
        message: Optional[dict[str, Any]] = None,
    ) -> Optional[MessageContext]:
        payload = context.platform_specific or {}
        if payload.get("chat_type") != "supergroup":
            return None
        if not context.thread_id and not payload.get("is_topic_message"):
            return None

        topic_name = self._derive_topic_title(seed_text, message or {})
        created = await telegram_api.create_forum_topic(self.config.bot_token, context.channel_id, topic_name)
        topic = created.get("result") or {}
        topic_id = topic.get("message_thread_id")
        if topic_id is None:
            raise RuntimeError("Telegram createForumTopic returned no message_thread_id")

        topic_context = MessageContext(
            user_id=context.user_id,
            channel_id=context.channel_id,
            thread_id=str(topic_id),
            message_id=context.message_id,
            platform="telegram",
            files=context.files,
            platform_specific={
                **payload,
                "is_topic_message": True,
                "auto_topic_created": True,
                "topic_name": topic_name,
            },
        )

        if str(context.thread_id or "") == "1":
            try:
                await self.send_message(
                    context,
                    self._t("telegram.autoTopicGeneralNotice", topic=topic_name),
                    reply_to=context.message_id,
                )
            except Exception:
                logger.debug("Failed to send Telegram General handoff notice", exc_info=True)

        try:
            intro = self._t("telegram.autoTopicIntro", topic=topic_name)
            if seed_text:
                intro = f"{intro}\n\n> {seed_text}"
            await self.send_message(topic_context, intro)
        except Exception:
            logger.debug("Failed to send Telegram topic intro", exc_info=True)

        return topic_context

    async def _handle_callback_query(self, payload: dict[str, Any]) -> None:
        message = payload.get("message") or {}
        chat = message.get("chat") or {}
        from_user = payload.get("from") or {}
        if not chat or not from_user:
            return
        self._remember_discovered_chat(chat, message)
        thread_id = message.get("message_thread_id")
        context = MessageContext(
            user_id=str(from_user.get("id")),
            channel_id=str(chat.get("id")),
            thread_id=str(thread_id) if thread_id is not None else None,
            message_id=str(message.get("message_id")),
            platform="telegram",
            platform_specific={
                "is_dm": chat.get("type") == "private",
                "chat_type": chat.get("type"),
                "chat_title": chat.get("title") or chat.get("username"),
                "is_topic_message": bool(message.get("is_topic_message")),
                "raw_message": message,
            },
        )
        callback_id = str(payload.get("id"))
        context.platform_specific = {
            **(context.platform_specific or {}),
            "callback_id": callback_id,
            "callback_query": payload,
        }
        if self.on_callback_query_callback:
            await self.on_callback_query_callback(context, payload.get("data", ""))
        await self.answer_callback(callback_id)

    def _build_message_context(self, message: dict[str, Any]) -> Optional[MessageContext]:
        chat = message.get("chat") or {}
        from_user = message.get("from") or {}
        if not chat or not from_user:
            return None
        self._remember_discovered_chat(chat, message)

        chat_id = str(chat.get("id"))
        user_id = str(from_user.get("id"))
        thread_id = message.get("message_thread_id")
        files = self._extract_files(message)

        return MessageContext(
            user_id=user_id,
            channel_id=chat_id,
            thread_id=str(thread_id) if thread_id is not None else None,
            message_id=str(message.get("message_id")),
            files=files,
            platform="telegram",
            platform_specific={
                "is_dm": chat.get("type") == "private",
                "chat_type": chat.get("type"),
                "chat_title": chat.get("title") or chat.get("username"),
                "raw_message": message,
            },
        )

    def _remember_discovered_chat(self, chat: dict[str, Any], message: Optional[dict[str, Any]] = None) -> None:
        try:
            parts = [str(chat.get("first_name") or "").strip(), str(chat.get("last_name") or "").strip()]
            display_name = " ".join(part for part in parts if part).strip()
            name = chat.get("title") or chat.get("username") or display_name or str(chat.get("id") or "")
            chat_type = str(chat.get("type") or "")
            is_topic_message = bool((message or {}).get("is_topic_message"))
            is_forum = bool(chat.get("is_forum")) or is_topic_message
            DiscoveredChatsStore.get_instance().remember_chat(
                platform="telegram",
                chat_id=str(chat.get("id")),
                name=name,
                username=str(chat.get("username") or ""),
                chat_type=chat_type,
                is_private=chat_type == "private",
                is_forum=is_forum,
                supports_topics=chat_type == "supergroup" and is_forum,
            )
        except Exception:
            logger.debug("Failed to remember Telegram discovered chat", exc_info=True)

    def _extract_files(self, message: dict[str, Any]) -> list[FileAttachment]:
        files: list[FileAttachment] = []
        document = message.get("document")
        if document:
            files.append(
                FileAttachment(
                    name=document.get("file_name") or "telegram-document",
                    mimetype=document.get("mime_type") or "application/octet-stream",
                    url=document.get("file_id"),
                    size=document.get("file_size"),
                )
            )
        photo = message.get("photo") or []
        if photo:
            best = photo[-1]
            files.append(
                FileAttachment(
                    name="telegram-photo.jpg",
                    mimetype="image/jpeg",
                    url=best.get("file_id"),
                    size=best.get("file_size"),
                )
            )
        return files

    def _normalize_command_text(self, text: str) -> str:
        stripped = (text or "").strip()
        if not stripped.startswith("/"):
            return stripped
        head, *tail = stripped.split(maxsplit=1)
        if "@" in head and self._bot_user and self._bot_user.get("username"):
            command, _, username = head.partition("@")
            if username.lower() == str(self._bot_user.get("username")).lower():
                head = command
        return " ".join([head, *tail]).strip()

    def _is_explicitly_addressed(self, message: dict[str, Any], text: str) -> bool:
        if text.startswith("/"):
            return True
        reply_to = message.get("reply_to_message") or {}
        reply_from = reply_to.get("from") or {}
        if self._bot_user and str(reply_from.get("id")) == str(self._bot_user.get("id")):
            return True
        username = str((self._bot_user or {}).get("username") or "")
        if not username:
            return False
        entities = message.get("entities") or []
        for entity in entities:
            if entity.get("type") != "mention":
                continue
            offset = int(entity.get("offset", 0))
            length = int(entity.get("length", 0))
            if text[offset : offset + length].lower() == f"@{username.lower()}":
                return True
        return False

    def _build_payload(
        self,
        context: MessageContext,
        text: Optional[str] = None,
        keyboard: Optional[InlineKeyboard] = None,
        *,
        reply_to: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": context.channel_id}
        if context.thread_id:
            payload["message_thread_id"] = int(context.thread_id)
        if text is not None:
            payload["text"] = text
        if reply_to:
            payload["reply_parameters"] = {"message_id": int(reply_to)}
        if keyboard is not None:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": button.text, "callback_data": button.callback_data} for button in row]
                    for row in keyboard.buttons
                ]
            }
        return payload

    async def send_message(
        self, context: MessageContext, text: str, parse_mode: Optional[str] = None, reply_to: Optional[str] = None
    ) -> str:
        payload = self._build_payload(context, self.format_markdown(text), reply_to=reply_to)
        result = await telegram_api.call_api(self.config.bot_token, "sendMessage", payload)
        return str(result["result"]["message_id"])

    async def send_message_with_buttons(
        self, context: MessageContext, text: str, keyboard: InlineKeyboard, parse_mode: Optional[str] = None
    ) -> str:
        payload = self._build_payload(context, self.format_markdown(text), keyboard=keyboard)
        result = await telegram_api.call_api(self.config.bot_token, "sendMessage", payload)
        return str(result["result"]["message_id"])

    async def edit_message(
        self,
        context: MessageContext,
        message_id: str,
        text: Optional[str] = None,
        keyboard: Optional[InlineKeyboard] = None,
        parse_mode: Optional[str] = None,
    ) -> bool:
        payload = {
            "chat_id": context.channel_id,
            "message_id": int(message_id),
        }
        if keyboard is not None:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": button.text, "callback_data": button.callback_data} for button in row]
                    for row in keyboard.buttons
                ]
            }
        if text is not None:
            payload["text"] = self.format_markdown(text)
            await telegram_api.call_api(self.config.bot_token, "editMessageText", payload)
            return True
        await telegram_api.call_api(self.config.bot_token, "editMessageReplyMarkup", payload)
        return True

    async def answer_callback(self, callback_id: str, text: Optional[str] = None, show_alert: bool = False) -> bool:
        payload = {"callback_query_id": callback_id, "show_alert": show_alert}
        if text:
            payload["text"] = text
        await telegram_api.call_api(self.config.bot_token, "answerCallbackQuery", payload)
        return True

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        result = await telegram_api.call_api(self.config.bot_token, "getChat", {"chat_id": user_id})
        chat = result["result"]
        display_name = chat.get("first_name") or chat.get("username") or "Telegram User"
        return {"id": user_id, "name": display_name, "display_name": display_name, "real_name": display_name}

    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        result = await telegram_api.call_api(self.config.bot_token, "getChat", {"chat_id": channel_id})
        chat = result["result"]
        name = chat.get("title") or chat.get("username") or channel_id
        return {"id": channel_id, "name": name, "type": chat.get("type")}

    async def send_dm(self, user_id: str, text: str, **kwargs) -> Optional[str]:
        context = MessageContext(user_id=user_id, channel_id=user_id, platform="telegram", platform_specific={"is_dm": True})
        return await self.send_message(context, text)

    async def send_typing_indicator(self, context: MessageContext) -> bool:
        payload = {"chat_id": context.channel_id, "action": "typing"}
        if context.thread_id:
            payload["message_thread_id"] = int(context.thread_id)
        await telegram_api.call_api(self.config.bot_token, "sendChatAction", payload)
        return True

    async def clear_typing_indicator(self, context: MessageContext) -> bool:
        return True

    async def upload_file_from_path(
        self,
        context: MessageContext,
        file_path: str,
        title: Optional[str] = None,
    ) -> str:
        payload = self._build_payload(context)
        if title:
            payload["caption"] = title
        result = await telegram_api.send_multipart_file(
            self.config.bot_token,
            "sendDocument",
            payload,
            file_path,
            "document",
        )
        return str(result["result"]["message_id"])

    async def upload_image_from_path(
        self,
        context: MessageContext,
        file_path: str,
        title: Optional[str] = None,
    ) -> str:
        payload = self._build_payload(context)
        if title:
            payload["caption"] = title
        result = await telegram_api.send_multipart_file(
            self.config.bot_token,
            "sendPhoto",
            payload,
            file_path,
            "photo",
        )
        return str(result["result"]["message_id"])

    async def download_file(
        self,
        file_info: Dict[str, Any],
        max_bytes: Optional[int] = None,
        timeout_seconds: int = 30,
    ) -> Optional[bytes]:
        file_id = (
            file_info.get("telegram_file_id")
            or file_info.get("url")
            or file_info.get("file_id")
        )
        if not file_id:
            raise ValueError("Telegram file_id is required")
        file_result = await telegram_api.get_file(self.config.bot_token, str(file_id))
        file_path = file_result["result"]["file_path"]
        content = await telegram_api.download_file(self.config.bot_token, file_path, timeout_seconds=timeout_seconds)
        if max_bytes is not None and len(content) > max_bytes:
            raise ValueError("Downloaded file exceeds max_bytes")
        return content
