"""Processing indicator lifecycle management.

This module owns the short-lived UI state shown while a user turn is being
processed: acknowledgement messages, acknowledgement reactions, and typing
indicators.  Agent implementations should not know platform-specific cleanup
details; they should only ask this service to delete or finish an indicator.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from modules.im import MessageContext
from vibe.i18n import t as i18n_t

logger = logging.getLogger(__name__)


@dataclass
class ProcessingIndicatorHandle:
    """Runtime handle for a processing indicator."""

    context: MessageContext
    ack_message_id: Optional[str] = None
    ack_message_channel_id: Optional[str] = None
    ack_reaction_message_id: Optional[str] = None
    ack_reaction_emoji: Optional[str] = None
    typing_indicator_active: bool = False
    typing_indicator_task: Optional[asyncio.Task] = None

    def to_snapshot(self) -> dict[str, Any]:
        payload = self.context.platform_specific or {}
        return {
            "platform": self.context.platform or payload.get("platform") or "",
            "user_id": self.context.user_id or "",
            "channel_id": self.context.channel_id or "",
            "thread_id": self.context.thread_id or "",
            "message_id": self.context.message_id or "",
            "context_token": str(payload.get("context_token") or ""),
            "ack_message_id": self.ack_message_id,
            "ack_message_channel_id": self.ack_message_channel_id,
            "ack_reaction_message_id": self.ack_reaction_message_id,
            "ack_reaction_emoji": self.ack_reaction_emoji,
            "typing_indicator_active": self.typing_indicator_active,
        }

    @classmethod
    def from_snapshot(cls, data: dict[str, Any]) -> "ProcessingIndicatorHandle":
        platform = str(data.get("platform") or "")
        context_token = str(data.get("context_token") or "")
        platform_specific: dict[str, Any] = {}
        if platform:
            platform_specific["platform"] = platform
        if context_token:
            platform_specific["context_token"] = context_token
        context = MessageContext(
            user_id=str(data.get("user_id") or ""),
            channel_id=str(data.get("channel_id") or ""),
            platform=platform or None,
            thread_id=data.get("thread_id") or None,
            message_id=data.get("message_id") or None,
            platform_specific=platform_specific or None,
        )
        return cls(
            context=context,
            ack_message_id=data.get("ack_message_id") or None,
            ack_message_channel_id=data.get("ack_message_channel_id") or data.get("channel_id") or None,
            ack_reaction_message_id=data.get("ack_reaction_message_id") or None,
            ack_reaction_emoji=data.get("ack_reaction_emoji") or None,
            typing_indicator_active=bool(data.get("typing_indicator_active", False)),
        )


class ProcessingIndicatorService:
    """Start and finish processing indicators through one owner."""

    def __init__(self, controller):
        self.controller = controller
        self.config = controller.config

    def _get_im_client(self, context: MessageContext):
        getter = getattr(self.controller, "get_im_client_for_context", None)
        if callable(getter):
            return getter(context)
        return self.controller.im_client

    def _get_context_platform(self, context: MessageContext) -> str:
        return (
            context.platform
            or (context.platform_specific or {}).get("platform")
            or getattr(self.config, "platform", "")
        )

    def _should_use_typing_ack(self, context: MessageContext) -> bool:
        if self._get_context_platform(context) == "wechat":
            return True
        return getattr(self.config, "ack_mode", "typing") == "typing"

    def _get_target_context(self, context: MessageContext) -> MessageContext:
        im_client = self._get_im_client(context)
        if im_client.should_use_thread_for_reply() and context.thread_id:
            return MessageContext(
                user_id=context.user_id,
                channel_id=context.channel_id,
                platform=context.platform,
                thread_id=context.thread_id,
                message_id=context.message_id,
                platform_specific=context.platform_specific,
            )
        return context

    def _get_ack_text(self, agent_name: str) -> str:
        label = agent_name or self.controller.agent_service.default_agent
        agent_label = label.capitalize() if label else ""
        lang = self.controller._get_lang() if hasattr(self.controller, "_get_lang") else getattr(self.config, "language", "en")
        return f"📨 {i18n_t('message.ack', lang, agent=agent_label)}"

    async def _typing_keepalive_loop(self, context: MessageContext) -> None:
        im_client = self._get_im_client(context)
        try:
            while True:
                await asyncio.sleep(5)
                ok = await im_client.send_typing_indicator(context)
                if not ok:
                    logger.debug("Typing keepalive not applied for %s", context.user_id)
        except asyncio.CancelledError:
            raise

    async def start(self, context: MessageContext, agent_name: str, *, enabled: bool = True) -> ProcessingIndicatorHandle:
        handle = ProcessingIndicatorHandle(context=context)
        if not enabled:
            return handle

        im_client = self._get_im_client(context)
        ack_mode = getattr(self.config, "ack_mode", "typing")
        effective_ack_mode = "typing" if self._get_context_platform(context) == "wechat" else ack_mode

        if effective_ack_mode == "message":
            ack_context = self._get_target_context(context)
            try:
                handle.ack_message_id = await self._get_im_client(ack_context).send_message(
                    ack_context,
                    self._get_ack_text(agent_name),
                )
                handle.ack_message_channel_id = ack_context.channel_id
            except Exception as ack_err:
                logger.debug("Failed to send ack message: %s", ack_err)
            return handle

        if self._should_use_typing_ack(context):
            try:
                ok = await im_client.send_typing_indicator(context)
                if ok:
                    handle.typing_indicator_active = True
                    handle.typing_indicator_task = asyncio.create_task(self._typing_keepalive_loop(context))
                    return handle
                logger.info("Typing indicator not applied (platform returned False)")
            except Exception as ack_err:
                logger.debug("Failed to send typing ack: %s", ack_err)
            if self._get_context_platform(context) == "wechat":
                return handle

        try:
            if context.message_id:
                handle.ack_reaction_message_id = context.message_id
                handle.ack_reaction_emoji = ":eyes:"
                ok = await im_client.add_reaction(
                    context,
                    handle.ack_reaction_message_id,
                    handle.ack_reaction_emoji,
                )
                if not ok:
                    logger.info("Ack reaction not applied (platform returned False)")
        except Exception as ack_err:
            logger.debug("Failed to add reaction ack: %s", ack_err)
        return handle

    def apply_to_request(self, request: Any, handle: ProcessingIndicatorHandle) -> None:
        request.processing_indicator = handle
        request.ack_message_id = handle.ack_message_id
        request.ack_reaction_message_id = handle.ack_reaction_message_id
        request.ack_reaction_emoji = handle.ack_reaction_emoji
        request.typing_indicator_active = handle.typing_indicator_active
        request.typing_indicator_task = handle.typing_indicator_task

    def handle_from_request(self, request: Any) -> ProcessingIndicatorHandle:
        handle = getattr(request, "processing_indicator", None)
        if isinstance(handle, ProcessingIndicatorHandle):
            handle.ack_message_id = handle.ack_message_id or getattr(request, "ack_message_id", None)
            handle.ack_reaction_message_id = handle.ack_reaction_message_id or getattr(
                request,
                "ack_reaction_message_id",
                None,
            )
            handle.ack_reaction_emoji = handle.ack_reaction_emoji or getattr(request, "ack_reaction_emoji", None)
            handle.typing_indicator_active = handle.typing_indicator_active or bool(
                getattr(request, "typing_indicator_active", False)
            )
            handle.typing_indicator_task = handle.typing_indicator_task or getattr(request, "typing_indicator_task", None)
            return handle
        return ProcessingIndicatorHandle(
            context=request.context,
            ack_message_id=getattr(request, "ack_message_id", None),
            ack_message_channel_id=getattr(request.context, "channel_id", None),
            ack_reaction_message_id=getattr(request, "ack_reaction_message_id", None),
            ack_reaction_emoji=getattr(request, "ack_reaction_emoji", None),
            typing_indicator_active=bool(getattr(request, "typing_indicator_active", False)),
            typing_indicator_task=getattr(request, "typing_indicator_task", None),
        )

    def handle_from_snapshot(self, data: dict[str, Any]) -> ProcessingIndicatorHandle:
        return ProcessingIndicatorHandle.from_snapshot(data)

    def snapshot_request(self, request: Any) -> dict[str, Any]:
        return self.handle_from_request(request).to_snapshot()

    async def delete_ack_message(self, request: Any, *, channel_id: Optional[str] = None) -> None:
        handle = self.handle_from_request(request)
        await self._delete_ack_message_for_handle(handle, request=request, channel_id=channel_id)
        if getattr(request, "processing_indicator", None) is None:
            request.processing_indicator = handle

    async def _delete_ack_message_for_handle(
        self,
        handle: ProcessingIndicatorHandle,
        *,
        request: Optional[Any] = None,
        channel_id: Optional[str] = None,
    ) -> None:
        ack_id = handle.ack_message_id
        if ack_id:
            im_client = self._get_im_client(handle.context)
            if hasattr(im_client, "delete_message"):
                try:
                    await im_client.delete_message(
                        channel_id or handle.ack_message_channel_id or handle.context.channel_id,
                        ack_id,
                    )
                except Exception as err:
                    logger.debug("Could not delete ack message: %s", err)
                finally:
                    handle.ack_message_id = None
                    if request is not None:
                        request.ack_message_id = None

    async def finish(self, request_or_handle: Any) -> None:
        if isinstance(request_or_handle, ProcessingIndicatorHandle):
            handle = request_or_handle
            request = None
        else:
            request = request_or_handle
            handle = self.handle_from_request(request)

        await self._delete_ack_message_for_handle(handle, request=request)

        typing_task = handle.typing_indicator_task
        if typing_task is not None:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("Failed to stop typing keepalive task", exc_info=True)
            finally:
                handle.typing_indicator_task = None
                if request is not None:
                    request.typing_indicator_task = None

        if handle.typing_indicator_active:
            try:
                await self._get_im_client(handle.context).clear_typing_indicator(handle.context)
            except Exception as err:
                logger.debug("Failed to clear typing indicator: %s", err)
            finally:
                handle.typing_indicator_active = False
                if request is not None:
                    request.typing_indicator_active = False

        if handle.ack_reaction_message_id and handle.ack_reaction_emoji:
            try:
                await self._get_im_client(handle.context).remove_reaction(
                    handle.context,
                    handle.ack_reaction_message_id,
                    handle.ack_reaction_emoji,
                )
            except Exception as err:
                logger.debug("Failed to remove reaction ack: %s", err)
            finally:
                handle.ack_reaction_message_id = None
                handle.ack_reaction_emoji = None
                if request is not None:
                    request.ack_reaction_message_id = None
                    request.ack_reaction_emoji = None

        if request is not None and getattr(request, "processing_indicator", None) is None:
            request.processing_indicator = handle
