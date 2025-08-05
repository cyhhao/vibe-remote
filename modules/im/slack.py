import asyncio
import logging
from typing import Dict, Any, Optional, Callable
from slack_sdk.web.async_client import AsyncWebClient
from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.request import SocketModeRequest
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.errors import SlackApiError

from .base import BaseIMClient, MessageContext, InlineKeyboard, InlineButton
from config.settings import SlackConfig

logger = logging.getLogger(__name__)


class SlackBot(BaseIMClient):
    """Slack implementation of the IM client"""
    
    def __init__(self, config: SlackConfig):
        super().__init__(config)
        self.config = config
        self.web_client = None
        self.socket_client = None
        
        # Note: Thread handling now uses user's message timestamp directly
        
        # Store callback handlers
        self.command_handlers: Dict[str, Callable] = {}
        self.slash_command_handlers: Dict[str, Callable] = {}
        
        # Store trigger IDs for modal interactions
        self.trigger_ids: Dict[str, str] = {}
    
    def _ensure_clients(self):
        """Ensure web and socket clients are initialized"""
        if self.web_client is None:
            self.web_client = AsyncWebClient(token=self.config.bot_token)
        
        if self.socket_client is None and self.config.app_token:
            self.socket_client = SocketModeClient(
                app_token=self.config.app_token,
                web_client=self.web_client
            )
        
    async def send_message(self, context: MessageContext, text: str,
                          parse_mode: Optional[str] = None,
                          reply_to: Optional[str] = None) -> str:
        """Send a message to Slack"""
        self._ensure_clients()
        try:
            # Prepare message kwargs
            kwargs = {
                'channel': context.channel_id,
                'text': text
            }
            
            # Handle thread replies
            if context.thread_id:
                kwargs['thread_ts'] = context.thread_id
                # Optionally broadcast to channel
                if context.platform_specific and context.platform_specific.get('reply_broadcast'):
                    kwargs['reply_broadcast'] = True
            elif reply_to:
                # If reply_to is specified, use it as thread timestamp
                kwargs['thread_ts'] = reply_to
            
            # Handle formatting
            if parse_mode == 'markdown':
                kwargs['mrkdwn'] = True
            
            # Send message
            response = await self.web_client.chat_postMessage(**kwargs)
            
            return response['ts']
            
        except SlackApiError as e:
            logger.error(f"Error sending Slack message: {e}")
            raise
    
    async def send_message_with_buttons(self, context: MessageContext, text: str,
                                      keyboard: InlineKeyboard,
                                      parse_mode: Optional[str] = None) -> str:
        """Send a message with interactive buttons"""
        self._ensure_clients()
        try:
            # Convert our generic keyboard to Slack blocks
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn" if parse_mode == 'markdown' else "plain_text",
                        "text": text
                    }
                }
            ]
            
            # Add action blocks for buttons
            for row_idx, row in enumerate(keyboard.buttons):
                elements = []
                for button in row:
                    elements.append({
                        "type": "button",
                        "text": {
                            "type": "plain_text",
                            "text": button.text
                        },
                        "action_id": button.callback_data,
                        "value": button.callback_data
                    })
                
                blocks.append({
                    "type": "actions",
                    "block_id": f"actions_{row_idx}",
                    "elements": elements
                })
            
            # Prepare message kwargs
            kwargs = {
                'channel': context.channel_id,
                'blocks': blocks,
                'text': text  # Fallback text
            }
            
            # Handle thread replies
            if context.thread_id:
                kwargs['thread_ts'] = context.thread_id
            
            response = await self.web_client.chat_postMessage(**kwargs)
            return response['ts']
            
        except SlackApiError as e:
            logger.error(f"Error sending Slack message with buttons: {e}")
            raise
    
    async def edit_message(self, context: MessageContext, message_id: str,
                          text: Optional[str] = None,
                          keyboard: Optional[InlineKeyboard] = None) -> bool:
        """Edit an existing Slack message"""
        self._ensure_clients()
        try:
            kwargs = {
                'channel': context.channel_id,
                'ts': message_id
            }
            
            if text:
                kwargs['text'] = text
            
            if keyboard:
                # Convert keyboard to blocks (similar to send_message_with_buttons)
                blocks = []
                if text:
                    blocks.append({
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": text
                        }
                    })
                
                for row_idx, row in enumerate(keyboard.buttons):
                    elements = []
                    for button in row:
                        elements.append({
                            "type": "button",
                            "text": {
                                "type": "plain_text",
                                "text": button.text
                            },
                            "action_id": button.callback_data,
                            "value": button.callback_data
                        })
                    
                    blocks.append({
                        "type": "actions",
                        "block_id": f"actions_{row_idx}",
                        "elements": elements
                    })
                
                kwargs['blocks'] = blocks
            
            await self.web_client.chat_update(**kwargs)
            return True
            
        except SlackApiError as e:
            logger.error(f"Error editing Slack message: {e}")
            return False
    
    async def answer_callback(self, callback_id: str, text: Optional[str] = None,
                            show_alert: bool = False) -> bool:
        """Answer a Slack interactive callback"""
        # In Slack, we don't have a direct equivalent to Telegram's answer_callback_query
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
            elif req.type == "slash_commands":
                # Handle slash commands
                await self._handle_slash_command(req.payload)
            elif req.type == "interactive":
                # Handle interactive components (buttons, etc.)
                await self._handle_interactive(req.payload)
            
            # Acknowledge the request
            response = SocketModeResponse(envelope_id=req.envelope_id)
            await client.send_socket_mode_response(response)
            
        except Exception as e:
            logger.error(f"Error handling socket mode request: {e}")
            # Still acknowledge even on error
            response = SocketModeResponse(envelope_id=req.envelope_id)
            await client.send_socket_mode_response(response)
    
    async def _handle_event(self, payload: Dict[str, Any]):
        """Handle Events API events"""
        event = payload.get("event", {})
        event_type = event.get("type")
        
        if event_type == "message":
            # Ignore bot messages
            if event.get("bot_id"):
                return
            
            channel_id = event.get("channel")
            
            # Check if target_channel is configured and message is not in that channel
            if self.config.target_channel and channel_id != self.config.target_channel:
                # Check if this is a DM (starts with 'D')
                if not channel_id.startswith('D'):
                    logger.info(f"Ignoring message from non-target channel: {channel_id}")
                    return
            
            # Check if this message contains a bot mention
            # If it does, skip processing as it will be handled by app_mention event
            text = event.get("text", "")
            import re
            if re.search(r'<@[\w]+>', text):
                logger.info(f"Skipping message event with bot mention: '{text}'")
                return
            
            # Check if channel is authorized based on whitelist
            if not await self._is_authorized_channel(channel_id):
                logger.info(f"Ignoring message from unauthorized channel: {channel_id}")
                return
            
            # Check if we require mention in channels (not DMs)
            if self.config.require_mention and not channel_id.startswith('D'):
                logger.info(f"Ignoring non-mention message in channel: '{text}'")
                return
            
            # Extract context
            context = MessageContext(
                user_id=event.get("user"),
                channel_id=channel_id,
                thread_id=event.get("thread_ts"),
                message_id=event.get("ts"),
                platform_specific={
                    "team_id": payload.get("team_id"),
                    "event": event
                }
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
                logger.info(f"Ignoring mention from unauthorized channel: {channel_id}")
                return
            
            context = MessageContext(
                user_id=event.get("user"),
                channel_id=channel_id,
                thread_id=event.get("thread_ts"),
                message_id=event.get("ts"),
                platform_specific={
                    "team_id": payload.get("team_id"),
                    "event": event
                }
            )
            
            # Remove the mention from the text
            text = event.get("text", "")
            import re
            text = re.sub(r'<@[\w]+>', '', text).strip()
            
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
            logger.info(f"Ignoring slash command from unauthorized channel: {channel_id}")
            # Send a response to user about unauthorized channel
            response_url = payload.get("response_url")
            if response_url:
                await self.send_slash_response(response_url, "❌ This channel is not authorized to use bot commands.")
            return
        
        # Map Slack slash commands to internal commands
        command_mapping = {
            'claude-start': 'start',
            'claude-status': 'status',
            'claude-clear': 'clear',
            'claude-cwd': 'cwd',
            'claude-set-cwd': 'set_cwd',
            'claude-queue': 'queue',
            'claude-settings': 'settings',
            'claude-execute': 'execute'
        }
        
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
                "payload": payload
            }
        )
        
        # Send immediate acknowledgment to Slack
        response_url = payload.get("response_url")
        
        # Try to handle as registered command
        if actual_command in self.on_command_callbacks:
            handler = self.on_command_callbacks[actual_command]
            
            # Send immediate "processing" response for long-running commands
            if response_url and actual_command not in ['start', 'status', 'clear', 'cwd', 'queue']:
                await self.send_slash_response(response_url, f"⏳ Processing `/{command}`...")
            
            await handler(context, payload.get("text", ""))
        elif actual_command in self.slash_command_handlers:
            handler = self.slash_command_handlers[actual_command]
            await handler(context, payload.get("text", ""))
        else:
            # Send response back to Slack for unknown command
            if response_url:
                available_commands = [f"`/{k}`" for k in command_mapping.keys()]
                await self.send_slash_response(
                    response_url, 
                    f"❌ Unknown command: `/{command}`\n\nAvailable commands:\n{chr(10).join(available_commands)}"
                )
    
    async def _handle_interactive(self, payload: Dict[str, Any]):
        """Handle interactive components (buttons, modal submissions, etc.)"""
        if payload.get("type") == "block_actions":
            # Handle button clicks
            user = payload.get("user", {})
            actions = payload.get("actions", [])
            
            for action in actions:
                if action.get("type") == "button":
                    callback_data = action.get("action_id")
                    
                    if self.on_callback_query_callback:
                        # Create a context for the callback
                        context = MessageContext(
                            user_id=user.get("id"),
                            channel_id=payload.get("channel", {}).get("id"),
                            message_id=payload.get("message", {}).get("ts"),
                            platform_specific={
                                "trigger_id": payload.get("trigger_id"),
                                "response_url": payload.get("response_url"),
                                "action": action,
                                "payload": payload
                            }
                        )
                        
                        await self.on_callback_query_callback(context, callback_data)
                        
        elif payload.get("type") == "view_submission":
            # Handle modal submissions
            await self._handle_view_submission(payload)
    
    async def _handle_view_submission(self, payload: Dict[str, Any]):
        """Handle modal dialog submissions"""
        view = payload.get("view", {})
        callback_id = view.get("callback_id")
        
        if callback_id == "settings_modal":
            # Handle settings modal submission
            user_id = payload.get("user", {}).get("id")
            values = view.get("state", {}).get("values", {})
            
            # Extract selected hidden message types
            hidden_types_data = values.get("hidden_message_types", {}).get("hidden_types_select", {})
            selected_options = hidden_types_data.get("selected_options", [])
            
            # Get the values from selected options
            hidden_types = [opt.get("value") for opt in selected_options]
            
            # Get channel_id from the view's private_metadata if available
            channel_id = view.get("private_metadata")
            
            # Update settings - need access to settings manager
            if hasattr(self, '_on_settings_update'):
                await self._on_settings_update(user_id, hidden_types, channel_id)
            
            # Send success message to the user (via DM or channel)
            # We need to find the right channel to send the message
            # For now, we'll rely on the controller to handle this
    
    def run(self):
        """Run the Slack bot"""
        if self.config.app_token:
            # Socket Mode
            logger.info("Starting Slack bot in Socket Mode...")
            
            async def start():
                self._ensure_clients()
                self.register_handlers()
                await self.socket_client.connect()
                await asyncio.sleep(float("inf"))
            
            asyncio.run(start())
        else:
            # Web API only mode (for development/testing)
            logger.warning("No app token provided, running in Web API only mode")
            # In this mode, you would typically run a web server to receive events
            # For now, just keep the program running
            try:
                asyncio.run(asyncio.sleep(float("inf")))
            except KeyboardInterrupt:
                logger.info("Shutting down...")
    
    async def get_user_info(self, user_id: str) -> Dict[str, Any]:
        """Get information about a Slack user"""
        self._ensure_clients()
        try:
            response = await self.web_client.users_info(user=user_id)
            user = response['user']
            return {
                'id': user['id'],
                'name': user.get('name'),
                'real_name': user.get('real_name'),
                'display_name': user.get('profile', {}).get('display_name'),
                'email': user.get('profile', {}).get('email'),
                'is_bot': user.get('is_bot', False)
            }
        except SlackApiError as e:
            logger.error(f"Error getting user info: {e}")
            raise
    
    async def get_channel_info(self, channel_id: str) -> Dict[str, Any]:
        """Get information about a Slack channel"""
        self._ensure_clients()
        try:
            response = await self.web_client.conversations_info(channel=channel_id)
            channel = response['channel']
            return {
                'id': channel['id'],
                'name': channel.get('name'),
                'is_private': channel.get('is_private', False),
                'is_im': channel.get('is_im', False),
                'is_channel': channel.get('is_channel', False),
                'topic': channel.get('topic', {}).get('value'),
                'purpose': channel.get('purpose', {}).get('value')
            }
        except SlackApiError as e:
            logger.error(f"Error getting channel info: {e}")
            raise
    
    def format_markdown(self, text: str) -> str:
        """Format markdown text for Slack mrkdwn format
        
        Slack uses single asterisks for bold and different formatting rules
        """
        # Convert double asterisks to single for bold
        formatted = text.replace('**', '*')
        
        # Convert inline code blocks (backticks work the same)
        # Lists work similarly
        # Links work similarly [text](url) -> <url|text>
        # But we'll keep simple for now - just handle bold
        
        return formatted
    
    async def open_settings_modal(self, trigger_id: str, user_settings: Any, message_types: list, display_names: dict, channel_id: str = None):
        """Open a modal dialog for settings"""
        self._ensure_clients()
        
        # Create options for the multi-select menu
        options = []
        selected_options = []
        
        for msg_type in message_types:
            display_name = display_names.get(msg_type, msg_type)
            option = {
                "text": {
                    "type": "plain_text",
                    "text": display_name,
                    "emoji": True
                },
                "value": msg_type,
                "description": {
                    "type": "plain_text",
                    "text": self._get_message_type_description(msg_type),
                    "emoji": True
                }
            }
            options.append(option)
            
            # If this type is hidden, add THE SAME option object to selected options
            if msg_type in user_settings.hidden_message_types:
                selected_options.append(option)  # Same object reference!
        
        logger.info(f"Creating modal with {len(options)} options, {len(selected_options)} selected")
        logger.info(f"Hidden types: {user_settings.hidden_message_types}")
        
        # Debug: Log the actual data being sent
        import json
        logger.info(f"Options: {json.dumps(options, indent=2)}")
        logger.info(f"Selected options: {json.dumps(selected_options, indent=2)}")
        
        # Create the multi-select element
        multi_select_element = {
            "type": "multi_static_select",
            "placeholder": {
                "type": "plain_text",
                "text": "Select message types to hide",
                "emoji": True
            },
            "options": options,
            "action_id": "hidden_types_select"
        }
        
        # Only add initial_options if there are selected options
        if selected_options:
            multi_select_element["initial_options"] = selected_options
        
        # Create the modal view
        view = {
            "type": "modal",
            "callback_id": "settings_modal",
            "private_metadata": channel_id or "",  # Store channel_id for later use
            "title": {
                "type": "plain_text",
                "text": "Settings",
                "emoji": True
            },
            "submit": {
                "type": "plain_text",
                "text": "Save",
                "emoji": True
            },
            "close": {
                "type": "plain_text",
                "text": "Cancel",
                "emoji": True
            },
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "Message Visibility Settings",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": "Choose which message types to *hide* from Claude Code output. Hidden messages won't appear in your Slack workspace."
                    }
                },
                {
                    "type": "divider"
                },
                {
                    "type": "input",
                    "block_id": "hidden_message_types",
                    "element": multi_select_element,
                    "label": {
                        "type": "plain_text",
                        "text": "Hide these message types:",
                        "emoji": True
                    },
                    "optional": True
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "_💡 Tip: You can show/hide message types at any time. Changes apply immediately to new messages._"
                        }
                    ]
                }
            ]
        }
        
        try:
            await self.web_client.views_open(
                trigger_id=trigger_id,
                view=view
            )
        except SlackApiError as e:
            logger.error(f"Error opening modal: {e}")
            raise
    
    def _get_message_type_description(self, msg_type: str) -> str:
        """Get description for a message type"""
        descriptions = {
            "system": "System initialization and status messages",
            "response": "Tool execution responses and results",
            "assistant": "Claude's messages and explanations",
            "result": "Final execution results and summaries"
        }
        return descriptions.get(msg_type, f"{msg_type} messages")
    
    def register_callbacks(self, on_message: Optional[Callable] = None,
                         on_command: Optional[Dict[str, Callable]] = None,
                         on_callback_query: Optional[Callable] = None,
                         **kwargs):
        """Register callback functions for different events"""
        super().register_callbacks(on_message, on_command, on_callback_query, **kwargs)
        
        # Register command handlers
        if on_command:
            self.command_handlers.update(on_command)
        
        # Register any slash command handlers passed in kwargs
        if 'on_slash_command' in kwargs:
            slash_commands = kwargs['on_slash_command']
            if isinstance(slash_commands, dict):
                self.slash_command_handlers.update(slash_commands)
        
        # Register settings update handler
        if 'on_settings_update' in kwargs:
            self._on_settings_update = kwargs['on_settings_update']
    
    async def get_or_create_thread(self, channel_id: str, user_id: str) -> Optional[str]:
        """Get existing thread timestamp or return None for new thread"""
        # Deprecated: Thread handling now uses user's message timestamp directly
        return None
    
    async def send_slash_response(self, response_url: str, text: str, 
                                 ephemeral: bool = True) -> bool:
        """Send response to a slash command via response_url"""
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                await session.post(response_url, json={
                    "text": text,
                    "response_type": "ephemeral" if ephemeral else "in_channel"
                })
            return True
        except Exception as e:
            logger.error(f"Error sending slash command response: {e}")
            return False
    
    async def _is_authorized_channel(self, channel_id: str) -> bool:
        """Check if a channel is authorized based on whitelist configuration"""
        target_channel = self.config.target_channel
        
        # If None/null, accept all channels
        if target_channel is None:
            return True
        
        # If empty list, only accept DMs
        if isinstance(target_channel, list) and len(target_channel) == 0:
            # In Slack, DMs start with 'D', group DMs with 'G'
            return channel_id.startswith(('D', 'G'))
        
        # If list with IDs, check whitelist
        if isinstance(target_channel, list):
            return channel_id in target_channel
        
        # Should not reach here, but handle gracefully
        logger.warning(f"Unexpected target_channel type: {type(target_channel)}")
        return True