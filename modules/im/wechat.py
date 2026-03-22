"""WeChat personal messaging adapter via iLink bot protocol.

Implements BaseIMClient for WeChat using HTTP long-poll for inbound messages
and HTTP POST for outbound messages, with CDN upload/download for media.
"""

import asyncio
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import aiohttp

from .base import (
    BaseIMClient,
    BaseIMConfig,
    FileAttachment,
    FileDownloadResult,
    InlineKeyboard,
    MessageContext,
)
from config.paths import get_state_dir
from vibe.i18n import t as i18n_t
from modules.im import wechat_api as _wechat_api_mod

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_POLL_TIMEOUT_MS = 35000
_SHORT_RETRY_SECONDS = 2
_LONG_RETRY_SECONDS = 30
_MAX_CONSECUTIVE_FAILURES = 3
_DEDUP_SET_MAX = 1000
_DEDUP_CLEAN_INTERVAL_SECONDS = 300
_SESSION_EXPIRED_ERRCODE = -14

# Regex patterns for stripping markdown
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")
_MD_ITALIC_STAR = re.compile(r"\*(.+?)\*")
_MD_ITALIC_UNDER = re.compile(r"_(.+?)_")
_MD_STRIKETHROUGH = re.compile(r"~~(.+?)~~")
_MD_INLINE_CODE = re.compile(r"`([^`]+)`")
_MD_CODE_FENCE = re.compile(r"```[\w]*\n?(.*?)```", re.DOTALL)
_MD_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_HEADING = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_HR = re.compile(r"^---+$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class WeChatConfig(BaseIMConfig):
    """Configuration for the WeChat iLink bot adapter."""

    bot_token: str = ""
    base_url: str = "https://api.ilink.bot"
    allowed_users: List[str] = field(default_factory=list)
    proxy_url: Optional[str] = None

    def validate(self) -> None:
        self.validate_required_string(self.bot_token, "wechat.bot_token")


# ---------------------------------------------------------------------------
# Low-level iLink API helpers
# ---------------------------------------------------------------------------


class _WeChatAPI:
    """Thin async wrapper delegating to the ``wechat_api`` module.

    The ``wechat_api`` module contains the correctly ported iLink HTTP API
    calls (proper URLs, headers, request bodies).  This class keeps the
    same interface that the rest of ``WeChatBot`` expects.
    """

    def __init__(self, timeout_total: int = 60):
        self._timeout_total = timeout_total

    async def get_updates(
        self,
        base_url: str,
        token: str,
        sync_buf: str,
        timeout_ms: int = _POLL_TIMEOUT_MS,
        proxy: Optional[str] = None,
    ) -> dict:
        """Long-poll for new messages."""
        return await _wechat_api_mod.get_updates(
            base_url,
            token,
            sync_buf,
            timeout_ms=timeout_ms,
        )

    async def send_message(
        self,
        base_url: str,
        token: str,
        to_user_id: str,
        context_token: str,
        item_list: List[Dict[str, Any]],
        proxy: Optional[str] = None,
    ) -> dict:
        """Send a message (one or more items) to a user."""
        return await _wechat_api_mod.send_message(
            base_url,
            token,
            to_user_id,
            context_token,
            item_list,
        )

    async def send_typing(
        self,
        base_url: str,
        token: str,
        to_user_id: str,
        context_token: str,
        proxy: Optional[str] = None,
    ) -> bool:
        """Send a typing indicator (best-effort)."""
        try:
            await _wechat_api_mod.send_typing(
                base_url,
                token,
                to_user_id,
                context_token,
            )
            return True
        except Exception:
            return False


wechat_api = _WeChatAPI()


# ---------------------------------------------------------------------------
# CDN helpers
# ---------------------------------------------------------------------------


class _WeChatCDN:
    """Thin async wrapper around iLink CDN upload/download."""

    def __init__(self, timeout_total: int = 120):
        self._timeout = aiohttp.ClientTimeout(total=timeout_total)

    async def upload_file_to_cdn(
        self,
        base_url: str,
        token: str,
        file_path: str,
        proxy: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Upload a file and return CDN metadata (file_id, aes_key, etc.)."""
        url = f"{base_url}/uploadFile"
        path = Path(file_path)
        if not path.is_file():
            logger.error("Upload file not found: %s", file_path)
            return None

        data = aiohttp.FormData()
        data.add_field("token", token)
        data.add_field(
            "file",
            open(file_path, "rb"),
            filename=path.name,
        )

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(url, data=data, proxy=proxy) as resp:
                    resp.raise_for_status()
                    result = await resp.json()
                    if result.get("ret", -1) != 0:
                        logger.error("CDN upload failed: %s", result)
                        return None
                    return result.get("data", result)
        except Exception as exc:
            logger.error("CDN upload error: %s", exc)
            return None

    async def upload_image_to_cdn(
        self,
        base_url: str,
        token: str,
        file_path: str,
        proxy: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Upload an image and return CDN metadata."""
        url = f"{base_url}/uploadImage"
        path = Path(file_path)
        if not path.is_file():
            logger.error("Upload image not found: %s", file_path)
            return None

        data = aiohttp.FormData()
        data.add_field("token", token)
        data.add_field(
            "image",
            open(file_path, "rb"),
            filename=path.name,
        )

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(url, data=data, proxy=proxy) as resp:
                    resp.raise_for_status()
                    result = await resp.json()
                    if result.get("ret", -1) != 0:
                        logger.error("CDN image upload failed: %s", result)
                        return None
                    return result.get("data", result)
        except Exception as exc:
            logger.error("CDN image upload error: %s", exc)
            return None

    async def download_and_decrypt(
        self,
        base_url: str,
        token: str,
        file_info: Dict[str, Any],
        target_path: str,
        proxy: Optional[str] = None,
    ) -> bool:
        """Download and decrypt a CDN file to a local path."""
        url = f"{base_url}/downloadFile"
        payload = {
            "token": token,
            "file_id": file_info.get("file_id", ""),
            "aes_key": file_info.get("aes_key", ""),
            "file_size": file_info.get("file_size", 0),
        }

        dest = Path(target_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(url, json=payload, proxy=proxy) as resp:
                    resp.raise_for_status()
                    content_type = resp.headers.get("Content-Type", "")
                    if "application/json" in content_type:
                        # Error response
                        error_data = await resp.json()
                        logger.error("CDN download error response: %s", error_data)
                        return False
                    with open(target_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            f.write(chunk)
                    return True
        except Exception as exc:
            logger.error("CDN download error: %s", exc)
            return False


wechat_cdn = _WeChatCDN()


# ---------------------------------------------------------------------------
# Auth manager (QR code login lifecycle)
# ---------------------------------------------------------------------------


class WeChatAuthManager:
    """Manages QR code login flow and token refresh for iLink bots."""

    def __init__(self) -> None:
        self.login_url: Optional[str] = None
        self.is_logged_in: bool = False

    async def check_login_status(self, base_url: str, token: str) -> bool:
        """Verify the bot token is valid and the session is active."""
        try:
            url = f"{base_url}/getLoginStatus"
            payload = {"token": token}
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
            ) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        return False
                    data = await resp.json()
                    self.is_logged_in = data.get("ret", -1) == 0
                    return self.is_logged_in
        except Exception as exc:
            logger.warning("Login status check failed: %s", exc)
            return False

    async def request_qr_login(self, base_url: str, token: str) -> Optional[str]:
        """Request a new QR code login URL."""
        try:
            url = f"{base_url}/getQRCode"
            payload = {"token": token}
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
            ) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    self.login_url = data.get("qr_url")
                    return self.login_url
        except Exception as exc:
            logger.warning("QR login request failed: %s", exc)
            return None


# ---------------------------------------------------------------------------
# Main adapter
# ---------------------------------------------------------------------------


class WeChatBot(BaseIMClient):
    """WeChat personal messaging adapter via iLink bot protocol."""

    def __init__(self, config: WeChatConfig):
        super().__init__(config)
        self.config: WeChatConfig = config
        self.formatter = None  # WeChat uses plain text; no markdown formatter

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._poll_task: Optional[asyncio.Task] = None

        # Context tokens per user (needed for replies)
        self._context_tokens: Dict[str, str] = {}

        # getUpdates cursor
        self._sync_buf: str = ""

        # Auth manager
        self._auth_manager = WeChatAuthManager()

        # Injected collaborators (set by controller)
        self.settings_manager: Any = None
        self.sessions: Any = None
        self._controller: Any = None

        # Extra callbacks captured via register_callbacks
        self._on_ready: Optional[Callable] = None
        self._on_settings_update: Optional[Callable] = None
        self._on_change_cwd: Optional[Callable] = None
        self._on_routing_update: Optional[Callable] = None
        self._on_resume_session: Optional[Callable] = None

        # Event deduplication
        self._seen_message_ids: Set[str] = set()
        self._last_dedup_clean: float = time.monotonic()

    # ------------------------------------------------------------------
    # Lifecycle / injection
    # ------------------------------------------------------------------

    def set_settings_manager(self, settings_manager: Any) -> None:
        """Set the settings manager for user/channel tracking."""
        self.settings_manager = settings_manager
        self.sessions = getattr(settings_manager, "sessions", None)

    def set_controller(self, controller: Any) -> None:
        """Set the controller reference."""
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

    def _t(self, key: str, channel_id: Optional[str] = None, **kwargs: Any) -> str:
        lang = self._get_lang(channel_id)
        return i18n_t(key, lang, **kwargs)

    # ------------------------------------------------------------------
    # Platform metadata
    # ------------------------------------------------------------------

    def get_default_parse_mode(self) -> str:
        """WeChat only supports plain text."""
        return "plain"

    def should_use_thread_for_reply(self) -> bool:
        """WeChat DMs have no thread concept."""
        return False

    def should_use_thread_for_dm_session(self) -> bool:
        """WeChat DMs have no thread concept."""
        return False

    def format_markdown(self, text: str) -> str:
        """Strip markdown formatting for WeChat plain text rendering."""
        if not text:
            return text
        # Order matters: code fences before inline code, bold before italic
        result = _MD_CODE_FENCE.sub(r"\1", text)
        result = _MD_IMAGE.sub(r"\1", result)
        result = _MD_LINK.sub(r"\1", result)
        result = _MD_BOLD.sub(r"\1", result)
        result = _MD_STRIKETHROUGH.sub(r"\1", result)
        result = _MD_ITALIC_STAR.sub(r"\1", result)
        result = _MD_ITALIC_UNDER.sub(r"\1", result)
        result = _MD_INLINE_CODE.sub(r"\1", result)
        result = _MD_HEADING.sub("", result)
        result = _MD_HR.sub("---", result)
        return result.strip()

    # ------------------------------------------------------------------
    # Callback registration
    # ------------------------------------------------------------------

    def register_callbacks(
        self,
        on_message: Optional[Callable] = None,
        on_command: Optional[Dict[str, Callable]] = None,
        on_callback_query: Optional[Callable] = None,
        **kwargs: Any,
    ) -> None:
        """Register callbacks, capturing WeChat-specific extras."""
        super().register_callbacks(on_message, on_command, on_callback_query, **kwargs)
        if "on_settings_update" in kwargs:
            self._on_settings_update = kwargs["on_settings_update"]
        if "on_change_cwd" in kwargs:
            self._on_change_cwd = kwargs["on_change_cwd"]
        if "on_routing_update" in kwargs:
            self._on_routing_update = kwargs["on_routing_update"]
        if "on_resume_session" in kwargs:
            self._on_resume_session = kwargs["on_resume_session"]
        if "on_ready" in kwargs:
            self._on_ready = kwargs["on_ready"]

    def register_handlers(self) -> None:
        """No-op: handlers are wired via register_callbacks."""
        pass

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
        """Send a plain text message to a WeChat user."""
        if not text:
            raise ValueError("WeChat send_message requires non-empty text")

        user_id = context.user_id
        context_token = self._get_context_token(context)

        # Build TEXT item
        item_list = [{"type": 1, "text_item": {"text": self.format_markdown(text)}}]

        try:
            resp = await wechat_api.send_message(
                self.config.base_url,
                self.config.bot_token,
                user_id,
                context_token,
                item_list,
                proxy=self.config.proxy_url,
            )
            if resp.get("ret", -1) != 0:
                logger.error(
                    "WeChat send_message failed: ret=%s msg=%s",
                    resp.get("ret"),
                    resp.get("msg", ""),
                )
        except Exception as exc:
            logger.error("WeChat send_message error: %s", exc)

        # Generate a synthetic message ID (iLink may not return one)
        message_id = resp.get("message_id", "") if resp else ""
        if not message_id:
            message_id = f"wc-{uuid.uuid4().hex[:12]}"
        return message_id

    async def send_message_with_buttons(
        self,
        context: MessageContext,
        text: str,
        keyboard: InlineKeyboard,
        parse_mode: Optional[str] = None,
    ) -> str:
        """Send a message with button labels appended as text hints.

        WeChat personal messaging does not support inline buttons, so we
        render button labels as a text footer.
        """
        # Build button hint footer
        button_labels: List[str] = []
        for row in keyboard.buttons:
            for btn in row:
                button_labels.append(f"[{btn.text}]")

        footer = ""
        if button_labels:
            footer = f"\n\n---\nOptions: {' '.join(button_labels)}"

        return await self.send_message(context, text + footer, parse_mode=parse_mode)

    async def edit_message(
        self,
        context: MessageContext,
        message_id: str,
        text: Optional[str] = None,
        keyboard: Optional[InlineKeyboard] = None,
        parse_mode: Optional[str] = None,
    ) -> bool:
        """WeChat does not support editing sent messages."""
        return False

    async def answer_callback(
        self,
        callback_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        """WeChat does not support callback queries."""
        return False

    # ------------------------------------------------------------------
    # User / channel info
    # ------------------------------------------------------------------

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """Return basic user info. WeChat doesn't expose rich user profiles."""
        return {
            "id": user_id,
            "name": user_id,
            "platform": "wechat",
        }

    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        """Return basic channel info (always DM for personal WeChat)."""
        return {
            "id": channel_id,
            "name": "WeChat DM",
            "type": "dm",
        }

    # ------------------------------------------------------------------
    # Typing indicator
    # ------------------------------------------------------------------

    async def send_typing_indicator(
        self,
        context: MessageContext,
    ) -> bool:
        """Send a typing indicator (best-effort, don't block on errors)."""
        user_id = context.user_id
        context_token = self._get_context_token(context)
        if not context_token:
            return False
        return await wechat_api.send_typing(
            self.config.base_url,
            self.config.bot_token,
            user_id,
            context_token,
            proxy=self.config.proxy_url,
        )

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    async def upload_file_from_path(
        self,
        context: MessageContext,
        file_path: str,
        title: Optional[str] = None,
    ) -> str:
        """Upload a file via CDN and send it as a file message."""
        cdn_meta = await wechat_cdn.upload_file_to_cdn(
            self.config.base_url,
            self.config.bot_token,
            file_path,
            proxy=self.config.proxy_url,
        )
        if cdn_meta is None:
            logger.error("Failed to upload file to CDN: %s", file_path)
            return ""

        user_id = context.user_id
        context_token = self._get_context_token(context)
        display_name = title or Path(file_path).name

        item_list = [
            {
                "type": 4,  # FILE
                "file_item": {
                    "media": cdn_meta,
                    "file_name": display_name,
                    "len": str(cdn_meta.get("file_size", 0)),
                },
            }
        ]

        try:
            await wechat_api.send_message(
                self.config.base_url,
                self.config.bot_token,
                user_id,
                context_token,
                item_list,
                proxy=self.config.proxy_url,
            )
        except Exception as exc:
            logger.error("WeChat send file message error: %s", exc)
            return ""

        return cdn_meta.get("file_id", f"wc-file-{uuid.uuid4().hex[:8]}")

    async def upload_image_from_path(
        self,
        context: MessageContext,
        file_path: str,
        title: Optional[str] = None,
    ) -> str:
        """Upload an image via CDN and send it as an image message."""
        cdn_meta = await wechat_cdn.upload_image_to_cdn(
            self.config.base_url,
            self.config.bot_token,
            file_path,
            proxy=self.config.proxy_url,
        )
        if cdn_meta is None:
            logger.error("Failed to upload image to CDN: %s", file_path)
            return ""

        user_id = context.user_id
        context_token = self._get_context_token(context)

        item_list = [
            {
                "type": 2,  # IMAGE
                "image_item": {
                    "media": cdn_meta,
                },
            }
        ]

        try:
            await wechat_api.send_message(
                self.config.base_url,
                self.config.bot_token,
                user_id,
                context_token,
                item_list,
                proxy=self.config.proxy_url,
            )
        except Exception as exc:
            logger.error("WeChat send image message error: %s", exc)
            return ""

        return cdn_meta.get("file_id", f"wc-img-{uuid.uuid4().hex[:8]}")

    async def download_file_to_path(
        self,
        file_info: Dict[str, Any],
        target_path: str,
        max_bytes: Optional[int] = None,
        timeout_seconds: int = 30,
    ) -> FileDownloadResult:
        """Download a CDN file to a local path."""
        success = await wechat_cdn.download_and_decrypt(
            self.config.base_url,
            self.config.bot_token,
            file_info,
            target_path,
            proxy=self.config.proxy_url,
        )
        if not success:
            return FileDownloadResult(False, "CDN download/decrypt failed")

        # Enforce max_bytes after download if specified
        if max_bytes is not None:
            dest = Path(target_path)
            if dest.exists() and dest.stat().st_size > max_bytes:
                dest.unlink(missing_ok=True)
                return FileDownloadResult(
                    False,
                    f"File exceeds max_bytes ({max_bytes})",
                )

        return FileDownloadResult(True)

    async def download_file(
        self,
        file_info: Dict[str, Any],
        max_bytes: Optional[int] = None,
        timeout_seconds: int = 30,
    ) -> Optional[bytes]:
        """Download a CDN file into memory."""
        import tempfile

        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        try:
            result = await self.download_file_to_path(
                file_info,
                tmp_path,
                max_bytes=max_bytes,
                timeout_seconds=timeout_seconds,
            )
            if not result.success:
                return None
            return Path(tmp_path).read_bytes()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # DM convenience
    # ------------------------------------------------------------------

    async def send_dm(self, user_id: str, text: str, **kwargs: Any) -> Optional[str]:
        """Send a direct message by user ID.

        For WeChat, every conversation is already a DM, so we just need a
        cached context_token for the user.
        """
        context_token = self._context_tokens.get(user_id, "")
        if not context_token:
            logger.warning(
                "No context_token cached for user %s; cannot send DM",
                user_id,
            )
            return None

        context = MessageContext(
            user_id=user_id,
            channel_id=user_id,
            thread_id=None,
            message_id=None,
            platform_specific={"is_dm": True, "context_token": context_token},
        )
        try:
            return await self.send_message(context, text)
        except Exception as exc:
            logger.error("send_dm failed for user %s: %s", user_id, exc)
            return None

    # ------------------------------------------------------------------
    # Run / shutdown
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Start the WeChat bot: validate config, run the async poll loop."""
        if not self.config.bot_token:
            logger.warning(
                "WeChat bot_token is not configured. "
                "The bot will idle until a token is set via the Web UI QR login. "
                "Access the setup wizard to complete WeChat configuration."
            )

        logger.info("Starting WeChat bot via iLink protocol...")

        async def _start() -> None:
            self._loop = asyncio.get_running_loop()
            self._stop_event = asyncio.Event()

            # Notify on_ready callback (starts UI server even without token)
            if self._on_ready:
                try:
                    await self._on_ready()
                except Exception as exc:
                    logger.error("on_ready callback failed: %s", exc, exc_info=True)

            if not self.config.bot_token:
                logger.info("WeChat bot idling (no bot_token). Complete QR login via the Web UI to activate.")
                await self._stop_event.wait()
                return

            # Load persisted sync buffer
            self._load_sync_buf()

            # Check login status
            logged_in = await self._auth_manager.check_login_status(
                self.config.base_url,
                self.config.bot_token,
            )
            if logged_in:
                logger.info("WeChat bot session is active")
            else:
                logger.warning(
                    "WeChat bot session is not active; messages may fail until the session is re-authenticated",
                )

            # Start poll loop as a background task
            self._poll_task = asyncio.create_task(self._poll_loop())

            logger.info("WeChat bot started, entering poll loop")

            # Block until stop is signalled
            await self._stop_event.wait()

            # Cancel poll task on shutdown
            if self._poll_task and not self._poll_task.done():
                self._poll_task.cancel()
                try:
                    await self._poll_task
                except asyncio.CancelledError:
                    pass

            logger.info("WeChat bot stopped")

        try:
            asyncio.run(_start())
        except KeyboardInterrupt:
            logger.info("WeChat bot shutting down (keyboard interrupt)...")

    def stop(self) -> None:
        """Signal the bot to stop."""
        if self._stop_event is None:
            return
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop_event.set)
        else:
            self._stop_event.set()

    async def shutdown(self) -> None:
        """Best-effort async shutdown for platform resources."""
        if self._stop_event is not None:
            self._stop_event.set()
        # Persist sync buffer so we don't re-process old messages on restart
        self._save_sync_buf()

    # ------------------------------------------------------------------
    # Long-poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Main message receiving loop via HTTP long-poll."""
        consecutive_failures = 0

        while not self._stop_event.is_set():
            try:
                resp = await wechat_api.get_updates(
                    self.config.base_url,
                    self.config.bot_token,
                    self._sync_buf,
                    timeout_ms=_POLL_TIMEOUT_MS,
                    proxy=self.config.proxy_url,
                )

                ret = resp.get("ret", 0)
                if ret != 0:
                    errcode = resp.get("errcode", ret)
                    logger.warning(
                        "getUpdates error: ret=%s errcode=%s msg=%s",
                        ret,
                        errcode,
                        resp.get("msg", ""),
                    )

                    # Handle session expired
                    if errcode == _SESSION_EXPIRED_ERRCODE:
                        logger.error(
                            "WeChat session expired (errcode %s); re-authentication required",
                            errcode,
                        )
                        self._auth_manager.is_logged_in = False

                    consecutive_failures += 1
                    if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                        logger.warning(
                            "Multiple consecutive poll failures (%d); backing off to %ds",
                            consecutive_failures,
                            _LONG_RETRY_SECONDS,
                        )
                        await asyncio.sleep(_LONG_RETRY_SECONDS)
                        consecutive_failures = 0
                    else:
                        await asyncio.sleep(_SHORT_RETRY_SECONDS)
                    continue

                # Success path
                consecutive_failures = 0

                # Update sync cursor
                new_buf = resp.get("get_updates_buf", "")
                if new_buf:
                    self._sync_buf = new_buf
                    self._save_sync_buf()

                # Process messages
                msgs = resp.get("msgs", [])
                if msgs:
                    logger.info("Received %d message(s)", len(msgs))
                for msg in msgs:
                    try:
                        await self._process_inbound_message(msg)
                    except Exception as msg_exc:
                        logger.error(
                            "Failed to process message %s: %s",
                            msg.get("message_id", "?"),
                            msg_exc,
                            exc_info=True,
                        )

            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Poll loop error: %s", exc, exc_info=True)
                consecutive_failures += 1
                delay = (
                    _LONG_RETRY_SECONDS if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES else _SHORT_RETRY_SECONDS
                )
                await asyncio.sleep(delay)
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0

    # ------------------------------------------------------------------
    # Inbound message processing
    # ------------------------------------------------------------------

    async def _process_inbound_message(self, msg: dict) -> None:
        """Convert an iLink message to MessageContext and dispatch."""
        message_id = str(msg.get("message_id", ""))

        # Dedup
        if message_id and not self._mark_message_seen(message_id):
            return

        from_user = msg.get("from_user_id", "")
        if not from_user:
            logger.debug("Skipping message with empty from_user_id")
            return

        context_token = msg.get("context_token", "")
        logger.info(
            "Processing inbound message: id=%s from=%s context_token=%s items=%d",
            message_id,
            from_user,
            context_token[:16] + "..." if len(context_token) > 16 else context_token,
            len(msg.get("item_list", [])),
        )

        # Cache context_token for replies
        if context_token:
            self._context_tokens[from_user] = context_token

        # Extract text from item_list
        text = self._extract_text(msg)

        # Build MessageContext
        context = MessageContext(
            user_id=from_user,
            channel_id=from_user,  # WeChat DM: channel == user
            thread_id=None,  # No threads in WeChat
            message_id=message_id,
            platform_specific={
                "message": msg,
                "is_dm": True,  # Always DM for personal messaging
                "context_token": context_token,
            },
            files=[],
        )

        # Handle media attachments
        await self._process_media_items(msg, context)

        # Authorization check
        auth_result = self.check_authorization(
            user_id=from_user,
            channel_id=from_user,
            is_dm=True,
            text=text,
            settings_manager=self.settings_manager,
        )
        if auth_result is not None and auth_result is not True:
            # auth_result is a denial reason string
            denial_text = self.build_auth_denial_text(
                auth_result,
                channel_id=from_user,
            )
            if denial_text:
                try:
                    await self.send_message(context, denial_text)
                except Exception as exc:
                    logger.error("Failed to send auth denial: %s", exc)
            return

        # Try slash command dispatch first
        allow_plain_bind = self.should_allow_plain_bind(
            user_id=from_user,
            is_dm=True,
            settings_manager=self.settings_manager,
        )
        if await self.dispatch_text_command(
            context,
            text,
            allow_plain_bind=allow_plain_bind,
        ):
            return

        # Dispatch to message handler
        if self.on_message_callback:
            try:
                await self.on_message_callback(context, text)
            except Exception as exc:
                logger.error(
                    "Message callback error for user %s: %s",
                    from_user,
                    exc,
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Text / media extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_text(msg: dict) -> str:
        """Extract text content from iLink message item_list.

        The ``type`` field is an integer: 1=TEXT, 2=IMAGE, 3=VOICE, 4=FILE, 5=VIDEO.
        """
        text_parts: List[str] = []
        for item in msg.get("item_list", []):
            item_type = item.get("type", 0)
            # type 1 = TEXT
            if item_type == 1 or item_type in ("TEXT", "text"):
                content = item.get("content", "")
                if content:
                    text_parts.append(content)

        return " ".join(text_parts).strip()

    async def _process_media_items(
        self,
        msg: dict,
        context: MessageContext,
    ) -> None:
        """Populate context.files from media items in the message."""
        if context.files is None:
            context.files = []

        for item in msg.get("item_list", []):
            item_type = item.get("type", 0)
            # 2=IMAGE, 3=VOICE, 4=FILE, 5=VIDEO
            type_name_map = {2: "IMAGE", 3: "VOICE", 4: "FILE", 5: "VIDEO"}
            type_name = type_name_map.get(item_type)
            if type_name is None:
                continue

            cdn_info = item.get("cdn", {})
            file_name = item.get("content", "") or item.get("file_name", "")

            mime_map = {
                "IMAGE": "image/jpeg",
                "FILE": "application/octet-stream",
                "VIDEO": "video/mp4",
                "VOICE": "audio/amr",
            }
            mimetype = mime_map.get(type_name, "application/octet-stream")

            attachment = FileAttachment(
                name=file_name or f"{type_name.lower()}_attachment",
                mimetype=mimetype,
                url=cdn_info.get("url", ""),
                size=cdn_info.get("file_size"),
            )
            # Store CDN info for later download
            attachment.__dict__["cdn_info"] = cdn_info
            context.files.append(attachment)

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _mark_message_seen(self, message_id: str) -> bool:
        """Return True if this is a new message; False if already seen."""
        self._maybe_clean_dedup_set()

        if message_id in self._seen_message_ids:
            logger.debug("Duplicate message ignored: %s", message_id)
            return False

        self._seen_message_ids.add(message_id)
        return True

    def _maybe_clean_dedup_set(self) -> None:
        """Periodically trim the dedup set to avoid unbounded growth."""
        now = time.monotonic()
        if now - self._last_dedup_clean < _DEDUP_CLEAN_INTERVAL_SECONDS:
            return

        self._last_dedup_clean = now
        if len(self._seen_message_ids) > _DEDUP_SET_MAX:
            # Keep the most recent half by clearing the whole set.
            # This is a simple strategy; messages arriving during the clean
            # window are unlikely to be duplicates.
            excess = len(self._seen_message_ids) - _DEDUP_SET_MAX // 2
            # Sets don't have ordering, so just discard arbitrary elements
            to_remove = list(self._seen_message_ids)[:excess]
            for mid in to_remove:
                self._seen_message_ids.discard(mid)
            logger.debug(
                "Dedup set trimmed: removed %d, remaining %d",
                len(to_remove),
                len(self._seen_message_ids),
            )

    # ------------------------------------------------------------------
    # Sync buffer persistence
    # ------------------------------------------------------------------

    def _get_sync_buf_path(self) -> Path:
        """Return the path for persisting the getUpdates cursor."""
        state_dir = get_state_dir()
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir / "wechat_sync_buf.json"

    def _load_sync_buf(self) -> None:
        """Load the persisted sync buffer from disk."""
        path = self._get_sync_buf_path()
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._sync_buf = data.get("sync_buf", "")
            if self._sync_buf:
                logger.info("Loaded persisted sync buffer (%d chars)", len(self._sync_buf))
        except Exception as exc:
            logger.warning("Failed to load sync buffer: %s", exc)

    def _save_sync_buf(self) -> None:
        """Persist the current sync buffer to disk."""
        if not self._sync_buf:
            return
        try:
            path = self._get_sync_buf_path()
            path.write_text(
                json.dumps({"sync_buf": self._sync_buf}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to save sync buffer: %s", exc)

    # ------------------------------------------------------------------
    # Context token helpers
    # ------------------------------------------------------------------

    def _get_context_token(self, context: MessageContext) -> str:
        """Resolve the context_token for a given message context.

        Tries platform_specific first, then falls back to the cached map.
        """
        ps = context.platform_specific or {}
        token = ps.get("context_token", "")
        if token:
            return token
        return self._context_tokens.get(context.user_id, "")

    # ------------------------------------------------------------------
    # Reactions (unsupported)
    # ------------------------------------------------------------------

    async def add_reaction(
        self,
        context: MessageContext,
        message_id: str,
        emoji: str,
    ) -> bool:
        """WeChat personal messaging does not support reactions."""
        return False

    async def remove_reaction(
        self,
        context: MessageContext,
        message_id: str,
        emoji: str,
    ) -> bool:
        """WeChat personal messaging does not support reactions."""
        return False
