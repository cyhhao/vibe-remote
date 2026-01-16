import asyncio
import logging
from typing import Callable, Optional, Dict, Any
from telegramify_markdown import markdownify
from telegram import (
    Update,
    Bot,
    InlineKeyboardMarkup as TGInlineKeyboardMarkup,
    InlineKeyboardButton as TGInlineKeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler,
)
from telegram.error import TelegramError
from config.settings import TelegramConfig
from .base import BaseIMClient, MessageContext, InlineKeyboard, InlineButton
from .formatters import TelegramFormatter


logger = logging.getLogger(__name__)


class TelegramBot(BaseIMClient):
    def __init__(self, config: TelegramConfig):
        super().__init__(config)
        self.application = Application.builder().token(config.bot_token).build()

        # Initialize Telegram formatter
        self.formatter = TelegramFormatter()

        # Store callback queries for answering
        self._callback_queries: Dict[str, Any] = {}
    
    def _convert_to_markdownv2(self, text: str) -> str:
        """Convert markdown text to Telegram MarkdownV2 format"""
        try:
            # Use telegramify_markdown to convert to MarkdownV2
            return markdownify(text)
        except Exception as e:
            logger.warning(f"Error converting to MarkdownV2: {e}, sending as plain text")
            # Fallback: escape basic special characters
            import re
            # Escape special characters for MarkdownV2
            escape_chars = r'_*[]()~`>#+-=|{}.!'
            for char in escape_chars:
                text = text.replace(char, f'\\{char}')
            return text
    
    
    def get_default_parse_mode(self) -> str:
        """Get the default parse mode for Telegram - uses MarkdownV2"""
        return "MarkdownV2"
    
    def should_use_thread_for_reply(self) -> bool:
        """Telegram doesn't use threads for replies"""
        return False

    async def handle_telegram_message(
        self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle incoming text messages from Telegram"""
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type

        # Check if message is authorized based on whitelist
        if not self._is_authorized_chat(chat_id, chat_type):
            logger.info(f"Unauthorized message from chat: {chat_id}")
            await self._send_unauthorized_message(chat_id)
            return

        # Create MessageContext
        context = MessageContext(
            user_id=str(update.effective_user.id),
            channel_id=str(chat_id),
            message_id=str(update.message.message_id),
            platform_specific={"update": update, "tg_context": tg_context},
        )

        message_text = update.message.text

        # Check if it's a command
        if message_text.startswith("/"):
            parts = message_text.split(maxsplit=1)
            command = parts[0][1:]  # Remove the /
            args = parts[1] if len(parts) > 1 else ""

            if command in self.on_command_callbacks:
                await self.on_command_callbacks[command](context, args)
        elif self.on_message_callback:
            await self.on_message_callback(context, message_text)

    async def handle_telegram_callback(
        self, update: Update, tg_context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle callback queries from inline keyboards"""
        query = update.callback_query
        chat_id = query.message.chat_id
        chat_type = query.message.chat.type
        
        logger.info(f"Telegram callback received: data='{query.data}', user={query.from_user.id}, chat={chat_id}")

        # Check if callback is authorized based on whitelist
        if not self._is_authorized_chat(chat_id, chat_type):
            logger.info(f"Unauthorized callback from chat: {chat_id}")
            # For callback queries, we can answer with an alert
            await query.answer(
                "❌ This chat is not authorized to use bot commands.", show_alert=True
            )
            return

        # Store the query for later use in answer_callback
        self._callback_queries[query.id] = query

        # Create MessageContext
        context = MessageContext(
            user_id=str(query.from_user.id),
            channel_id=str(chat_id),
            message_id=str(query.message.message_id),
            platform_specific={
                "query": query,
                "update": update,
                "tg_context": tg_context,
                "callback_id": query.id,
            },
        )

        if self.on_callback_query_callback:
            logger.info(f"Calling on_callback_query_callback with data: {query.data}")
            await self.on_callback_query_callback(context, query.data)
            logger.info(f"Finished on_callback_query_callback for data: {query.data}")
        else:
            logger.warning("No on_callback_query_callback registered!")
        
        # Always answer the callback to stop loading animation
        try:
            await query.answer()
        except Exception as e:
            logger.warning(f"Failed to answer callback query: {e}")

    async def _wrap_command(
        self, command_name: str, update: Update, tg_context: ContextTypes.DEFAULT_TYPE
    ):
        """Wrap a command handler to convert Update to MessageContext"""
        if command_name not in self.on_command_callbacks:
            return

        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type

        # Check if command is authorized based on whitelist
        if not self._is_authorized_chat(chat_id, chat_type):
            logger.info(f"Unauthorized command from chat: {chat_id}")
            await self._send_unauthorized_message(chat_id)
            return

        # Extract args
        message_text = update.message.text
        parts = message_text.split(maxsplit=1)
        args = parts[1] if len(parts) > 1 else ""

        # Create MessageContext
        context = MessageContext(
            user_id=str(update.effective_user.id),
            channel_id=str(update.effective_chat.id),
            message_id=str(update.message.message_id),
            platform_specific={"update": update, "tg_context": tg_context},
        )

        await self.on_command_callbacks[command_name](context, args)

    def setup_handlers(self):
        """Setup bot command and message handlers"""
        # Register command handlers dynamically
        for command in self.on_command_callbacks:
            async def handler(update, context, cmd=command):
                try:
                    await self._wrap_command(cmd, update, context)
                except Exception as e:
                    logger.error(f"Error in command handler {cmd}: {e}", exc_info=True)
                    try:
                        await update.message.reply_text(f"❌ Error processing command: {str(e)}")
                    except Exception as reply_error:
                        logger.error(f"Failed to send error reply: {reply_error}")
            
            self.application.add_handler(CommandHandler(command, handler))

        # Register callback query handler
        self.application.add_handler(
            CallbackQueryHandler(self.handle_telegram_callback)
        )

        # Register message handler
        self.application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND, self.handle_telegram_message
            )
        )

    async def send_settings_message(
        self, chat_id: int, text: str, reply_markup: TGInlineKeyboardMarkup
    ):
        """Send message with inline keyboard"""
        bot = self.application.bot
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="MarkdownV2",
            reply_markup=reply_markup,
        )


    def run(self):
        """Run the bot with infinite retry mechanism"""
        import time

        self.setup_handlers()

        retry_delay = 5  # seconds
        attempt = 1

        while True:
            try:
                logger.info(f"Starting Telegram bot (attempt {attempt})...")
                self.application.run_polling()
                break  # If successful, break out of retry loop

            except KeyboardInterrupt:
                logger.info("Received keyboard interrupt, shutting down...")
                break

            except Exception as e:
                logger.error(f"Telegram bot failed (attempt {attempt}): {e}")
                logger.info(f"Retrying in {retry_delay} seconds...")

                try:
                    time.sleep(retry_delay)
                except KeyboardInterrupt:
                    logger.info(
                        "Received keyboard interrupt during retry wait, shutting down..."
                    )
                    break

                retry_delay = min(retry_delay * 1.5, 60)  # Exponential backoff, max 60s
                attempt += 1

    # Implementation of BaseIMClient abstract methods

    async def send_message(
        self,
        context: MessageContext,
        text: str,
        parse_mode: Optional[str] = None,  # Kept for interface compatibility, but ignored
        reply_to: Optional[str] = None,
    ) -> str:
        """Send a text message - BaseIMClient implementation"""
        bot = self.application.bot

        # Convert MessageContext to Telegram chat_id
        chat_id = int(context.channel_id)

        # Convert markdown to MarkdownV2 for better compatibility
        markdownv2_text = self._convert_to_markdownv2(text)
        kwargs = {"chat_id": chat_id, "text": markdownv2_text}

        # Always use MarkdownV2 since we converted with markdownify
        kwargs["parse_mode"] = "MarkdownV2"

        if reply_to or context.thread_id:
            kwargs["reply_to_message_id"] = int(reply_to or context.thread_id)

        try:
            message = await bot.send_message(**kwargs)
            return str(message.message_id)
        except TelegramError as e:
            logger.error(f"Error sending message: {e}")
            raise

    async def add_reaction(self, context: MessageContext, message_id: str, emoji: str) -> bool:
        """Add a reaction emoji to a Telegram message."""
        bot = self.application.bot

        reaction = (emoji or "").strip()
        if reaction in [":eyes:", "eyes", "👀"]:
            reaction = "👀"

        if not reaction:
            return False

        try:
            await bot.set_message_reaction(
                chat_id=int(context.channel_id),
                message_id=int(message_id),
                reaction=reaction,
            )
            return True
        except TelegramError as e:
            logger.debug(f"Failed to add Telegram reaction: {e}")
            return False
        except Exception as e:
            logger.debug(f"Failed to add Telegram reaction: {e}")
            return False

    async def remove_reaction(self, context: MessageContext, message_id: str, emoji: str) -> bool:
        """Remove a reaction emoji from a Telegram message."""
        bot = self.application.bot

        try:
            await bot.set_message_reaction(
                chat_id=int(context.channel_id),
                message_id=int(message_id),
                reaction=None,
            )
            return True
        except TelegramError as e:
            logger.debug(f"Failed to remove Telegram reaction: {e}")
            return False
        except Exception as e:
            logger.debug(f"Failed to remove Telegram reaction: {e}")
            return False

    async def send_message_with_buttons(
        self,
        context: MessageContext,
        text: str,
        keyboard: InlineKeyboard,
        parse_mode: Optional[str] = None,
    ) -> str:
        """Send a message with inline buttons - BaseIMClient implementation"""
        bot = self.application.bot

        # Convert markdown to MarkdownV2 for better compatibility
        markdownv2_text = self._convert_to_markdownv2(text)

        # Convert our generic keyboard to Telegram keyboard
        tg_keyboard = []
        for row in keyboard.buttons:
            tg_row = []
            for button in row:
                tg_button = TGInlineKeyboardButton(
                    text=button.text, callback_data=button.callback_data
                )
                tg_row.append(tg_button)
            tg_keyboard.append(tg_row)

        reply_markup = TGInlineKeyboardMarkup(tg_keyboard)

        chat_id = int(context.channel_id)

        try:
            message = await bot.send_message(
                chat_id=chat_id,
                text=markdownv2_text,
                parse_mode="MarkdownV2",
                reply_markup=reply_markup,
            )
            return str(message.message_id)
        except TelegramError as e:
            logger.error(f"Error sending message with buttons: {e}")
            raise

    async def edit_message(
        self,
        context: MessageContext,
        message_id: str,
        text: Optional[str] = None,
        keyboard: Optional[InlineKeyboard] = None,
        parse_mode: Optional[str] = None,
    ) -> bool:
        """Edit an existing message - BaseIMClient implementation"""
        bot = self.application.bot
        chat_id = int(context.channel_id)

        markdownv2_text = None
        if text is not None:
            markdownv2_text = (
                self._convert_to_markdownv2(text)
                if parse_mode in (None, "markdown")
                else text
            )

        try:
            if markdownv2_text is not None and keyboard:
                # Convert keyboard
                tg_keyboard = []
                for row in keyboard.buttons:
                    tg_row = []
                    for button in row:
                        tg_button = TGInlineKeyboardButton(
                            text=button.text, callback_data=button.callback_data
                        )
                        tg_row.append(tg_button)
                    tg_keyboard.append(tg_row)

                reply_markup = TGInlineKeyboardMarkup(tg_keyboard)

                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=int(message_id),
                    text=markdownv2_text,
                    parse_mode="MarkdownV2",
                    reply_markup=reply_markup,
                )
            elif markdownv2_text is not None:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=int(message_id),
                    text=markdownv2_text,
                    parse_mode="MarkdownV2",
                )
            elif keyboard:
                # Convert keyboard
                tg_keyboard = []
                for row in keyboard.buttons:
                    tg_row = []
                    for button in row:
                        tg_button = TGInlineKeyboardButton(
                            text=button.text, callback_data=button.callback_data
                        )
                        tg_row.append(tg_button)
                    tg_keyboard.append(tg_row)

                reply_markup = TGInlineKeyboardMarkup(tg_keyboard)

                await bot.edit_message_reply_markup(
                    chat_id=chat_id,
                    message_id=int(message_id),
                    reply_markup=reply_markup,
                )

            return True
        except TelegramError as e:
            logger.error(f"Error editing message: {e}")
            return False

    async def answer_callback(
        self, callback_id: str, text: Optional[str] = None, show_alert: bool = False
    ) -> bool:
        """Answer a callback query - BaseIMClient implementation"""
        # Get the stored callback query
        if callback_id in self._callback_queries:
            query = self._callback_queries[callback_id]
            try:
                await query.answer(text=text, show_alert=show_alert)
                # Clean up
                del self._callback_queries[callback_id]
                return True
            except TelegramError as e:
                logger.error(f"Error answering callback: {e}")
                return False
        return False

    def register_handlers(self):
        """Register platform-specific handlers - BaseIMClient implementation"""
        # This is already implemented as setup_handlers()
        self.setup_handlers()

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """Get information about a user - BaseIMClient implementation"""
        bot = self.application.bot

        try:
            user = await bot.get_chat(int(user_id))
            return {
                "id": str(user.id),
                "username": user.username,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "type": user.type,
            }
        except TelegramError as e:
            logger.error(f"Error getting user info: {e}")
            raise

    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        """Get information about a channel/chat - BaseIMClient implementation"""
        bot = self.application.bot

        try:
            chat = await bot.get_chat(int(channel_id))
            return {
                "id": str(chat.id),
                "title": chat.title,
                "type": chat.type,
                "username": chat.username,
            }
        except TelegramError as e:
            logger.error(f"Error getting channel info: {e}")
            raise

    def format_markdown(self, text: str) -> str:
        """Format markdown text for Telegram using telegramify_markdown
        
        Converts standard markdown to Telegram's MarkdownV2 format
        """
        return self._convert_to_markdownv2(text)

    def _is_authorized_chat(self, chat_id: int, chat_type: str) -> bool:
        """Check if a chat is authorized based on whitelist configuration"""
        target_chat_id = self.config.target_chat_id

        # If None/null, accept all chats
        if target_chat_id is None:
            return True

        # If list with IDs, check whitelist
        if isinstance(target_chat_id, list):
            return chat_id in target_chat_id

        # Should not reach here, but handle gracefully
        logger.warning(f"Unexpected target_chat_id type: {type(target_chat_id)}")
        return False

    async def _send_unauthorized_message(self, chat_id: int):
        """Send unauthorized access message to chat"""
        try:
            bot = self.application.bot
            await bot.send_message(
                chat_id=chat_id,
                text="❌ This chat is not authorized to use bot commands.",
            )
        except Exception as e:
            logger.error(f"Failed to send unauthorized message to {chat_id}: {e}")
