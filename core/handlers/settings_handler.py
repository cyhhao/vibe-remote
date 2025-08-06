"""Settings and configuration handlers"""

import logging
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
                context,
                f"❌ Error showing settings: {str(e)}"
            )
    
    async def _handle_settings_traditional(self, context: MessageContext):
        """Handle settings for non-Slack platforms (Telegram, etc)"""
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
            is_hidden = msg_type in user_settings.hidden_message_types
            checkbox = "☑️" if is_hidden else "⬜"
            display_name = display_names.get(msg_type, msg_type)
            button = InlineButton(
                text=f"{checkbox} Hide {display_name}",
                callback_data=f"toggle_msg_{msg_type}"
            )
            row.append(button)
            
            # Create 2x2 layout
            if len(row) == 2 or i == len(message_types) - 1:
                buttons.append(row)
                row = []
        
        # Add info button on its own row
        buttons.append([InlineButton("ℹ️ About Message Types", callback_data="info_msg_types")])
        
        keyboard = InlineKeyboard(buttons=buttons)
        
        # Send settings message with escaped dash
        await self.im_client.send_message_with_buttons(
            context,
            "⚙️ *Settings \\- Message Visibility*\n\nSelect which message types to hide from Claude output:",
            keyboard
        )
    
    async def _handle_settings_slack(self, context: MessageContext):
        """Handle settings for Slack using modal dialog"""
        # For slash commands or direct triggers, we might have trigger_id
        trigger_id = context.platform_specific.get('trigger_id') if context.platform_specific else None
        
        if trigger_id and hasattr(self.im_client, 'open_settings_modal'):
            # We have trigger_id, open modal directly
            settings_key = self._get_settings_key(context)
            user_settings = self.settings_manager.get_user_settings(settings_key)
            message_types = self.settings_manager.get_available_message_types()
            display_names = self.settings_manager.get_message_type_display_names()
            
            try:
                await self.im_client.open_settings_modal(trigger_id, user_settings, message_types, display_names, context.channel_id)
            except Exception as e:
                logger.error(f"Error opening settings modal: {e}")
                await self.im_client.send_message(context, "❌ Failed to open settings. Please try again.")
        else:
            # No trigger_id, show button to open modal
            buttons = [[
                InlineButton(
                    text="🛠️ Open Settings",
                    callback_data="open_settings_modal"
                )
            ]]
            
            keyboard = InlineKeyboard(buttons=buttons)
            
            await self.im_client.send_message_with_buttons(
                context,
                "⚙️ *Personalization Settings*\n\nConfigure how Claude Code messages appear in your Slack workspace.",
                keyboard
            )
    
    async def handle_toggle_message_type(self, context: MessageContext, msg_type: str):
        """Handle toggle for message type visibility"""
        try:
            # Toggle message type visibility
            settings_key = self._get_settings_key(context)
            is_hidden = self.settings_manager.toggle_hidden_message_type(settings_key, msg_type)
            
            # Update the keyboard
            user_settings = self.settings_manager.get_user_settings(settings_key)
            message_types = self.settings_manager.get_available_message_types()
            display_names = self.settings_manager.get_message_type_display_names()
            
            buttons = []
            row = []
            
            for i, mt in enumerate(message_types):
                is_hidden_now = mt in user_settings.hidden_message_types
                checkbox = "☑️" if is_hidden_now else "⬜"
                display_name = display_names.get(mt, mt)
                button = InlineButton(
                    text=f"{checkbox} Hide {display_name}",
                    callback_data=f"toggle_msg_{mt}"
                )
                row.append(button)
                
                # Create 2x2 layout
                if len(row) == 2 or i == len(message_types) - 1:
                    buttons.append(row)
                    row = []
            
            buttons.append([InlineButton("ℹ️ About Message Types", callback_data="info_msg_types")])
            
            keyboard = InlineKeyboard(buttons=buttons)
            
            # Update message
            if context.message_id:
                await self.im_client.edit_message(
                    context,
                    context.message_id,
                    keyboard=keyboard
                )
            
            # Answer callback (for Telegram)
            display_name = display_names.get(msg_type, msg_type)
            action = "hidden" if is_hidden else "shown"
            
            # Platform-specific callback answering
            if self.config.platform == "telegram":
                # For Telegram, we need the actual callback query object
                # This is handled in the telegram bot handler
                pass
            elif self.config.platform == "slack":
                # For Slack, we might send an ephemeral message
                await self.im_client.send_message(
                    context,
                    f"{display_name} messages are now {action}"
                )
            
        except Exception as e:
            logger.error(f"Error toggling message type {msg_type}: {e}")
            await self.im_client.send_message(
                context,
                self.formatter.format_error(f"Failed to toggle setting: {str(e)}")
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
                    ("Response", "Tool execution responses and results"),
                    ("Assistant", "Claude's messages and explanations"),
                    ("Result", "Final execution results and summaries")
                ],
                footer="Hidden messages won't be sent to your IM platform."
            )
            
            # Send as new message
            await self.im_client.send_message(context, info_text)
            logger.info(f"Sent info_msg_types message to user {context.user_id}")
            
        except Exception as e:
            logger.error(f"Error in info_msg_types handler: {e}", exc_info=True)
            await self.im_client.send_message(context, "❌ Error showing message types info")
    
    async def handle_info_how_it_works(self, context: MessageContext):
        """Show information about how the bot works"""
        try:
            formatter = self.im_client.formatter
            
            # Use format_info_message for clean, platform-agnostic formatting
            info_text = formatter.format_info_message(
                title="How Claude Code Bot Works:",
                emoji="📚",
                items=[
                    ("Real-time", "Messages are immediately sent to Claude Code"),
                    ("Persistent", "Each chat maintains its own conversation context"),
                    ("Commands", "Use /start for menu, /clear to reset session"),
                    ("Work Dir", "Change working directory with /set_cwd or via menu"),
                    ("Settings", "Customize message visibility in Settings")
                ],
                footer="Just type normally to chat with Claude Code!"
            )
            
            # Send as new message
            await self.im_client.send_message(context, info_text)
            logger.info(f"Sent how_it_works info to user {context.user_id}")
            
        except Exception as e:
            logger.error(f"Error in handle_info_how_it_works: {e}", exc_info=True)
            await self.im_client.send_message(context, "❌ Error showing help information")