import asyncio
import io
import json
import logging
from typing import Dict, Any, Optional, Callable, List

import aiohttp
import discord

from .base import BaseIMClient, MessageContext, InlineKeyboard, InlineButton, FileAttachment
from config.v2_config import DiscordConfig
from .formatters import DiscordFormatter
from vibe.i18n import get_supported_languages, t as i18n_t

logger = logging.getLogger(__name__)


class DiscordBot(BaseIMClient):
    """Discord implementation of the IM client."""

    def __init__(self, config: DiscordConfig):
        super().__init__(config)
        self.config = config

        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.messages = True
        intents.dm_messages = True
        intents.reactions = True

        self.client = discord.Client(intents=intents)
        self.formatter = DiscordFormatter()

        self.settings_manager = None
        self._controller = None
        self._on_ready: Optional[Callable] = None

        self.client.on_ready = self._on_ready_event
        self.client.on_message = self._on_message_event

    def set_settings_manager(self, settings_manager):
        self.settings_manager = settings_manager

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

    def _get_lang(self, channel_id: Optional[str] = None) -> str:
        if self._controller and hasattr(self._controller, "config"):
            if hasattr(self._controller, "_get_lang"):
                return self._controller._get_lang()
            return getattr(self._controller.config, "language", "en")
        return "en"

    def _t(self, key: str, channel_id: Optional[str] = None, **kwargs) -> str:
        lang = self._get_lang(channel_id)
        return i18n_t(key, lang, **kwargs)

    def get_default_parse_mode(self) -> str:
        return "markdown"

    def should_use_thread_for_reply(self) -> bool:
        return True

    def format_markdown(self, text: str) -> str:
        return text

    def register_handlers(self):
        return

    def run(self):
        if not self.config.bot_token:
            raise ValueError("Discord bot token is required")
        self.client.run(self.config.bot_token)

    async def shutdown(self) -> None:
        await self.client.close()

    # ---------------------------------------------------------------------
    # Message helpers
    # ---------------------------------------------------------------------
    def _to_int_id(self, value: Optional[str]) -> Optional[int]:
        if not value:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    async def _fetch_channel(self, channel_id: Optional[str]) -> Optional[discord.abc.Messageable]:
        cid = self._to_int_id(channel_id)
        if cid is None:
            return None
        channel = self.client.get_channel(cid)
        if channel is not None:
            return channel
        try:
            return await self.client.fetch_channel(cid)
        except Exception as err:
            logger.debug("Failed to fetch channel %s: %s", channel_id, err)
            return None

    def _extract_context_ids(self, channel: discord.abc.GuildChannel | discord.Thread) -> tuple[str, Optional[str]]:
        if isinstance(channel, discord.Thread):
            parent_id = str(channel.parent_id) if channel.parent_id else str(channel.id)
            return parent_id, str(channel.id)
        return str(channel.id), None

    def _clean_message_text(self, text: str) -> str:
        return (text or "").strip()

    def _is_allowed_guild(self, guild_id: Optional[str]) -> bool:
        allow = set(self.config.guild_allowlist or [])
        deny = set(self.config.guild_denylist or [])
        if guild_id and guild_id in deny:
            return False
        if allow and (not guild_id or guild_id not in allow):
            return False
        return True

    async def send_message(
        self,
        context: MessageContext,
        text: str,
        parse_mode: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> str:
        if not text:
            raise ValueError("Discord send_message requires non-empty text")
        target = None
        if context.thread_id:
            target = await self._fetch_channel(context.thread_id)
            if not isinstance(target, discord.Thread):
                target = None
        if target is None:
            target = await self._fetch_channel(context.channel_id)
        if target is None:
            raise RuntimeError("Discord channel not found")
        message = await target.send(content=text)
        if self.settings_manager and context.thread_id:
            try:
                self.settings_manager.mark_thread_active(context.user_id, context.channel_id, context.thread_id)
            except Exception:
                pass
        return str(message.id)

    async def send_message_with_buttons(
        self,
        context: MessageContext,
        text: str,
        keyboard: InlineKeyboard,
        parse_mode: Optional[str] = None,
    ) -> str:
        target = None
        if context.thread_id:
            target = await self._fetch_channel(context.thread_id)
            if not isinstance(target, discord.Thread):
                target = None
        if target is None:
            target = await self._fetch_channel(context.channel_id)
        if target is None:
            raise RuntimeError("Discord channel not found")

        view = _DiscordButtonView(self, context, keyboard)
        message = await target.send(content=text, view=view)
        if self.settings_manager and context.thread_id:
            try:
                self.settings_manager.mark_thread_active(context.user_id, context.channel_id, context.thread_id)
            except Exception:
                pass
        return str(message.id)

    async def edit_message(
        self,
        context: MessageContext,
        message_id: str,
        text: Optional[str] = None,
        keyboard: Optional[InlineKeyboard] = None,
        parse_mode: Optional[str] = None,
    ) -> bool:
        target = None
        if context.thread_id:
            target = await self._fetch_channel(context.thread_id)
            if not isinstance(target, discord.Thread):
                target = None
        if target is None:
            target = await self._fetch_channel(context.channel_id)
        if target is None:
            return False
        try:
            msg = await target.fetch_message(int(message_id))
            view = _DiscordButtonView(self, context, keyboard) if keyboard else None
            await msg.edit(content=text, view=view)
            return True
        except Exception as err:
            logger.debug("Failed to edit Discord message: %s", err)
            return False

    async def remove_inline_keyboard(
        self,
        context: MessageContext,
        message_id: str,
        text: Optional[str] = None,
        parse_mode: Optional[str] = None,
    ) -> bool:
        return await self.edit_message(context, message_id, text=text, keyboard=None, parse_mode=parse_mode)

    async def answer_callback(self, callback_id: str, text: Optional[str] = None, show_alert: bool = False) -> bool:
        return True

    async def add_reaction(self, context: MessageContext, message_id: str, emoji: str) -> bool:
        target = await self._fetch_channel(context.thread_id or context.channel_id)
        if target is None:
            return False
        try:
            msg = await target.fetch_message(int(message_id))
            await msg.add_reaction(emoji)
            return True
        except Exception as err:
            logger.debug("Failed to add Discord reaction: %s", err)
            return False

    async def remove_reaction(self, context: MessageContext, message_id: str, emoji: str) -> bool:
        target = await self._fetch_channel(context.thread_id or context.channel_id)
        if target is None:
            return False
        try:
            msg = await target.fetch_message(int(message_id))
            await msg.remove_reaction(emoji, self.client.user)
            return True
        except Exception as err:
            logger.debug("Failed to remove Discord reaction: %s", err)
            return False

    async def upload_markdown(
        self,
        context: MessageContext,
        title: str,
        content: str,
        filetype: str = "markdown",
    ) -> str:
        target = await self._fetch_channel(context.thread_id or context.channel_id)
        if target is None:
            raise RuntimeError("Discord channel not found")
        data = (content or "").encode("utf-8")
        file_obj = discord.File(io.BytesIO(data), filename=title)
        message = await target.send(file=file_obj)
        return str(message.id)

    async def download_file(
        self,
        file_info: Dict[str, Any],
        max_bytes: int = 20 * 1024 * 1024,
        timeout_seconds: int = 30,
    ) -> Optional[bytes]:
        url = file_info.get("url") or file_info.get("url_private_download") or file_info.get("url_private")
        if not url:
            return None
        try:
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return None
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > max_bytes:
                        return None
                    chunks = []
                    total_size = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        total_size += len(chunk)
                        if total_size > max_bytes:
                            return None
                        chunks.append(chunk)
                    return b"".join(chunks)
        except Exception as err:
            logger.debug("Failed to download Discord file: %s", err)
            return None

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        uid = self._to_int_id(user_id)
        if uid is None:
            return {"id": user_id}
        user = self.client.get_user(uid)
        if user is None:
            try:
                user = await self.client.fetch_user(uid)
            except Exception:
                user = None
        if user is None:
            return {"id": user_id}
        return {"id": str(user.id), "name": user.name, "display_name": user.display_name}

    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        channel = await self._fetch_channel(channel_id)
        if channel is None:
            return {"id": channel_id, "name": channel_id}
        name = getattr(channel, "name", None) or channel_id
        return {"id": str(channel.id), "name": name}

    # ---------------------------------------------------------------------
    # Discord-specific interaction helpers
    # ---------------------------------------------------------------------
    async def _on_ready_event(self):
        logger.info("Discord client ready")
        if self._on_ready:
            await self._on_ready()

    async def _is_authorized_channel(self, channel_id: str) -> bool:
        if not self.settings_manager:
            logger.warning("No settings_manager configured; rejecting by default")
            return False
        settings = self.settings_manager.get_channel_settings(channel_id)
        if settings is None:
            logger.warning("No channel settings found; rejecting by default")
            return False
        return settings.enabled

    async def _send_unauthorized_message(self, channel_id: str):
        try:
            channel = await self._fetch_channel(channel_id)
            if channel is None:
                return
            await channel.send(content=f"❌ {self._t('error.channelNotEnabled', channel_id)}")
        except Exception as err:
            logger.debug("Failed to send unauthorized message: %s", err)

    async def _maybe_create_thread(self, message: discord.Message) -> Optional[discord.Thread]:
        if isinstance(message.channel, discord.Thread):
            return message.channel
        if message.guild is None:
            return None
        try:
            snippet = (message.content or "").strip()
            if snippet:
                snippet = snippet[:50]
            name = snippet or "vibe-remote session"
            thread = await message.create_thread(name=name, auto_archive_duration=60)
            return thread
        except Exception as err:
            logger.warning("Failed to create thread: %s", err)
            return None

    async def _on_message_event(self, message: discord.Message):
        if message.author and message.author.bot:
            return

        content = self._clean_message_text(message.content)

        channel = message.channel
        channel_id, thread_id = self._extract_context_ids(channel)

        if message.guild and not self._is_allowed_guild(str(message.guild.id)):
            return

        # File attachments
        files = None
        if message.attachments:
            files = []
            for attachment in message.attachments:
                files.append(
                    FileAttachment(
                        name=attachment.filename,
                        mimetype=attachment.content_type or "application/octet-stream",
                        url=attachment.url,
                        size=attachment.size,
                    )
                )

        if not content and not files:
            return

        # Authorization
        if not await self._is_authorized_channel(channel_id):
            await self._send_unauthorized_message(channel_id)
            return

        # Mention logic for guild channels
        is_dm = isinstance(channel, discord.DMChannel) or message.guild is None
        effective_require_mention = self.config.require_mention
        if self.settings_manager:
            effective_require_mention = self.settings_manager.get_require_mention(
                channel_id, global_default=self.config.require_mention
            )

        if effective_require_mention and not is_dm:
            if isinstance(channel, discord.Thread):
                if self.settings_manager:
                    if not self.settings_manager.is_thread_active(str(message.author.id), channel_id, str(channel.id)):
                        return
                else:
                    return
            else:
                if not message.mentions or (self.client.user and self.client.user not in message.mentions):
                    return

        # Strip bot mention from content
        if self.client.user:
            bot_id = str(self.client.user.id)
            content = content.replace(f"<@{bot_id}>", "").replace(f"<@!{bot_id}>", "").strip()

        # For non-thread guild messages, create a real thread
        if not isinstance(channel, discord.Thread) and message.guild is not None:
            thread = await self._maybe_create_thread(message)
            if thread is not None:
                thread_id = str(thread.id)

        context = MessageContext(
            user_id=str(message.author.id),
            channel_id=channel_id,
            thread_id=thread_id,
            message_id=str(message.id),
            platform_specific={"message": message},
            files=files,
        )

        # Handle slash-like commands in plain messages
        if content.startswith("/"):
            parts = content.split(maxsplit=1)
            command = parts[0][1:]
            args = parts[1] if len(parts) > 1 else ""
            if command in self.on_command_callbacks:
                handler = self.on_command_callbacks[command]
                await handler(context, args)
                return

        if self.on_message_callback:
            await self.on_message_callback(context, content)

    # ---------------------------------------------------------------------
    # Discord UI helpers (modals and selects)
    # ---------------------------------------------------------------------
    async def open_change_cwd_modal(self, interaction: discord.Interaction, current_cwd: str, channel_id: str):
        class ChangeCwdModal(discord.ui.Modal, title="Change Working Directory"):
            new_cwd = discord.ui.TextInput(label="New working directory", default=current_cwd or "", required=True)

            async def on_submit(self, submit_interaction: discord.Interaction):
                if not hasattr(self, "_outer"):
                    return
                outer: DiscordBot = getattr(self, "_outer")
                if hasattr(outer, "_on_change_cwd"):
                    await outer._on_change_cwd(
                        str(submit_interaction.user.id),
                        str(self.new_cwd.value or ""),
                        channel_id,
                    )
                await submit_interaction.response.send_message("✅ Working directory updated.", ephemeral=True)

        modal = ChangeCwdModal()
        modal._outer = self
        await interaction.response.send_modal(modal)

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
        interaction = trigger_id if isinstance(trigger_id, discord.Interaction) else None

        selected_types = set(user_settings.show_message_types or [])
        type_options = [
            discord.SelectOption(
                label=display_names.get(mt, mt),
                value=mt,
                default=mt in selected_types,
            )
            for mt in message_types
        ]
        require_options = [
            discord.SelectOption(label="Default", value="__default__", default=current_require_mention is None),
            discord.SelectOption(label="Require mention", value="true", default=current_require_mention is True),
            discord.SelectOption(label="No mention required", value="false", default=current_require_mention is False),
        ]
        language_options = [
            discord.SelectOption(label=lang, value=lang, default=lang == current_language)
            for lang in get_supported_languages()
        ]

        class SettingsView(discord.ui.View):
            def __init__(self, outer: DiscordBot):
                super().__init__(timeout=900)
                self.outer = outer
                self.types_select = discord.ui.Select(
                    placeholder="Visible message types",
                    options=type_options,
                    min_values=0,
                    max_values=len(type_options) if type_options else 1,
                )
                self.require_select = discord.ui.Select(
                    placeholder="Require mention",
                    options=require_options,
                    min_values=1,
                    max_values=1,
                )
                self.lang_select = discord.ui.Select(
                    placeholder="Language",
                    options=language_options,
                    min_values=1,
                    max_values=1,
                )
                self.add_item(self.types_select)
                self.add_item(self.require_select)
                self.add_item(self.lang_select)
                self.add_item(discord.ui.Button(label="Save", style=discord.ButtonStyle.primary))

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                return True

            async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
                logger.debug("SettingsView error: %s", error)

            async def on_timeout(self) -> None:
                return

        view = SettingsView(self)

        async def save_callback(save_interaction: discord.Interaction):
            show_types = list(view.types_select.values or [])
            require_value = view.require_select.values[0] if view.require_select.values else "__default__"
            if require_value == "__default__":
                require_mention = None
            elif require_value == "true":
                require_mention = True
            else:
                require_mention = False
            language = view.lang_select.values[0] if view.lang_select.values else current_language
            if hasattr(self, "_on_settings_update"):
                await self._on_settings_update(
                    str(save_interaction.user.id),
                    show_types,
                    channel_id or str(save_interaction.channel_id or ""),
                    require_mention,
                    language,
                )
            await save_interaction.response.edit_message(content="✅ Settings updated.", view=None)

        # Patch the save button callback
        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.label == "Save":
                item.callback = save_callback

        if interaction:
            await interaction.response.send_message(
                "⚙️ Settings",
                view=view,
                ephemeral=True,
            )
        else:
            channel = await self._fetch_channel(channel_id)
            if channel is None:
                raise RuntimeError("Discord channel not found")
            await channel.send("⚙️ Settings", view=view)

    async def open_resume_session_modal(
        self,
        trigger_id: Any,
        sessions_by_agent: dict,
        channel_id: str,
        thread_id: str,
        host_message_ts: Optional[str] = None,
    ):
        interaction = trigger_id if isinstance(trigger_id, discord.Interaction) else None
        options = []
        for agent, mapping in sessions_by_agent.items():
            for thread_key, session_id in mapping.items():
                label = f"{agent}:{session_id[:24]}"
                options.append(discord.SelectOption(label=label, value=f"{agent}|{session_id}"))
        if not options:
            options = [discord.SelectOption(label="No stored sessions", value="__none__")]

        agent_options = [discord.SelectOption(label=agent, value=agent) for agent in sorted(sessions_by_agent.keys())]
        if not agent_options:
            agent_options = [discord.SelectOption(label="default", value="opencode")]

        class ManualSessionModal(discord.ui.Modal, title="Resume Session"):
            session_id = discord.ui.TextInput(label="Session ID", required=True)

            async def on_submit(self, submit_interaction: discord.Interaction):
                if not hasattr(self, "_view"):
                    return
                view: ResumeView = getattr(self, "_view")
                view.manual_session = str(self.session_id.value)
                await submit_interaction.response.send_message("✅ Session ID captured.", ephemeral=True)

        class ResumeView(discord.ui.View):
            def __init__(self, outer: DiscordBot):
                super().__init__(timeout=900)
                self.outer = outer
                self.manual_session: Optional[str] = None
                self.session_select = discord.ui.Select(
                    placeholder="Choose a stored session",
                    options=options,
                    min_values=1,
                    max_values=1,
                )
                self.agent_select = discord.ui.Select(
                    placeholder="Agent (for manual input)",
                    options=agent_options,
                    min_values=1,
                    max_values=1,
                )
                self.add_item(self.session_select)
                self.add_item(self.agent_select)
                self.add_item(discord.ui.Button(label="Enter session ID", style=discord.ButtonStyle.secondary))
                self.add_item(discord.ui.Button(label="Resume", style=discord.ButtonStyle.primary))

        view = ResumeView(self)

        async def manual_callback(manual_interaction: discord.Interaction):
            modal = ManualSessionModal()
            modal._view = view
            await manual_interaction.response.send_modal(modal)

        async def resume_callback(resume_interaction: discord.Interaction):
            selected = view.session_select.values[0] if view.session_select.values else None
            chosen_agent = view.agent_select.values[0] if view.agent_select.values else None
            chosen_session = None
            if selected and selected != "__none__" and "|" in selected:
                chosen_agent, chosen_session = selected.split("|", 1)
            if view.manual_session:
                chosen_session = view.manual_session
            if hasattr(self, "_on_resume_session"):
                await self._on_resume_session(
                    str(resume_interaction.user.id),
                    channel_id,
                    thread_id,
                    chosen_agent,
                    chosen_session,
                    host_message_ts,
                )
            await resume_interaction.response.edit_message(content="✅ Session resumed.", view=None)

        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.label == "Enter session ID":
                item.callback = manual_callback
            if isinstance(item, discord.ui.Button) and item.label == "Resume":
                item.callback = resume_callback

        if interaction:
            await interaction.response.send_message("⏮️ Resume session", view=view, ephemeral=True)
        else:
            channel = await self._fetch_channel(channel_id)
            if channel is None:
                raise RuntimeError("Discord channel not found")
            await channel.send("⏮️ Resume session", view=view)

    async def open_routing_modal(
        self,
        trigger_id: Any,
        channel_id: str,
        registered_backends: list,
        current_backend: str,
        current_routing: Any,
        opencode_agents: list,
        opencode_models: dict,
        opencode_default_config: dict,
        claude_agents: list,
        claude_models: list,
        codex_models: list,
    ):
        interaction = trigger_id if isinstance(trigger_id, discord.Interaction) else None

        class RoutingView(discord.ui.View):
            def __init__(self, outer: DiscordBot):
                super().__init__(timeout=900)
                self.outer = outer
                self.step = "backend"
                self.selected_backend = current_backend or (
                    registered_backends[0] if registered_backends else "opencode"
                )
                self.oc_agent = getattr(current_routing, "opencode_agent", None) if current_routing else None
                self.oc_model = getattr(current_routing, "opencode_model", None) if current_routing else None
                self.oc_reasoning = (
                    getattr(current_routing, "opencode_reasoning_effort", None) if current_routing else None
                )
                self.claude_agent = getattr(current_routing, "claude_agent", None) if current_routing else None
                self.claude_model = getattr(current_routing, "claude_model", None) if current_routing else None
                self.codex_model = getattr(current_routing, "codex_model", None) if current_routing else None
                self.codex_reasoning = (
                    getattr(current_routing, "codex_reasoning_effort", None) if current_routing else None
                )
                self._render()

            def _render(self):
                self.clear_items()
                if self.step == "backend":
                    options = [
                        discord.SelectOption(label=backend, value=backend, default=backend == self.selected_backend)
                        for backend in registered_backends
                    ]
                    backend_select = discord.ui.Select(
                        placeholder="Select backend",
                        options=options,
                        min_values=1,
                        max_values=1,
                    )

                    async def backend_callback(select_interaction: discord.Interaction):
                        self.selected_backend = backend_select.values[0]
                        await select_interaction.response.defer()

                    backend_select.callback = backend_callback
                    self.add_item(backend_select)
                    next_button = discord.ui.Button(label="Next", style=discord.ButtonStyle.primary)
                    next_button.callback = self._on_next
                    self.add_item(next_button)
                    return

                if self.step == "opencode":
                    agent_options = [discord.SelectOption(label="Default", value="__default__")]
                    agent_options += [
                        discord.SelectOption(label=a, value=a, default=a == self.oc_agent) for a in opencode_agents
                    ]
                    model_options = [discord.SelectOption(label="Default", value="__default__")]
                    model_ids = []
                    for provider in opencode_models.get("providers", []):
                        provider_id = provider.get("id") or provider.get("name") or provider.get("provider_id")
                        if not provider_id:
                            continue
                        models = provider.get("models", {})
                        if isinstance(models, dict):
                            model_ids.extend([f"{provider_id}/{mid}" for mid in models.keys()])
                    model_options += [
                        discord.SelectOption(label=m, value=m, default=m == self.oc_model) for m in model_ids
                    ]
                    reasoning_options = [
                        discord.SelectOption(label="Default", value="__default__"),
                        discord.SelectOption(label="low", value="low", default=self.oc_reasoning == "low"),
                        discord.SelectOption(label="medium", value="medium", default=self.oc_reasoning == "medium"),
                        discord.SelectOption(label="high", value="high", default=self.oc_reasoning == "high"),
                        discord.SelectOption(label="xhigh", value="xhigh", default=self.oc_reasoning == "xhigh"),
                    ]

                    agent_select = discord.ui.Select(
                        placeholder="OpenCode agent", options=agent_options, min_values=1, max_values=1
                    )
                    model_select = discord.ui.Select(
                        placeholder="OpenCode model", options=model_options, min_values=1, max_values=1
                    )
                    reasoning_select = discord.ui.Select(
                        placeholder="Reasoning effort", options=reasoning_options, min_values=1, max_values=1
                    )

                    async def agent_callback(select_interaction: discord.Interaction):
                        self.oc_agent = agent_select.values[0]
                        await select_interaction.response.defer()

                    async def model_callback(select_interaction: discord.Interaction):
                        self.oc_model = model_select.values[0]
                        await select_interaction.response.defer()

                    async def reasoning_callback(select_interaction: discord.Interaction):
                        self.oc_reasoning = reasoning_select.values[0]
                        await select_interaction.response.defer()

                    agent_select.callback = agent_callback
                    model_select.callback = model_callback
                    reasoning_select.callback = reasoning_callback
                    self.add_item(agent_select)
                    self.add_item(model_select)
                    self.add_item(reasoning_select)
                    back_button = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary)
                    back_button.callback = self._on_back
                    save_button = discord.ui.Button(label="Save", style=discord.ButtonStyle.primary)
                    save_button.callback = self._on_save
                    self.add_item(back_button)
                    self.add_item(save_button)
                    return

                if self.step == "claude":
                    agent_options = [discord.SelectOption(label="Default", value="__default__")]
                    agent_options += [
                        discord.SelectOption(label=a, value=a, default=a == self.claude_agent) for a in claude_agents
                    ]
                    model_options = [discord.SelectOption(label="Default", value="__default__")]
                    model_options += [
                        discord.SelectOption(label=m, value=m, default=m == self.claude_model) for m in claude_models
                    ]

                    agent_select = discord.ui.Select(
                        placeholder="Claude agent", options=agent_options, min_values=1, max_values=1
                    )
                    model_select = discord.ui.Select(
                        placeholder="Claude model", options=model_options, min_values=1, max_values=1
                    )

                    async def agent_callback(select_interaction: discord.Interaction):
                        self.claude_agent = agent_select.values[0]
                        await select_interaction.response.defer()

                    async def model_callback(select_interaction: discord.Interaction):
                        self.claude_model = model_select.values[0]
                        await select_interaction.response.defer()

                    agent_select.callback = agent_callback
                    model_select.callback = model_callback
                    self.add_item(agent_select)
                    self.add_item(model_select)
                    back_button = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary)
                    back_button.callback = self._on_back
                    save_button = discord.ui.Button(label="Save", style=discord.ButtonStyle.primary)
                    save_button.callback = self._on_save
                    self.add_item(back_button)
                    self.add_item(save_button)
                    return

                if self.step == "codex":
                    model_options = [discord.SelectOption(label="Default", value="__default__")]
                    model_options += [
                        discord.SelectOption(label=m, value=m, default=m == self.codex_model) for m in codex_models
                    ]
                    reasoning_options = [
                        discord.SelectOption(label="Default", value="__default__"),
                        discord.SelectOption(
                            label="minimal", value="minimal", default=self.codex_reasoning == "minimal"
                        ),
                        discord.SelectOption(label="low", value="low", default=self.codex_reasoning == "low"),
                        discord.SelectOption(label="medium", value="medium", default=self.codex_reasoning == "medium"),
                        discord.SelectOption(label="high", value="high", default=self.codex_reasoning == "high"),
                        discord.SelectOption(label="xhigh", value="xhigh", default=self.codex_reasoning == "xhigh"),
                    ]
                    model_select = discord.ui.Select(
                        placeholder="Codex model", options=model_options, min_values=1, max_values=1
                    )
                    reasoning_select = discord.ui.Select(
                        placeholder="Reasoning effort", options=reasoning_options, min_values=1, max_values=1
                    )

                    async def model_callback(select_interaction: discord.Interaction):
                        self.codex_model = model_select.values[0]
                        await select_interaction.response.defer()

                    async def reasoning_callback(select_interaction: discord.Interaction):
                        self.codex_reasoning = reasoning_select.values[0]
                        await select_interaction.response.defer()

                    model_select.callback = model_callback
                    reasoning_select.callback = reasoning_callback
                    self.add_item(model_select)
                    self.add_item(reasoning_select)
                    back_button = discord.ui.Button(label="Back", style=discord.ButtonStyle.secondary)
                    back_button.callback = self._on_back
                    save_button = discord.ui.Button(label="Save", style=discord.ButtonStyle.primary)
                    save_button.callback = self._on_save
                    self.add_item(back_button)
                    self.add_item(save_button)
                    return

            async def _on_next(self, interaction: discord.Interaction):
                backend = self.selected_backend
                self.step = backend
                self._render()
                await interaction.response.edit_message(content="🤖 Agent routing", view=self)

            async def _on_back(self, interaction: discord.Interaction):
                self.step = "backend"
                self._render()
                await interaction.response.edit_message(content="🤖 Agent routing", view=self)

            async def _on_save(self, interaction: discord.Interaction):
                def _normalize(value: Optional[str]) -> Optional[str]:
                    if value in (None, "__default__"):
                        return None
                    return value

                if hasattr(self.outer, "_on_routing_update"):
                    await self.outer._on_routing_update(
                        str(interaction.user.id),
                        channel_id,
                        self.selected_backend,
                        _normalize(self.oc_agent),
                        _normalize(self.oc_model),
                        _normalize(self.oc_reasoning),
                        _normalize(self.claude_agent),
                        _normalize(self.claude_model),
                        _normalize(self.codex_model),
                        _normalize(self.codex_reasoning),
                    )
                await interaction.response.edit_message(content="✅ Routing updated.", view=None)

        view = RoutingView(self)

        if interaction:
            await interaction.response.send_message("🤖 Agent routing", view=view, ephemeral=True)
        else:
            channel = await self._fetch_channel(channel_id)
            if channel is None:
                raise RuntimeError("Discord channel not found")
            await channel.send("🤖 Agent routing", view=view)

    async def open_question_modal(
        self,
        trigger_id: Any,
        context: MessageContext,
        pending: Any,
        callback_prefix: str = "opencode_question",
    ):
        interaction = trigger_id if isinstance(trigger_id, discord.Interaction) else None
        questions = getattr(pending, "questions", None) or []
        if not questions or len(questions) > 4:
            await self.send_message(
                context,
                "Too many questions for Discord UI. Please reply with a custom message.",
            )
            return

        class QuestionView(discord.ui.View):
            def __init__(self, outer: DiscordBot):
                super().__init__(timeout=900)
                self.outer = outer
                self.answers: list[list[str]] = [[] for _ in questions]
                for idx, q in enumerate(questions):
                    options = [discord.SelectOption(label=o.label, value=o.label) for o in q.options]
                    max_values = len(options) if getattr(q, "multiple", False) else 1
                    select = discord.ui.Select(
                        placeholder=q.header or f"Question {idx + 1}",
                        options=options,
                        min_values=1,
                        max_values=max_values,
                    )

                    async def make_callback(select_interaction: discord.Interaction, i=idx, sel=select):
                        self.answers[i] = list(sel.values)
                        await select_interaction.response.defer()

                    select.callback = make_callback
                    self.add_item(select)
                self.add_item(discord.ui.Button(label="Submit", style=discord.ButtonStyle.primary))

        view = QuestionView(self)

        async def submit_callback(submit_interaction: discord.Interaction):
            payload = {"answers": view.answers}
            if self.on_callback_query_callback:
                callback_data = f"{callback_prefix}:modal:" + json.dumps(payload)
                ctx = MessageContext(
                    user_id=str(submit_interaction.user.id),
                    channel_id=context.channel_id,
                    thread_id=context.thread_id,
                    message_id=context.message_id,
                    platform_specific={"interaction": submit_interaction},
                )
                await self.on_callback_query_callback(ctx, callback_data)
            await submit_interaction.response.edit_message(content="✅ Answer submitted.", view=None)

        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.label == "Submit":
                item.callback = submit_callback

        if interaction:
            await interaction.response.send_message("Please answer:", view=view, ephemeral=True)
        else:
            channel = await self._fetch_channel(context.thread_id or context.channel_id)
            if channel is None:
                raise RuntimeError("Discord channel not found")
            await channel.send("Please answer:", view=view)


class _DiscordButtonView(discord.ui.View):
    def __init__(self, outer: DiscordBot, base_context: MessageContext, keyboard: InlineKeyboard):
        super().__init__(timeout=900)
        self.outer = outer
        self.base_context = base_context
        for row_idx, row in enumerate(keyboard.buttons):
            for button in row:
                item = discord.ui.Button(
                    label=button.text,
                    style=discord.ButtonStyle.secondary,
                    custom_id=button.callback_data,
                    row=row_idx,
                )

                async def on_click(interaction: discord.Interaction, data=button.callback_data):
                    try:
                        await interaction.response.defer(ephemeral=True)
                    except Exception:
                        pass
                    channel_id, thread_id = self.outer._extract_context_ids(interaction.channel)
                    context = MessageContext(
                        user_id=str(interaction.user.id),
                        channel_id=channel_id,
                        thread_id=thread_id,
                        message_id=str(interaction.message.id) if interaction.message else None,
                        platform_specific={"interaction": interaction},
                    )
                    if self.outer.on_callback_query_callback:
                        await self.outer.on_callback_query_callback(context, data)

                item.callback = on_click
                self.add_item(item)
