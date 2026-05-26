"""Avibe — Vibe Remote's own Web UI surfaced as a first-class IM platform.

Most IM adapters wrap an external API (Slack RTM, Discord gateway, etc.)
with a long-poll or socket loop. Avibe is different: the workbench Web
UI lives in the same process as the Vibe Remote service, so there is no
remote handshake to perform. Inbound messages arrive via REST POST
(handled by ``vibe/ui_server.py`` in commit 07) and outbound messages
get fanned out to subscribed browsers via Server-Sent Events (commit
08).

This module ships the platform-side contract that the
``core/handlers`` / ``message_dispatcher`` layer can call uniformly
across every platform. The REST + SSE wiring lands in later commits and
will register itself with the ``AvibeBot`` instance held by the
controller (see ``IMFactory.create_client``).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from .base import BaseIMClient, BaseIMConfig, InlineKeyboard, MessageContext
from .formatters.avibe_formatter import AvibeFormatter

logger = logging.getLogger(__name__)


SsePublisher = Callable[..., Awaitable[None]]


@dataclass
class AvibeConfig(BaseIMConfig):
    """Avibe platform config.

    Avibe runs in-process inside the Vibe Remote service — there are no
    credentials to validate. ``enabled`` lets headless deployments skip
    the workbench surface entirely when they only need the IM-bridge
    platforms.
    """

    enabled: bool = True

    def validate(self) -> None:
        # No remote credentials to check.
        return None


def _new_message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:16]}"


class AvibeBot(BaseIMClient):
    """Avibe (Web UI) platform client.

    Methods stay deliberately thin: the REST + SSE plumbing is owned by
    ``vibe/ui_server.py`` (commit 07+) and outbound transport flows
    through whatever publisher the UI server registers via
    :meth:`bind_sse_publisher`. The shape of every method matches every
    other ``BaseIMClient`` so ``core/handlers`` can dispatch Avibe like
    any other platform.
    """

    def __init__(self, config: AvibeConfig):
        super().__init__(config)
        self.formatter = AvibeFormatter()
        self._sse_publisher: Optional[SsePublisher] = None

    # ------------------------------------------------------------------
    # SSE binding — used by ui_server in commit 08 to register a fan-out
    # function. Kept on the bot so the controller can hand its single
    # AvibeBot instance to whoever owns the SSE broker.
    # ------------------------------------------------------------------
    def bind_sse_publisher(self, publisher: Optional[SsePublisher]) -> None:
        self._sse_publisher = publisher

    # ------------------------------------------------------------------
    # Capability hints used by core/message_dispatcher and friends.
    # ------------------------------------------------------------------
    def get_default_parse_mode(self) -> Optional[str]:
        # Web UI renders CommonMark + GFM (strikethrough, tables, fenced
        # code) natively, so we tell the dispatcher to emit markdown
        # straight through.
        return "markdown"

    def supports_message_editing(self, context: Optional[MessageContext] = None) -> bool:
        # The browser tracks each message by id and re-renders on edit.
        return True

    def should_use_thread_for_dm_session(self) -> bool:
        # Every workbench session is its own scope (mapped to a project)
        # — the session_handler already maps session -> scope_key, so we
        # don't need an extra "thread" level here.
        return False

    # ------------------------------------------------------------------
    # Outbound messaging — stub today, SSE-backed once commit 08 lands.
    # ------------------------------------------------------------------
    async def send_message(
        self,
        context: MessageContext,
        text: str,
        parse_mode: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> str:
        message_id = _new_message_id()
        logger.debug(
            "AvibeBot.send_message: scope=%s message_id=%s len=%s reply_to=%s",
            getattr(context, "channel_id", None),
            message_id,
            len(text or ""),
            reply_to,
        )
        if self._sse_publisher is not None:
            await self._sse_publisher(
                "message.new",
                context=context,
                message_id=message_id,
                text=text,
                parse_mode=parse_mode,
                reply_to=reply_to,
            )
        return message_id

    async def send_message_with_buttons(
        self,
        context: MessageContext,
        text: str,
        keyboard: InlineKeyboard,
        parse_mode: Optional[str] = None,
    ) -> str:
        message_id = _new_message_id()
        button_count = len(getattr(keyboard, "buttons", []) or [])
        logger.debug(
            "AvibeBot.send_message_with_buttons: scope=%s message_id=%s buttons=%s",
            getattr(context, "channel_id", None),
            message_id,
            button_count,
        )
        if self._sse_publisher is not None:
            await self._sse_publisher(
                "message.new",
                context=context,
                message_id=message_id,
                text=text,
                parse_mode=parse_mode,
                keyboard=keyboard,
            )
        return message_id

    async def edit_message(
        self,
        context: MessageContext,
        message_id: str,
        text: Optional[str] = None,
        keyboard: Optional[InlineKeyboard] = None,
        parse_mode: Optional[str] = None,
    ) -> bool:
        logger.debug(
            "AvibeBot.edit_message: scope=%s message_id=%s text_len=%s keyboard=%s",
            getattr(context, "channel_id", None),
            message_id,
            len(text or "") if text is not None else None,
            keyboard is not None,
        )
        if self._sse_publisher is not None:
            await self._sse_publisher(
                "message.updated",
                context=context,
                message_id=message_id,
                text=text,
                parse_mode=parse_mode,
                keyboard=keyboard,
            )
        return True

    async def answer_callback(
        self,
        callback_id: str,
        text: Optional[str] = None,
        show_alert: bool = False,
    ) -> bool:
        # The browser handles its own button clicks (no remote callback
        # to ack), so this just logs for parity with other platforms.
        logger.debug(
            "AvibeBot.answer_callback: callback_id=%s text=%s show_alert=%s",
            callback_id,
            text,
            show_alert,
        )
        return True

    # ------------------------------------------------------------------
    # Lifecycle — nothing to start. The HTTP server (ui_server) is the
    # transport; this bot just exposes the contract.
    # ------------------------------------------------------------------
    def register_handlers(self) -> None:
        # No inbound webhook to mount. REST handlers in ui_server route
        # incoming user messages straight into ``self.on_message_callback``
        # (set by ``register_callbacks`` on controller wiring).
        return None

    def run(self) -> None:
        logger.debug(
            "AvibeBot.run: no event loop to start "
            "(REST + SSE owned by vibe/ui_server.py)"
        )

    # ------------------------------------------------------------------
    # Introspection — returns the locally-known identity for the single
    # workbench user. Vibe Cloud remote-access state owns the real
    # identity; Avibe surfaces a stable shape for the dispatcher.
    # ------------------------------------------------------------------
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        return {"id": user_id, "name": user_id, "platform": "avibe"}

    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        # channel_id is the project scope's native_id (commit 05 wires
        # the lookup through the scopes table; this stub keeps the
        # contract intact in the meantime).
        return {"id": channel_id, "name": channel_id, "platform": "avibe"}

    def format_markdown(self, text: str) -> str:
        # Web UI consumes CommonMark + GFM directly; no platform-level
        # rewriting needed.
        return text
