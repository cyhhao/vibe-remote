"""Feishu/Lark implementation of the IM client using lark-oapi SDK."""

import asyncio
import io
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from .base import BaseIMClient, FileAttachment, InlineButton, InlineKeyboard, MessageContext
from .formatters import FeishuFormatter
from config.v2_config import LarkConfig
from vibe.i18n import get_supported_languages, t as i18n_t
from modules.agents.opencode.utils import (
    build_opencode_model_option_items,
    build_reasoning_effort_options,
    resolve_opencode_allowed_providers,
    resolve_opencode_provider_preferences,
)

logger = logging.getLogger(__name__)

# Feishu emoji name mapping (common reactions)
# See: https://open.feishu.cn/document/server-docs/im-v1/message-reaction/emojis-introduce
_EMOJI_MAP: Dict[str, str] = {
    "eyes": "OnIt",
    "👀": "OnIt",
    "robot_face": "SMART",
    "🤖": "SMART",
    "thumbsup": "THUMBSUP",
    "👍": "THUMBSUP",
    "+1": "THUMBSUP",
    "thumbsdown": "ThumbsDown",
    "👎": "ThumbsDown",
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
    "fire": "FIRE",
    "🔥": "FIRE",
    "clap": "APPLAUSE",
    "👏": "APPLAUSE",
    "muscle": "MUSCLE",
    "💪": "MUSCLE",
    "tada": "PARTY",
    "🎉": "PARTY",
    "thinking": "THINKING",
    "🤔": "THINKING",
    "done": "DONE",
    "lgtm": "LGTM",
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
        # Cache for two-step routing flow (channel_id:user_id -> kwargs from settings_handler)
        self._routing_cache: Dict[str, Dict[str, Any]] = {}
        self._stop_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._recent_event_ids: Dict[str, float] = {}
        self._cached_token: Optional[str] = None
        self._token_expires_at: Optional[float] = None
        self._user_info_cache: Dict[str, Dict[str, Any]] = {}

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

    def _get_sdk_domain(self) -> str:
        """Return the lark-oapi SDK domain constant based on config."""
        import lark_oapi as lark

        if getattr(self.config, "domain", "feishu") == "lark":
            return lark.LARK_DOMAIN
        return lark.FEISHU_DOMAIN

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
            .domain(self._get_sdk_domain())
            .log_level(lark.LogLevel.WARNING)
            .build()
        )

    async def _get_tenant_token(self) -> Optional[str]:
        """Get tenant access token for raw HTTP calls (non-blocking)."""
        # Return cached token if still valid (expires after ~2h, refresh at 1h50m)
        if self._cached_token and self._token_expires_at and time.time() < self._token_expires_at:
            return self._cached_token
        try:
            url = f"{self.config.api_base_url}/open-apis/auth/v3/tenant_access_token/internal"
            payload = json.dumps({"app_id": self.config.app_id, "app_secret": self.config.app_secret})
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, data=payload, headers={"Content-Type": "application/json"}) as resp:
                    result = await resp.json()
                    if result.get("code") == 0:
                        token = result.get("tenant_access_token")
                        expire = result.get("expire", 7200)
                        self._cached_token = token
                        # Refresh 10 minutes before expiry
                        self._token_expires_at = time.time() + max(expire - 600, 60)
                        return token
        except Exception as exc:
            logger.error("Failed to get tenant_access_token: %s", exc)
        return None

    async def _fetch_bot_info(self):
        """Fetch the bot's own open_id on startup."""
        try:
            token = await self._get_tenant_token()
            if not token:
                return
            url = f"{self.config.api_base_url}/open-apis/bot/v3/info"
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                    result = await resp.json()
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
    async def send_dm(self, user_id: str, text: str, **kwargs) -> Optional[str]:
        """Send a direct message to a Feishu/Lark user by open_id.

        Uses receive_id_type=open_id to send directly to the user without
        needing to open a DM chat first.
        """
        self._ensure_client()
        try:
            from lark_oapi.api.im.v1 import (
                CreateMessageRequest,
                CreateMessageRequestBody,
            )

            content = self._build_card_json(text)
            body = (
                CreateMessageRequestBody.builder().receive_id(user_id).msg_type("interactive").content(content).build()
            )
            request = CreateMessageRequest.builder().receive_id_type("open_id").request_body(body).build()
            response = await self._lark_client.im.v1.message.acreate(request)
            if not response.success():
                logger.error(
                    "Failed to send Feishu DM to %s: code=%s msg=%s",
                    user_id,
                    response.code,
                    response.msg,
                )
                return None
            return response.data.message_id
        except Exception as e:
            logger.error("Failed to send DM to Feishu user %s: %s", user_id, e)
            return None

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
            message_id = await self._reply_message(root_id, text)
            if self.settings_manager:
                self.settings_manager.mark_thread_active(context.user_id, context.channel_id, root_id)
            return message_id

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
        """Reply to an existing message as a topic (reply_in_thread).

        Using ``reply_in_thread=True`` keeps replies collapsed inside
        a topic, similar to Slack threads, instead of flooding the
        main channel timeline.
        """
        self._ensure_client()
        from lark_oapi.api.im.v1 import (
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        request = (
            ReplyMessageRequest.builder()
            .message_id(parent_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("interactive")
                .content(self._build_card_json(text))
                .reply_in_thread(True)
                .build()
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
        """Build interactive card JSON (v2) for a message with optional buttons.

        Uses JSON 2.0 card schema so that button callbacks are delivered via
        the ``card.action.trigger`` event, which supports WebSocket long
        connections.
        """
        elements: list = [{"tag": "markdown", "content": text}]
        if buttons:
            for row in buttons:
                if len(row) == 1:
                    btn = row[0]
                    behaviors_value: dict = {"key": btn["callback_data"]}
                    if btn.get("thread_id"):
                        behaviors_value["thread_id"] = btn["thread_id"]
                    elements.append(
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": btn["text"]},
                            "type": btn.get("type", "default"),
                            "width": "fill",
                            "behaviors": [
                                {"type": "callback", "value": behaviors_value},
                            ],
                        }
                    )
                else:
                    columns = []
                    for btn in row:
                        behaviors_value = {"key": btn["callback_data"]}
                        if btn.get("thread_id"):
                            behaviors_value["thread_id"] = btn["thread_id"]
                        columns.append(
                            {
                                "tag": "column",
                                "width": "weighted",
                                "weight": 1,
                                "elements": [
                                    {
                                        "tag": "button",
                                        "text": {"tag": "plain_text", "content": btn["text"]},
                                        "type": btn.get("type", "default"),
                                        "behaviors": [
                                            {"type": "callback", "value": behaviors_value},
                                        ],
                                    }
                                ],
                            }
                        )
                    elements.append(
                        {
                            "tag": "column_set",
                            "flex_mode": "none",
                            "background_style": "default",
                            "columns": columns,
                        }
                    )

        card = {
            "schema": "2.0",
            "body": {
                "direction": "vertical",
                "elements": elements,
            },
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
                btn_row.append(
                    {
                        "text": button.text,
                        "callback_data": button.callback_data,
                        "thread_id": context.thread_id or "",
                    }
                )
            button_rows.append(btn_row)

        card_json = self._build_card_json(text, button_rows)

        # Thread reply
        root_id = context.thread_id
        if root_id:
            message_id = await self._reply_message_with_card(root_id, card_json)
            if self.settings_manager:
                self.settings_manager.mark_thread_active(context.user_id, context.channel_id, root_id)
            return message_id

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
        """Reply to a message with an interactive card (as topic)."""
        self._ensure_client()
        from lark_oapi.api.im.v1 import (
            ReplyMessageRequest,
            ReplyMessageRequestBody,
        )

        request = (
            ReplyMessageRequest.builder()
            .message_id(parent_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("interactive")
                .content(card_json)
                .reply_in_thread(True)
                .build()
            )
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
                logger.warning(
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
            url = f"{self.config.api_base_url}/open-apis/im/v1/messages/{message_id}/reactions"
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
            url = f"{self.config.api_base_url}/open-apis/im/v1/files"
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
                    .request_body(
                        ReplyMessageRequestBody.builder()
                        .msg_type("file")
                        .content(file_content)
                        .reply_in_thread(True)
                        .build()
                    )
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
            url = f"{self.config.api_base_url}/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=file"
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
            url = (
                f"{self.config.api_base_url}/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=file"
                if message_id and file_key
                else None
            )
            attachments.append(
                FileAttachment(
                    name=file_name,
                    mimetype="application/octet-stream",
                    url=url,
                    size=msg_content.get("file_size"),
                )
            )

        elif msg_type == "image":
            image_key = msg_content.get("image_key", "")
            url = (
                f"{self.config.api_base_url}/open-apis/im/v1/messages/{message_id}/resources/{image_key}?type=image"
                if message_id and image_key
                else None
            )
            # Don't assume PNG — use generic mimetype; actual type is
            # detected from magic bytes after download in message_handler.
            attachments.append(
                FileAttachment(
                    name=f"{image_key}.image",
                    mimetype="image/unknown",
                    url=url,
                    size=None,
                )
            )

        elif msg_type == "media":
            file_key = msg_content.get("file_key", "")
            file_name = msg_content.get("file_name", "unknown")
            url = (
                f"{self.config.api_base_url}/open-apis/im/v1/messages/{message_id}/resources/{file_key}?type=file"
                if message_id and file_key
                else None
            )
            attachments.append(
                FileAttachment(
                    name=file_name,
                    mimetype=msg_content.get("mime_type", "application/octet-stream"),
                    url=url,
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
        """Get information about a Feishu user by open_id (cached permanently)."""
        cached = self._user_info_cache.get(user_id)
        if cached is not None:
            return cached
        self._ensure_client()
        try:
            token = await self._get_tenant_token()
            if not token:
                return {"id": user_id}
            url = f"{self.config.api_base_url}/open-apis/contact/v3/users/{user_id}?user_id_type=open_id"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
                    result = await resp.json()
                    if result.get("code") != 0:
                        return {"id": user_id}
                    user = result.get("data", {}).get("user", {})
                    info = {
                        "id": user_id,
                        "name": user.get("name", ""),
                        "display_name": user.get("name", ""),
                        "email": user.get("email"),
                    }
                    self._user_info_cache[user_id] = info
                    return info
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
            url = f"{self.config.api_base_url}/open-apis/im/v1/chats/{channel_id}"
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
            # Hot-reload config BEFORE reading any config values (require_mention, etc.)
            if self._controller and hasattr(self._controller, "_refresh_config_from_disk"):
                self._controller._refresh_config_from_disk()

            event = event_data
            message = event.get("message", {})
            sender = event.get("sender", {})
            sender_id = sender.get("sender_id", {})
            sender_type = sender.get("sender_type", "")

            logger.info(
                "Feishu message event received: chat_id=%s, msg_id=%s, sender_type=%s, msg_type=%s, chat_type=%s",
                message.get("chat_id", "?"),
                message.get("message_id", "?"),
                sender_type,
                message.get("message_type", "?"),
                message.get("chat_type", "?"),
            )

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
            mentions = message.get("mentions") or []
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

            if not text and not file_attachments and not shared_text and not bot_mentioned:
                return

            if not user_id:
                return

            # Require-mention logic (bypass for p2p/DM chats)
            chat_type = message.get("chat_type", "")
            is_p2p = chat_type == "p2p"
            is_thread_reply = bool(root_id)
            effective_require_mention = self.config.require_mention
            if self.settings_manager:
                effective_require_mention = self.settings_manager.get_require_mention(
                    chat_id, global_default=self.config.require_mention
                )

            logger.info(
                "Feishu mention check: require_mention=%s, is_p2p=%s, is_thread_reply=%s, bot_mentioned=%s, chat_id=%s",
                effective_require_mention,
                is_p2p,
                is_thread_reply,
                bot_mentioned,
                chat_id,
            )

            if effective_require_mention and not is_p2p:
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

            # DM (p2p) authorization gate: require user to be bound for DM access
            if is_p2p and self.settings_manager:
                store = self.settings_manager.store
                if not store.is_bound_user(user_id):
                    # Allow /bind command through for unbound users
                    if text.startswith("/bind ") or text == "/bind":
                        pass  # Will be handled by command routing below
                    else:
                        try:
                            hint_ctx = MessageContext(user_id="system", channel_id=chat_id)
                            hint = self._t("bind.dmNotBound", chat_id)
                            await self.send_message(hint_ctx, hint)
                        except Exception as exc:
                            logger.error("Failed to send DM bind hint: %s", exc)
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
        """Process a card button click or form submission asynchronously."""
        try:
            action = event_data.get("action", {})
            value = action.get("value") or {}
            callback_data = value.get("key", "")
            callback_thread_id = value.get("thread_id", "")
            form_value = action.get("form_value")
            button_name = action.get("name", "")
            user_id = event_data.get("operator", {}).get("open_id", "")
            # card.action.trigger puts IDs inside a "context" sub-object
            ctx_data = event_data.get("context", {})
            message_id = ctx_data.get("open_message_id", "") or event_data.get("open_message_id", "")
            chat_id = ctx_data.get("open_chat_id", "") or event_data.get("open_chat_id", "")

            # --- Dedup: prevent re-delivery of the same card action ---
            # Include a hash of form values so that intentional re-submissions
            # with different selections are not mistakenly deduplicated.
            form_hash = ""
            if form_value:
                try:
                    form_hash = str(hash(json.dumps(form_value, sort_keys=True)))
                except Exception:
                    form_hash = str(id(form_value))
            dedup_key = f"card:{message_id}:{button_name or callback_data}:{user_id}:{form_hash}"
            if self._is_duplicate_event(dedup_key):
                return

            # --- Channel authorization (same as message handler) ---
            if not chat_id or not await self._is_authorized_channel(chat_id):
                logger.info("Card action from unauthorized/unknown channel %s, ignoring", chat_id)
                return

            context = MessageContext(
                user_id=user_id,
                channel_id=chat_id,
                message_id=message_id,
                thread_id=callback_thread_id or None,
                platform_specific={
                    "event": event_data,
                    "action": action,
                    # Provide trigger_id so question_handler.open_question_modal()
                    # doesn't abort with "Modal UI is not available".
                    # Feishu doesn't use a real trigger_id (we send cards, not
                    # popup modals), but the handler checks for a truthy value.
                    "trigger_id": "feishu_card_action",
                },
            )

            # --- Form submissions (action_type: form_submit) ---
            if form_value is not None:
                if button_name == "cwd_submit":
                    await self._handle_cwd_form_submit(context, form_value)
                elif button_name == "settings_submit":
                    await self._handle_settings_form_submit(context, form_value)
                elif button_name == "routing_backend_select":
                    await self._handle_routing_backend_select(context, form_value)
                elif button_name == "routing_submit":
                    await self._handle_routing_form_submit(context, form_value)
                elif button_name.startswith("resume_submit"):
                    await self._handle_resume_form_submit(context, form_value, button_name)
                elif button_name.startswith("question_submit"):
                    await self._handle_question_form_submit(context, form_value, button_name)
                else:
                    logger.warning("Unknown form submit button: %s", button_name)
                return

            # --- Regular button callbacks ---
            if not callback_data:
                return

            # Route all callbacks through the generic handler, which sends
            # them to message_handler.handle_callback_query for proper routing.
            if self.on_callback_query_callback:
                await self.on_callback_query_callback(context, callback_data)

        except Exception as exc:
            logger.error("Error handling Feishu card action: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Form submission handlers (JSON 2.0 form container)
    # ------------------------------------------------------------------
    async def _handle_cwd_form_submit(self, context: MessageContext, form_value: Dict[str, Any]):
        """Handle CWD change form submission."""
        new_cwd = form_value.get("new_cwd", "").strip()
        if not new_cwd:
            logger.warning("CWD form submitted with empty path")
            return
        if self._on_change_cwd:
            await self._on_change_cwd(context.user_id, new_cwd, context.channel_id)

    async def _handle_settings_form_submit(self, context: MessageContext, form_value: Dict[str, Any]):
        """Handle settings form submission."""
        show_message_types = form_value.get("show_message_types", [])
        # Ensure it's a list (Feishu multi_select_static returns a list)
        if isinstance(show_message_types, str):
            show_message_types = [show_message_types]

        require_mention_raw = form_value.get("require_mention", "__default__")
        if require_mention_raw == "true":
            require_mention = True
        elif require_mention_raw == "false":
            require_mention = False
        else:
            require_mention = None  # __default__

        language = form_value.get("language")

        if self._on_settings_update:
            await self._on_settings_update(
                context.user_id,
                show_message_types,
                context.channel_id,
                require_mention,
                language,
            )

    async def _handle_routing_backend_select(self, context: MessageContext, form_value: Dict[str, Any]):
        """Handle step 1 of routing: user selected a backend, now show backend-specific options."""
        selected_backend = form_value.get("backend", "")
        if not selected_backend:
            logger.warning("Routing backend select submitted with empty backend")
            return

        cache_key = f"{context.channel_id}:{context.user_id}"
        cached = self._routing_cache.get(cache_key)
        if not cached:
            logger.warning("No cached routing data for %s", cache_key)
            return

        await self._send_routing_backend_options_card(
            channel_id=context.channel_id,
            selected_backend=selected_backend,
            _user_id=context.user_id,
            **cached,
        )

    async def _handle_routing_form_submit(self, context: MessageContext, form_value: Dict[str, Any]):
        """Handle step 2 routing form submission (backend-specific options)."""
        # Backend is embedded in form as a disabled select or retrieved from cache
        backend = form_value.get("backend", "")
        if not backend:
            # Fallback: get from cache
            cache_key = f"{context.channel_id}:{context.user_id}"
            cached = self._routing_cache.get(cache_key, {})
            backend = cached.get("_selected_backend", "")

        if not backend:
            logger.warning("Routing form submitted with empty backend, ignoring")
            return

        # Helper to normalise "__default__" to None
        def _val(key: str):
            v = form_value.get(key)
            if v == "__default__" or not v:
                return None
            return v

        opencode_agent = _val("opencode_agent")
        opencode_model = _val("opencode_model")
        opencode_reasoning = _val("opencode_reasoning")
        claude_agent = _val("claude_agent")
        claude_model = _val("claude_model")
        codex_model = _val("codex_model")
        codex_reasoning = _val("codex_reasoning")

        if self._on_routing_update:
            await self._on_routing_update(
                context.user_id,
                context.channel_id,
                backend,
                opencode_agent,
                opencode_model,
                opencode_reasoning,
                claude_agent,
                claude_model,
                codex_model,
                codex_reasoning,
            )

    # ------------------------------------------------------------------
    # Question form submission handler
    # ------------------------------------------------------------------
    async def _handle_question_form_submit(self, context: MessageContext, form_value: Dict[str, Any], button_name: str):
        """Handle question modal form submission.

        Extracts answers from form fields (q0, q1, ...) and routes them
        through ``on_callback_query_callback`` as
        ``{callback_prefix}:modal:{json_payload}`` — the same format that
        Slack and Discord use, so the agent question handler can parse it
        identically.

        The button name encodes metadata as
        ``question_submit:{callback_prefix}:{question_count}:{thread_id}``.
        """
        # Parse metadata from button name:  question_submit:opencode_question:2:om_xxx
        parts = button_name.split(":")
        callback_prefix = parts[1] if len(parts) > 1 else "opencode_question"
        question_count = int(parts[2]) if len(parts) > 2 else 1
        embedded_thread_id = parts[3] if len(parts) > 3 else ""

        # Restore thread_id on context if available from the embedded metadata,
        # since Feishu form submissions lose the thread context.
        if embedded_thread_id and not context.thread_id:
            context = MessageContext(
                user_id=context.user_id,
                channel_id=context.channel_id,
                message_id=context.message_id,
                thread_id=embedded_thread_id,
                platform_specific=context.platform_specific,
            )

        answers: list = []
        for idx in range(question_count):
            field_key = f"q{idx}"
            raw = form_value.get(field_key)
            if isinstance(raw, list):
                # multi_select_static returns a list of selected values
                answers.append([str(v) for v in raw if v])
            elif raw:
                # select_static returns a single string value
                answers.append([str(raw)])
            else:
                answers.append([])

        logger.info(
            "Question form submitted: prefix=%s, count=%d, answers=%s",
            callback_prefix,
            question_count,
            answers,
        )

        payload = json.dumps({"answers": answers})
        callback_data = f"{callback_prefix}:modal:{payload}"

        # Replace the interactive form card with a static confirmation so it
        # doesn't stay clickable after submission (Feishu cards persist by default).
        if context.message_id:
            flat = ", ".join(a for group in answers for a in group) or "-"
            done_card = json.dumps(
                {
                    "schema": "2.0",
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": "✅ " + self._t("common.submitted", context.channel_id),
                        },
                        "template": "green",
                    },
                    "body": {
                        "direction": "vertical",
                        "elements": [{"tag": "markdown", "content": flat}],
                    },
                },
                ensure_ascii=False,
            )
            try:
                from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

                req = (
                    PatchMessageRequest.builder()
                    .message_id(context.message_id)
                    .request_body(PatchMessageRequestBody.builder().content(done_card).build())
                    .build()
                )
                resp = await self._lark_client.im.v1.message.apatch(req)
                if not resp.success():
                    logger.warning("Failed to update question card after submit: %s", resp.msg)
            except Exception as exc:
                logger.warning("Could not update question card: %s", exc)

        if self.on_callback_query_callback:
            await self.on_callback_query_callback(context, callback_data)

    # ------------------------------------------------------------------
    # Resume session form submission handler
    # ------------------------------------------------------------------
    async def _handle_resume_form_submit(self, context: MessageContext, form_value: Dict[str, Any], button_name: str):
        """Handle resume session form submission.

        Extracts the selected or manually entered session ID and agent,
        then delegates to ``_on_resume_session`` (same as Slack/Discord).

        Button name encodes metadata as ``resume_submit:{thread_id}:{host_message_ts}``.
        """
        # Parse metadata from button name
        parts = button_name.split(":")
        embedded_thread_id = parts[1] if len(parts) > 1 and parts[1] else None
        host_message_ts = parts[2] if len(parts) > 2 and parts[2] else None

        # Restore thread_id on context if available from embedded metadata
        if embedded_thread_id and not context.thread_id:
            context = MessageContext(
                user_id=context.user_id,
                channel_id=context.channel_id,
                message_id=context.message_id,
                thread_id=embedded_thread_id,
                platform_specific=context.platform_specific,
            )

        # Extract form values
        session_select = form_value.get("session_select", "")  # "agent|session_id"
        manual_session_id = (form_value.get("manual_session_id") or "").strip()
        agent_select = form_value.get("agent_select", "")

        chosen_agent = None
        chosen_session = None

        # Manual input takes precedence (same logic as Slack/Discord)
        if manual_session_id:
            chosen_session = manual_session_id
            chosen_agent = agent_select or "opencode"
        elif session_select and "|" in session_select:
            chosen_agent, chosen_session = session_select.split("|", 1)
        elif session_select:
            # No pipe — treat as session_id with agent from selector
            chosen_session = session_select
            chosen_agent = agent_select or "opencode"

        logger.info(
            "Resume form submitted: agent=%s, session=%s, manual=%s, select=%s",
            chosen_agent,
            chosen_session,
            manual_session_id,
            session_select,
        )

        if not chosen_session:
            # Nothing selected or entered — send hint
            t = lambda key, **kw: self._t(key, context.channel_id, **kw)
            await self.send_message(context, f"⚠️ {t('modal.resume.description')}")
            return

        # Replace the form card with a confirmation
        if context.message_id:
            agent_label = (chosen_agent or "").capitalize()
            done_card = json.dumps(
                {
                    "schema": "2.0",
                    "header": {
                        "title": {
                            "tag": "plain_text",
                            "content": "✅ " + self._t("common.submitted", context.channel_id),
                        },
                        "template": "green",
                    },
                    "body": {
                        "direction": "vertical",
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": f"Agent: **{agent_label}**\nSession: `{chosen_session[:60]}`",
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            )
            try:
                from lark_oapi.api.im.v1 import PatchMessageRequest, PatchMessageRequestBody

                req = (
                    PatchMessageRequest.builder()
                    .message_id(context.message_id)
                    .request_body(PatchMessageRequestBody.builder().content(done_card).build())
                    .build()
                )
                resp = await self._lark_client.im.v1.message.apatch(req)
                if not resp.success():
                    logger.warning("Failed to update resume card after submit: %s", resp.msg)
            except Exception as exc:
                logger.warning("Could not update resume card: %s", exc)

        # Delegate to the resume session callback (same as Slack/Discord)
        if hasattr(self, "_on_resume_session") and self._on_resume_session:
            await self._on_resume_session(
                context.user_id,
                context.channel_id,
                context.thread_id,
                chosen_agent,
                chosen_session,
                host_message_ts,
            )

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
        """Send a JSON 2.0 form card with settings (message types, @mention, language)."""
        t = lambda key, **kw: self._t(key, channel_id, **kw)

        current_types = getattr(user_settings, "show_message_types", [])

        # --- Build message type multi-select options ---
        msg_type_options = []
        for mt in message_types:
            msg_type_options.append(
                {
                    "text": {"tag": "plain_text", "content": display_names.get(mt, mt)},
                    "value": mt,
                }
            )

        # --- Build require-mention single-select options ---
        global_status = (
            t("modal.settings.mentionStatusOn") if global_require_mention else t("modal.settings.mentionStatusOff")
        )
        mention_options = [
            {
                "text": {"tag": "plain_text", "content": t("modal.settings.optionDefault", status=global_status)},
                "value": "__default__",
            },
            {
                "text": {"tag": "plain_text", "content": t("modal.settings.optionRequireMention")},
                "value": "true",
            },
            {
                "text": {"tag": "plain_text", "content": t("modal.settings.optionDontRequireMention")},
                "value": "false",
            },
        ]
        # Determine current mention selection
        if current_require_mention is True:
            mention_initial = "true"
        elif current_require_mention is False:
            mention_initial = "false"
        else:
            mention_initial = "__default__"

        # --- Build language single-select options ---
        supported_langs = get_supported_languages()
        lang_options = []
        for lang in supported_langs:
            lang_options.append(
                {
                    "text": {"tag": "plain_text", "content": lang},
                    "value": lang,
                }
            )
        lang_initial = current_language if current_language in supported_langs else supported_langs[0]

        # --- Assemble form card ---
        form_elements: list = [
            # Message visibility multi-select
            {"tag": "markdown", "content": f"**{t('modal.settings.showMessageTypes')}**"},
            {
                "tag": "multi_select_static",
                "name": "show_message_types",
                "placeholder": {
                    "tag": "plain_text",
                    "content": t("modal.settings.showMessageTypesPlaceholder"),
                },
                "options": msg_type_options,
                "selected_values": current_types,
            },
            # Require @mention single-select
            {"tag": "markdown", "content": f"**{t('modal.settings.requireMention')}**"},
            {
                "tag": "select_static",
                "name": "require_mention",
                "placeholder": {
                    "tag": "plain_text",
                    "content": t("modal.settings.selectMentionBehavior"),
                },
                "options": mention_options,
                "initial_option": mention_initial,
            },
            # Lark permission note for @mention
            {"tag": "markdown", "content": f"_{t('modal.settings.requireMentionLarkNote')}_"},
            # Language single-select
            {"tag": "markdown", "content": f"**{t('modal.settings.language')}**"},
            {
                "tag": "select_static",
                "name": "language",
                "placeholder": {
                    "tag": "plain_text",
                    "content": t("modal.settings.language"),
                },
                "options": lang_options,
                "initial_option": lang_initial,
            },
            # Tip
            {
                "tag": "markdown",
                "content": t("modal.settings.tip"),
            },
            # Submit button
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": t("common.save")},
                "type": "primary",
                "action_type": "form_submit",
                "name": "settings_submit",
            },
        ]

        card = {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": t("modal.settings.title")},
                "template": "blue",
            },
            "body": {
                "direction": "vertical",
                "elements": [
                    {
                        "tag": "form",
                        "name": "settings_form",
                        "elements": form_elements,
                    },
                ],
            },
        }

        ctx = MessageContext(user_id="system", channel_id=channel_id or "")
        try:
            await self._send_card_to_channel(ctx, card)
        except Exception as exc:
            logger.error("Failed to send settings card: %s", exc)

    async def open_change_cwd_modal(self, trigger_id: Any, current_cwd: str, channel_id: str = None):
        """Send a JSON 2.0 form card for changing the working directory."""
        t = lambda key, **kw: self._t(key, channel_id, **kw)
        card = {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": t("modal.cwd.title")},
                "template": "blue",
            },
            "body": {
                "direction": "vertical",
                "elements": [
                    {
                        "tag": "markdown",
                        "content": t("modal.cwd.current") + f" `{current_cwd}`",
                    },
                    {
                        "tag": "form",
                        "name": "cwd_form",
                        "elements": [
                            {
                                "tag": "input",
                                "name": "new_cwd",
                                "required": True,
                                "label": {
                                    "tag": "plain_text",
                                    "content": t("modal.cwd.new"),
                                },
                                "placeholder": {
                                    "tag": "plain_text",
                                    "content": t("modal.cwd.placeholder"),
                                },
                                "default_value": current_cwd,
                            },
                            {
                                "tag": "markdown",
                                "content": t("modal.cwd.hint"),
                            },
                            {
                                "tag": "button",
                                "text": {
                                    "tag": "plain_text",
                                    "content": t("common.submit"),
                                },
                                "type": "primary",
                                "action_type": "form_submit",
                                "name": "cwd_submit",
                            },
                        ],
                    },
                ],
            },
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
        """Step 1: send a card with only the backend selector.

        All backend/model/agent data is cached so that step 2
        (``_send_routing_backend_options_card``) can render the
        backend-specific options without re-fetching.
        """
        t = lambda key, **kw: self._t(key, channel_id, **kw)

        registered_backends = kwargs.get("registered_backends", [])

        # Cache everything for step 2, keyed by channel+user to avoid crosstalk
        # when multiple users open routing in the same channel concurrently.
        cache_user_id = trigger_id.user_id if isinstance(trigger_id, MessageContext) else "unknown"
        cache_key = f"{channel_id or ''}:{cache_user_id}"
        self._routing_cache[cache_key] = {
            "current_routing": kwargs.get("current_routing"),
            "registered_backends": registered_backends,
            "opencode_agents": kwargs.get("opencode_agents", []),
            "opencode_models": kwargs.get("opencode_models", {}),
            "opencode_default_config": kwargs.get("opencode_default_config", {}),
            "claude_agents": kwargs.get("claude_agents", []),
            "claude_models": kwargs.get("claude_models", []),
            "codex_models": kwargs.get("codex_models", []),
        }

        # --- Backend selector options ---
        backend_options = []
        for b in registered_backends:
            backend_options.append(
                {
                    "text": {"tag": "plain_text", "content": b},
                    "value": b,
                }
            )
        backend_initial = (
            current_backend
            if current_backend and current_backend in registered_backends
            else (registered_backends[0] if registered_backends else None)
        )

        if not backend_options:
            # No backends — just show info
            ctx = MessageContext(user_id="system", channel_id=channel_id or "")
            await self.send_message(ctx, "No agent backends available.")
            return

        form_elements: list = [
            {"tag": "markdown", "content": f"**{t('modal.routing.backend')}**"},
            {
                "tag": "select_static",
                "name": "backend",
                "placeholder": {"tag": "plain_text", "content": t("modal.routing.selectBackend")},
                "options": backend_options,
                **({"initial_option": backend_initial} if backend_initial else {}),
            },
            {"tag": "markdown", "content": t("modal.routing.tip")},
            {
                "tag": "button",
                "text": {
                    "tag": "plain_text",
                    "content": t("common.next"),
                },
                "type": "primary",
                "action_type": "form_submit",
                "name": "routing_backend_select",
            },
        ]

        card = {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": t("modal.routing.title")},
                "template": "blue",
            },
            "body": {
                "direction": "vertical",
                "elements": [
                    {
                        "tag": "form",
                        "name": "routing_backend_form",
                        "elements": form_elements,
                    },
                ],
            },
        }

        ctx = MessageContext(user_id="system", channel_id=channel_id or "")
        try:
            await self._send_card_to_channel(ctx, card)
        except Exception as exc:
            logger.error("Failed to send routing backend card: %s", exc)

    async def _send_routing_backend_options_card(
        self,
        channel_id: str,
        selected_backend: str,
        **kwargs,
    ):
        """Step 2: send a card with options specific to the selected backend."""
        t = lambda key, **kw: self._t(key, channel_id, **kw)

        current_routing = kwargs.get("current_routing")
        opencode_agents = kwargs.get("opencode_agents", [])
        opencode_models = kwargs.get("opencode_models", {})
        opencode_default_config = kwargs.get("opencode_default_config", {})
        claude_agents = kwargs.get("claude_agents", [])
        claude_models = kwargs.get("claude_models", [])
        codex_models = kwargs.get("codex_models", [])

        # Store selected backend for the final submit handler
        user_id = kwargs.get("_user_id", "unknown")
        cache_key = f"{channel_id}:{user_id}"
        cache = self._routing_cache.get(cache_key, {})
        cache["_selected_backend"] = selected_backend
        self._routing_cache[cache_key] = cache

        form_elements: list = [
            # Show selected backend (read-only display)
            {"tag": "markdown", "content": f"**{t('modal.routing.backend')}**: `{selected_backend}`"},
        ]

        # ---- OpenCode section ----
        if selected_backend == "opencode":
            # OpenCode agent selector
            if opencode_agents:
                oc_agent_options = [{"text": {"tag": "plain_text", "content": "(Default)"}, "value": "__default__"}]
                for agent_info in opencode_agents:
                    name = agent_info if isinstance(agent_info, str) else agent_info.get("name", str(agent_info))
                    oc_agent_options.append({"text": {"tag": "plain_text", "content": name}, "value": name})
                oc_current_agent = getattr(current_routing, "opencode_agent", None) if current_routing else None
                form_elements.append({"tag": "markdown", "content": t("modal.routing.opencodeAgent")})
                form_elements.append(
                    {
                        "tag": "select_static",
                        "name": "opencode_agent",
                        "placeholder": {"tag": "plain_text", "content": t("modal.routing.selectOpencodeAgent")},
                        "options": oc_agent_options,
                        "initial_option": oc_current_agent if oc_current_agent else "__default__",
                    }
                )

            # OpenCode model selector (using shared utility)
            oc_current_model = getattr(current_routing, "opencode_model", None) if current_routing else None
            preferred_providers = resolve_opencode_provider_preferences(
                opencode_default_config,
                oc_current_model,
            )
            allowed_providers = resolve_opencode_allowed_providers(
                opencode_default_config,
                opencode_models,
            )
            model_entries = build_opencode_model_option_items(
                opencode_models,
                max_total=99,
                preferred_providers=preferred_providers,
                allowed_providers=allowed_providers,
            )
            if model_entries:
                oc_model_options = [{"text": {"tag": "plain_text", "content": "(Default)"}, "value": "__default__"}]
                for entry in model_entries:
                    label = entry.get("label", "")
                    value = entry.get("value", "")
                    if not label or not value:
                        continue
                    oc_model_options.append({"text": {"tag": "plain_text", "content": label[:60]}, "value": value})
                form_elements.append({"tag": "markdown", "content": t("modal.routing.model")})
                form_elements.append(
                    {
                        "tag": "select_static",
                        "name": "opencode_model",
                        "placeholder": {"tag": "plain_text", "content": t("modal.routing.selectModel")},
                        "options": oc_model_options,
                        "initial_option": oc_current_model if oc_current_model else "__default__",
                    }
                )

            # OpenCode reasoning effort (using shared utility)
            re_entries = build_reasoning_effort_options(opencode_models, oc_current_model)
            re_options = []
            for entry in re_entries:
                label = entry.get("label", "")
                value = entry.get("value", "")
                if not label or not value:
                    continue
                re_options.append({"text": {"tag": "plain_text", "content": label}, "value": value})
            oc_current_re = getattr(current_routing, "opencode_reasoning_effort", None) if current_routing else None
            if re_options:
                form_elements.append({"tag": "markdown", "content": t("modal.routing.reasoningEffort")})
                form_elements.append(
                    {
                        "tag": "select_static",
                        "name": "opencode_reasoning",
                        "placeholder": {"tag": "plain_text", "content": t("modal.routing.selectReasoningEffort")},
                        "options": re_options,
                        "initial_option": oc_current_re if oc_current_re else "__default__",
                    }
                )

        # ---- Claude section ----
        elif selected_backend == "claude":
            # Claude agent selector
            if claude_agents:
                cl_agent_options = [{"text": {"tag": "plain_text", "content": "(Default)"}, "value": "__default__"}]
                for agent_info in claude_agents:
                    name = agent_info if isinstance(agent_info, str) else agent_info.get("name", str(agent_info))
                    cl_agent_options.append({"text": {"tag": "plain_text", "content": name}, "value": name})
                cl_current_agent = getattr(current_routing, "claude_agent", None) if current_routing else None
                form_elements.append({"tag": "markdown", "content": t("modal.routing.claudeAgent")})
                form_elements.append(
                    {
                        "tag": "select_static",
                        "name": "claude_agent",
                        "placeholder": {"tag": "plain_text", "content": t("modal.routing.selectClaudeAgent")},
                        "options": cl_agent_options,
                        "initial_option": cl_current_agent if cl_current_agent else "__default__",
                    }
                )

            # Claude model selector
            if claude_models:
                cl_model_options = [{"text": {"tag": "plain_text", "content": "(Default)"}, "value": "__default__"}]
                for m in claude_models:
                    mid = m if isinstance(m, str) else m.get("id", str(m))
                    cl_model_options.append({"text": {"tag": "plain_text", "content": mid}, "value": mid})
                cl_current_model = getattr(current_routing, "claude_model", None) if current_routing else None
                form_elements.append({"tag": "markdown", "content": t("modal.routing.model")})
                form_elements.append(
                    {
                        "tag": "select_static",
                        "name": "claude_model",
                        "placeholder": {"tag": "plain_text", "content": t("modal.routing.selectModel")},
                        "options": cl_model_options,
                        "initial_option": cl_current_model if cl_current_model else "__default__",
                    }
                )

        # ---- Codex section ----
        elif selected_backend == "codex":
            # Codex model selector
            if codex_models:
                cx_model_options = [{"text": {"tag": "plain_text", "content": "(Default)"}, "value": "__default__"}]
                for m in codex_models:
                    mid = m if isinstance(m, str) else m.get("id", str(m))
                    cx_model_options.append({"text": {"tag": "plain_text", "content": mid}, "value": mid})
                cx_current_model = getattr(current_routing, "codex_model", None) if current_routing else None
                form_elements.append({"tag": "markdown", "content": t("modal.routing.model")})
                form_elements.append(
                    {
                        "tag": "select_static",
                        "name": "codex_model",
                        "placeholder": {"tag": "plain_text", "content": t("modal.routing.selectModel")},
                        "options": cx_model_options,
                        "initial_option": cx_current_model if cx_current_model else "__default__",
                    }
                )

            # Codex reasoning effort — reuse shared builder for consistency
            from modules.agents.opencode.utils import build_codex_reasoning_options

            codex_re_defs = build_codex_reasoning_options()
            cx_re_options = []
            for item in codex_re_defs:
                cx_re_options.append({"text": {"tag": "plain_text", "content": item["label"]}, "value": item["value"]})
            cx_current_re = getattr(current_routing, "codex_reasoning_effort", None) if current_routing else None
            form_elements.append({"tag": "markdown", "content": t("modal.routing.codexReasoningEffort")})
            form_elements.append(
                {
                    "tag": "select_static",
                    "name": "codex_reasoning",
                    "placeholder": {"tag": "plain_text", "content": t("modal.routing.selectReasoningEffort")},
                    "options": cx_re_options,
                    "initial_option": cx_current_re if cx_current_re else "__default__",
                }
            )

        # Submit button
        form_elements.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": t("common.save")},
                "type": "primary",
                "action_type": "form_submit",
                "name": "routing_submit",
            }
        )

        card = {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": f"{t('modal.routing.title')} — {selected_backend}"},
                "template": "blue",
            },
            "body": {
                "direction": "vertical",
                "elements": [
                    {
                        "tag": "form",
                        "name": "routing_options_form",
                        "elements": form_elements,
                    },
                ],
            },
        }

        ctx = MessageContext(user_id="system", channel_id=channel_id or "")
        try:
            await self._send_card_to_channel(ctx, card)
        except Exception as exc:
            logger.error("Failed to send routing options card: %s", exc)

    async def open_resume_session_modal(
        self,
        trigger_id: Any,
        sessions_by_agent: Dict[str, Dict[str, str]] = None,
        channel_id: str = None,
        thread_id: str = None,
        host_message_ts: str = None,
        # Legacy compat — ignored; use sessions_by_agent instead
        sessions: list = None,
        **kwargs,
    ):
        """Send an interactive JSON 2.0 form card for session resume.

        Aligned with Slack/Discord: session selector dropdown (grouped by agent),
        manual session ID text input, agent backend selector, and a submit button.
        """
        t = lambda key, **kw: self._t(key, channel_id, **kw)

        if sessions_by_agent is None:
            sessions_by_agent = {}

        # --- Build agent options (from registered backends or fallback) ---
        common_agents = ["claude", "codex", "opencode"]
        registered_backends = None
        if getattr(self, "_controller", None) and getattr(self._controller, "agent_service", None):
            registered_backends = list(self._controller.agent_service.agents.keys())
        allowed_agents = set(registered_backends) if registered_backends else set(common_agents)
        agent_options = []
        for agent in sorted(allowed_agents):
            agent_options.append({"text": {"tag": "plain_text", "content": agent.capitalize()}, "value": agent})

        # --- Build session options for select_static ---
        session_options = []
        total = 0
        max_options = 100
        for agent_name, mapping in sessions_by_agent.items():
            if agent_name not in allowed_agents:
                continue
            if not mapping:
                continue
            for thread_key, session_id in mapping.items():
                if total >= max_options:
                    break
                thread_label = thread_key.replace("lark_", "", 1) if thread_key.startswith("lark_") else thread_key
                label = f"[{agent_name}] {session_id[:24]}"
                desc = f"thread {thread_label[:30]}"
                session_options.append(
                    {
                        "text": {"tag": "plain_text", "content": label},
                        "value": f"{agent_name}|{session_id}",
                    }
                )
                total += 1
            if total >= max_options:
                break

        # --- Build form elements ---
        form_elements: list = []

        # Description
        form_elements.append({"tag": "markdown", "content": t("modal.resume.description")})

        # 1) Session selector (optional — only if sessions exist)
        if session_options:
            form_elements.append({"tag": "markdown", "content": f"**{t('modal.resume.pickExisting')}**"})
            form_elements.append(
                {
                    "tag": "select_static",
                    "name": "session_select",
                    "placeholder": {"tag": "plain_text", "content": t("modal.resume.selectSession")},
                    "options": session_options,
                }
            )
            if total >= max_options:
                form_elements.append({"tag": "markdown", "content": f"_{t('modal.resume.showingFirst100')}_"})
        else:
            form_elements.append({"tag": "markdown", "content": f"_{t('modal.resume.noSessionsFound')}_"})

        # Divider
        form_elements.append({"tag": "column_set", "flex_mode": "none", "columns": []})

        # 2) Manual session ID input
        form_elements.append({"tag": "markdown", "content": f"**{t('modal.resume.pasteId')}**"})
        form_elements.append(
            {
                "tag": "input",
                "name": "manual_session_id",
                "placeholder": {"tag": "plain_text", "content": t("modal.resume.pasteIdPlaceholder")},
                "label": {"tag": "plain_text", "content": " "},
            }
        )

        # 3) Agent backend selector (always shown — needed when using manual input)
        form_elements.append({"tag": "markdown", "content": f"**{t('modal.resume.agentBackend')}**"})
        if agent_options:
            form_elements.append(
                {
                    "tag": "select_static",
                    "name": "agent_select",
                    "placeholder": {"tag": "plain_text", "content": t("modal.resume.selectAgentBackend")},
                    "options": agent_options,
                    "value": {"value": agent_options[0]["value"]} if agent_options else None,
                }
            )
        else:
            form_elements.append({"tag": "markdown", "content": "_No agent backends available_"})

        # Submit button — encode thread context in name field
        # (form_value loses behaviors.value, so we encode metadata in button name)
        meta_parts = [
            "resume_submit",
            thread_id or "",
            host_message_ts or "",
        ]
        submit_button_name = ":".join(meta_parts)

        form_elements.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": t("common.resume")},
                "type": "primary",
                "width": "fill",
                "action_type": "form_submit",
                "name": submit_button_name,
            }
        )

        card = {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": t("modal.resume.title")},
                "template": "blue",
            },
            "body": {
                "direction": "vertical",
                "elements": [
                    {
                        "tag": "form",
                        "name": "resume_form",
                        "elements": form_elements,
                    },
                ],
            },
        }
        ctx = MessageContext(user_id="system", channel_id=channel_id or "")
        try:
            await self._send_card_to_channel(ctx, card)
        except Exception as exc:
            logger.error("Failed to send resume session card: %s", exc)

    async def open_question_modal(
        self,
        trigger_id: Any,
        context: MessageContext = None,
        pending: Any = None,
        callback_prefix: str = "opencode_question",
        **kwargs,
    ):
        """Send a JSON 2.0 form card with question select fields.

        Aligns with the Slack/Discord ``open_question_modal`` signature so that
        ``question_handler.py`` can call it identically across platforms.

        ``pending`` can be:
        - A dict with ``"questions"`` key (OpenCode question handler format).
        - A ``PendingQuestion`` dataclass with ``.questions`` attribute.

        Each question has ``header``, ``question``, ``multiple``, and ``options``
        (each option has ``label`` and optionally ``description``).
        """
        if pending is None:
            logger.warning("open_question_modal called with pending=None, nothing to show")
            return

        # Support both dict and dataclass
        if hasattr(pending, "questions"):
            questions = pending.questions or []
        elif isinstance(pending, dict):
            questions = pending.get("questions") or []
        else:
            questions = []

        if not questions:
            logger.warning("open_question_modal called with empty questions list")
            return

        # Determine channel/thread from the context
        channel_id = context.channel_id if context else ""
        thread_id = context.thread_id if context else None

        t = lambda key, **kw: self._t(key, channel_id, **kw)

        form_elements: list = []

        for idx, q in enumerate(questions):
            # Support both Question dataclass and dict
            if hasattr(q, "header"):
                header = (q.header or f"Question {idx + 1}").strip()
                prompt = (q.question or "").strip()
                multiple = bool(q.multiple)
                options = q.options or []
            elif isinstance(q, dict):
                header = (q.get("header") or f"Question {idx + 1}").strip()
                prompt = (q.get("question") or "").strip()
                multiple = bool(q.get("multiple") or q.get("multiSelect"))
                options = q.get("options") if isinstance(q.get("options"), list) else []
            else:
                continue

            # Build option items for the select component
            option_items = []
            for opt in options:
                if hasattr(opt, "label"):
                    label = opt.label
                    desc = getattr(opt, "description", "")
                elif isinstance(opt, dict):
                    label = opt.get("label")
                    desc = opt.get("description", "")
                else:
                    label = str(opt)
                    desc = ""
                if label is None:
                    continue
                option_items.append(
                    {
                        "text": {"tag": "plain_text", "content": str(label)[:75]},
                        "value": str(label),
                    }
                )

            if not option_items:
                continue

            # Label text (markdown element before the select — Feishu selects don't support label)
            label_text = header
            if prompt:
                label_text = f"{header}: {prompt}"
            form_elements.append(
                {
                    "tag": "markdown",
                    "content": f"**{label_text}**",
                }
            )

            field_name = f"q{idx}"
            if multiple:
                form_elements.append(
                    {
                        "tag": "multi_select_static",
                        "name": field_name,
                        "placeholder": {"tag": "plain_text", "content": t("common.selectOneOrMore")},
                        "options": option_items,
                    }
                )
            else:
                form_elements.append(
                    {
                        "tag": "select_static",
                        "name": field_name,
                        "placeholder": {"tag": "plain_text", "content": t("common.selectOne")},
                        "options": option_items,
                    }
                )

        if not form_elements:
            logger.warning("open_question_modal: no valid questions produced form elements")
            return

        # Submit button — encode metadata in button name since Feishu
        # does NOT include behaviors.value for form_submit actions.
        # Format: question_submit:{callback_prefix}:{question_count}:{thread_id}
        thread_id_enc = thread_id or ""
        submit_name = f"question_submit:{callback_prefix}:{len(questions)}:{thread_id_enc}"
        form_elements.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": t("common.submit")},
                "type": "primary",
                "width": "fill",
                "action_type": "form_submit",
                "name": submit_name,
            }
        )

        title = t("modal.question.claudeCode") if callback_prefix.startswith("claude") else t("modal.question.opencode")

        card = {
            "schema": "2.0",
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": "blue",
            },
            "body": {
                "direction": "vertical",
                "elements": [
                    {
                        "tag": "form",
                        "name": "question_form",
                        "elements": form_elements,
                    },
                ],
            },
        }

        ctx = MessageContext(
            user_id=context.user_id if context else "system",
            channel_id=channel_id,
            thread_id=thread_id,
        )
        try:
            await self._send_card_to_channel(ctx, card)
        except Exception as exc:
            logger.error("Failed to send question card: %s", exc)

    async def open_opencode_question_modal(
        self,
        trigger_id: Any,
        context: MessageContext = None,
        pending: Any = None,
    ):
        """Convenience wrapper matching the preferred OpenCode question handler call."""
        await self.open_question_modal(
            trigger_id=trigger_id,
            context=context,
            pending=pending,
            callback_prefix="opencode_question",
        )

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
            """Callback for card.action.trigger events.

            Must return a P2CardActionTriggerResponse so the SDK can
            reply to Feishu within the 3-second window.
            """
            from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse

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
            # Always return a response to avoid Feishu "request failed" toast
            return P2CardActionTriggerResponse({})

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
                log_level=lark.LogLevel.INFO,
                domain=self._get_sdk_domain(),
            )

            import threading

            def _ws_thread_target():
                try:
                    logger.info("Feishu WS thread starting, domain=%s", self._get_sdk_domain())
                    self._ws_client.start()
                except Exception as exc:
                    logger.error("Feishu WS thread crashed: %s", exc, exc_info=True)

            ws_thread = threading.Thread(target=_ws_thread_target, daemon=True, name="feishu-ws")
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
