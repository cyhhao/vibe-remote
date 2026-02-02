import asyncio
import hashlib
import logging
import time
import aiohttp
from typing import Dict, Any, Optional, Callable, List
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.errors import SlackApiError
from markdown_to_mrkdwn import SlackMarkdownConverter

from .base import BaseIMClient, MessageContext, InlineKeyboard, InlineButton, FileAttachment
from config.v2_config import SlackConfig
from .formatters import SlackFormatter
from vibe.i18n import t as i18n_t

logger = logging.getLogger(__name__)

_UNSET = object()


class SlackBot(BaseIMClient):
    """Slack implementation of the IM client"""

    def __init__(self, config: SlackConfig):
        super().__init__(config)
        self.config = config
        self.web_client: Optional[AsyncWebClient] = None
        self.socket_client: Optional[SocketModeClient] = None

        # Initialize Slack formatter
        self.formatter = SlackFormatter()

        # Initialize markdown to mrkdwn converter
        self.markdown_converter = SlackMarkdownConverter()

        # Note: Thread handling now uses user's message timestamp directly

        # Store callback handlers
        self.command_handlers: Dict[str, Callable] = {}
        self.slash_command_handlers: Dict[str, Callable] = {}

        # Store trigger IDs for modal interactions
        self.trigger_ids: Dict[str, str] = {}

        # Settings manager for thread tracking (will be injected later)
        self.settings_manager = None
        # Controller reference for update button handling (will be injected later)
        self._controller = None
        self._recent_event_ids: Dict[str, float] = {}
        self._stop_event: Optional[asyncio.Event] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._on_ready: Optional[Callable] = None

    def set_settings_manager(self, settings_manager):
        """Set the settings manager for thread tracking"""
        self.settings_manager = settings_manager

    def set_controller(self, controller):
        """Set the controller reference for handling update button clicks"""
        self._controller = controller

    def _get_lang(self, channel_id: Optional[str] = None) -> str:
        """Get the language for a channel from settings."""
        if channel_id and self.settings_manager:
            return self.settings_manager.get_language(channel_id, default="en")
        return "en"

    def _t(self, key: str, channel_id: Optional[str] = None, **kwargs) -> str:
        """Translate a key for the given channel's language."""
        lang = self._get_lang(channel_id)
        return i18n_t(key, lang, **kwargs)

    def _is_duplicate_event(self, event_id: Optional[str]) -> bool:
        """Deduplicate Slack events using event_id with a short TTL."""
        if not event_id:
            return False
        now = time.time()
        expiry = now - 30  # retain for 30s
        for key in list(self._recent_event_ids.keys()):
            if self._recent_event_ids[key] < expiry:
                del self._recent_event_ids[key]
        if event_id in self._recent_event_ids:
            logger.debug(f"Ignoring duplicate Slack event_id {event_id}")
            return True
        self._recent_event_ids[event_id] = now
        return False

    def get_default_parse_mode(self) -> str:
        """Get the default parse mode for Slack"""
        return "markdown"

    def should_use_thread_for_reply(self) -> bool:
        """Slack uses threads for replies"""
        return True

    def _ensure_clients(self):
        """Ensure web and socket clients are initialized"""
        if self.web_client is None:
            self.web_client = AsyncWebClient(token=self.config.bot_token)

        if self.socket_client is None and self.config.app_token:
            self.socket_client = SocketModeClient(app_token=self.config.app_token, web_client=self.web_client)

    def _convert_markdown_to_slack_mrkdwn(self, text: str) -> str:
        """Convert standard markdown to Slack mrkdwn format using third-party library

        Uses markdown-to-mrkdwn library for comprehensive conversion including:
        - Bold: ** to *
        - Italic: * to _
        - Strikethrough: ~~ to ~
        - Code blocks: ``` preserved
        - Inline code: ` preserved
        - Links: [text](url) to <url|text>
        - Headers, lists, quotes, and more
        """
        try:
            # Use the third-party converter for comprehensive markdown to mrkdwn conversion
            converted_text = self.markdown_converter.convert(text)
            return converted_text
        except Exception as e:
            logger.warning(f"Error converting markdown to mrkdwn: {e}, using original text")
            # Fallback to original text if conversion fails
            return text

    async def send_message(
        self,
        context: MessageContext,
        text: str,
        parse_mode: Optional[str] = None,
        reply_to: Optional[str] = None,
    ) -> str:
        """Send a message to Slack"""
        self._ensure_clients()
        try:
            if not text:
                raise ValueError("Slack send_message requires non-empty text")
            # Convert markdown to Slack mrkdwn if needed
            if parse_mode == "markdown":
                text = self._convert_markdown_to_slack_mrkdwn(text)

            # Prepare message kwargs
            kwargs = {"channel": context.channel_id, "text": text}

            # Handle thread replies
            if context.thread_id:
                kwargs["thread_ts"] = context.thread_id
                # Optionally broadcast to channel
                if context.platform_specific and context.platform_specific.get("reply_broadcast"):
                    kwargs["reply_broadcast"] = True
            elif reply_to:
                # If reply_to is specified, use it as thread timestamp
                kwargs["thread_ts"] = reply_to

            # Handle formatting
            if parse_mode == "markdown":
                kwargs["mrkdwn"] = True

            # Workaround: ensure multi-line content is preserved. Slack sometimes collapses
            # rich_text rendering for bot messages; sending with blocks+mrkdwn forces line breaks.
            if "\n" in text and "blocks" not in kwargs and len(text) <= 3000:
                kwargs["blocks"] = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn" if parse_mode == "markdown" else "plain_text",
                            "text": text,
                        },
                    }
                ]

            # Send message
            response = await self.web_client.chat_postMessage(**kwargs)

            # Mark thread as active if we sent a message to a thread
            if self.settings_manager and (context.thread_id or reply_to):
                thread_ts = context.thread_id or reply_to
                self.settings_manager.mark_thread_active(context.user_id, context.channel_id, thread_ts)
                logger.debug(f"Marked thread {thread_ts} as active after bot message")

            return response["ts"]

        except SlackApiError as e:
            logger.error(f"Error sending Slack message: {e}")
            raise

    async def upload_markdown(
        self,
        context: MessageContext,
        title: str,
        content: str,
        filetype: str = "markdown",
    ) -> str:
        self._ensure_clients()
        data = content or ""
        result = await self.web_client.files_upload_v2(
            channel=context.channel_id,
            thread_ts=context.thread_id,
            filename=title,
            title=title,
            content=data,
        )
        file_id = result.get("file", {}).get("id")
        if not file_id:
            file_id = result.get("files", [{}])[0].get("id")
        return file_id or ""

    async def download_file(
        self,
        file_info: Dict[str, Any],
        max_bytes: int = 20 * 1024 * 1024,  # 20MB default limit
        timeout_seconds: int = 30,
    ) -> Optional[bytes]:
        """Download a Slack file using the private URL.

        Args:
            file_info: Slack file object containing url_private_download and other metadata
            max_bytes: Maximum file size to download (default 20MB)
            timeout_seconds: Request timeout in seconds (default 30s)

        Returns:
            File content as bytes, or None if download fails
        """
        url = file_info.get("url_private_download") or file_info.get("url_private")
        if not url:
            logger.warning(f"No download URL for file: {file_info.get('name')}")
            return None

        # Check file size before download if available
        file_size = file_info.get("size")
        if file_size and file_size > max_bytes:
            logger.warning(f"File too large ({file_size} bytes > {max_bytes}), skipping: {file_info.get('name')}")
            return None

        try:
            headers = {"Authorization": f"Bearer {self.config.bot_token}"}
            timeout = aiohttp.ClientTimeout(total=timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.error(f"Failed to download file: HTTP {response.status}")
                        return None

                    # Check content-length header
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > max_bytes:
                        logger.warning(f"File too large ({content_length} bytes), skipping: {file_info.get('name')}")
                        return None

                    # Stream download with size limit
                    chunks = []
                    total_size = 0
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        total_size += len(chunk)
                        if total_size > max_bytes:
                            logger.warning(f"File exceeds max size during download, aborting: {file_info.get('name')}")
                            return None
                        chunks.append(chunk)

                    return b"".join(chunks)

        except asyncio.TimeoutError:
            logger.error(f"Timeout downloading file: {file_info.get('name')}")
            return None
        except Exception as e:
            logger.error(f"Error downloading Slack file: {e}")
            return None

    def _extract_file_attachments(self, files: List[Dict[str, Any]]) -> List[FileAttachment]:
        """Convert Slack file objects to FileAttachment list.

        Args:
            files: List of Slack file objects from event

        Returns:
            List of FileAttachment objects
        """
        attachments = []
        for f in files:
            attachment = FileAttachment(
                name=f.get("name", "unknown"),
                mimetype=f.get("mimetype", "application/octet-stream"),
                url=f.get("url_private_download") or f.get("url_private"),
                size=f.get("size"),
            )
            attachments.append(attachment)
        return attachments

    async def add_reaction(self, context: MessageContext, message_id: str, emoji: str) -> bool:
        """Add a reaction emoji to a Slack message."""
        self._ensure_clients()

        name = (emoji or "").strip()
        if name.startswith(":") and name.endswith(":") and len(name) > 2:
            name = name[1:-1]
        if name in ["👀", "eyes", "eye"]:
            name = "eyes"

        if not name:
            return False

        try:
            await self.web_client.reactions_add(
                channel=context.channel_id,
                timestamp=message_id,
                name=name,
            )
            return True
        except SlackApiError as err:
            try:
                if getattr(err, "response", None) and err.response.get("error") == "already_reacted":
                    return True
            except Exception:
                pass

            error_code = None
            needed = None
            try:
                if getattr(err, "response", None):
                    error_code = err.response.get("error")
                    needed = err.response.get("needed")
            except Exception:
                pass

            # NOTE: reaction failures were previously DEBUG-only; surface at INFO/WARN for operability.
            if error_code in ["missing_scope", "not_in_channel", "channel_not_found"]:
                logger.warning(f"Slack reaction add failed: error={error_code}, needed={needed}")
            else:
                logger.info(f"Slack reaction add failed: {err}")
            return False
        except Exception as err:
            logger.debug(f"Failed to add Slack reaction: {err}")
            return False

    async def remove_reaction(self, context: MessageContext, message_id: str, emoji: str) -> bool:
        """Remove a reaction emoji from a Slack message."""
        self._ensure_clients()

        name = (emoji or "").strip()
        if name.startswith(":") and name.endswith(":") and len(name) > 2:
            name = name[1:-1]
        if name in ["👀", "eyes", "eye"]:
            name = "eyes"

        if not name:
            return False

        try:
            await self.web_client.reactions_remove(
                channel=context.channel_id,
                timestamp=message_id,
                name=name,
            )
            return True
        except SlackApiError as err:
            logger.debug(f"Failed to remove Slack reaction: {err}")
            return False
        except Exception as err:
            logger.debug(f"Failed to remove Slack reaction: {err}")
            return False

    async def send_message_with_buttons(
        self,
        context: MessageContext,
        text: str,
        keyboard: InlineKeyboard,
        parse_mode: Optional[str] = None,
    ) -> str:
        """Send a message with interactive buttons"""
        self._ensure_clients()
        try:
            # Default to markdown for Slack if not specified
            if not parse_mode:
                parse_mode = "markdown"

            # Convert markdown to Slack mrkdwn if needed
            if parse_mode == "markdown":
                text = self._convert_markdown_to_slack_mrkdwn(text)

            # Convert our generic keyboard to Slack blocks
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn" if parse_mode == "markdown" else "plain_text",
                        "text": text,
                        "verbatim": True,
                    },
                }
            ]

            # Add action blocks for buttons
            for row_idx, row in enumerate(keyboard.buttons):
                elements = []
                for button in row:
                    elements.append(
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": button.text},
                            "action_id": button.callback_data,
                            "value": button.callback_data,
                        }
                    )

                blocks.append(
                    {
                        "type": "actions",
                        "block_id": f"actions_{row_idx}",
                        "elements": elements,
                    }
                )

            # Prepare message kwargs
            kwargs = {
                "channel": context.channel_id,
                "blocks": blocks,
                "text": text,  # Fallback text
            }

            # Handle thread replies
            if context.thread_id:
                kwargs["thread_ts"] = context.thread_id

            response = await self.web_client.chat_postMessage(**kwargs)

            # Mark thread as active if we sent a message to a thread
            if self.settings_manager and context.thread_id:
                self.settings_manager.mark_thread_active(context.user_id, context.channel_id, context.thread_id)
                logger.debug(f"Marked thread {context.thread_id} as active after bot message with buttons")

            return response["ts"]

        except SlackApiError as e:
            logger.error(f"Error sending Slack message with buttons: {e}")
            raise

    async def edit_message(
        self,
        context: MessageContext,
        message_id: str,
        text: Optional[str] = None,
        keyboard: Optional[InlineKeyboard] = None,
        parse_mode: Optional[str] = None,
    ) -> bool:
        """Edit an existing Slack message"""
        self._ensure_clients()
        try:
            if text and parse_mode == "markdown":
                text = self._convert_markdown_to_slack_mrkdwn(text)

            kwargs = {"channel": context.channel_id, "ts": message_id}

            if text is not None:
                kwargs["text"] = text

            if keyboard:
                # Convert keyboard to blocks (similar to send_message_with_buttons)
                blocks = []
                if text:
                    blocks.append(
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn" if parse_mode == "markdown" else "plain_text",
                                "text": text,
                            },
                        }
                    )

                for row_idx, row in enumerate(keyboard.buttons):
                    elements = []
                    for button in row:
                        elements.append(
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": button.text},
                                "action_id": button.callback_data,
                                "value": button.callback_data,
                            }
                        )

                    blocks.append(
                        {
                            "type": "actions",
                            "block_id": f"actions_{row_idx}",
                            "elements": elements,
                        }
                    )

                kwargs["blocks"] = blocks

            await self.web_client.chat_update(**kwargs)
            return True

        except SlackApiError as e:
            logger.error(f"Error editing Slack message: {e}")
            return False

    async def remove_inline_keyboard(
        self,
        context: MessageContext,
        message_id: str,
        text: Optional[str] = None,
        parse_mode: Optional[str] = None,
    ) -> bool:
        """Remove interactive buttons from a Slack message."""
        self._ensure_clients()
        try:
            blocks = []
            fallback_text = text
            if fallback_text is not None and parse_mode == "markdown":
                fallback_text = self._convert_markdown_to_slack_mrkdwn(fallback_text)

            if fallback_text:
                blocks = [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn" if parse_mode == "markdown" else "plain_text",
                            "text": fallback_text,
                        },
                    }
                ]

            kwargs = {"channel": context.channel_id, "ts": message_id, "blocks": blocks}
            if fallback_text is not None:
                kwargs["text"] = fallback_text
            await self.web_client.chat_update(**kwargs)
            return True
        except SlackApiError as e:
            logger.error(f"Error removing Slack buttons: {e}")
            return False

    async def answer_callback(self, callback_id: str, text: Optional[str] = None, show_alert: bool = False) -> bool:
        """Answer a Slack interactive callback"""
        # Slack does not have a direct equivalent to answer_callback_query
        # Instead, we typically update the message or send an ephemeral message
        # This will be handled in the event processing
        return True

    def register_handlers(self):
        """Register Slack event handlers"""
        if not self.socket_client:
            logger.warning("Socket mode client not configured, skipping handler registration")
            return

        # Register socket mode request handler
        self.socket_client.socket_mode_request_listeners.append(self._handle_socket_mode_request)

    async def _handle_socket_mode_request(self, client: SocketModeClient, req: SocketModeRequest):
        """Handle incoming Socket Mode requests"""
        try:
            if req.type == "events_api":
                # Handle Events API events
                await self._handle_event(req.payload)
                # Acknowledge after handling events
                response = SocketModeResponse(envelope_id=req.envelope_id)
                await client.send_socket_mode_response(response)
            elif req.type == "slash_commands":
                # Handle slash commands
                await self._handle_slash_command(req.payload)
                # Acknowledge after handling slash commands
                response = SocketModeResponse(envelope_id=req.envelope_id)
                await client.send_socket_mode_response(response)
            elif req.type == "interactive":
                # For interactive components, acknowledge FIRST to avoid Slack timeout
                # This is important for long-running operations like updates
                response = SocketModeResponse(envelope_id=req.envelope_id)
                await client.send_socket_mode_response(response)
                # Then handle the interaction
                await self._handle_interactive(req.payload)
            else:
                # Unknown request type, still acknowledge
                response = SocketModeResponse(envelope_id=req.envelope_id)
                await client.send_socket_mode_response(response)

        except Exception as e:
            logger.error(f"Error handling socket mode request: {e}")
            # Still acknowledge even on error (if not already acknowledged)
            try:
                response = SocketModeResponse(envelope_id=req.envelope_id)
                await client.send_socket_mode_response(response)
            except Exception:
                pass  # Already acknowledged or connection issue

    async def _handle_event(self, payload: Dict[str, Any]):
        """Handle Events API events"""
        event = payload.get("event", {})
        event_type = event.get("type")
        event_id = payload.get("event_id")
        if self._is_duplicate_event(event_id):
            return

        if event_type == "message":
            # Ignore bot messages
            if event.get("bot_id"):
                return

            # Check for file attachments first
            slack_files = event.get("files", [])
            has_files = bool(slack_files)

            # Ignore most message subtypes (edited, deleted, joins, etc.)
            # But allow file-related subtypes when files are present
            event_subtype = event.get("subtype")
            file_subtypes = {"file_share", "file_comment", "file_mention"}
            if event_subtype:
                if not has_files and event_subtype not in file_subtypes:
                    logger.debug(f"Ignoring Slack message with subtype: {event_subtype}")
                    return

            channel_id = event.get("channel")

            # Check if this message contains a bot mention
            # If it does, skip processing as it will be handled by app_mention event
            text = (event.get("text") or "").strip()
            import re

            if re.search(r"<@[\w]+>", text):
                logger.info(f"Skipping message event with bot mention: '{text}'")
                return

            # Extract file attachments (slack_files already checked above)
            file_attachments = self._extract_file_attachments(slack_files) if slack_files else None

            # Ignore messages without user or without actual text/files
            user_id = event.get("user")
            if not user_id:
                logger.debug("Ignoring Slack message without user id")
                return
            if not text and not file_attachments:
                logger.debug("Ignoring Slack message with empty text and no files")
                return

            # Check if we require mention in channels (not DMs)
            # For threads: only respond if the bot is active in that thread
            is_thread_reply = event.get("thread_ts") is not None

            # Resolve effective require_mention: per-channel override or global default
            effective_require_mention = self.config.require_mention
            if self.settings_manager:
                effective_require_mention = self.settings_manager.get_require_mention(
                    channel_id, global_default=self.config.require_mention
                )

            if effective_require_mention and not channel_id.startswith("D"):
                # In channel main thread: require mention (silently ignore)
                if not is_thread_reply:
                    logger.debug(f"Ignoring non-mention message in channel: '{text}'")
                    return

                # In thread: check if bot is active in this thread
                if is_thread_reply:
                    thread_ts = event.get("thread_ts")
                    # If we have settings_manager, check if thread is active
                    if self.settings_manager:
                        if not self.settings_manager.is_thread_active(user_id, channel_id, thread_ts):
                            logger.debug(f"Ignoring message in inactive thread {thread_ts}: '{text}'")
                            return
                    else:
                        # Without settings_manager, fall back to ignoring non-mention in threads
                        logger.debug(f"No settings_manager, ignoring thread message: '{text}'")
                        return

            # Only check channel authorization for messages we're actually going to process
            if not await self._is_authorized_channel(channel_id):
                logger.info(f"Unauthorized message from channel: {channel_id}")
                await self._send_unauthorized_message(channel_id)
                return

            # Extract context
            # For Slack: if no thread_ts, use the message's own ts as thread_id (start of thread)
            thread_id = event.get("thread_ts") or event.get("ts")

            context = MessageContext(
                user_id=user_id,
                channel_id=channel_id,
                thread_id=thread_id,  # Always have a thread_id
                message_id=event.get("ts"),
                platform_specific={"team_id": payload.get("team_id"), "event": event},
                files=file_attachments,
            )

            # Handle slash commands in regular messages
            if text.startswith("/"):
                parts = text.split(maxsplit=1)
                command = parts[0][1:]  # Remove the /
                args = parts[1] if len(parts) > 1 else ""

                if command in self.on_command_callbacks:
                    handler = self.on_command_callbacks[command]
                    await handler(context, args)
                    return

            # Handle as regular message
            if self.on_message_callback:
                await self.on_message_callback(context, text)

        elif event_type == "app_mention":
            # Handle @mentions
            channel_id = event.get("channel")

            # Check if channel is authorized based on whitelist
            if not await self._is_authorized_channel(channel_id):
                logger.info(f"Unauthorized mention from channel: {channel_id}")
                await self._send_unauthorized_message(channel_id)
                return

            # For Slack: if no thread_ts, use the message's own ts as thread_id (start of thread)
            thread_id = event.get("thread_ts") or event.get("ts")

            # Extract file attachments if present
            slack_files = event.get("files", [])
            file_attachments = self._extract_file_attachments(slack_files) if slack_files else None

            context = MessageContext(
                user_id=event.get("user"),
                channel_id=channel_id,
                thread_id=thread_id,  # Always have a thread_id
                message_id=event.get("ts"),
                platform_specific={"team_id": payload.get("team_id"), "event": event},
                files=file_attachments,
            )

            # Mark thread as active when bot is @mentioned
            if self.settings_manager and thread_id:
                self.settings_manager.mark_thread_active(event.get("user"), channel_id, thread_id)
                logger.info(f"Marked thread {thread_id} as active due to @mention")

            # Remove the mention from the text
            text = event.get("text", "")
            import re

            text = re.sub(r"<@[\w]+>", "", text).strip()

            logger.info(f"App mention processed: original='{event.get('text')}', cleaned='{text}'")

            # Check if this is a command after mention
            if text.startswith("/"):
                parts = text.split(maxsplit=1)
                command = parts[0][1:]  # Remove the /
                args = parts[1] if len(parts) > 1 else ""

                logger.info(f"Command detected: '{command}', available: {list(self.on_command_callbacks.keys())}")

                if command in self.on_command_callbacks:
                    logger.info(f"Executing command handler for: {command}")
                    handler = self.on_command_callbacks[command]
                    await handler(context, args)
                    return
                else:
                    logger.warning(f"Command '{command}' not found in callbacks")

            # Handle as regular message
            logger.info(f"Handling as regular message: '{text}'")
            if self.on_message_callback:
                await self.on_message_callback(context, text)

    async def _handle_slash_command(self, payload: Dict[str, Any]):
        """Handle native Slack slash commands"""
        command = payload.get("command", "").lstrip("/")
        channel_id = payload.get("channel_id")

        # Check if channel is authorized based on whitelist
        if not await self._is_authorized_channel(channel_id):
            logger.info(f"Unauthorized slash command from channel: {channel_id}")
            # Send a response to user about unauthorized channel
            response_url = payload.get("response_url")
            if response_url:
                await self.send_slash_response(
                    response_url,
                    f"❌ {self._t('error.channelNotEnabled', channel_id)}",
                )
            return

        # Map Slack slash commands to internal commands
        # Only /start and /stop commands are exposed to users
        command_mapping = {"start": "start", "stop": "stop"}

        # Get the actual command name
        actual_command = command_mapping.get(command, command)

        # Create context for slash command
        context = MessageContext(
            user_id=payload.get("user_id"),
            channel_id=payload.get("channel_id"),
            platform_specific={
                "trigger_id": payload.get("trigger_id"),
                "response_url": payload.get("response_url"),
                "command": command,
                "text": payload.get("text"),
                "payload": payload,
            },
        )

        # Send immediate acknowledgment to Slack
        response_url = payload.get("response_url")

        # Try to handle as registered command
        if actual_command in self.on_command_callbacks:
            handler = self.on_command_callbacks[actual_command]

            # Send immediate "processing" response for long-running commands
            if response_url and actual_command not in [
                "start",
                "status",
                "clear",
                "cwd",
                "queue",
            ]:
                await self.send_slash_response(
                    response_url, f"⏳ {self._t('common.processing', channel_id, command=command)}"
                )

            await handler(context, payload.get("text", ""))
        elif actual_command in self.slash_command_handlers:
            handler = self.slash_command_handlers[actual_command]
            await handler(context, payload.get("text", ""))
        else:
            # Send response back to Slack for unknown command
            if response_url:
                await self.send_slash_response(
                    response_url,
                    f"❌ {self._t('error.unknownCommand', channel_id, command=command)}",
                )

    async def _handle_interactive(self, payload: Dict[str, Any]):
        """Handle interactive components (buttons, modal submissions, etc.)"""
        if payload.get("type") == "block_actions":
            # Handle button clicks / select changes
            user = payload.get("user", {})
            actions = payload.get("actions", [])
            view = payload.get("view", {})

            # Check for update button click (handled before channel authorization)
            for action in actions:
                if action.get("action_id") == "vibe_update_now":
                    from core.update_checker import handle_update_button_click

                    if hasattr(self, "_controller") and self._controller:
                        await handle_update_button_click(self._controller, payload)
                    return

            # In Slack modals, `channel` is often missing. We store the originating
            # channel_id in `view.private_metadata` when opening the modal.
            channel_id = payload.get("channel", {}).get("id") or payload.get("container", {}).get("channel_id")

            # For modal actions, try to extract channel_id from private_metadata JSON
            if not channel_id and isinstance(view, dict):
                private_metadata = view.get("private_metadata")
                if private_metadata:
                    try:
                        import json

                        metadata = json.loads(private_metadata)
                        channel_id = metadata.get("channel_id") if isinstance(metadata, dict) else private_metadata
                    except (json.JSONDecodeError, TypeError):
                        # Fallback: treat private_metadata as channel_id directly (legacy behavior)
                        channel_id = private_metadata

            # Check if channel is authorized for interactive components
            if not await self._is_authorized_channel(channel_id):
                logger.info(f"Unauthorized interactive action from channel: {channel_id}")
                try:
                    await self._send_unauthorized_message(channel_id)
                except Exception as e:
                    logger.debug("Failed to send unauthorized message to channel %s: %s", channel_id, e)
                return

            view = payload.get("view", {})
            for action in actions:
                action_type = action.get("type")
                if action_type == "button":
                    callback_data = action.get("action_id")

                    if self.on_callback_query_callback:
                        thread_id = (
                            payload.get("container", {}).get("thread_ts")
                            or payload.get("message", {}).get("thread_ts")
                            or payload.get("message", {}).get("ts")
                        )
                        # Create a context for the callback
                        context = MessageContext(
                            user_id=user.get("id"),
                            channel_id=channel_id,
                            thread_id=thread_id,
                            message_id=payload.get("message", {}).get("ts"),
                            platform_specific={
                                "trigger_id": payload.get("trigger_id"),
                                "response_url": payload.get("response_url"),
                                "action": action,
                                "payload": payload,
                            },
                        )

                        await self.on_callback_query_callback(context, callback_data)
                elif action_type in {"static_select", "external_select"}:
                    action_id = action.get("action_id")
                    # Trigger modal update for backend selection and all backend-specific selectors
                    routing_modal_actions = {
                        "backend_select",
                        "opencode_agent_select",
                        "opencode_model_select",
                        "claude_agent_select",
                        "claude_model_select",
                        "codex_model_select",
                    }
                    # Also check for prefixed action IDs (reasoning selects have unique suffixes)
                    should_update = (
                        action_id in routing_modal_actions
                        or (action_id and action_id.startswith("opencode_reasoning_select"))
                        or (action_id and action_id.startswith("codex_reasoning_select"))
                    )
                    if should_update:
                        if hasattr(self, "_on_routing_modal_update"):
                            channel_from_view = view.get("private_metadata")
                            await self._on_routing_modal_update(
                                user.get("id"),
                                channel_from_view or channel_id,
                                view,
                                action,
                            )
                elif action_type == "plain_text_input":
                    action_id = action.get("action_id")
                    # Handle manual_input in resume_session_modal - show agent_block when user types
                    if action_id == "manual_input" and view.get("callback_id") == "resume_session_modal":
                        await self._handle_resume_modal_manual_input(view, action)

        elif payload.get("type") == "view_submission":
            # Handle modal submissions asynchronously to avoid Slack timeouts
            asyncio.create_task(self._handle_view_submission(payload))
            return

    async def _handle_view_submission(self, payload: Dict[str, Any]):
        """Handle modal dialog submissions"""
        view = payload.get("view", {})
        callback_id = view.get("callback_id")

        if callback_id == "settings_modal":
            # Handle settings modal submission
            user_id = payload.get("user", {}).get("id")
            values = view.get("state", {}).get("values", {})

            # Extract selected show message types
            show_types_data = values.get("show_message_types", {}).get("show_types_select", {})
            selected_options = show_types_data.get("selected_options", [])

            # Get the values from selected options
            show_types = [opt.get("value") for opt in selected_options]

            # Extract require_mention setting
            require_mention_data = values.get("require_mention_block", {}).get("require_mention_select", {})
            require_mention_value = require_mention_data.get("selected_option", {}).get("value")
            # Convert to Optional[bool]: "__default__" -> None, "true" -> True, "false" -> False
            if require_mention_value == "__default__":
                require_mention = None
            elif require_mention_value == "true":
                require_mention = True
            elif require_mention_value == "false":
                require_mention = False
            else:
                require_mention = None

            # Extract language setting
            language_data = values.get("language_block", {}).get("language_select", {})
            language_value = language_data.get("selected_option", {}).get("value")
            # Convert to Optional[str]: "__default__" -> None, else use the value
            language = None if language_value == "__default__" else language_value

            # Get channel_id from the view's private_metadata if available
            channel_id = view.get("private_metadata")

            # Update settings - need access to settings manager
            if hasattr(self, "_on_settings_update"):
                await self._on_settings_update(user_id, show_types, channel_id, require_mention, language)

        elif callback_id == "change_cwd_modal":
            # Handle change CWD modal submission
            user_id = payload.get("user", {}).get("id")
            values = view.get("state", {}).get("values", {})

            # Extract new CWD path
            new_cwd_data = values.get("new_cwd_block", {}).get("new_cwd_input", {})
            new_cwd = new_cwd_data.get("value", "")

            # Get channel_id from private_metadata
            channel_id = view.get("private_metadata")

            # Update CWD - need access to controller or settings manager
            if hasattr(self, "_on_change_cwd"):
                await self._on_change_cwd(user_id, new_cwd, channel_id)

            # Send success message to the user (via DM or channel)
            # We need to find the right channel to send the message
            # For now, we'll rely on the controller to handle this

        elif callback_id == "resume_session_modal":
            user_id = payload.get("user", {}).get("id")
            values = view.get("state", {}).get("values", {})

            agent_data = values.get("agent_block", {}).get("agent_select", {})
            agent = agent_data.get("selected_option", {}).get("value")

            manual_data = values.get("manual_block", {}).get("manual_input", {})
            manual_session = (manual_data.get("value") or "").strip()

            session_data = values.get("session_block", {}).get("session_select", {}) if values else {}
            selected_option = session_data.get("selected_option") or {}
            selected_value = selected_option.get("value") if isinstance(selected_option, dict) else None
            selected_session = None
            selected_agent = None
            if selected_value and "|" in selected_value:
                selected_agent, selected_session = selected_value.split("|", 1)

            metadata_raw = view.get("private_metadata")
            channel_id = None
            thread_id = None
            host_message_ts = None
            default_agent = None
            try:
                import json

                md = json.loads(metadata_raw) if metadata_raw else {}
                channel_id = md.get("channel_id")
                thread_id = md.get("thread_id")
                host_message_ts = md.get("host_message_ts")
                default_agent = md.get("default_agent")
            except Exception:
                pass

            # Manual input takes precedence and should respect the manual agent selector.
            if manual_session:
                chosen_session = manual_session
                # When manually entering session ID, use selected agent or default
                chosen_agent = agent or selected_agent or default_agent
            else:
                chosen_session = selected_session
                chosen_agent = selected_agent or agent

            if hasattr(self, "_on_resume_session"):
                await self._on_resume_session(
                    user_id, channel_id, thread_id, chosen_agent, chosen_session, host_message_ts
                )

        elif callback_id == "opencode_question_modal" or callback_id == "claude_question_modal":
            # Generic question modal handling for both OpenCode and Claude
            user_id = payload.get("user", {}).get("id")
            values = view.get("state", {}).get("values", {})
            metadata_raw = view.get("private_metadata")

            try:
                import json

                metadata = json.loads(metadata_raw) if metadata_raw else {}
            except Exception:
                metadata = {}

            channel_id = metadata.get("channel_id")
            thread_id = metadata.get("thread_id")
            # Get callback_prefix from metadata, fallback to deriving from callback_id
            callback_prefix = metadata.get("callback_prefix")
            if not callback_prefix:
                callback_prefix = callback_id.replace("_modal", "")

            answers = []
            q_count = int(metadata.get("question_count") or 1)
            for idx in range(q_count):
                block_id = f"q{idx}"
                action_id = "select"
                data = values.get(block_id, {}).get(action_id, {})
                selected_options = data.get("selected_options")
                if isinstance(selected_options, list):
                    answers.append([opt.get("value") for opt in selected_options if opt.get("value")])
                else:
                    selected = data.get("selected_option")
                    if selected and selected.get("value") is not None:
                        answers.append([str(selected.get("value"))])
                    else:
                        answers.append([])

            if self.on_callback_query_callback:
                context = MessageContext(
                    user_id=user_id,
                    channel_id=str(channel_id) if channel_id else "",
                    thread_id=str(thread_id) if thread_id else None,
                    platform_specific={"payload": payload},
                )
                # Use callback_prefix to route to correct agent
                await self.on_callback_query_callback(
                    context,
                    f"{callback_prefix}:modal:" + json.dumps({"answers": answers}),
                )

        elif callback_id == "routing_modal":
            # Handle routing modal submission
            user_id = payload.get("user", {}).get("id")
            values = view.get("state", {}).get("values", {})
            channel_id = view.get("private_metadata")

            # Extract backend
            backend_data = values.get("backend_block", {}).get("backend_select", {})
            backend = backend_data.get("selected_option", {}).get("value")

            # Extract OpenCode agent (optional)
            oc_agent_data = values.get("opencode_agent_block", {}).get("opencode_agent_select", {})
            oc_agent = oc_agent_data.get("selected_option", {}).get("value")
            if oc_agent == "__default__":
                oc_agent = None

            # Extract OpenCode model (optional)
            oc_model_data = values.get("opencode_model_block", {}).get("opencode_model_select", {})
            oc_model = oc_model_data.get("selected_option", {}).get("value")
            if oc_model == "__default__":
                oc_model = None

            # Extract OpenCode reasoning effort (optional)
            oc_reasoning = None
            reasoning_block = values.get("opencode_reasoning_block", {})
            if isinstance(reasoning_block, dict):
                for action_id, action_data in reasoning_block.items():
                    if (
                        isinstance(action_id, str)
                        and action_id.startswith("opencode_reasoning_select")
                        and isinstance(action_data, dict)
                    ):
                        oc_reasoning = action_data.get("selected_option", {}).get("value")
                        break
            if oc_reasoning == "__default__":
                oc_reasoning = None

            # Extract require_mention (optional)
            require_mention_data = values.get("require_mention_block", {}).get("require_mention_select", {})
            require_mention_value = require_mention_data.get("selected_option", {}).get("value")
            # Convert to Optional[bool]: "__default__" -> None, "true" -> True, "false" -> False
            if require_mention_value == "__default__":
                require_mention = None
            elif require_mention_value == "true":
                require_mention = True
            elif require_mention_value == "false":
                require_mention = False
            else:
                require_mention = None

            # Extract Claude agent (optional)
            claude_agent_data = values.get("claude_agent_block", {}).get("claude_agent_select", {})
            claude_agent = claude_agent_data.get("selected_option", {}).get("value")
            if claude_agent == "__default__":
                claude_agent = None

            # Extract Claude model (optional)
            claude_model_data = values.get("claude_model_block", {}).get("claude_model_select", {})
            claude_model = claude_model_data.get("selected_option", {}).get("value")
            if claude_model == "__default__":
                claude_model = None

            # Extract Codex model (optional)
            codex_model_data = values.get("codex_model_block", {}).get("codex_model_select", {})
            codex_model = codex_model_data.get("selected_option", {}).get("value")
            if codex_model == "__default__":
                codex_model = None

            # Extract Codex reasoning effort (optional)
            codex_reasoning = None
            codex_reasoning_block = values.get("codex_reasoning_block", {})
            if isinstance(codex_reasoning_block, dict):
                for action_id, action_data in codex_reasoning_block.items():
                    if (
                        isinstance(action_id, str)
                        and action_id.startswith("codex_reasoning_select")
                        and isinstance(action_data, dict)
                    ):
                        codex_reasoning = action_data.get("selected_option", {}).get("value")
                        break
            if codex_reasoning == "__default__":
                codex_reasoning = None

            # Update routing via callback
            if hasattr(self, "_on_routing_update"):
                await self._on_routing_update(
                    user_id,
                    channel_id,
                    backend,
                    oc_agent,
                    oc_model,
                    oc_reasoning,
                    claude_agent,
                    claude_model,
                    codex_model,
                    codex_reasoning,
                )

    def run(self):
        """Run the Slack bot"""
        if self.config.app_token:
            # Socket Mode
            logger.info("Starting Slack bot in Socket Mode...")

            async def start():
                self._ensure_clients()
                self.register_handlers()
                self._loop = asyncio.get_running_loop()
                self._stop_event = asyncio.Event()
                await self.socket_client.connect()
                # Call on_ready callback after successful connection
                if self._on_ready:
                    try:
                        await self._on_ready()
                    except Exception as e:
                        logger.error(f"on_ready callback failed: {e}", exc_info=True)
                await self._stop_event.wait()
                await self._async_close()

            asyncio.run(start())
        else:
            # Web API only mode (for development/testing)
            logger.warning("No app token provided, running in Web API only mode")

            async def start():
                self._ensure_clients()
                self._loop = asyncio.get_running_loop()
                self._stop_event = asyncio.Event()
                # Call on_ready callback (even in Web API only mode)
                if self._on_ready:
                    try:
                        await self._on_ready()
                    except Exception as e:
                        logger.error(f"on_ready callback failed: {e}", exc_info=True)
                await self._stop_event.wait()
                await self._async_close()

            try:
                asyncio.run(start())
            except KeyboardInterrupt:
                logger.info("Shutting down...")

    def stop(self) -> None:
        if self._stop_event is None:
            return
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop_event.set)
        else:
            self._stop_event.set()

    async def shutdown(self) -> None:
        """Best-effort async shutdown for Slack clients."""
        if self._stop_event is not None:
            self._stop_event.set()

        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        if self._loop and self._loop.is_running() and self._loop is not current_loop:
            try:
                future = asyncio.run_coroutine_threadsafe(self._async_close(), self._loop)
                future.result(timeout=5)
            except Exception as exc:
                logger.debug(f"Slack shutdown dispatch failed: {exc}")
            return

        await self._async_close()

    async def _async_close(self) -> None:
        if self.socket_client is not None:
            try:
                disconnect = getattr(self.socket_client, "disconnect", None)
                if callable(disconnect):
                    result = disconnect()
                    if asyncio.iscoroutine(result):
                        await result
            except Exception as exc:
                logger.debug(f"Socket mode disconnect failed: {exc}")
            try:
                close = getattr(self.socket_client, "close", None)
                if callable(close):
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
            except Exception as exc:
                logger.debug(f"Socket mode close failed: {exc}")

        if self.web_client is not None:
            try:
                await self.web_client.close()
            except Exception as exc:
                logger.debug(f"Slack web client close failed: {exc}")

    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """Get information about a Slack user"""
        self._ensure_clients()
        try:
            response = await self.web_client.users_info(user=user_id)
            user = response["user"]
            return {
                "id": user["id"],
                "name": user.get("name"),
                "real_name": user.get("real_name"),
                "display_name": user.get("profile", {}).get("display_name"),
                "email": user.get("profile", {}).get("email"),
                "is_bot": user.get("is_bot", False),
            }
        except SlackApiError as e:
            logger.error(f"Error getting user info: {e}")
            raise

    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        """Get information about a Slack channel"""
        self._ensure_clients()
        try:
            response = await self.web_client.conversations_info(channel=channel_id)
            channel = response["channel"]
            return {
                "id": channel["id"],
                "name": channel.get("name"),
                "is_private": channel.get("is_private", False),
                "is_im": channel.get("is_im", False),
                "is_channel": channel.get("is_channel", False),
                "topic": channel.get("topic", {}).get("value"),
                "purpose": channel.get("purpose", {}).get("value"),
            }
        except SlackApiError as e:
            logger.error(f"Error getting channel info: {e}")
            raise

    def format_markdown(self, text: str) -> str:
        """Format markdown text for Slack mrkdwn format

        Slack uses single asterisks for bold and different formatting rules
        """
        # Convert double asterisks to single for bold
        formatted = text.replace("**", "*")

        # Convert inline code blocks (backticks work the same)
        # Lists work similarly
        # Links work similarly [text](url) -> <url|text>
        # But we'll keep simple for now - just handle bold

        return formatted

    async def open_settings_modal(
        self,
        trigger_id: str,
        user_settings: Any,
        message_types: list,
        display_names: dict,
        channel_id: str = None,
        current_require_mention: object = None,  # None=default, True, False
        global_require_mention: bool = False,
        current_language: str = None,  # Current language setting
    ):
        """Open a modal dialog for settings"""
        self._ensure_clients()

        # Get translations for the channel's language
        t = lambda key, **kwargs: self._t(key, channel_id, **kwargs)

        # Create options for the multi-select menu
        options = []
        selected_options = []

        for msg_type in message_types:
            display_name = display_names.get(msg_type, msg_type)
            option = {
                "text": {"type": "plain_text", "text": display_name, "emoji": True},
                "value": msg_type,
                "description": {
                    "type": "plain_text",
                    "text": self._get_message_type_description(msg_type, channel_id),
                    "emoji": True,
                },
            }
            options.append(option)

            # If this type is shown, add THE SAME option object to selected options
            if msg_type in user_settings.show_message_types:
                selected_options.append(option)  # Same object reference!

        logger.info(f"Creating modal with {len(options)} options, {len(selected_options)} selected")
        logger.info(f"Show types: {user_settings.show_message_types}")

        # Debug: Log the actual data being sent
        import json

        logger.info(f"Options: {json.dumps(options, indent=2)}")
        logger.info(f"Selected options: {json.dumps(selected_options, indent=2)}")

        # Create the multi-select element
        multi_select_element = {
            "type": "multi_static_select",
            "placeholder": {
                "type": "plain_text",
                "text": t("modal.settings.showMessageTypesPlaceholder"),
                "emoji": True,
            },
            "options": options,
            "action_id": "show_types_select",
        }

        # Only add initial_options if there are selected options
        if selected_options:
            multi_select_element["initial_options"] = selected_options

        # Build require_mention selector
        global_mention_label = "On" if global_require_mention else "Off"
        require_mention_options = [
            {
                "text": {"type": "plain_text", "text": t("modal.settings.optionDefault", status=global_mention_label)},
                "value": "__default__",
            },
            {
                "text": {"type": "plain_text", "text": t("modal.settings.optionRequireMention")},
                "value": "true",
            },
            {
                "text": {"type": "plain_text", "text": t("modal.settings.optionDontRequireMention")},
                "value": "false",
            },
        ]

        # Determine initial option for require_mention
        initial_require_mention = require_mention_options[0]  # Default
        if current_require_mention is not None:
            target_value = "true" if current_require_mention else "false"
            for opt in require_mention_options:
                if opt["value"] == target_value:
                    initial_require_mention = opt
                    break

        require_mention_select = {
            "type": "static_select",
            "action_id": "require_mention_select",
            "placeholder": {"type": "plain_text", "text": t("modal.settings.selectMentionBehavior")},
            "options": require_mention_options,
            "initial_option": initial_require_mention,
        }

        # Build language selector
        language_options = [
            {"text": {"type": "plain_text", "text": t("language.systemDefault")}, "value": "__default__"},
            {"text": {"type": "plain_text", "text": "English"}, "value": "en"},
            {"text": {"type": "plain_text", "text": "中文"}, "value": "zh"},
        ]

        # Determine initial option for language
        initial_language = language_options[0]  # System default
        if current_language:
            for opt in language_options:
                if opt["value"] == current_language:
                    initial_language = opt
                    break

        language_select = {
            "type": "static_select",
            "action_id": "language_select",
            "placeholder": {"type": "plain_text", "text": t("modal.settings.language")},
            "options": language_options,
            "initial_option": initial_language,
        }

        # Create the modal view
        view = {
            "type": "modal",
            "callback_id": "settings_modal",
            "private_metadata": channel_id or "",  # Store channel_id for later use
            "title": {"type": "plain_text", "text": t("modal.settings.title"), "emoji": True},
            "submit": {"type": "plain_text", "text": t("common.save"), "emoji": True},
            "close": {"type": "plain_text", "text": t("common.cancel"), "emoji": True},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "language_block",
                    "element": language_select,
                    "label": {
                        "type": "plain_text",
                        "text": t("modal.settings.language"),
                        "emoji": True,
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"_{t('modal.settings.languageHint')}_",
                        }
                    ],
                },
                {"type": "divider"},
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": t("modal.settings.channelBehavior"),
                        "emoji": True,
                    },
                },
                {
                    "type": "input",
                    "block_id": "require_mention_block",
                    "element": require_mention_select,
                    "label": {
                        "type": "plain_text",
                        "text": t("modal.settings.requireMention"),
                        "emoji": True,
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"_{t('modal.settings.requireMentionHint')}_",
                        }
                    ],
                },
                {"type": "divider"},
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": t("modal.settings.messageVisibility"),
                        "emoji": True,
                    },
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": t("modal.settings.messageVisibilityDesc"),
                    },
                },
                {
                    "type": "input",
                    "block_id": "show_message_types",
                    "element": multi_select_element,
                    "label": {
                        "type": "plain_text",
                        "text": t("modal.settings.showMessageTypes"),
                        "emoji": True,
                    },
                    "optional": True,
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"_💡 {t('modal.settings.tip')}_",
                        }
                    ],
                },
            ],
        }

        try:
            await self.web_client.views_open(trigger_id=trigger_id, view=view)
        except SlackApiError as e:
            logger.error(f"Error opening modal: {e}")
            raise

    def _get_message_type_description(self, msg_type: str, channel_id: str = None) -> str:
        """Get description for a message type"""
        key = f"messageType.{msg_type}Desc"
        return self._t(key, channel_id)

    async def open_change_cwd_modal(self, trigger_id: str, current_cwd: str, channel_id: str = None):
        """Open a modal dialog for changing working directory"""
        self._ensure_clients()

        # Get translations for the channel's language
        t = lambda key, **kwargs: self._t(key, channel_id, **kwargs)

        # Create the modal view
        view = {
            "type": "modal",
            "callback_id": "change_cwd_modal",
            "private_metadata": channel_id or "",  # Store channel_id for later use
            "title": {
                "type": "plain_text",
                "text": t("modal.cwd.title"),
                "emoji": True,
            },
            "submit": {"type": "plain_text", "text": t("common.change"), "emoji": True},
            "close": {"type": "plain_text", "text": t("common.cancel"), "emoji": True},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"{t('modal.cwd.current')}\n`{current_cwd}`",
                    },
                },
                {"type": "divider"},
                {
                    "type": "input",
                    "block_id": "new_cwd_block",
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "new_cwd_input",
                        "placeholder": {
                            "type": "plain_text",
                            "text": t("modal.cwd.placeholder"),
                            "emoji": True,
                        },
                        "initial_value": current_cwd,
                    },
                    "label": {
                        "type": "plain_text",
                        "text": t("modal.cwd.new"),
                        "emoji": True,
                    },
                    "hint": {
                        "type": "plain_text",
                        "text": t("modal.cwd.hint"),
                        "emoji": True,
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"💡 _{t('modal.cwd.tip')}_",
                        }
                    ],
                },
            ],
        }

        try:
            await self.web_client.views_open(trigger_id=trigger_id, view=view)
        except SlackApiError as e:
            logger.error(f"Error opening change CWD modal: {e}")
            raise

    async def open_resume_session_modal(
        self,
        trigger_id: str,
        sessions_by_agent: Dict[str, Dict[str, str]],
        channel_id: Optional[str],
        thread_id: Optional[str],
        host_message_ts: Optional[str],
    ):
        """Open a modal to let users select or input a session to resume."""
        self._ensure_clients()

        # Build agent options limited to enabled backends when available
        common_agents = ["claude", "codex", "opencode"]
        registered_backends = None
        if getattr(self, "_controller", None) and getattr(self._controller, "agent_service", None):
            registered_backends = list(self._controller.agent_service.agents.keys())
        allowed_agents = set(registered_backends) if registered_backends else set(common_agents)
        agent_keys = allowed_agents
        agent_options = []
        for agent in sorted(agent_keys):
            agent_options.append(
                {
                    "text": {"type": "plain_text", "text": agent.capitalize(), "emoji": True},
                    "value": agent,
                }
            )

        session_option_groups = []
        total_session_options = 0
        max_session_options = 100
        for agent, mapping in sessions_by_agent.items():
            if agent not in allowed_agents:
                continue
            if not mapping:
                continue
            options = []
            for thread, session_id in mapping.items():
                if total_session_options >= max_session_options:
                    break
                thread_label = thread.replace("slack_", "", 1) if thread.startswith("slack_") else thread
                truncated = session_id[:60]
                desc = f"thread {thread_label}"
                options.append(
                    {
                        "text": {"type": "plain_text", "text": truncated, "emoji": True},
                        "value": f"{agent}|{session_id}",
                        "description": {"type": "plain_text", "text": desc[:75], "emoji": True},
                    }
                )
                total_session_options += 1
            if options:
                session_option_groups.append(
                    {
                        "label": {"type": "plain_text", "text": agent.capitalize(), "emoji": True},
                        "options": options,
                    }
                )
            if total_session_options >= max_session_options:
                break

        blocks: list = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Select a saved session or paste a session ID to resume work in this thread.",
                },
            },
        ]

        if session_option_groups:
            blocks.append(
                {
                    "type": "input",
                    "block_id": "session_block",
                    "optional": True,
                    "label": {"type": "plain_text", "text": "Pick an existing session", "emoji": True},
                    "element": {
                        "type": "static_select",
                        "action_id": "session_select",
                        "option_groups": session_option_groups,
                        "placeholder": {"type": "plain_text", "text": "Select a session", "emoji": True},
                    },
                }
            )
            if total_session_options >= max_session_options:
                blocks.append(
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": "_Showing the first 100 saved sessions. Paste a session ID below for others._",
                            }
                        ],
                    }
                )
        else:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "_No stored sessions found. Paste a session ID below to resume._",
                        }
                    ],
                }
            )

        blocks.append(
            {
                "type": "input",
                "block_id": "manual_block",
                "optional": True,
                "label": {"type": "plain_text", "text": "Or paste a session ID", "emoji": True},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "manual_input",
                    "placeholder": {"type": "plain_text", "text": "e.g., ses_12345", "emoji": True},
                    "dispatch_action_config": {
                        "trigger_actions_on": ["on_character_entered"],
                    },
                },
                "dispatch_action": True,
            }
        )

        # Agent backend selector is NOT included initially.
        # It will be dynamically added when user types in the manual session ID field.

        metadata = {
            "channel_id": channel_id,
            "thread_id": thread_id,
            "host_message_ts": host_message_ts,
            "default_agent": agent_options[0]["value"] if agent_options else "opencode",
            "agent_options": agent_options,  # Store for dynamic update
        }
        import json

        view = {
            "type": "modal",
            "callback_id": "resume_session_modal",
            "private_metadata": json.dumps(metadata),
            "title": {"type": "plain_text", "text": "Resume Session", "emoji": True},
            "submit": {"type": "plain_text", "text": "Resume", "emoji": True},
            "close": {"type": "plain_text", "text": "Cancel", "emoji": True},
            "blocks": blocks,
        }

        try:
            await self.web_client.views_open(trigger_id=trigger_id, view=view)
        except SlackApiError as e:
            logger.error(f"Error opening resume modal: {e}")
            raise

    async def _handle_resume_modal_manual_input(self, view: Dict[str, Any], action: Dict[str, Any]):
        """Handle manual_input changes in resume_session_modal - dynamically show/hide agent_block."""
        import json

        self._ensure_clients()

        view_id = view.get("id")
        blocks = view.get("blocks", [])
        metadata_raw = view.get("private_metadata", "{}")

        try:
            metadata = json.loads(metadata_raw)
        except Exception:
            metadata = {}

        agent_options = metadata.get("agent_options", [])
        if not agent_options:
            return

        # Check if agent_block already exists
        has_agent_block = any(b.get("block_id") == "agent_block" for b in blocks)

        # Get current input value
        input_value = (action.get("value") or "").strip()

        # Determine if we need to update
        should_show_agent = bool(input_value)

        if should_show_agent and has_agent_block:
            # Already showing, no update needed
            return
        if not should_show_agent and not has_agent_block:
            # Already hidden, no update needed
            return

        # Build updated blocks
        new_blocks = [b for b in blocks if b.get("block_id") != "agent_block"]

        if should_show_agent:
            # Add agent_block at the end
            new_blocks.append(
                {
                    "type": "input",
                    "block_id": "agent_block",
                    "label": {"type": "plain_text", "text": "Agent backend", "emoji": True},
                    "element": {
                        "type": "static_select",
                        "action_id": "agent_select",
                        "options": agent_options,
                        "placeholder": {"type": "plain_text", "text": "Select agent backend", "emoji": True},
                    },
                }
            )

        updated_view = {
            "type": "modal",
            "callback_id": "resume_session_modal",
            "private_metadata": metadata_raw,
            "title": {"type": "plain_text", "text": "Resume Session", "emoji": True},
            "submit": {"type": "plain_text", "text": "Resume", "emoji": True},
            "close": {"type": "plain_text", "text": "Cancel", "emoji": True},
            "blocks": new_blocks,
        }

        try:
            await self.web_client.views_update(view_id=view_id, view=updated_view)
        except SlackApiError as e:
            logger.debug(f"Failed to update resume modal: {e}")

    def _get_default_opencode_agent_name(self, opencode_agents: list) -> Optional[str]:
        """Resolve the default OpenCode agent name."""
        for agent in opencode_agents:
            name = agent.get("name")
            if name == "build":
                return name
        for agent in opencode_agents:
            name = agent.get("name")
            if name:
                return name
        return None

    def _resolve_opencode_default_model(
        self,
        opencode_default_config: dict,
        opencode_agents: list,
        selected_agent: Optional[str],
    ) -> Optional[str]:
        """Resolve the default model for a selected OpenCode agent."""
        agent_name = selected_agent or self._get_default_opencode_agent_name(opencode_agents)
        if isinstance(opencode_default_config, dict):
            agents_config = opencode_default_config.get("agent", {})
            if isinstance(agents_config, dict) and agent_name:
                agent_config = agents_config.get(agent_name, {})
                if isinstance(agent_config, dict):
                    model = agent_config.get("model")
                    if isinstance(model, str) and model:
                        return model
            model = opencode_default_config.get("model")
            if isinstance(model, str) and model:
                return model
        return None

    def _build_routing_modal_view(
        self,
        channel_id: str,
        registered_backends: list,
        current_backend: str,
        current_routing,
        opencode_agents: list,
        opencode_models: dict,
        opencode_default_config: dict,
        claude_agents: list = None,
        claude_models: list = None,
        codex_models: list = None,
        selected_backend: object = _UNSET,
        selected_opencode_agent: object = _UNSET,
        selected_opencode_model: object = _UNSET,
        selected_opencode_reasoning: object = _UNSET,
        selected_claude_agent: object = _UNSET,
        selected_claude_model: object = _UNSET,
        selected_codex_model: object = _UNSET,
        selected_codex_reasoning: object = _UNSET,
    ) -> dict:
        """Build modal view for agent/model routing settings."""
        # Build backend options
        backend_display_names = {
            "claude": "ClaudeCode",
            "codex": "Codex",
            "opencode": "OpenCode",
        }
        backend_options = []
        for backend in registered_backends:
            display_name = backend_display_names.get(backend, backend.capitalize())
            backend_options.append(
                {
                    "text": {"type": "plain_text", "text": display_name},
                    "value": backend,
                }
            )

        # Find initial backend option
        selected_backend_value = current_backend if selected_backend is _UNSET else selected_backend
        initial_backend = None
        for option in backend_options:
            if option["value"] == selected_backend_value:
                initial_backend = option
                break
        if initial_backend is None and backend_options:
            initial_backend = backend_options[0]

        backend_select = {
            "type": "static_select",
            "action_id": "backend_select",
            "placeholder": {"type": "plain_text", "text": "Select backend"},
            "options": backend_options,
            "initial_option": initial_backend,
        }

        # Build modal blocks
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Current Backend:* {backend_display_names.get(current_backend, current_backend)}",
                },
            },
            {"type": "divider"},
            {
                "type": "input",
                "block_id": "backend_block",
                "dispatch_action": True,
                "element": backend_select,
                "label": {"type": "plain_text", "text": "Backend"},
            },
        ]

        # Determine effective backend for showing backend-specific options
        effective_backend = selected_backend_value or current_backend or "opencode"

        # OpenCode-specific options (only if opencode is selected)
        if effective_backend == "opencode" and "opencode" in registered_backends:
            # Get current opencode settings
            if selected_opencode_agent is _UNSET:
                current_oc_agent = current_routing.opencode_agent if current_routing else None
            else:
                current_oc_agent = selected_opencode_agent

            if selected_opencode_model is _UNSET:
                current_oc_model = current_routing.opencode_model if current_routing else None
            else:
                current_oc_model = selected_opencode_model

            if selected_opencode_reasoning is _UNSET:
                current_oc_reasoning = current_routing.opencode_reasoning_effort if current_routing else None
            else:
                current_oc_reasoning = selected_opencode_reasoning

            # Determine default agent/model from OpenCode config
            default_model_str = self._resolve_opencode_default_model(
                opencode_default_config, opencode_agents, current_oc_agent
            )

            # Build agent options
            agent_options = [{"text": {"type": "plain_text", "text": "(Default)"}, "value": "__default__"}]
            for agent in opencode_agents:
                agent_name = agent.get("name", "")
                if agent_name:
                    agent_options.append(
                        {
                            "text": {"type": "plain_text", "text": agent_name},
                            "value": agent_name,
                        }
                    )

            # Find initial agent
            initial_agent = agent_options[0]  # Default
            if current_oc_agent:
                for opt in agent_options:
                    if opt["value"] == current_oc_agent:
                        initial_agent = opt
                        break

            agent_select = {
                "type": "static_select",
                "action_id": "opencode_agent_select",
                "placeholder": {"type": "plain_text", "text": "Select OpenCode agent"},
                "options": agent_options,
                "initial_option": initial_agent,
            }

            # Build model options
            default_label = "(Default)"
            if default_model_str:
                default_label = f"(Default) - {default_model_str}"
            model_options = [{"text": {"type": "plain_text", "text": default_label}, "value": "__default__"}]

            # Add models from providers
            providers_data = opencode_models.get("providers", [])
            defaults = opencode_models.get("default", {})

            # Calculate max models per provider to fit within Slack's 100 option limit
            # Reserve 1 for "(Default)" option
            num_providers = len(providers_data)
            max_per_provider = max(5, (99 // num_providers)) if num_providers > 0 else 99

            def model_sort_key(model_item):
                """Sort models by release_date (newest first), deprioritize utility models."""
                model_id, model_info = model_item
                mid_lower = model_id.lower()

                # Deprioritize embedding and utility models (put them at the end)
                is_utility = any(
                    kw in mid_lower for kw in ["embedding", "tts", "whisper", "ada", "davinci", "turbo-instruct"]
                )
                utility_penalty = 1 if is_utility else 0

                # Get release_date for sorting (newest first)
                # Default to old date if not available, convert to negative int for DESC sort
                release_date = "1970-01-01"
                if isinstance(model_info, dict):
                    release_date = model_info.get("release_date", "1970-01-01") or "1970-01-01"
                # Convert YYYY-MM-DD to int (e.g., 20250414) and negate for descending order
                try:
                    date_int = -int(release_date.replace("-", ""))
                except (ValueError, AttributeError):
                    date_int = 0

                # Sort by: utility_penalty ASC, release_date DESC (via negative int), model_id ASC
                return (utility_penalty, date_int, model_id)

            for provider in providers_data:
                provider_id = provider.get("id", "")
                provider_name = provider.get("name", provider_id)
                models = provider.get("models", {})

                # Handle both dict and list formats for models
                if isinstance(models, dict):
                    model_items = list(models.items())
                elif isinstance(models, list):
                    model_items = [(m, m) if isinstance(m, str) else (m.get("id", ""), m) for m in models]
                else:
                    model_items = []

                # Sort models by priority
                model_items.sort(key=model_sort_key)

                # Limit models per provider
                provider_model_count = 0
                for model_id, model_info in model_items:
                    if provider_model_count >= max_per_provider:
                        break

                    # Get model name
                    if isinstance(model_info, dict):
                        model_name = model_info.get("name", model_id)
                    else:
                        model_name = model_id

                    if model_id:
                        full_model = f"{provider_id}/{model_id}"
                        # Mark if this is the provider's default
                        is_default = defaults.get(provider_id) == model_id
                        display = f"{provider_name}: {model_name}"
                        if is_default:
                            display += " (default)"

                        model_options.append(
                            {
                                "text": {"type": "plain_text", "text": display[:75]},  # Slack limit
                                "value": full_model,
                            }
                        )
                        provider_model_count += 1

            # Final safety check for Slack's 100 option limit
            if len(model_options) > 100:
                model_options = model_options[:100]
                logger.warning("Truncated model options to 100 for Slack modal")

            # Find initial model
            initial_model = model_options[0]  # Default
            if current_oc_model:
                for opt in model_options:
                    if opt["value"] == current_oc_model:
                        initial_model = opt
                        break

            model_select = {
                "type": "static_select",
                "action_id": "opencode_model_select",
                "placeholder": {"type": "plain_text", "text": "Select model"},
                "options": model_options,
                "initial_option": initial_model,
            }

            # Build reasoning effort options dynamically based on model variants
            target_model = current_oc_model or default_model_str
            model_variants: Dict[str, Any] = {}

            reasoning_model_key = target_model or "__default__"
            reasoning_action_id = (
                "opencode_reasoning_select__" + hashlib.sha1(reasoning_model_key.encode("utf-8")).hexdigest()[:8]
            )

            if target_model:
                # Parse provider/model format
                parts = target_model.split("/", 1)
                if len(parts) == 2:
                    target_provider, target_model_id = parts
                    # Search for this model in providers data
                    for provider in providers_data:
                        if provider.get("id") != target_provider:
                            continue

                        models = provider.get("models", {})
                        model_info: Optional[dict] = None

                        if isinstance(models, dict):
                            candidate = models.get(target_model_id)
                            if isinstance(candidate, dict):
                                model_info = candidate
                        elif isinstance(models, list):
                            for entry in models:
                                if isinstance(entry, dict) and entry.get("id") == target_model_id:
                                    model_info = entry
                                    break

                        if isinstance(model_info, dict):
                            variants = model_info.get("variants", {})
                            if isinstance(variants, dict):
                                model_variants = variants

                        break

            # Build options from variants or use fallback
            reasoning_effort_options = [{"text": {"type": "plain_text", "text": "(Default)"}, "value": "__default__"}]

            if model_variants:
                # Use model-specific variants with stable ordering
                variant_order = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]
                variant_display_names = {
                    "none": "None",
                    "minimal": "Minimal",
                    "low": "Low",
                    "medium": "Medium",
                    "high": "High",
                    "xhigh": "Extra High",
                    "max": "Max",
                }
                # Sort variants by predefined order, unknown variants go to end alphabetically
                sorted_variants = sorted(
                    model_variants.keys(),
                    key=lambda x: (
                        variant_order.index(x) if x in variant_order else len(variant_order),
                        x,
                    ),
                )
                for variant_key in sorted_variants:
                    display_name = variant_display_names.get(variant_key, variant_key.capitalize())
                    reasoning_effort_options.append(
                        {
                            "text": {"type": "plain_text", "text": display_name},
                            "value": variant_key,
                        }
                    )
            else:
                # Fallback to common options
                reasoning_effort_options.extend(
                    [
                        {"text": {"type": "plain_text", "text": "Low"}, "value": "low"},
                        {"text": {"type": "plain_text", "text": "Medium"}, "value": "medium"},
                        {"text": {"type": "plain_text", "text": "High"}, "value": "high"},
                    ]
                )

            # Find initial reasoning effort
            initial_reasoning = reasoning_effort_options[0]  # Default
            if current_oc_reasoning:
                for opt in reasoning_effort_options:
                    if opt["value"] == current_oc_reasoning:
                        initial_reasoning = opt
                        break

            reasoning_select = {
                "type": "static_select",
                "action_id": reasoning_action_id,
                "placeholder": {"type": "plain_text", "text": "Select reasoning effort"},
                "options": reasoning_effort_options,
                "initial_option": initial_reasoning,
            }

            # Add OpenCode section
            blocks.extend(
                [
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*OpenCode Settings*",
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "opencode_agent_block",
                        "optional": True,
                        "dispatch_action": True,
                        "element": agent_select,
                        "label": {"type": "plain_text", "text": "OpenCode Agent"},
                    },
                    {
                        "type": "input",
                        "block_id": "opencode_model_block",
                        "optional": True,
                        "dispatch_action": True,
                        "element": model_select,
                        "label": {"type": "plain_text", "text": "Model"},
                    },
                    {
                        "type": "input",
                        "block_id": "opencode_reasoning_block",
                        "optional": True,
                        "element": reasoning_select,
                        "label": {"type": "plain_text", "text": "Reasoning Effort (Thinking Mode)"},
                    },
                ]
            )

        # Claude-specific options (only if claude is selected)
        if effective_backend == "claude" and "claude" in registered_backends:
            claude_agents = claude_agents or []
            claude_models = claude_models or []

            # Get current claude settings
            if selected_claude_agent is _UNSET:
                current_cl_agent = current_routing.claude_agent if current_routing else None
            else:
                current_cl_agent = selected_claude_agent

            if selected_claude_model is _UNSET:
                current_cl_model = current_routing.claude_model if current_routing else None
            else:
                current_cl_model = selected_claude_model

            # Build agent options
            cl_agent_options = [{"text": {"type": "plain_text", "text": "(Default)"}, "value": "__default__"}]
            for agent in claude_agents:
                agent_id = agent.get("id", "")
                agent_name = agent.get("name", agent_id)
                if agent_id:
                    cl_agent_options.append(
                        {
                            "text": {"type": "plain_text", "text": agent_name[:75]},
                            "value": agent_id,
                        }
                    )

            # Find initial agent
            initial_cl_agent = cl_agent_options[0]
            if current_cl_agent:
                for opt in cl_agent_options:
                    if opt["value"] == current_cl_agent:
                        initial_cl_agent = opt
                        break

            cl_agent_select = {
                "type": "static_select",
                "action_id": "claude_agent_select",
                "placeholder": {"type": "plain_text", "text": "Select Claude agent"},
                "options": cl_agent_options,
                "initial_option": initial_cl_agent,
            }

            # Build model options
            cl_model_options = [{"text": {"type": "plain_text", "text": "(Default)"}, "value": "__default__"}]
            for model in claude_models:
                if model:
                    cl_model_options.append(
                        {
                            "text": {"type": "plain_text", "text": model[:75]},
                            "value": model,
                        }
                    )

            # Add current model if not in list (preserve custom models)
            if current_cl_model and not any(opt["value"] == current_cl_model for opt in cl_model_options):
                cl_model_options.append(
                    {
                        "text": {"type": "plain_text", "text": current_cl_model[:75]},
                        "value": current_cl_model,
                    }
                )

            # Limit to 100 options
            if len(cl_model_options) > 100:
                cl_model_options = cl_model_options[:100]

            # Find initial model
            initial_cl_model = cl_model_options[0]
            if current_cl_model:
                for opt in cl_model_options:
                    if opt["value"] == current_cl_model:
                        initial_cl_model = opt
                        break

            cl_model_select = {
                "type": "static_select",
                "action_id": "claude_model_select",
                "placeholder": {"type": "plain_text", "text": "Select model"},
                "options": cl_model_options,
                "initial_option": initial_cl_model,
            }

            # Add Claude section
            blocks.extend(
                [
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*Claude Settings*",
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "claude_agent_block",
                        "optional": True,
                        "element": cl_agent_select,
                        "label": {"type": "plain_text", "text": "Claude Agent"},
                    },
                    {
                        "type": "input",
                        "block_id": "claude_model_block",
                        "optional": True,
                        "element": cl_model_select,
                        "label": {"type": "plain_text", "text": "Model"},
                    },
                ]
            )

        # Codex-specific options (only if codex is selected)
        if effective_backend == "codex" and "codex" in registered_backends:
            codex_models = codex_models or []

            # Get current codex settings
            if selected_codex_model is _UNSET:
                current_cx_model = current_routing.codex_model if current_routing else None
            else:
                current_cx_model = selected_codex_model

            if selected_codex_reasoning is _UNSET:
                current_cx_reasoning = current_routing.codex_reasoning_effort if current_routing else None
            else:
                current_cx_reasoning = selected_codex_reasoning

            # Build model options
            cx_model_options = [{"text": {"type": "plain_text", "text": "(Default)"}, "value": "__default__"}]
            for model in codex_models:
                if model:
                    cx_model_options.append(
                        {
                            "text": {"type": "plain_text", "text": model[:75]},
                            "value": model,
                        }
                    )

            # Add current model if not in list (preserve custom models)
            if current_cx_model and not any(opt["value"] == current_cx_model for opt in cx_model_options):
                cx_model_options.append(
                    {
                        "text": {"type": "plain_text", "text": current_cx_model[:75]},
                        "value": current_cx_model,
                    }
                )

            # Limit to 100 options
            if len(cx_model_options) > 100:
                cx_model_options = cx_model_options[:100]

            # Find initial model
            initial_cx_model = cx_model_options[0]
            if current_cx_model:
                for opt in cx_model_options:
                    if opt["value"] == current_cx_model:
                        initial_cx_model = opt
                        break

            cx_model_select = {
                "type": "static_select",
                "action_id": "codex_model_select",
                "placeholder": {"type": "plain_text", "text": "Select model"},
                "options": cx_model_options,
                "initial_option": initial_cx_model,
            }

            # Build reasoning effort options
            cx_reasoning_options = [
                {"text": {"type": "plain_text", "text": "(Default)"}, "value": "__default__"},
                {"text": {"type": "plain_text", "text": "Low"}, "value": "low"},
                {"text": {"type": "plain_text", "text": "Medium"}, "value": "medium"},
                {"text": {"type": "plain_text", "text": "High"}, "value": "high"},
            ]

            # Find initial reasoning
            initial_cx_reasoning = cx_reasoning_options[0]
            if current_cx_reasoning:
                for opt in cx_reasoning_options:
                    if opt["value"] == current_cx_reasoning:
                        initial_cx_reasoning = opt
                        break

            cx_reasoning_select = {
                "type": "static_select",
                "action_id": "codex_reasoning_select",
                "placeholder": {"type": "plain_text", "text": "Select reasoning effort"},
                "options": cx_reasoning_options,
                "initial_option": initial_cx_reasoning,
            }

            # Add Codex section
            blocks.extend(
                [
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "*Codex Settings*",
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "codex_model_block",
                        "optional": True,
                        "element": cx_model_select,
                        "label": {"type": "plain_text", "text": "Model"},
                    },
                    {
                        "type": "input",
                        "block_id": "codex_reasoning_block",
                        "optional": True,
                        "element": cx_reasoning_select,
                        "label": {"type": "plain_text", "text": "Reasoning Effort"},
                    },
                ]
            )

        # Add tip
        blocks.append(
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": "_💡 Select (Default) to use the backend's configured defaults._",
                    }
                ],
            }
        )

        return {
            "type": "modal",
            "callback_id": "routing_modal",
            "private_metadata": channel_id,
            "title": {"type": "plain_text", "text": "Agent Settings"},
            "submit": {"type": "plain_text", "text": "Save"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": blocks,
        }

    async def open_opencode_question_modal(
        self,
        trigger_id: str,
        context: MessageContext,
        pending: Dict[str, Any],
    ):
        self._ensure_clients()

        questions = pending.get("questions")
        questions = questions if isinstance(questions, list) else []
        if not questions:
            raise ValueError("No questions available")

        import json

        private_metadata = json.dumps(
            {
                "channel_id": context.channel_id,
                "thread_id": context.thread_id,
                "question_count": len(questions),
            }
        )

        blocks: list[Dict[str, Any]] = []
        for idx, q in enumerate(questions):
            if not isinstance(q, dict):
                continue
            header = (q.get("header") or f"Question {idx + 1}").strip()
            prompt = (q.get("question") or "").strip()
            multiple = bool(q.get("multiple"))
            options = q.get("options") if isinstance(q.get("options"), list) else []

            option_items = []
            for opt in options:
                if not isinstance(opt, dict):
                    continue
                label = opt.get("label")
                if label is None:
                    continue
                desc = opt.get("description")
                item: Dict[str, Any] = {
                    "text": {
                        "type": "plain_text",
                        "text": str(label)[:75],
                        "emoji": True,
                    },
                    "value": str(label),
                }
                if desc:
                    item["description"] = {
                        "type": "plain_text",
                        "text": str(desc)[:75],
                        "emoji": True,
                    }
                option_items.append(item)

            element: Dict[str, Any]
            if multiple:
                element = {
                    "type": "multi_static_select",
                    "action_id": "select",
                    "options": option_items,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select one or more",
                        "emoji": True,
                    },
                }
            else:
                element = {
                    "type": "static_select",
                    "action_id": "select",
                    "options": option_items,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select one",
                        "emoji": True,
                    },
                }

            label_text = header
            if prompt:
                label_text = f"{header}: {prompt}"[:150]

            blocks.append(
                {
                    "type": "input",
                    "block_id": f"q{idx}",
                    "label": {
                        "type": "plain_text",
                        "text": label_text,
                        "emoji": True,
                    },
                    "element": element,
                }
            )

        view = {
            "type": "modal",
            "callback_id": "opencode_question_modal",
            "private_metadata": private_metadata,
            "title": {"type": "plain_text", "text": "OpenCode", "emoji": True},
            "submit": {"type": "plain_text", "text": "Submit", "emoji": True},
            "close": {"type": "plain_text", "text": "Cancel", "emoji": True},
            "blocks": blocks,
        }

        await self.web_client.views_open(trigger_id=trigger_id, view=view)

    async def open_question_modal(
        self,
        trigger_id: str,
        context: MessageContext,
        pending: Any,  # PendingQuestion from question_ui module
        callback_prefix: str = "opencode_question",
    ):
        """Open a question modal for any agent backend.

        This is a generalized version of open_opencode_question_modal that
        supports different callback prefixes for different agents.

        Args:
            trigger_id: Slack trigger ID for opening the modal
            context: Message context
            pending: PendingQuestion instance with questions data
            callback_prefix: Prefix for callback routing (e.g., "opencode_question", "claude_question")
        """
        self._ensure_clients()

        # Support both PendingQuestion dataclass and dict format
        if hasattr(pending, "questions"):
            questions = pending.questions
        else:
            questions = pending.get("questions") if isinstance(pending, dict) else []

        if not questions:
            raise ValueError("No questions available")

        import json

        private_metadata = json.dumps(
            {
                "channel_id": context.channel_id,
                "thread_id": context.thread_id,
                "question_count": len(questions),
                "callback_prefix": callback_prefix,
            }
        )

        blocks: list[Dict[str, Any]] = []
        for idx, q in enumerate(questions):
            # Support both Question dataclass and dict format
            if hasattr(q, "header"):
                header = (q.header or f"Question {idx + 1}").strip()
                prompt = (q.question or "").strip()
                multiple = bool(q.multiple)
                options = q.options
            elif isinstance(q, dict):
                header = (q.get("header") or f"Question {idx + 1}").strip()
                prompt = (q.get("question") or "").strip()
                multiple = bool(q.get("multiple") or q.get("multiSelect"))
                options = q.get("options") if isinstance(q.get("options"), list) else []
            else:
                continue

            option_items = []
            for opt in options:
                # Support both QuestionOption dataclass and dict format
                if hasattr(opt, "label"):
                    label = opt.label
                    desc = opt.description
                elif isinstance(opt, dict):
                    label = opt.get("label")
                    desc = opt.get("description")
                else:
                    continue

                if label is None:
                    continue
                item: Dict[str, Any] = {
                    "text": {
                        "type": "plain_text",
                        "text": str(label)[:75],
                        "emoji": True,
                    },
                    "value": str(label),
                }
                if desc:
                    item["description"] = {
                        "type": "plain_text",
                        "text": str(desc)[:75],
                        "emoji": True,
                    }
                option_items.append(item)

            element: Dict[str, Any]
            if multiple:
                element = {
                    "type": "multi_static_select",
                    "action_id": "select",
                    "options": option_items,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select one or more",
                        "emoji": True,
                    },
                }
            else:
                element = {
                    "type": "static_select",
                    "action_id": "select",
                    "options": option_items,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Select one",
                        "emoji": True,
                    },
                }

            label_text = header
            if prompt:
                label_text = f"{header}: {prompt}"[:150]

            blocks.append(
                {
                    "type": "input",
                    "block_id": f"q{idx}",
                    "label": {
                        "type": "plain_text",
                        "text": label_text,
                        "emoji": True,
                    },
                    "element": element,
                }
            )

        # Use callback_prefix to generate callback_id
        callback_id = f"{callback_prefix}_modal"
        title = "Claude Code" if callback_prefix.startswith("claude") else "OpenCode"

        view = {
            "type": "modal",
            "callback_id": callback_id,
            "private_metadata": private_metadata,
            "title": {"type": "plain_text", "text": title, "emoji": True},
            "submit": {"type": "plain_text", "text": "Submit", "emoji": True},
            "close": {"type": "plain_text", "text": "Cancel", "emoji": True},
            "blocks": blocks,
        }

        await self.web_client.views_open(trigger_id=trigger_id, view=view)

    async def open_routing_modal(
        self,
        trigger_id: str,
        channel_id: str,
        registered_backends: list,
        current_backend: str,
        current_routing,  # Optional[ChannelRouting]
        opencode_agents: list,
        opencode_models: dict,
        opencode_default_config: dict,
        claude_agents: list = None,
        claude_models: list = None,
        codex_models: list = None,
    ):
        """Open a modal dialog for agent/model routing settings"""
        self._ensure_clients()

        view = self._build_routing_modal_view(
            channel_id=channel_id,
            registered_backends=registered_backends,
            current_backend=current_backend,
            current_routing=current_routing,
            opencode_agents=opencode_agents,
            opencode_models=opencode_models,
            opencode_default_config=opencode_default_config,
            claude_agents=claude_agents,
            claude_models=claude_models,
            codex_models=codex_models,
        )

        try:
            await self.web_client.views_open(trigger_id=trigger_id, view=view)
        except SlackApiError as e:
            logger.error(f"Error opening routing modal: {e}")
            raise

    async def update_routing_modal(
        self,
        view_id: str,
        view_hash: str,
        channel_id: str,
        registered_backends: list,
        current_backend: str,
        current_routing,
        opencode_agents: list,
        opencode_models: dict,
        opencode_default_config: dict,
        claude_agents: list = None,
        claude_models: list = None,
        codex_models: list = None,
        selected_backend: Optional[str] = None,
        selected_opencode_agent: Optional[str] = None,
        selected_opencode_model: Optional[str] = None,
        selected_opencode_reasoning: Optional[str] = None,
        selected_claude_agent: Optional[str] = None,
        selected_claude_model: Optional[str] = None,
        selected_codex_model: Optional[str] = None,
        selected_codex_reasoning: Optional[str] = None,
    ) -> None:
        """Update routing modal when selections change."""
        self._ensure_clients()

        view = self._build_routing_modal_view(
            channel_id=channel_id,
            registered_backends=registered_backends,
            current_backend=current_backend,
            current_routing=current_routing,
            opencode_agents=opencode_agents,
            opencode_models=opencode_models,
            opencode_default_config=opencode_default_config,
            claude_agents=claude_agents,
            claude_models=claude_models,
            codex_models=codex_models,
            selected_backend=selected_backend,
            selected_opencode_agent=selected_opencode_agent,
            selected_opencode_model=selected_opencode_model,
            selected_opencode_reasoning=selected_opencode_reasoning,
            selected_claude_agent=selected_claude_agent,
            selected_claude_model=selected_claude_model,
            selected_codex_model=selected_codex_model,
            selected_codex_reasoning=selected_codex_reasoning,
        )

        try:
            await self.web_client.views_update(view_id=view_id, hash=view_hash, view=view)
        except SlackApiError as e:
            logger.error(f"Error updating routing modal: {e}")
            raise

    def register_callbacks(
        self,
        on_message: Optional[Callable] = None,
        on_command: Optional[Dict[str, Callable]] = None,
        on_callback_query: Optional[Callable] = None,
        **kwargs,
    ):
        """Register callback functions for different events"""
        super().register_callbacks(on_message, on_command, on_callback_query, **kwargs)

        # Register command handlers
        if on_command:
            self.command_handlers.update(on_command)

        # Register any slash command handlers passed in kwargs
        if "on_slash_command" in kwargs:
            slash_commands = kwargs["on_slash_command"]
            if isinstance(slash_commands, dict):
                self.slash_command_handlers.update(slash_commands)

        # Register settings update handler
        if "on_settings_update" in kwargs:
            self._on_settings_update = kwargs["on_settings_update"]

        # Register change CWD handler
        if "on_change_cwd" in kwargs:
            self._on_change_cwd = kwargs["on_change_cwd"]

        # Register routing update handler
        if "on_routing_update" in kwargs:
            self._on_routing_update = kwargs["on_routing_update"]

        # Register routing modal update handler
        if "on_routing_modal_update" in kwargs:
            self._on_routing_modal_update = kwargs["on_routing_modal_update"]

        # Register resume session handler
        if "on_resume_session" in kwargs:
            self._on_resume_session = kwargs["on_resume_session"]

        # Register on_ready handler (called when connected)
        if "on_ready" in kwargs:
            self._on_ready = kwargs["on_ready"]

    async def get_or_create_thread(self, channel_id: str, user_id: str) -> Optional[str]:
        """Get existing thread timestamp or return None for new thread"""
        # Deprecated: Thread handling now uses user's message timestamp directly
        return None

    async def send_slash_response(self, response_url: str, text: str, ephemeral: bool = True) -> bool:
        """Send response to a slash command via response_url"""
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                await session.post(
                    response_url,
                    json={
                        "text": text,
                        "response_type": "ephemeral" if ephemeral else "in_channel",
                    },
                )
            return True
        except Exception as e:
            logger.error(f"Error sending slash command response: {e}")
            return False

    async def _is_authorized_channel(self, channel_id: str) -> bool:
        """Check if a channel is authorized based on whitelist configuration"""
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
        """Send unauthorized access message to channel"""
        try:
            self._ensure_clients()
            await self.web_client.chat_postMessage(
                channel=channel_id,
                text=f"❌ {self._t('error.channelNotEnabled', channel_id)}",
            )
        except Exception as e:
            logger.error(f"Failed to send unauthorized message to {channel_id}: {e}")
