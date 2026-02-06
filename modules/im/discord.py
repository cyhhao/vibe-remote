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
from modules.agents.opencode.utils import (
    build_opencode_model_option_items,
    build_reasoning_effort_options,
    resolve_opencode_allowed_providers,
    resolve_opencode_default_model,
    resolve_opencode_provider_preferences,
)

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
            normalized = emoji
            if normalized in [":eyes:", "eyes", "eye", "👀"]:
                normalized = "👀"
            await msg.add_reaction(normalized)
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
            normalized = emoji
            if normalized in [":eyes:", "eyes", "eye", "👀"]:
                normalized = "👀"
            await msg.remove_reaction(normalized, self.client.user)
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
            try:
                await self._on_ready()
            except Exception as err:
                logger.error("Discord on_ready callback failed: %s", err, exc_info=True)

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

        mentioned_bot = bool(self.client.user and message.mentions and self.client.user in message.mentions)

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

        # Handle slash-like commands in plain messages
        if content.startswith("/"):
            command_context = MessageContext(
                user_id=str(message.author.id),
                channel_id=channel_id,
                thread_id=thread_id,
                message_id=str(message.id),
                platform_specific={"message": message},
                files=files,
            )
            parts = content.split(maxsplit=1)
            command = parts[0][1:]
            args = parts[1] if len(parts) > 1 else ""
            if command in self.on_command_callbacks:
                handler = self.on_command_callbacks[command]
                await handler(command_context, args)
                return

        if not content and not files:
            if mentioned_bot and self.on_message_callback:
                context = MessageContext(
                    user_id=str(message.author.id),
                    channel_id=channel_id,
                    thread_id=thread_id,
                    message_id=str(message.id),
                    platform_specific={"message": message},
                    files=files,
                )
                await self.on_message_callback(context, "")
            return

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

        def _prefixed_label(prefix_key: str, label: str, limit: int = 100) -> str:
            prefix = self._t(prefix_key)
            combined = f"{prefix}: {label}" if prefix else label
            if len(combined) > limit:
                return combined[:limit]
            return combined

        class SettingsView(discord.ui.View):
            def __init__(self, outer: DiscordBot, owner_id: Optional[str]):
                super().__init__(timeout=900)
                self.outer = outer
                self.owner_id = owner_id
                self.selected_types = set(user_settings.show_message_types or [])
                if current_require_mention is None:
                    self.require_value = "__default__"
                elif current_require_mention is True:
                    self.require_value = "true"
                else:
                    self.require_value = "false"
                self.language_value = current_language or outer._get_lang()
                self._save_callback = None
                type_options = []
                for mt in message_types:
                    display_name = display_names.get(mt, mt)
                    label = _prefixed_label("discord.labels.messageTypes", str(display_name))
                    type_options.append(
                        discord.SelectOption(
                            label=label,
                            value=mt,
                            default=mt in self.selected_types,
                        )
                    )
                default_status = self.outer._t("common.on") if global_require_mention else self.outer._t("common.off")
                require_options = [
                    discord.SelectOption(
                        label=_prefixed_label(
                            "discord.labels.mentionPolicy",
                            self.outer._t("modal.settings.optionDefault", status=default_status),
                        ),
                        value="__default__",
                        default=self.require_value == "__default__",
                    ),
                    discord.SelectOption(
                        label=_prefixed_label(
                            "discord.labels.mentionPolicy",
                            self.outer._t("modal.settings.optionRequireMention"),
                        ),
                        value="true",
                        default=self.require_value == "true",
                    ),
                    discord.SelectOption(
                        label=_prefixed_label(
                            "discord.labels.mentionPolicy",
                            self.outer._t("modal.settings.optionDontRequireMention"),
                        ),
                        value="false",
                        default=self.require_value == "false",
                    ),
                ]
                language_options = [
                    discord.SelectOption(
                        label=_prefixed_label("discord.labels.language", lang),
                        value=lang,
                        default=lang == self.language_value,
                    )
                    for lang in get_supported_languages()
                ]

                self.types_select = discord.ui.Select(
                    placeholder=self.outer._t("modal.settings.showMessageTypesPlaceholder"),
                    options=type_options,
                    min_values=0,
                    max_values=len(type_options) if type_options else 1,
                )
                self.require_select = discord.ui.Select(
                    placeholder=self.outer._t("modal.settings.selectMentionBehavior"),
                    options=require_options,
                    min_values=1,
                    max_values=1,
                )
                self.lang_select = discord.ui.Select(
                    placeholder=self.outer._t("modal.settings.language"),
                    options=language_options,
                    min_values=1,
                    max_values=1,
                )

                async def types_callback(select_interaction: discord.Interaction):
                    self.selected_types = set(self.types_select.values or [])
                    await select_interaction.response.defer()

                async def require_callback(select_interaction: discord.Interaction):
                    if self.require_select.values:
                        self.require_value = self.require_select.values[0]
                    await select_interaction.response.defer()

                async def language_callback(select_interaction: discord.Interaction):
                    if self.lang_select.values:
                        self.language_value = self.lang_select.values[0]
                    await select_interaction.response.defer()

                self.types_select.callback = types_callback
                self.require_select.callback = require_callback
                self.lang_select.callback = language_callback
                self.add_item(self.types_select)
                self.add_item(self.require_select)
                self.add_item(self.lang_select)
                save_button = discord.ui.Button(
                    label=self.outer._t("common.save"),
                    style=discord.ButtonStyle.primary,
                )
                save_button.callback = self._on_save
                self.add_item(save_button)

            async def _on_save(self, interaction: discord.Interaction):
                if self._save_callback:
                    await self._save_callback(interaction)

            async def interaction_check(self, interaction: discord.Interaction) -> bool:
                if self.owner_id and str(interaction.user.id) != self.owner_id:
                    return False
                return True

            async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
                logger.debug("SettingsView error: %s", error)

            async def on_timeout(self) -> None:
                return

        owner_id = str(interaction.user.id) if interaction else None
        view = SettingsView(self, owner_id)

        async def save_callback(save_interaction: discord.Interaction):
            show_types = list(view.selected_types or [])
            require_value = view.require_value
            if require_value == "__default__":
                require_mention = None
            elif require_value == "true":
                require_mention = True
            else:
                require_mention = False
            language = view.language_value
            try:
                await save_interaction.response.defer()
                if hasattr(self, "_on_settings_update"):
                    await self._on_settings_update(
                        str(save_interaction.user.id),
                        show_types,
                        channel_id or str(save_interaction.channel_id or ""),
                        require_mention,
                        language,
                        notify_user=False,
                    )
                await save_interaction.edit_original_response(
                    content=f"✅ {self._t('success.settingsUpdated')}",
                    embed=None,
                    view=None,
                )
            except Exception as err:
                await save_interaction.edit_original_response(
                    content=f"❌ {self._t('error.settingsUpdateFailed', error=str(err))}",
                    embed=None,
                    view=None,
                )

        view._save_callback = save_callback
        settings_title = f"⚙️ {self._t('modal.settings.title')}"
        settings_embed = discord.Embed(
            title=settings_title,
            description=self._t("discord.settingsSubtitle"),
        )
        if interaction:
            await interaction.response.send_message(
                embed=settings_embed,
                view=view,
                ephemeral=True,
            )
        else:
            channel = await self._fetch_channel(channel_id)
            if channel is None:
                raise RuntimeError("Discord channel not found")
            await channel.send(embed=settings_embed, view=view)

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
        if len(options) > 25:
            options = options[:25]
        if not options:
            options = [discord.SelectOption(label="No stored sessions", value="__none__")]

        agent_options = [discord.SelectOption(label=agent, value=agent) for agent in sorted(sessions_by_agent.keys())]
        if len(agent_options) > 25:
            agent_options = agent_options[:25]
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
            def __init__(self, outer: DiscordBot, owner_id: Optional[str]):
                super().__init__(timeout=900)
                self.outer = outer
                self.owner_id = owner_id
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

                async def _defer(interaction: discord.Interaction):
                    await interaction.response.defer()

                self.session_select.callback = _defer
                self.agent_select.callback = _defer
                self.add_item(self.session_select)
                self.add_item(self.agent_select)
                self.add_item(discord.ui.Button(label="Enter session ID", style=discord.ButtonStyle.secondary))
                self.add_item(discord.ui.Button(label="Resume", style=discord.ButtonStyle.primary))

        owner_id = str(interaction.user.id) if interaction else None
        view = ResumeView(self, owner_id)

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

        backend_display_names = {
            "claude": "ClaudeCode",
            "codex": "Codex",
            "opencode": "OpenCode",
        }

        def _prefixed_label(prefix_key: str, label: str, limit: int = 100) -> str:
            prefix = self._t(prefix_key)
            combined = f"{prefix}: {label}" if prefix else label
            if len(combined) > limit:
                return combined[:limit]
            return combined

        def _normalize_agent_name(agent: Any) -> Optional[str]:
            if isinstance(agent, str):
                return agent
            if isinstance(agent, dict):
                for key in ("name", "id", "label", "agent"):
                    value = agent.get(key)
                    if isinstance(value, str) and value:
                        return value
            return None

        def _unique_agent_names(agents: list) -> list:
            seen = set()
            names = []
            for agent in agents or []:
                name = _normalize_agent_name(agent)
                if not name or name in seen:
                    continue
                seen.add(name)
                names.append(name)
            return names

        class RoutingView(discord.ui.View):
            def __init__(self, outer: DiscordBot, owner_id: Optional[str]):
                super().__init__(timeout=900)
                self.outer = outer
                self.owner_id = owner_id
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
                options = []
                for backend in registered_backends:
                    display = backend_display_names.get(backend, backend.capitalize())
                    label = _prefixed_label("discord.labels.backend", display)
                    options.append(
                        discord.SelectOption(
                            label=label,
                            value=backend,
                            default=backend == self.selected_backend,
                        )
                    )
                if len(options) > 25:
                    options = options[:25]
                backend_select = discord.ui.Select(
                    placeholder=self.outer._t("modal.routing.selectBackend"),
                    options=options,
                    min_values=1,
                    max_values=1,
                )

                async def backend_callback(select_interaction: discord.Interaction):
                    if backend_select.values:
                        self.selected_backend = backend_select.values[0]
                    self._render()
                    updated_embed = discord.Embed(
                        title=self._content(),
                        description=self.outer._t("discord.routingSubtitle"),
                    )
                    await select_interaction.response.edit_message(embed=updated_embed, view=self)

                backend_select.callback = backend_callback
                self.add_item(backend_select)

                if self.selected_backend == "opencode":
                    opencode_agent_names = _unique_agent_names(opencode_agents)
                    default_model_str = resolve_opencode_default_model(
                        opencode_default_config,
                        opencode_agents,
                        self.oc_agent if self.oc_agent not in ("__default__", None) else None,
                    )
                    target_model = (
                        self.oc_model
                        if self.oc_model not in (None, "__default__")
                        else default_model_str
                    )
                    preferred_providers = resolve_opencode_provider_preferences(
                        opencode_default_config,
                        target_model,
                    )
                    allowed_providers = resolve_opencode_allowed_providers(
                        opencode_default_config,
                        opencode_models,
                    )
                    agent_options = [
                        discord.SelectOption(
                            label=_prefixed_label(
                                "discord.labels.opencodeAgent",
                                self.outer._t("common.default"),
                            ),
                            value="__default__",
                            default=self.oc_agent in (None, "__default__"),
                        )
                    ]
                    agent_options += [
                        discord.SelectOption(
                            label=_prefixed_label("discord.labels.opencodeAgent", a),
                            value=a,
                            default=a == self.oc_agent,
                        )
                        for a in opencode_agent_names
                    ]
                    if len(agent_options) > 25:
                        agent_options = agent_options[:25]
                    agent_select = discord.ui.Select(
                        placeholder=self.outer._t("modal.routing.selectOpencodeAgent"),
                        options=agent_options,
                        min_values=1,
                        max_values=1,
                    )

                    async def agent_callback(select_interaction: discord.Interaction):
                        if agent_select.values:
                            self.oc_agent = agent_select.values[0]
                        await select_interaction.response.defer()

                    agent_select.callback = agent_callback
                    self.add_item(agent_select)

                    default_label = self.outer._t("common.default")
                    if default_model_str:
                        default_label = f"{default_label} - {default_model_str}"
                    model_options = [
                        discord.SelectOption(
                            label=_prefixed_label("discord.labels.model", default_label),
                            value="__default__",
                            default=self.oc_model in (None, "__default__"),
                        )
                    ]
                    model_entries = build_opencode_model_option_items(
                        opencode_models,
                        max_total=24,
                        preferred_providers=preferred_providers,
                        allowed_providers=allowed_providers,
                    )
                    for entry in model_entries:
                        label = entry.get("label", "")
                        value = entry.get("value", "")
                        if not label or not value:
                            continue
                        model_options.append(
                            discord.SelectOption(
                                label=_prefixed_label("discord.labels.model", label),
                                value=value,
                                default=value == self.oc_model,
                            )
                        )
                    model_select = discord.ui.Select(
                        placeholder=self.outer._t("modal.routing.selectModel"),
                        options=model_options,
                        min_values=1,
                        max_values=1,
                    )

                    async def model_callback(select_interaction: discord.Interaction):
                        if model_select.values:
                            self.oc_model = model_select.values[0]
                        self._render()
                        updated_embed = discord.Embed(
                            title=self._content(),
                            description=self.outer._t("discord.routingSubtitle"),
                        )
                        await select_interaction.response.edit_message(embed=updated_embed, view=self)

                    model_select.callback = model_callback
                    self.add_item(model_select)

                    reasoning_entries = build_reasoning_effort_options(opencode_models, target_model)
                    selected_reasoning = (
                        self.oc_reasoning if self.oc_reasoning not in (None, "__default__") else "__default__"
                    )
                    available_reasoning = {entry.get("value") for entry in reasoning_entries}
                    if selected_reasoning not in available_reasoning:
                        selected_reasoning = "__default__"
                    reasoning_options = []
                    for entry in reasoning_entries:
                        value = entry.get("value")
                        if not value:
                            continue
                        if value == "__default__":
                            label = self.outer._t("common.default")
                        else:
                            translated = self.outer._t(f"reasoning.{value}")
                            label = translated if translated != f"reasoning.{value}" else entry.get("label", value)
                        reasoning_options.append(
                            discord.SelectOption(
                                label=_prefixed_label("discord.labels.reasoningEffort", label),
                                value=value,
                                default=value == selected_reasoning,
                            )
                        )
                    reasoning_select = discord.ui.Select(
                        placeholder=self.outer._t("modal.routing.selectReasoningEffort"),
                        options=reasoning_options,
                        min_values=1,
                        max_values=1,
                    )

                    async def reasoning_callback(select_interaction: discord.Interaction):
                        if reasoning_select.values:
                            self.oc_reasoning = reasoning_select.values[0]
                        await select_interaction.response.defer()

                    reasoning_select.callback = reasoning_callback
                    self.add_item(reasoning_select)

                if self.selected_backend == "claude":
                    claude_agent_names = _unique_agent_names(claude_agents)
                    agent_options = [
                        discord.SelectOption(
                            label=_prefixed_label(
                                "discord.labels.claudeAgent",
                                self.outer._t("common.default"),
                            ),
                            value="__default__",
                            default=self.claude_agent in (None, "__default__"),
                        )
                    ]
                    agent_options += [
                        discord.SelectOption(label=a, value=a, default=a == self.claude_agent) for a in claude_agent_names
                    ]
                    if len(agent_options) > 25:
                        agent_options = agent_options[:25]
                    agent_select = discord.ui.Select(
                        placeholder=self.outer._t("modal.routing.selectClaudeAgent"),
                        options=agent_options,
                        min_values=1,
                        max_values=1,
                    )

                    async def claude_agent_callback(select_interaction: discord.Interaction):
                        if agent_select.values:
                            self.claude_agent = agent_select.values[0]
                        await select_interaction.response.defer()

                    agent_select.callback = claude_agent_callback
                    self.add_item(agent_select)

                    model_options = [
                        discord.SelectOption(
                            label=_prefixed_label("discord.labels.model", self.outer._t("common.default")),
                            value="__default__",
                            default=self.claude_model in (None, "__default__"),
                        )
                    ]
                    model_options += [
                        discord.SelectOption(
                            label=_prefixed_label("discord.labels.model", m),
                            value=m,
                            default=m == self.claude_model,
                        )
                        for m in claude_models
                    ]
                    if len(model_options) > 25:
                        model_options = model_options[:25]
                    model_select = discord.ui.Select(
                        placeholder=self.outer._t("modal.routing.selectModel"),
                        options=model_options,
                        min_values=1,
                        max_values=1,
                    )

                    async def claude_model_callback(select_interaction: discord.Interaction):
                        if model_select.values:
                            self.claude_model = model_select.values[0]
                        await select_interaction.response.defer()

                    model_select.callback = claude_model_callback
                    self.add_item(model_select)

                if self.selected_backend == "codex":
                    model_options = [
                        discord.SelectOption(
                            label=_prefixed_label("discord.labels.model", self.outer._t("common.default")),
                            value="__default__",
                            default=self.codex_model in (None, "__default__"),
                        )
                    ]
                    model_options += [
                        discord.SelectOption(
                            label=_prefixed_label("discord.labels.model", m),
                            value=m,
                            default=m == self.codex_model,
                        )
                        for m in codex_models
                    ]
                    if len(model_options) > 25:
                        model_options = model_options[:25]
                    model_select = discord.ui.Select(
                        placeholder=self.outer._t("modal.routing.selectModel"),
                        options=model_options,
                        min_values=1,
                        max_values=1,
                    )

                    async def codex_model_callback(select_interaction: discord.Interaction):
                        if model_select.values:
                            self.codex_model = model_select.values[0]
                        await select_interaction.response.defer()

                    model_select.callback = codex_model_callback
                    self.add_item(model_select)

                    reasoning_options = [
                        discord.SelectOption(
                            label=_prefixed_label(
                                "discord.labels.reasoningEffort",
                                self.outer._t("common.default"),
                            ),
                            value="__default__",
                            default=self.codex_reasoning in (None, "__default__"),
                        ),
                        discord.SelectOption(
                            label=_prefixed_label(
                                "discord.labels.reasoningEffort",
                                self.outer._t("reasoning.minimal"),
                            ),
                            value="minimal",
                            default=self.codex_reasoning == "minimal",
                        ),
                        discord.SelectOption(
                            label=_prefixed_label(
                                "discord.labels.reasoningEffort",
                                self.outer._t("reasoning.low"),
                            ),
                            value="low",
                            default=self.codex_reasoning == "low",
                        ),
                        discord.SelectOption(
                            label=_prefixed_label(
                                "discord.labels.reasoningEffort",
                                self.outer._t("reasoning.medium"),
                            ),
                            value="medium",
                            default=self.codex_reasoning == "medium",
                        ),
                        discord.SelectOption(
                            label=_prefixed_label(
                                "discord.labels.reasoningEffort",
                                self.outer._t("reasoning.high"),
                            ),
                            value="high",
                            default=self.codex_reasoning == "high",
                        ),
                        discord.SelectOption(
                            label=_prefixed_label(
                                "discord.labels.reasoningEffort",
                                self.outer._t("reasoning.xhigh"),
                            ),
                            value="xhigh",
                            default=self.codex_reasoning == "xhigh",
                        ),
                    ]
                    reasoning_select = discord.ui.Select(
                        placeholder=self.outer._t("modal.routing.selectReasoningEffort"),
                        options=reasoning_options,
                        min_values=1,
                        max_values=1,
                    )

                    async def codex_reasoning_callback(select_interaction: discord.Interaction):
                        if reasoning_select.values:
                            self.codex_reasoning = reasoning_select.values[0]
                        await select_interaction.response.defer()

                    reasoning_select.callback = codex_reasoning_callback
                    self.add_item(reasoning_select)

                save_button = discord.ui.Button(
                    label=self.outer._t("common.save"),
                    style=discord.ButtonStyle.primary,
                )
                save_button.callback = self._on_save
                self.add_item(save_button)

            def _content(self) -> str:
                return f"🤖 {self.outer._t('modal.routing.title')}"

            async def _on_save(self, interaction: discord.Interaction):
                def _normalize(value: Optional[str]) -> Optional[str]:
                    if value in (None, "__default__"):
                        return None
                    return value

                try:
                    await interaction.response.defer()
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
                            notify_user=False,
                        )
                    await interaction.edit_original_response(
                        content=f"✅ {self.outer._t('success.routingUpdated')}",
                        embed=None,
                        view=None,
                    )
                except Exception as err:
                    await interaction.edit_original_response(
                        content=f"❌ {self.outer._t('error.routingUpdateFailed', error=str(err))}",
                        embed=None,
                        view=None,
                    )

        owner_id = str(interaction.user.id) if interaction else None
        view = RoutingView(self, owner_id)

        routing_embed = discord.Embed(
            title=view._content(),
            description=self._t("discord.routingSubtitle"),
        )
        if interaction:
            await interaction.response.send_message(embed=routing_embed, view=view, ephemeral=True)
        else:
            channel = await self._fetch_channel(channel_id)
            if channel is None:
                raise RuntimeError("Discord channel not found")
            await channel.send(embed=routing_embed, view=view)

    async def open_question_modal(
        self,
        trigger_id: Any,
        context: MessageContext,
        pending: Any,
        callback_prefix: str = "opencode_question",
    ):
        interaction = trigger_id if isinstance(trigger_id, discord.Interaction) else None
        if isinstance(pending, dict):
            questions = pending.get("questions") or []
        else:
            questions = getattr(pending, "questions", None) or []
        if not questions or len(questions) > 4:
            await self.send_message(
                context,
                "Too many questions for Discord UI. Please reply with a custom message.",
            )
            return

        def _normalize_question(raw: Any) -> tuple[str, list, bool]:
            if isinstance(raw, dict):
                header = raw.get("header") or raw.get("question") or raw.get("title") or ""
                options = raw.get("options") or []
                multiple = bool(raw.get("multiple"))
                return str(header), options, multiple
            header = getattr(raw, "header", None) or getattr(raw, "question", None) or ""
            options = getattr(raw, "options", None) or []
            multiple = bool(getattr(raw, "multiple", False))
            return str(header), options, multiple

        def _normalize_option(option: Any) -> str:
            if isinstance(option, dict):
                label = option.get("label") or option.get("value") or option.get("name")
                if label is not None:
                    return str(label)
            return str(option)

        class QuestionView(discord.ui.View):
            def __init__(self, outer: DiscordBot):
                super().__init__(timeout=900)
                self.outer = outer
                self.answers: list[list[str]] = [[] for _ in questions]
                for idx, q in enumerate(questions):
                    header, options_raw, multiple = _normalize_question(q)
                    option_labels = [_normalize_option(opt) for opt in options_raw]
                    option_labels = [label for label in option_labels if label]
                    if len(option_labels) > 25:
                        option_labels = option_labels[:25]
                    options = [
                        discord.SelectOption(label=label[:100], value=label[:100])
                        for label in option_labels
                    ]
                    max_values = len(options) if multiple else 1
                    select = discord.ui.Select(
                        placeholder=header or f"Question {idx + 1}",
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
            if interaction.response.is_done():
                await interaction.followup.send("Please answer:", view=view, ephemeral=True)
            else:
                await interaction.response.send_message("Please answer:", view=view, ephemeral=True)
        else:
            channel = await self._fetch_channel(context.thread_id or context.channel_id)
            if channel is None:
                raise RuntimeError("Discord channel not found")
            await channel.send("Please answer:", view=view)

    async def open_opencode_question_modal(
        self,
        trigger_id: Any,
        context: MessageContext,
        pending: Any,
    ):
        await self.open_question_modal(
            trigger_id=trigger_id,
            context=context,
            pending=pending,
            callback_prefix="opencode_question",
        )


class _DiscordButtonView(discord.ui.View):
    def __init__(
        self,
        outer: DiscordBot,
        base_context: MessageContext,
        keyboard: InlineKeyboard,
        owner_id: Optional[str] = None,
    ):
        super().__init__(timeout=900)
        self.outer = outer
        self.base_context = base_context
        self.owner_id = owner_id
        for row_idx, row in enumerate(keyboard.buttons):
            for button in row:
                item = discord.ui.Button(
                    label=button.text,
                    style=discord.ButtonStyle.secondary,
                    custom_id=button.callback_data,
                    row=row_idx,
                )

                async def on_click(interaction: discord.Interaction, data=button.callback_data):
                    needs_modal = data.endswith(":open_modal") or data in {
                        "cmd_change_cwd",
                        "cmd_settings",
                        "cmd_routing",
                        "cmd_resume",
                    }
                    if data.startswith("vibe_update_now") and interaction.guild:
                        is_owner = interaction.user.id == interaction.guild.owner_id
                        is_admin = getattr(interaction.user, "guild_permissions", None)
                        if not (is_owner or (is_admin and is_admin.administrator)):
                            try:
                                await interaction.response.send_message(
                                    "You do not have permission to run updates.", ephemeral=True
                                )
                            except Exception:
                                pass
                            return
                    if data == "opencode_question:open_modal":
                        try:
                            await interaction.response.defer(ephemeral=True)
                        except Exception:
                            pass
                    elif not needs_modal:
                        try:
                            await interaction.response.defer(ephemeral=True)
                        except Exception:
                            pass
                    channel_id, thread_id = self.outer._extract_context_ids(interaction.channel)
                    guild_id = str(interaction.guild_id) if interaction.guild_id else None
                    if guild_id and not self.outer._is_allowed_guild(guild_id):
                        return
                    if not await self.outer._is_authorized_channel(channel_id):
                        await self.outer._send_unauthorized_message(channel_id)
                        return
                    context = MessageContext(
                        user_id=str(interaction.user.id),
                        channel_id=channel_id,
                        thread_id=thread_id,
                        message_id=self.base_context.message_id
                        or (str(interaction.message.id) if interaction.message else None),
                        platform_specific={"interaction": interaction},
                    )
                    if self.outer.on_callback_query_callback:
                        await self.outer.on_callback_query_callback(context, data)

                item.callback = on_click
                self.add_item(item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id and str(interaction.user.id) != self.owner_id:
            return False
        return True
