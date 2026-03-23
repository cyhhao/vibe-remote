"""Multi-platform IM runtime wrapper."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from typing import Any, Callable, Dict, Optional, cast

from config.v2_settings import _infer_channel_platform, _infer_user_platform
from .base import BaseIMClient, InlineKeyboard, MessageContext

logger = logging.getLogger(__name__)


class MultiIMClient(BaseIMClient):
    """Delegate inbound/outbound messaging across multiple IM clients."""

    def __init__(self, clients: Dict[str, BaseIMClient], primary_platform: str):
        if primary_platform not in clients:
            raise ValueError(f"Primary platform '{primary_platform}' is not in enabled clients")
        self.clients = clients
        self.primary_platform = primary_platform
        self._threads: Dict[str, threading.Thread] = {}
        self._stop_requested = threading.Event()
        self._ready_platforms: set[str] = set()
        self._ready_emitted = False
        super().__init__(clients[primary_platform].config)
        self.formatter = clients[primary_platform].formatter

    def _resolve_platform(self, context: Optional[MessageContext] = None) -> str:
        if context is not None:
            if context.platform:
                return context.platform
            ps = context.platform_specific or {}
            platform = ps.get("platform")
            if isinstance(platform, str) and platform in self.clients:
                return platform
        return self.primary_platform

    def get_client(self, platform: str) -> BaseIMClient:
        try:
            return self.clients[platform]
        except KeyError as exc:
            raise ValueError(f"Platform '{platform}' is not enabled") from exc

    def get_client_for_context(self, context: Optional[MessageContext] = None) -> BaseIMClient:
        return self.get_client(self._resolve_platform(context))

    def get_default_parse_mode(self) -> Optional[str]:
        return self.clients[self.primary_platform].get_default_parse_mode()

    def should_use_thread_for_reply(self) -> bool:
        return (
            len({client.should_use_thread_for_reply() for client in self.clients.values()}) == 1
            and self.clients[self.primary_platform].should_use_thread_for_reply()
        )

    def should_use_thread_for_dm_session(self) -> bool:
        return (
            len({client.should_use_thread_for_dm_session() for client in self.clients.values()}) == 1
            and self.clients[self.primary_platform].should_use_thread_for_dm_session()
        )

    def register_callbacks(
        self,
        on_message: Optional[Callable] = None,
        on_command: Optional[Dict[str, Callable]] = None,
        on_callback_query: Optional[Callable] = None,
        **kwargs: Any,
    ):
        super().register_callbacks(
            on_message=on_message, on_command=on_command, on_callback_query=on_callback_query, **kwargs
        )

        for platform, client in self.clients.items():
            wrapped_kwargs = {key: self._wrap_additional_callback(platform, value) for key, value in kwargs.items()}
            wrapped_kwargs["on_ready"] = self._wrap_on_ready(platform, kwargs.get("on_ready"))
            wrapped_commands: Optional[Dict[str, Callable]] = None
            if on_command is not None:
                wrapped_commands = {}
                for name, handler in on_command.items():
                    wrapped = self._wrap_context_callback(platform, handler)
                    if wrapped is not None:
                        wrapped_commands[name] = cast(Callable, wrapped)
            client.register_callbacks(
                on_message=self._wrap_context_callback(platform, on_message),
                on_command=wrapped_commands,
                on_callback_query=self._wrap_callback_query(platform, on_callback_query),
                **wrapped_kwargs,
            )

    def _wrap_additional_callback(self, platform: str, callback: Optional[Callable]) -> Optional[Callable]:
        if callback is None:
            return None

        signature = inspect.signature(callback)
        supports_platform = "platform" in signature.parameters or any(
            parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
        )

        async def _wrapped(*args: Any, **kwargs: Any):
            mutable_args = list(args)
            if mutable_args and isinstance(mutable_args[0], MessageContext):
                self._annotate_context(platform, mutable_args[0])
            if supports_platform and "platform" not in kwargs:
                kwargs["platform"] = platform
            await callback(*mutable_args, **kwargs)

        return _wrapped

    def _wrap_context_callback(self, platform: str, callback: Optional[Callable]) -> Optional[Callable]:
        if callback is None:
            return None

        async def _wrapped(context: MessageContext, *args: Any, **kwargs: Any):
            self._annotate_context(platform, context)
            await callback(context, *args, **kwargs)

        return _wrapped

    def _wrap_callback_query(self, platform: str, callback: Optional[Callable]) -> Optional[Callable]:
        if callback is None:
            return None

        async def _wrapped(context: MessageContext, *args: Any, **kwargs: Any):
            self._annotate_context(platform, context)
            await callback(context, *args, **kwargs)

        return _wrapped

    def _wrap_on_ready(self, platform: str, callback: Optional[Callable]) -> Optional[Callable]:
        if callback is None:
            return None

        async def _wrapped(*args: Any, **kwargs: Any):
            self._ready_platforms.add(platform)
            if self._ready_emitted:
                return
            self._ready_emitted = True
            await callback(*args, **kwargs)

        return _wrapped

    @staticmethod
    def _annotate_context(platform: str, context: MessageContext) -> None:
        context.platform = platform
        if context.platform_specific is None:
            context.platform_specific = {}
        context.platform_specific.setdefault("platform", platform)

    async def send_message(
        self,
        context: MessageContext,
        text: str,
        parse_mode: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> str:
        return await self.get_client_for_context(context).send_message(
            context, text, parse_mode=parse_mode, reply_to=reply_to
        )

    async def send_message_with_buttons(
        self,
        context: MessageContext,
        text: str,
        keyboard: InlineKeyboard,
        parse_mode: Optional[str] = None,
    ) -> str:
        return await self.get_client_for_context(context).send_message_with_buttons(
            context, text, keyboard, parse_mode=parse_mode
        )

    async def upload_markdown(
        self, context: MessageContext, title: str, content: str, filetype: str = "markdown"
    ) -> str:
        return await self.get_client_for_context(context).upload_markdown(context, title, content, filetype=filetype)

    async def upload_file_from_path(self, context: MessageContext, file_path: str, title: Optional[str] = None) -> str:
        return await self.get_client_for_context(context).upload_file_from_path(context, file_path, title=title)

    async def upload_image_from_path(self, context: MessageContext, file_path: str, title: Optional[str] = None) -> str:
        return await self.get_client_for_context(context).upload_image_from_path(context, file_path, title=title)

    async def upload_video_from_path(self, context: MessageContext, file_path: str, title: Optional[str] = None) -> str:
        return await self.get_client_for_context(context).upload_video_from_path(context, file_path, title=title)

    async def download_file(
        self, file_info: Dict[str, Any], max_bytes: Optional[int] = None, timeout_seconds: int = 30
    ):
        return await self.clients[self.primary_platform].download_file(
            file_info, max_bytes=max_bytes, timeout_seconds=timeout_seconds
        )

    async def download_file_to_path(
        self,
        file_info: Dict[str, Any],
        target_path: str,
        max_bytes: Optional[int] = None,
        timeout_seconds: int = 30,
    ):
        return await self.clients[self.primary_platform].download_file_to_path(
            file_info,
            target_path,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
        )

    async def edit_message(
        self,
        context: MessageContext,
        message_id: str,
        text: Optional[str] = None,
        keyboard: Optional[InlineKeyboard] = None,
        parse_mode: Optional[str] = None,
    ) -> bool:
        return await self.get_client_for_context(context).edit_message(
            context,
            message_id,
            text=text,
            keyboard=keyboard,
            parse_mode=parse_mode,
        )

    async def answer_callback(self, callback_id: str, text: Optional[str] = None, show_alert: bool = False) -> bool:
        return await self.clients[self.primary_platform].answer_callback(callback_id, text=text, show_alert=show_alert)

    def register_handlers(self):
        for client in self.clients.values():
            client.register_handlers()

    def run(self):
        self._stop_requested.clear()
        self._threads = {}

        for platform, client in self.clients.items():
            thread = threading.Thread(target=self._run_client, args=(platform, client), daemon=True)
            thread.start()
            self._threads[platform] = thread

        try:
            while not self._stop_requested.is_set():
                for platform, thread in list(self._threads.items()):
                    if thread.is_alive():
                        continue
                    logger.warning("IM runtime for %s exited", platform)
                    self._threads.pop(platform, None)
                if not self._threads:
                    break
                time.sleep(0.5)
        finally:
            self.stop()
            for thread in list(self._threads.values()):
                thread.join(timeout=1.0)

    @staticmethod
    def _run_client(platform: str, client: BaseIMClient) -> None:
        try:
            client.run()
        except Exception:
            logger.exception("IM runtime for %s crashed", platform)

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        platform = _infer_user_platform(user_id)
        client = self.clients.get(platform, self.clients[self.primary_platform])
        return await client.get_user_info(user_id)

    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        platform = _infer_channel_platform(channel_id)
        client = self.clients.get(platform, self.clients[self.primary_platform])
        return await client.get_channel_info(channel_id)

    async def add_reaction(self, context: MessageContext, message_id: str, emoji: str) -> bool:
        return await self.get_client_for_context(context).add_reaction(context, message_id, emoji)

    async def remove_reaction(self, context: MessageContext, message_id: str, emoji: str) -> bool:
        return await self.get_client_for_context(context).remove_reaction(context, message_id, emoji)

    async def send_typing_indicator(self, context: MessageContext) -> bool:
        return await self.get_client_for_context(context).send_typing_indicator(context)

    async def clear_typing_indicator(self, context: MessageContext) -> bool:
        return await self.get_client_for_context(context).clear_typing_indicator(context)

    async def send_dm(self, user_id: str, text: str, **kwargs):
        platform = _infer_user_platform(user_id)
        client = self.clients.get(platform, self.clients[self.primary_platform])
        return await client.send_dm(user_id, text, **kwargs)

    def stop(self):
        self._stop_requested.set()
        for client in self.clients.values():
            stop_attr = getattr(client, "stop", None)
            if callable(stop_attr) and not inspect.iscoroutinefunction(stop_attr):
                try:
                    stop_attr()
                except Exception:
                    logger.exception("Failed to stop IM client")

    async def shutdown(self) -> None:
        self._stop_requested.set()
        self.stop()
        for thread in list(self._threads.values()):
            await asyncio.to_thread(thread.join, 2.0)

        for client in self.clients.values():
            if any(thread.is_alive() for thread in self._threads.values()):
                break
            shutdown_attr = getattr(client, "shutdown", None)
            if callable(shutdown_attr):
                try:
                    if inspect.iscoroutinefunction(shutdown_attr):
                        await shutdown_attr()
                    else:
                        shutdown_attr()
                except Exception:
                    logger.exception("Failed to shutdown IM client")

    def format_markdown(self, text: str) -> str:
        return self.clients[self.primary_platform].format_markdown(text)
