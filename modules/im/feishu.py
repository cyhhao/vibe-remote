"""Feishu/Lark implementation of the IM client using lark-oapi SDK."""

import asyncio
import io
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from .base import BaseIMClient, FileAttachment, InlineButton, InlineKeyboard, MessageContext
from .formatters import FeishuFormatter
from config.v2_config import LarkConfig
from vibe.i18n import get_supported_languages, t as i18n_t

logger = logging.getLogger(__name__)

# Feishu emoji name mapping (common reactions)
_EMOJI_MAP: Dict[str, str] = {
    "eyes": "EYES",
    "👀": "EYES",
    "thumbsup": "THUMBSUP",
    "👍": "THUMBSUP",
    "+1": "THUMBSUP",
    "thumbsdown": "THUMBSDOWN",
    "👎": "THUMBSDOWN",
    "heart": "HEART",
    "❤️": "HEART",
    "check": "OK",
    "white_check_mark": "OK",
    "✅": "OK",
    "x": "CROSSMARK",
    "❌": "CROSSMARK",
    "rocket": "ROCKET",
    "🚀": "ROCKET",
    "smile": "SMILE",
    "😄": "SMILE",
}


def _normalize_emoji(emoji: str) -> str:
    """Convert common emoji names/unicode to Feishu emoji_type."""
    cleaned = emoji.strip().strip(":")
    if cleaned in _EMOJI_MAP:
        return _EMOJI_MAP[cleaned]
    return cleaned.upper()


class FeishuBot(BaseIMClient):
    """Feishu/Lark implementation of the IM client using lark-oapi SDK."""

    def __init__(self, config: LarkConfig):
        super().__init__(config)
        self.config = config
        self.formatter = FeishuFormatter()

        # Lark SDK client (for API calls)
        self._lark_client = None
        # WebSocket client (for event subscription)
        self._ws_client = None
        # Event handler
        self._event_handler = None
        # Bot open_id (populated on startup)
        self._bot_open_id: Optional[str] = None

        self.settings_manager = None
        self._controller = None
        self._on_ready: Optional[Callable] = None
        self._on_settings_update: Optional[Callable] = None
        self._on_change_cwd: Optional[Callable] = None
        self._on_routing_update: Optional[Callable] = None
        self._on_routing_modal_update: Optional[Callable] = None
        self._on_resume_session: Optional[Callable] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_event_ids: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Lifecycle / injection
    # ------------------------------------------------------------------
    def set_settings_manager(self, settings_manager):
        """Set the settings manager for thread tracking."""
        self.settings_manager = settings_manager

    def set_controller(self, controller):
        """Set the controller reference for handling update button clicks."""
        self._controller = controller

    # ------------------------------------------------------------------
    # i18n helpers
    # ------------------------------------------------------------------
    def _get_lang(self, channel_id: Optional[str] = None) -> str:
        if self._controller and hasattr(self._controller, "config"):
            if hasattr(self._controller, "_get_lang"):
                return self._controller._get_lang()
            return getattr(self._controller.config, "language", "en")
        return "en"

    def _t(self, key: str, channel_id: Optional[str] = None, **kwargs) -> str:
        lang = self._get_lang(channel_id)
        return i18n_t(key, lang, **kwargs)

    # ------------------------------------------------------------------
    # Platform metadata
    # ------------------------------------------------------------------
    def get_default_parse_mode(self) -> str:
        return "markdown"

    def should_use_thread_for_reply(self) -> bool:
        return True

    def format_markdown(self, text: str) -> str:
        # Feishu's interactive card markdown is close to standard markdown
        return text

    # ------------------------------------------------------------------
    # SDK initialisation helpers
    # ------------------------------------------------------------------
    def _ensure_client(self):
        """Ensure the lark-oapi REST client is initialised."""
        if self._lark_client is not None:
            return
        import lark_oapi as lark

        self._lark_client = (
            lark.Client.builder()
            .app_id(self.config.app_id)
            .app_secret(self.config.app_secret)
            .log_level(lark.LogLevel.WARNING)
            .build()
        )

    async def _get_tenant_token(self) -> Optional[str]:
        """Get tenant access token for raw HTTP calls."""
        try:
            import urllib.request

            url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
            data = json.dumps({"app_id": self.config.app_id, "app_secret": self.config.app_secret}).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                if result.get("code") == 0:
                    return result.get("tenant_access_token")
        except Exception as exc:
            logger.error("Failed to get tenant_access_token: %s", exc)
        return None

    async def _fetch_bot_info(self):
        """Fetch the bot's own open_id on startup."""
        try:
            token = await self._get_tenant_token()
            if not token:
                return
            url = "https://open.feishu.cn/open-apis/bot/v3/info"
            req_obj = __import__("urllib.request", fromlist=["Request"]).Request(
                url, headers={"Authorization": f"Bearer {token}"}
            )
            with __import__("urllib.request", fromlist=["urlopen"]).urlopen(req_obj, timeout=10) as resp:
                result = json.loads(resp.read().decode())
                if result.get("code") == 0:
                    bot = result.get("bot", {})
                    self._bot_open_id = bot.get("open_id")
                    logger.info("Feishu bot info: open_id=%s", self._bot_open_id)
        except Exception as exc:
            logger.warning("Failed to fetch bot info: %s", exc)

    # ------------------------------------------------------------------
    # Event deduplication
    # ------------------------------------------------------------------
    def _is_duplicate_event(self, event_id: Optional[str]) -> bool:
        if not event_id:
            return False
        now = time.time()
        expiry = now - 30
        for key in list(self._recent_event_ids.keys()):
            if self._recent_event_ids[key] < expiry:
                del self._recent_event_ids[key]
        if event_id in self._recent_event_ids:
            logger.debug("Ignoring duplicate Feishu event_id %s", event_id)
            return True
        self._recent_event_ids[event_id] = now
        return False

    # ------------------------------------------------------------------
    # Channel authorisation
    # ------------------------------------------------------------------
    async def _is_authorized_channel(self, channel_id: str) -> bool:
        if not self.settings_manager:
            logger.warning("No settings_manager configured; rejecting by default")
            return False
        settings = self.settings_manager.get_channel_settings(channel_id)
        if settings is None:
            logger.warning("No channel settings found; rejecting by default")
            return False
        if settings.enabled:
            return True
        logger.info("Channel not enabled in settings.json: %s", channel_id)
        return False

    async def _send_unauthorized_message(self, channel_id: str):
        try:
            ctx = MessageContext(user_id="system", channel_id=channel_id)
            await self.send_message(
                ctx,
                f"❌ {self._t('error.channelNotEnabled', channel_id)}",
            )
        except Exception as exc:
            logger.error("Failed to send unauthorized message to %s: %s", channel_id, exc)

    # ------------------------------------------------------------------
    # Sending messages
    # ------------------------------------------------------------------
    async def send_message(
        self,
        context: MessageContext,
        text: str,
        parse_mode: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> str:
        """Send a text message to Feishu."""
        self._ensure_client()
        if not text:
            raise ValueError("Feishu send_message requires non-empty text")

        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        body_builder = (
            CreateMessageRequestBody.builder()
            .receive_id(context.channel_id)
            .msg_type("interactive")
            .content(self._build_card_json(text))
        )

        # Thread reply
        root_id = context.thread_id or reply_to
        if root_id:
            body_builder = body_builder.uuid(f"{root_id}-{int(time.time() * 1000)}")

        request = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body_builder.build()).build()

        # If replying in thread, use reply API
        if root_id:
            return await self._reply_message(root_id, text)

        response = await self._lark_client.im.v1.message.acreate(request)
        if not response.success():
            logger.error(
                "Failed to send Feishu message: code=%s msg=%s",
                response.code,
                response.msg,
            )
            raise RuntimeError(f"Feishu send_message failed: {response.msg}")

        message_id = response.data.message_id
        if self.settings_manager and (context.thread_id or reply_to):
            thread = context.thread_id or reply_to
            self.settings_manager.mark_thread_active(context.user_id, context.channel_id, thread)
        return message_id

    async def _reply_message(self, parent_id: str, text: str) -> str:
        """Reply to an existing message (thread reply)."""
        self._ensure_client()
        from lark_oapi.api.im.v1 import (
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        request = (
            ReplyMessageRequest.builder()
            .message_id(parent_id)
            .request_body(
                ReplyMessageRequestBody.builder().msg_type("interactive").content(self._build_card_json(text)).build()
            )
            .build()
        )

        response = await self._lark_client.im.v1.message.areply(request)
        if not response.success():
            logger.error(
                "Failed to reply Feishu message: code=%s msg=%s",
                response.code,
                response.msg,
            )
            raise RuntimeError(f"Feishu reply failed: {response.msg}")
        return response.data.message_id

    def _build_card_json(
        self,
        text: str,
        buttons: Optional[List[List[dict]]] = None,
    ) -> str:
        """Build interactive card JSON for a message with optional buttons."""
        elements: list = [{"tag": "markdown", "content": text}]
        if buttons:
            for row in buttons:
                actions = []
                for btn in row:
                    actions.append(
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": btn["text"]},
                            "type": btn.get("type", "default"),
                            "value": {"key": btn["callback_data"]},
                        }
                    )
                elements.append({"tag": "action", "actions": actions})

        card = {
            "config": {"wide_screen_mode": True},
            "elements": elements,
        }
        return json.dumps(card, ensure_ascii=False)

    async def send_message_with_buttons(
        self,
        context: MessageContext,
        text: str,
        keyboard: InlineKeyboard,
        parse_mode: Optional[str] = None,
    ) -> str:
        """Send a message with interactive card buttons."""
        self._ensure_client()
        if not text:
            raise ValueError("Feishu send_message_with_buttons requires non-empty text")

        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        button_rows: List[List[dict]] = []
        for row in keyboard.buttons:
            btn_row = []
            for button in row:
                btn_row.append({"text": button.text, "callback_data": button.callback_data})
            button_rows.append(btn_row)

        card_json = self._build_card_json(text, button_rows)

        # Thread reply
        root_id = context.thread_id
        if root_id:
            return await self._reply_message_with_card(root_id, card_json)

        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(context.channel_id)
                .msg_type("interactive")
                .content(card_json)
                .build()
            )
            .build()
        )

        response = await self._lark_client.im.v1.message.acreate(request)
        if not response.success():
            logger.error(
                "Failed to send Feishu card message: code=%s msg=%s",
                response.code,
                response.msg,
            )
            raise RuntimeError(f"Feishu send card failed: {response.msg}")

        message_id = response.data.message_id
        if self.settings_manager and context.thread_id:
            self.settings_manager.mark_thread_active(context.user_id, context.channel_id, context.thread_id)
        return message_id

    async def _reply_message_with_card(self, parent_id: str, card_json: str) -> str:
        """Reply to a message with an interactive card."""
        self._ensure_client()
        from lark_oapi.api.im.v1 import (
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        request = (
            ReplyMessageRequest.builder()
            .message_id(parent_id)
            .request_body(ReplyMessageRequestBody.builder().msg_type("interactive").content(card_json).build())
            .build()
        )

        response = await self._lark_client.im.v1.message.areply(request)
        if not response.success():
            raise RuntimeError(f"Feishu reply card failed: {response.msg}")
        return response.data.message_id

    # ------------------------------------------------------------------
    # Edit message
    # ------------------------------------------------------------------
    async def edit_message(
        self,
        context: MessageContext,
        message_id: str,
        text: Optional[str] = None,
        keyboard: Optional[InlineKeyboard] = None,
        parse_mode: Optional[str] = None,
    ) -> bool:
        """Edit an existing Feishu message."""
        self._ensure_client()
        try:
            from lark_oapi.api.im.v1 import (
                PatchMessageRequest,
                PatchMessageRequestBody,
            )

            button_rows = None
            if keyboard:
                button_rows = []
                for row in keyboard.buttons:
                    btn_row = []
                    for button in row:
                        btn_row.append({"text": button.text, "callback_data": button.callback_data})
                    button_rows.append(btn_row)

            if text is not None:
                card_json = self._build_card_json(text, button_rows)
            elif keyboard is not None:
                # Only updating buttons, need some fallback text
                card_json = self._build_card_json("", button_rows)
            else:
                return True

            request = (
                PatchMessageRequest.builder()
                .message_id(message_id)
                .request_body(PatchMessageRequestBody.builder().content(card_json).build())
                .build()
            )

            response = await self._lark_client.im.v1.message.apatch(request)
            if not response.success():
                logger.error(
                    "Failed to edit Feishu message: code=%s msg=%s",
                    response.code,
                    response.msg,
                )
                return False
            return True
        except Exception as exc:
            logger.error("Error editing Feishu message %s: %s", message_id, exc)
            return False

    async def remove_inline_keyboard(
        self,
        context: MessageContext,
        message_id: str,
        text: Optional[str] = None,
        parse_mode: Optional[str] = None,
    ) -> bool:
        """Remove interactive buttons from a Feishu message."""
        # Re-patch the card with only markdown content (no actions)
        display_text = text or ""
        return await self.edit_message(context, message_id, text=display_text, keyboard=None, parse_mode=parse_mode)

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------
    async def answer_callback(self, callback_id: str, text: Optional[str] = None, show_alert: bool = False) -> bool:
        """Feishu card callbacks don't need explicit acknowledgement."""
        return True

    # ------------------------------------------------------------------
    # Reactions
    # ------------------------------------------------------------------
    async def add_reaction(self, context: MessageContext, message_id: str, emoji: str) -> bool:
        self._ensure_client()
        emoji_type = _normalize_emoji(emoji)
        try:
            from lark_oapi.api.im.v1 import (
                CreateMessageReactionRequest,
                CreateMessageReactionRequestBody,
                Emoji,
            )

            request = (
                CreateMessageReactionRequest.builder()
                .message_id(message_id)
                .request_body(
                    CreateMessageReactionRequestBody.builder()
                    .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                    .build()
                )
                .build()
            )
            response = await self._lark_client.im.v1.message_reaction.acreate(request)
            if not response.success():
                logger.debug(
                    "Feishu add_reaction failed: code=%s msg=%s",
                    response.code,
                    response.msg,
                )
                return False
            return True
        except Exception as exc:
            logger.debug("Failed to add Feishu reaction: %s", exc)
            return False

    async def remove_reaction(self, context: MessageContext, message_id: str, emoji: str) -> bool:
        self._ensure_client()
        # Feishu remove reaction requires reaction_id; we'd need to list reactions first.
        # For simplicity, attempt to delete by listing and finding matching reaction.
        try:
            token = await self._get_tenant_token()
            if not token:
                return False
            emoji_type = _normalize_emoji(emoji)
            # List reactions
            url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                    if resp.status != 200:
                        return False
                    data = await resp.json()
                    items = data.get("data", {}).get("items", [])
                    for item in items:
                        rt = item.get("reaction_type", {})
                        if rt.get("emoji_type") == emoji_type:
                            reaction_id = item.get("reaction_id")
                            if reaction_id:
                                del_url = f"{url}/{reaction_id}"
                                async with session.delete(
                                    del_url,
                                    headers={"Authorization": f"Bearer {token}"},
                                ) as del_resp:
                                    return del_resp.status == 200
            return False
        except Exception as exc:
            logger.debug("Failed to remove Feishu reaction: %s", exc)
            return False

    # ------------------------------------------------------------------
    # File handling
    # ------------------------------------------------------------------
    async def upload_markdown(
        self,
        context: MessageContext,
        title: str,
        content: str,
        filetype: str = "markdown",
    ) -> str:
        """Upload markdown content as a file to Feishu."""
        self._ensure_client()
        try:
            token = await self._get_tenant_token()
            if not token:
                raise RuntimeError("Failed to obtain tenant token for file upload")

            file_data = (content or "").encode("utf-8")
            # Upload via multipart form
            url = "https://open.feishu.cn/open-apis/im/v1/files"
            form = aiohttp.FormData()
            form.add_field("file_type", "stream")
            form.add_field("file_name", title)
            form.add_field(
                "file",
                io.BytesIO(file_data),
                filename=title,
                content_type="application/octet-stream",
            )

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=form,
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    result = await resp.json()
                    if result.get("code") != 0:
                        raise RuntimeError(f"Feishu file upload failed: {result.get('msg')}")
                    file_key = result.get("data", {}).get("file_key", "")

            # Send file message to the chat
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
            )

            target = context.thread_id or context.channel_id
            file_content = json.dumps({"file_key": file_key})

            # If in thread, reply
            if context.thread_id:
                from lark_oapi.api.im.v1 import (
                    ReplyMessageRequest,
                    ReplyMessageRequestBody,
                )

                request = (
                    ReplyMessageRequest.builder()
                    .message_id(context.thread_id)
                    .request_body(ReplyMessageRequestBody.builder().msg_type("file").content(file_content).build())
                    .build()
                )
                response = await self._lark_client.im.v1.message.areply(request)
            else:
                request = (
                    CreateMessageRequest.builder()
                    .receive_id_type("chat_id")
                    .request_body(
                        CreateMessageRequestBody.builder()
                        .receive_id(context.channel_id)
                        .msg_type("file")
                        .content(file_content)
                        .build()
                    )
                    .build()
                )
                response = await self._lark_client.im.v1.message.acreate(request)

            if not response.success():
                raise RuntimeError(f"Feishu file message failed: {response.msg}")
            return response.data.message_id
        except Exception as exc:
            logger.error("Error uploading markdown to Feishu: %s", exc)
            raise

    async def download_file(
        self,
        file_info: Dict[str, Any],
        max_bytes: int = 20 * 1024 * 1024,
        timeout_seconds: int = 30,
    ) -> Optional[bytes]:
        """Download a file from Feishu."""
        message_id = file_info.get("message_id")
        file_key = file_info.get("file_key")
        if not message_id or not file_key:
            # Try direct URL download
            url = file_info.get("url")
            if not url:
                logger.warning("No download info for Feishu file: %s", file_info.get("name"))
                return None
            try:
                token = await self._get_tenant_token()
                headers = {"Authorization": f"Bearer {token}"} if token else {}
                timeout = aiohttp.ClientTimeout(total=timeout_seconds)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url, headers=headers) as resp:
                        if resp.status != 200:
                            return None
                        chunks = []
                        total = 0
                        async for chunk in resp.content.iter_chunked(64 * 1024):
                            total += len(chunk)
                            if total > max_bytes:
                                return None
                            chunks.append(chunk)
                        return b"".join(chunks)
            except Exception as exc:
                logger.error("Error downloading Feishu file: %s", exc)
                return None

        try:
            token = await self._get_tenant_token()
            if not token:
                return None
            url = f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=file"
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                    if resp.status != 200:
                        logger.error("Failed to download Feishu file: HTTP %s", resp.status)
                        return None
                    chunks = []
                    total = 0
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        total += len(chunk)
                        if total > max_bytes:
                            logger.warning("Feishu file exceeds max size, aborting")
                            return None
                        chunks.append(chunk)
                    return b"".join(chunks)
        except asyncio.TimeoutError:
            logger.error("Timeout downloading Feishu file")
            return None
        except Exception as exc:
            logger.error("Error downloading Feishu file: %s", exc)
            return None

    def _extract_file_attachments(
        self, message_id: str, msg_content: Dict[str, Any], msg_type: str
    ) -> Optional[List[FileAttachment]]:
        """Extract file attachments from a Feishu message."""
        attachments: List[FileAttachment] = []

        if msg_type == "file":
            file_key = msg_content.get("file_key", "")
            file_name = msg_content.get("file_name", "unknown")
            attachments.append(
                FileAttachment(
                    name=file_name,
                    mimetype="application/octet-stream",
                    url=None,
                    size=msg_content.get("file_size"),
                )
            )
            # Store download info in platform_specific later

        elif msg_type == "image":
            image_key = msg_content.get("image_key", "")
            attachments.append(
                FileAttachment(
                    name=f"{image_key}.png",
                    mimetype="image/png",
                    url=None,
                    size=None,
                )
            )

        elif msg_type == "media":
            file_key = msg_content.get("file_key", "")
            file_name = msg_content.get("file_name", "unknown")
            attachments.append(
                FileAttachment(
                    name=file_name,
                    mimetype=msg_content.get("mime_type", "application/octet-stream"),
                    url=None,
                    size=None,
                )
            )

        return attachments if attachments else None

    # ------------------------------------------------------------------
    # Shared / forwarded message extraction
    # ------------------------------------------------------------------
    async def _extract_shared_message_content(self, msg_content: Dict[str, Any], msg_type: str) -> Optional[str]:
        """Extract content from shared/forwarded (merge_forward) messages."""
        if msg_type != "merge_forward":
            return None
        # merge_forward contains a list of forwarded messages
        messages = msg_content.get("message_list", [])
        if not messages:
            return None

        parts = [f"[Shared message with {len(messages)} forwarded message(s)]"]
        for msg in messages:
            body = msg.get("body", {})
            content_str = body.get("content", "")
            try:
                content = json.loads(content_str) if content_str else {}
            except (json.JSONDecodeError, TypeError):
                content = {}
            text = content.get("text", "")
            sender = msg.get("sender_id", {}).get("open_id", "unknown")
            if text:
                parts.append(f"<{sender}>: {text}")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # User / channel info
    # ------------------------------------------------------------------
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """Get information about a Feishu user by open_id."""
        self._ensure_client()
        try:
            token = await self._get_tenant_token()
            if not token:
                return {"id": user_id}
            url = f"https://open.feishu.cn/open-apis/contact/v3/users/{user_id}?user_id_type=open_id"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                    result = await resp.json()
                    if result.get("code") != 0:
                        return {"id": user_id}
                    user = result.get("data", {}).get("user", {})
                    return {
                        "id": user_id,
                        "name": user.get("name", ""),
                        "display_name": user.get("name", ""),
                        "email": user.get("email"),
                    }
        except Exception as exc:
            logger.error("Error getting Feishu user info: %s", exc)
            return {"id": user_id}

    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        """Get information about a Feishu chat."""
        self._ensure_client()
        try:
            token = await self._get_tenant_token()
            if not token:
                return {"id": channel_id, "name": channel_id}
            url = f"https://open.feishu.cn/open-apis/im/v1/chats/{channel_id}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                    result = await resp.json()
                    if result.get("code") != 0:
                        return {"id": channel_id, "name": channel_id}
                    chat = result.get("data", {})
                    return {
                        "id": channel_id,
                        "name": chat.get("name", channel_id),
                    }
        except Exception as exc:
            logger.error("Error getting Feishu channel info: %s", exc)
            return {"id": channel_id, "name": channel_id}

    # ------------------------------------------------------------------
    # Event handling
    # ------------------------------------------------------------------
    def _handle_message_event(self, event_data: Dict[str, Any]):
        """Handle im.message.receive_v1 event (called from SDK callback)."""
        if self._loop is None:
            logger.warning("Event loop not available, ignoring message event")
            return

        asyncio.run_coroutine_threadsafe(self._async_handle_message(event_data), self._loop)

    async def _async_handle_message(self, event_data: Dict[str, Any]):
        """Process an incoming message event asynchronously."""
        try:
            event = event_data
            message = event.get("message", {})
            sender = event.get("sender", {})
            sender_id = sender.get("sender_id", {})
            sender_type = sender.get("sender_type", "")

            # Ignore bot messages
            if sender_type == "app":
                return

            user_id = sender_id.get("open_id", "")
            chat_id = message.get("chat_id", "")
            message_id = message.get("message_id", "")
            msg_type = message.get("message_type", "text")
            root_id = message.get("root_id", "")  # thread parent
            parent_id = message.get("parent_id", "")

            # Parse message content
            content_str = message.get("content", "{}")
            try:
                msg_content = json.loads(content_str)
            except (json.JSONDecodeError, TypeError):
                msg_content = {}

            # Extract text
            text = ""
            if msg_type == "text":
                text = msg_content.get("text", "").strip()
            elif msg_type == "post":
                # Rich text: extract plain text from post structure
                text = self._extract_post_text(msg_content)
            elif msg_type == "interactive":
                # Card message, usually not from users
                return

            # Extract @mentions from text and clean them
            mentions = message.get("mentions", [])
            bot_mentioned = False
            for mention in mentions:
                mention_key = mention.get("key", "")
                mention_id = mention.get("id", {}).get("open_id", "")
                if mention_id == self._bot_open_id:
                    bot_mentioned = True
                # Remove @mention placeholders from text
                text = text.replace(mention_key, "").strip()

            # Extract file attachments
            file_attachments = None
            if msg_type in ("file", "image", "media"):
                file_attachments = self._extract_file_attachments(message_id, msg_content, msg_type)

            # Check for shared/forwarded messages
            shared_text = None
            if msg_type == "merge_forward":
                shared_text = await self._extract_shared_message_content(msg_content, msg_type)

            if not text and not file_attachments and not shared_text:
                return

            if not user_id:
                return

            # Require-mention logic
            is_thread_reply = bool(root_id)
            effective_require_mention = self.config.require_mention
            if self.settings_manager:
                effective_require_mention = self.settings_manager.get_require_mention(
                    chat_id, global_default=self.config.require_mention
                )

            if effective_require_mention:
                if not is_thread_reply:
                    if not bot_mentioned:
                        logger.debug("Ignoring non-mention message in channel")
                        return
                else:
                    if self.settings_manager:
                        if not self.settings_manager.is_thread_active(user_id, chat_id, root_id):
                            logger.debug("Ignoring message in inactive thread %s", root_id)
                            return
                    else:
                        return

            # Channel authorisation
            if not await self._is_authorized_channel(chat_id):
                logger.info("Unauthorized message from channel: %s", chat_id)
                await self._send_unauthorized_message(chat_id)
                return

            # Determine thread ID: use root_id if in thread, else use message_id as thread root
            thread_id = root_id or message_id

            context = MessageContext(
                user_id=user_id,
                channel_id=chat_id,
                thread_id=thread_id,
                message_id=message_id,
                platform_specific={
                    "event": event_data,
                    "msg_type": msg_type,
                    "mentions": mentions,
                },
                files=file_attachments,
            )

            # Handle commands (messages starting with /)
            if text.startswith("/"):
                parts = text.split(maxsplit=1)
                command = parts[0][1:]
                args = parts[1] if len(parts) > 1 else ""
                if command in self.on_command_callbacks:
                    handler = self.on_command_callbacks[command]
                    await handler(context, args)
                    return

            # Append shared content
            if shared_text:
                if text:
                    text = f"{text}\n\n{shared_text}"
                else:
                    text = shared_text

            # Handle as regular message
            if self.on_message_callback:
                await self.on_message_callback(context, text)

        except Exception as exc:
            logger.error("Error handling Feishu message event: %s", exc, exc_info=True)

    def _extract_post_text(self, content: Dict[str, Any]) -> str:
        """Extract plain text from a Feishu 'post' (rich text) message."""
        parts: List[str] = []
        # Post structure: {"title": "...", "content": [[{"tag": "text", "text": "..."}, ...]]}
        title = content.get("title", "")
        if title:
            parts.append(title)
        for line in content.get("content", []):
            line_parts = []
            for element in line:
                tag = element.get("tag", "")
                if tag == "text":
                    line_parts.append(element.get("text", ""))
                elif tag == "a":
                    line_parts.append(element.get("text", element.get("href", "")))
                elif tag == "at":
                    line_parts.append(element.get("user_name", ""))
            parts.append("".join(line_parts))
        return "\n".join(parts).strip()

    def _handle_card_action(self, event_data: Dict[str, Any]):
        """Handle card.action.trigger event (button clicks)."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._async_handle_card_action(event_data), self._loop)

    async def _async_handle_card_action(self, event_data: Dict[str, Any]):
        """Process a card button click asynchronously."""
        try:
            action = event_data.get("action", {})
            value = action.get("value", {})
            callback_data = value.get("key", "")
            user_id = event_data.get("operator", {}).get("open_id", "")
            # The open_message_id refers to the card message
            message_id = event_data.get("open_message_id", "")
            chat_id = event_data.get("open_chat_id", "")

            if not callback_data:
                return

            context = MessageContext(
                user_id=user_id,
                channel_id=chat_id,
                message_id=message_id,
                platform_specific={
                    "event": event_data,
                    "action": action,
                },
            )

            # Handle specific callback patterns for settings, routing, etc.
            if callback_data.startswith("settings_"):
                await self._handle_settings_callback(context, callback_data)
                return

            if callback_data.startswith("routing_"):
                await self._handle_routing_callback(context, callback_data)
                return

            if callback_data.startswith("cwd_"):
                await self._handle_cwd_callback(context, callback_data)
                return

            if callback_data.startswith("resume_"):
                await self._handle_resume_callback(context, callback_data)
                return

            if callback_data.startswith("question_"):
                await self._handle_question_callback(context, callback_data)
                return

            # Generic callback
            if self.on_callback_query_callback:
                await self.on_callback_query_callback(context, callback_data)

        except Exception as exc:
            logger.error("Error handling Feishu card action: %s", exc, exc_info=True)

    async def _handle_settings_callback(self, context: MessageContext, data: str):
        """Handle settings-related card callbacks."""
        if hasattr(self, "_on_settings_update") and self._on_settings_update:
            await self._on_settings_update(context, data)

    async def _handle_routing_callback(self, context: MessageContext, data: str):
        """Handle routing-related card callbacks."""
        if hasattr(self, "_on_routing_update") and self._on_routing_update:
            await self._on_routing_update(context, data)

    async def _handle_cwd_callback(self, context: MessageContext, data: str):
        """Handle CWD change card callbacks."""
        if hasattr(self, "_on_change_cwd") and self._on_change_cwd:
            parts = data.split(":", 1)
            new_cwd = parts[1] if len(parts) > 1 else ""
            await self._on_change_cwd(context.user_id, new_cwd, context.channel_id)

    async def _handle_resume_callback(self, context: MessageContext, data: str):
        """Handle session resume card callbacks."""
        if hasattr(self, "_on_resume_session") and self._on_resume_session:
            await self._on_resume_session(context, data)

    async def _handle_question_callback(self, context: MessageContext, data: str):
        """Handle question answer card callbacks."""
        if self.on_callback_query_callback:
            await self.on_callback_query_callback(context, data)

    # ------------------------------------------------------------------
    # Modal-equivalent: interactive cards sent as messages
    # ------------------------------------------------------------------
    async def open_settings_modal(
        self,
        trigger_id: Any,
        user_settings: Any,
        message_types: list,
        display_names: dict,
        channel_id: str = None,
        current_require_mention: object = None,
        global_require_mention: bool = False,
        current_language: str = None,
    ):
        """Send an interactive card with settings options.

        Since Feishu doesn't have Slack-style modals, we send a settings card.
        """
        t = lambda key, **kw: self._t(key, channel_id, **kw)

        # Build message type options as buttons
        elements: list = []
        elements.append({"tag": "markdown", "content": f"**{t('modal.settings.title')}**"})

        # Show message types
        types_text = ", ".join(f"`{display_names.get(mt, mt)}`" for mt in message_types)
        current_types = getattr(user_settings, "show_message_types", [])
        current_text = ", ".join(f"`{display_names.get(mt, mt)}`" for mt in current_types)
        elements.append(
            {
                "tag": "markdown",
                "content": f"{t('modal.settings.showMessageTypes')}: {current_text}",
            }
        )

        # Require mention status
        mention_status = "default"
        if current_require_mention is True:
            mention_status = "on"
        elif current_require_mention is False:
            mention_status = "off"
        elements.append(
            {
                "tag": "markdown",
                "content": f"Require @mention: **{mention_status}**",
            }
        )

        # Language
        if current_language:
            elements.append(
                {
                    "tag": "markdown",
                    "content": f"Language: **{current_language}**",
                }
            )

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": t("modal.settings.title")},
                "template": "blue",
            },
            "elements": elements,
        }

        ctx = MessageContext(user_id="system", channel_id=channel_id or "")
        try:
            await self._send_card_to_channel(ctx, card)
        except Exception as exc:
            logger.error("Failed to send settings card: %s", exc)

    async def open_change_cwd_modal(self, trigger_id: Any, current_cwd: str, channel_id: str = None):
        """Send an interactive card for CWD change."""
        t = lambda key, **kw: self._t(key, channel_id, **kw)
        elements = [
            {
                "tag": "markdown",
                "content": f"**Change Working Directory**\n\nCurrent: `{current_cwd}`",
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "Reply with `/cwd <new_path>` to change the working directory.",
                    }
                ],
            },
        ]
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "Working Directory"},
                "template": "blue",
            },
            "elements": elements,
        }
        ctx = MessageContext(user_id="system", channel_id=channel_id or "")
        try:
            await self._send_card_to_channel(ctx, card)
        except Exception as exc:
            logger.error("Failed to send CWD card: %s", exc)

    async def open_routing_modal(
        self,
        trigger_id: Any,
        channel_id: str = None,
        current_backend: str = None,
        **kwargs,
    ):
        """Send an interactive card for routing configuration."""
        elements = [
            {
                "tag": "markdown",
                "content": f"**Agent Routing**\n\nCurrent backend: `{current_backend or 'default'}`",
            },
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "Use /routing <backend> to change the agent backend.",
                    }
                ],
            },
        ]
        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "Agent Routing"},
                "template": "blue",
            },
            "elements": elements,
        }
        ctx = MessageContext(user_id="system", channel_id=channel_id or "")
        try:
            await self._send_card_to_channel(ctx, card)
        except Exception as exc:
            logger.error("Failed to send routing card: %s", exc)

    async def open_resume_session_modal(
        self,
        trigger_id: Any,
        sessions: list = None,
        channel_id: str = None,
        **kwargs,
    ):
        """Send an interactive card for session resume selection."""
        elements = [
            {"tag": "markdown", "content": "**Resume Session**"},
        ]

        if sessions:
            actions = []
            for session in sessions[:10]:  # Limit to 10 sessions
                session_id = session.get("id", "")
                label = session.get("label", session_id[:8])
                actions.append(
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": label},
                        "type": "default",
                        "value": {"key": f"resume_{session_id}"},
                    }
                )
            if actions:
                elements.append({"tag": "action", "actions": actions})
        else:
            elements.append({"tag": "markdown", "content": "_No active sessions found._"})

        card = {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": "Resume Session"},
                "template": "blue",
            },
            "elements": elements,
        }
        ctx = MessageContext(user_id="system", channel_id=channel_id or "")
        try:
            await self._send_card_to_channel(ctx, card)
        except Exception as exc:
            logger.error("Failed to send resume session card: %s", exc)

    async def open_question_modal(
        self,
        trigger_id: Any,
        question: str = "",
        options: list = None,
        channel_id: str = None,
        thread_id: str = None,
        **kwargs,
    ):
        """Send an interactive card with question options."""
        elements = [
            {"tag": "markdown", "content": question or "Please select an option:"},
        ]

        if options:
            actions = []
            for idx, opt in enumerate(options):
                if isinstance(opt, dict):
                    label = opt.get("label", opt.get("text", f"Option {idx + 1}"))
                    value = opt.get("value", opt.get("callback_data", f"question_{idx}"))
                else:
                    label = str(opt)
                    value = f"question_{idx}"
                actions.append(
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": label},
                        "type": "default",
                        "value": {"key": value},
                    }
                )
            if actions:
                elements.append({"tag": "action", "actions": actions})

        card = {
            "config": {"wide_screen_mode": True},
            "elements": elements,
        }
        ctx = MessageContext(
            user_id="system",
            channel_id=channel_id or "",
            thread_id=thread_id,
        )
        try:
            await self._send_card_to_channel(ctx, card)
        except Exception as exc:
            logger.error("Failed to send question card: %s", exc)

    async def _send_card_to_channel(self, context: MessageContext, card: dict) -> str:
        """Send a raw card JSON to a channel."""
        self._ensure_client()
        from lark_oapi.api.im.v1 import (
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        card_json = json.dumps(card, ensure_ascii=False)

        if context.thread_id:
            return await self._reply_message_with_card(context.thread_id, card_json)

        request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(context.channel_id)
                .msg_type("interactive")
                .content(card_json)
                .build()
            )
            .build()
        )

        response = await self._lark_client.im.v1.message.acreate(request)
        if not response.success():
            raise RuntimeError(f"Feishu send card failed: {response.msg}")
        return response.data.message_id

    # ------------------------------------------------------------------
    # Callback / handler registration
    # ------------------------------------------------------------------
    def register_callbacks(
        self,
        on_message: Optional[Callable] = None,
        on_command: Optional[Dict[str, Callable]] = None,
        on_callback_query: Optional[Callable] = None,
        **kwargs,
    ):
        """Register callback functions for different events."""
        super().register_callbacks(on_message, on_command, on_callback_query, **kwargs)

        if "on_settings_update" in kwargs:
            self._on_settings_update = kwargs["on_settings_update"]
        if "on_change_cwd" in kwargs:
            self._on_change_cwd = kwargs["on_change_cwd"]
        if "on_routing_update" in kwargs:
            self._on_routing_update = kwargs["on_routing_update"]
        if "on_routing_modal_update" in kwargs:
            self._on_routing_modal_update = kwargs["on_routing_modal_update"]
        if "on_resume_session" in kwargs:
            self._on_resume_session = kwargs["on_resume_session"]
        if "on_ready" in kwargs:
            self._on_ready = kwargs["on_ready"]

    def register_handlers(self):
        """Register Feishu event handlers via lark-oapi SDK.

        This sets up the event dispatcher for WebSocket events.
        The actual handler registration happens in the SDK's EventDispatcherHandler.
        """
        # Handlers are registered during _build_event_handler, called in run()
        pass

    # ------------------------------------------------------------------
    # Run / lifecycle
    # ------------------------------------------------------------------
    def _build_event_handler(self):
        """Build the lark-oapi event dispatcher handler."""
        import lark_oapi as lark

        def on_message_receive(data):
            """Callback for im.message.receive_v1 events."""
            try:
                if hasattr(data, "event") and data.event:
                    event_dict = {}
                    event = data.event
                    # Convert SDK event object to dict
                    if hasattr(event, "__dict__"):
                        event_dict = self._event_to_dict(event)
                    elif isinstance(event, dict):
                        event_dict = event

                    # Deduplication
                    event_id = None
                    if hasattr(data, "header") and data.header:
                        event_id = getattr(data.header, "event_id", None)
                    if self._is_duplicate_event(event_id):
                        return

                    self._handle_message_event(event_dict)
            except Exception as exc:
                logger.error("Error in on_message_receive: %s", exc, exc_info=True)

        def on_card_action(data):
            """Callback for card.action.trigger events."""
            try:
                event_dict = {}
                if hasattr(data, "event") and data.event:
                    event = data.event
                    if hasattr(event, "__dict__"):
                        event_dict = self._event_to_dict(event)
                    elif isinstance(event, dict):
                        event_dict = event
                self._handle_card_action(event_dict)
            except Exception as exc:
                logger.error("Error in on_card_action: %s", exc, exc_info=True)

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message_receive)
            .register_p2_card_action_trigger(on_card_action)
            .build()
        )
        return handler

    def _event_to_dict(self, obj: Any) -> Dict[str, Any]:
        """Recursively convert SDK event object to a plain dict."""
        if isinstance(obj, dict):
            return {k: self._event_to_dict(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._event_to_dict(item) for item in obj]
        if hasattr(obj, "__dict__"):
            result = {}
            for key, value in obj.__dict__.items():
                if key.startswith("_"):
                    continue
                result[key] = self._event_to_dict(value)
            return result
        return obj

    def run(self):
        """Start the Feishu bot with WebSocket long connection."""
        if not self.config.app_id or not self.config.app_secret:
            raise ValueError("Feishu app_id and app_secret are required")

        import lark_oapi as lark

        logger.info("Starting Feishu/Lark bot with WebSocket...")

        self._ensure_client()
        event_handler = self._build_event_handler()
        self._event_handler = event_handler

        async def start():
            self._loop = asyncio.get_running_loop()
            self._stop_event = asyncio.Event()

            # Fetch bot info
            await self._fetch_bot_info()

            # Start WebSocket client in a background thread
            self._ws_client = lark.ws.Client(
                app_id=self.config.app_id,
                app_secret=self.config.app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.WARNING,
            )

            import threading

            ws_thread = threading.Thread(target=self._ws_client.start, daemon=True, name="feishu-ws")
            ws_thread.start()

            logger.info("Feishu WebSocket client started")

            # Call on_ready callback
            if self._on_ready:
                try:
                    await self._on_ready()
                except Exception as exc:
                    logger.error("on_ready callback failed: %s", exc, exc_info=True)

            # Wait until stop is requested
            await self._stop_event.wait()

        try:
            asyncio.run(start())
        except KeyboardInterrupt:
            logger.info("Feishu bot shutting down...")

    def stop(self) -> None:
        """Signal the bot to stop."""
        if self._stop_event is None:
            return
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop_event.set)
        else:
            self._stop_event.set()

    async def shutdown(self) -> None:
        """Best-effort async shutdown."""
        if self._stop_event is not None:
            self._stop_event.set()

    # ------------------------------------------------------------------
    # Misc required implementations
    # ------------------------------------------------------------------
    async def get_or_create_thread(self, channel_id: str, user_id: str) -> Optional[str]:
        """Get existing thread or return None for new thread."""
        return None
