"""Settings and configuration handlers"""

import logging
from modules.agents import get_agent_display_name
from modules.im import MessageContext, InlineKeyboard, InlineButton

logger = logging.getLogger(__name__)


class SettingsHandler:
    """Handles settings and configuration operations"""

    def __init__(self, controller):
        """Initialize with reference to main controller"""
        self.controller = controller
        self.config = controller.config
        self.im_client = controller.im_client
        self.settings_manager = controller.settings_manager
        self.formatter = controller.im_client.formatter

    def _get_settings_key(self, context: MessageContext) -> str:
        """Get settings key - delegate to controller"""
        return self.controller._get_settings_key(context)

    def _get_agent_display_name(self, context: MessageContext) -> str:
        """Return a friendly agent name for the current context."""
        agent_name = self.controller.resolve_agent_for_context(context)
        default_agent = getattr(self.controller.agent_service, "default_agent", None)
        return get_agent_display_name(agent_name, fallback=default_agent)

    async def handle_settings(self, context: MessageContext, args: str = ""):
        """Handle settings command - show settings menu"""
        try:
            # For Slack, use modal dialog
            if self.config.platform == "slack":
                await self._handle_settings_slack(context)
            else:
                # For other platforms, use inline keyboard
                await self._handle_settings_traditional(context)

        except Exception as e:
            logger.error(f"Error showing settings: {e}")
            await self.im_client.send_message(
                context, f"❌ Error showing settings: {str(e)}"
            )

    async def _handle_settings_traditional(self, context: MessageContext):
        """Handle settings for non-Slack platforms"""
        # Get current settings
        settings_key = self._get_settings_key(context)
        user_settings = self.settings_manager.get_user_settings(settings_key)

        # Get available message types and display names
        message_types = self.settings_manager.get_available_message_types()
        display_names = self.settings_manager.get_message_type_display_names()

        # Create inline keyboard buttons in 2x2 layout
        buttons = []
        row = []

        for i, msg_type in enumerate(message_types):
            is_shown = msg_type in user_settings.show_message_types
            checkbox = "☑️" if is_shown else "⬜"
            display_name = display_names.get(msg_type, msg_type)
            button = InlineButton(
                text=f"{checkbox} Show {display_name}",
                callback_data=f"toggle_msg_{msg_type}",
            )
            row.append(button)

            # Create 2x2 layout
            if len(row) == 2 or i == len(message_types) - 1:
                buttons.append(row)
                row = []

        # Add info button on its own row
        buttons.append(
            [InlineButton("ℹ️ About Message Types", callback_data="info_msg_types")]
        )

        keyboard = InlineKeyboard(buttons=buttons)

        # Send settings message with escaped dash
        agent_label = self._get_agent_display_name(context)
        await self.im_client.send_message_with_buttons(
            context,
            f"⚙️ *Settings \\- Message Visibility*\n\nSelect which message types to hide from {agent_label} output:",
            keyboard,
        )

    async def _handle_settings_slack(self, context: MessageContext):
        """Handle settings for Slack using modal dialog"""
        # For slash commands or direct triggers, we might have trigger_id
        trigger_id = (
            context.platform_specific.get("trigger_id")
            if context.platform_specific
            else None
        )

        if trigger_id and hasattr(self.im_client, "open_settings_modal"):
            # We have trigger_id, open modal directly
            settings_key = self._get_settings_key(context)
            user_settings = self.settings_manager.get_user_settings(settings_key)
            message_types = self.settings_manager.get_available_message_types()
            display_names = self.settings_manager.get_message_type_display_names()

            # Get current require_mention override for this channel
            current_require_mention = self.settings_manager.get_require_mention_override(settings_key)
            global_require_mention = self.config.slack.require_mention

            try:
                await self.im_client.open_settings_modal(
                    trigger_id,
                    user_settings,
                    message_types,
                    display_names,
                    context.channel_id,
                    current_require_mention=current_require_mention,
                    global_require_mention=global_require_mention,
                )
            except Exception as e:
                logger.error(f"Error opening settings modal: {e}")
                await self.im_client.send_message(
                    context, "❌ Failed to open settings. Please try again."
                )
        else:
            # No trigger_id, show button to open modal
            buttons = [
                [
                    InlineButton(
                        text="🛠️ Open Settings", callback_data="open_settings_modal"
                    )
                ]
            ]

            keyboard = InlineKeyboard(buttons=buttons)

            await self.im_client.send_message_with_buttons(
                context,
                f"⚙️ *Personalization Settings*\n\nConfigure how {self._get_agent_display_name(context)} messages appear in your Slack workspace.",
                keyboard,
            )

    async def handle_toggle_message_type(self, context: MessageContext, msg_type: str):
        """Handle toggle for message type visibility"""
        try:
            # Toggle message type visibility
            settings_key = self._get_settings_key(context)
            is_shown = self.settings_manager.toggle_show_message_type(
                settings_key, msg_type
            )

            # Update the keyboard
            user_settings = self.settings_manager.get_user_settings(settings_key)
            message_types = self.settings_manager.get_available_message_types()
            display_names = self.settings_manager.get_message_type_display_names()

            buttons = []
            row = []

            for i, mt in enumerate(message_types):
                is_shown_now = mt in user_settings.show_message_types
                checkbox = "☑️" if is_shown_now else "⬜"
                display_name = display_names.get(mt, mt)
                button = InlineButton(
                    text=f"{checkbox} Show {display_name}",
                    callback_data=f"toggle_msg_{mt}",
                )
                row.append(button)

                # Create 2x2 layout
                if len(row) == 2 or i == len(message_types) - 1:
                    buttons.append(row)
                    row = []

            buttons.append(
                [InlineButton("ℹ️ About Message Types", callback_data="info_msg_types")]
            )

            keyboard = InlineKeyboard(buttons=buttons)

            # Update message
            if context.message_id:
                await self.im_client.edit_message(
                    context, context.message_id, keyboard=keyboard
                )

            # Answer callback (for Telegram)
            display_name = display_names.get(msg_type, msg_type)
            action = "shown" if is_shown else "hidden"

            # Platform-specific callback answering
            await self.im_client.send_message(
                context, f"{display_name} messages are now {action}"
            )

        except Exception as e:
            logger.error(f"Error toggling message type {msg_type}: {e}")
            await self.im_client.send_message(
                context,
                self.formatter.format_error(f"Failed to toggle setting: {str(e)}"),
            )

    async def handle_info_message_types(self, context: MessageContext):
        """Show information about different message types"""
        try:
            formatter = self.im_client.formatter

            # Use the new format_info_message method for clean, platform-agnostic formatting
            info_text = formatter.format_info_message(
                title="Message Types Info:",
                emoji="📋",
                items=[
                    ("System", "System initialization and status messages"),
                    ("Toolcall", "Agent tool name + params (one line)"),
                    ("Assistant", "Agent responses and explanations"),
                    ("Result", "Final execution result (always sent)"),
                ],
                footer="Hidden messages won't be sent to your IM platform.",
            )

            # Send as new message
            await self.im_client.send_message(context, info_text)
            logger.info(f"Sent info_msg_types message to user {context.user_id}")

        except Exception as e:
            logger.error(f"Error in info_msg_types handler: {e}", exc_info=True)
            await self.im_client.send_message(
                context, "❌ Error showing message types info"
            )

    async def handle_info_how_it_works(self, context: MessageContext):
        """Show information about how the bot works"""
        try:
            formatter = self.im_client.formatter
            agent_label = self._get_agent_display_name(context)

            # Use format_info_message for clean, platform-agnostic formatting
            info_text = formatter.format_info_message(
                title="How Vibe Remote Works:",
                emoji="📚",
                items=[
                    ("Real-time", f"Messages are immediately sent to {agent_label}"),
                    ("Persistent", "Each chat maintains its own conversation context"),
                    ("Commands", "Use @Vibe Remote /start for menu, @Vibe Remote /clear to reset session"),
                    ("Work Dir", "Change working directory with /set_cwd or via menu"),
                    ("Settings", "Customize message visibility in Settings"),
                ],
                footer=f"Just type normally to chat with {agent_label}!",
            )

            # Send as new message
            await self.im_client.send_message(context, info_text)
            logger.info(f"Sent how_it_works info to user {context.user_id}")

        except Exception as e:
            logger.error(f"Error in handle_info_how_it_works: {e}", exc_info=True)
            await self.im_client.send_message(
                context, "❌ Error showing help information"
            )

    async def handle_routing(self, context: MessageContext):
        """Handle routing command - show agent/model selection"""
        try:
            # Only Slack has modal support for now
            if self.config.platform == "slack":
                await self._handle_routing_slack(context)
            else:
                # For other platforms, show a simple message
                await self.im_client.send_message(
                    context,
                    "🤖 Agent switching is currently only available in Slack. "
                    "Use Slack Agent Settings to configure routing.",
                )
        except Exception as e:
            logger.error(f"Error showing routing settings: {e}", exc_info=True)
            await self.im_client.send_message(
                context, f"❌ Error showing routing settings: {str(e)}"
            )

    async def _handle_routing_slack(self, context: MessageContext):
        """Handle routing for Slack using modal dialog"""
        trigger_id = (
            context.platform_specific.get("trigger_id")
            if context.platform_specific
            else None
        )

        if not trigger_id:
            # No trigger_id, show button to open modal
            buttons = [
                [
                    InlineButton(
                        text="🤖 Open Agent Settings",
                        callback_data="open_routing_modal",
                    )
                ]
            ]
            keyboard = InlineKeyboard(buttons=buttons)
            await self.im_client.send_message_with_buttons(
                context,
                "🤖 *Agent & Model Settings*\n\nConfigure which backend to use for this channel.",
                keyboard,
            )
            return

        # Gather data for the modal
        settings_key = self._get_settings_key(context)
        current_routing = self.settings_manager.get_channel_routing(settings_key)

        # Get registered backends, prioritize opencode first
        all_backends = list(self.controller.agent_service.agents.keys())
        registered_backends = sorted(
            all_backends, key=lambda x: (x != "opencode", x)
        )

        # Get current backend (from routing or default)
        current_backend = self.controller.resolve_agent_for_context(context)

        # Get current require_mention override for this channel
        current_require_mention = self.settings_manager.get_require_mention_override(settings_key)
        global_require_mention = self.config.slack.require_mention

        # Get OpenCode agents/models if available
        opencode_agents = []
        opencode_models = {}
        opencode_default_config = {}

        if "opencode" in registered_backends:
            try:
                # Get OpenCode server manager
                opencode_agent = self.controller.agent_service.agents.get("opencode")
                if opencode_agent and hasattr(opencode_agent, "_get_server"):
                    server = await opencode_agent._get_server()
                    await server.ensure_running()

                    cwd = self.controller.get_cwd(context)
                    opencode_agents = await server.get_available_agents(cwd)
                    opencode_models = await server.get_available_models(cwd)
                    opencode_default_config = await server.get_default_config(cwd)
            except Exception as e:
                logger.warning(f"Failed to fetch OpenCode data: {e}")

        # Open modal
        try:
            await self.im_client.open_routing_modal(
                trigger_id=trigger_id,
                channel_id=context.channel_id,
                registered_backends=registered_backends,
                current_backend=current_backend,
                current_routing=current_routing,
                opencode_agents=opencode_agents,
                opencode_models=opencode_models,
                opencode_default_config=opencode_default_config,
                current_require_mention=current_require_mention,
                global_require_mention=global_require_mention,
            )
        except Exception as e:
            logger.error(f"Error opening routing modal: {e}", exc_info=True)
            await self.im_client.send_message(
                context, "❌ Failed to open settings. Please try again."
            )
