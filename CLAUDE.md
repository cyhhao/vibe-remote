# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a multi-platform remote control bot for Claude Code, supporting both Telegram and Slack. Users can send messages through IM platforms to execute Claude Code commands remotely. The system provides persistent sessions, real-time feedback, and maintains conversation context.

## Development Commands

### Environment Setup
```bash
# Create virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt

# Create configuration from template
cp .env.example .env
# Edit .env with your platform and credentials
```

### Running the Bot
```bash
# Start the bot (preferred - includes process management)
./start.sh

# Alternative direct start
python main.py

# Stop the bot
./stop.sh

# Check bot status
./status.sh
```

### Development Testing
```bash
# Test imports and basic functionality
python3 -c "from config.settings import AppConfig; from modules.im import IMFactory; print('Imports successful!')"

# Check logs in real-time
tail -f logs/bot_*.log

# Manual testing with specific platform
IM_PLATFORM=telegram python main.py
IM_PLATFORM=slack python main.py
```

## Architecture Overview

### Multi-Platform Design Pattern
The system uses an abstract factory pattern with platform-agnostic interfaces:

1. **BaseIMClient** (`modules/base_im_client.py`): Abstract base class defining the IM interface
2. **IMFactory** (`modules/im_factory.py`): Creates appropriate platform clients
3. **Controller** (`core/controller.py`): Platform-agnostic business logic coordinator

### Key Components

- **Configuration System** (`config/settings.py`): Environment-based config with platform-specific validation
- **Session Management** (`modules/session_manager.py`): Manages ClaudeSDKClient instances for persistent conversations
- **Claude Integration** (`modules/claude_client.py`): Message formatting and platform-specific rendering
- **Settings Management** (`modules/settings_manager.py`): User preferences, message visibility, and session ID mappings

### Platform Implementations
- **TelegramBot** (`modules/telegram_bot.py`): Telegram-specific implementation with inline keyboards
- **SlackBot** (`modules/slack_bot.py`): Slack-specific implementation with thread support and Socket Mode

### Message Flow
1. User sends message via IM platform
2. Platform-specific client converts to `MessageContext`
3. Controller immediately sends message to Claude Code via persistent session
4. Real-time output streamed back through same IM platform
5. Conversation context maintained for follow-up messages

## User Interface

### Slack Commands
Slack uses a simplified command structure with only one slash command:
- `/start` - Opens the main menu with interactive buttons

All functionality is accessed through buttons in the /start menu:
- **Current Dir** - Display current working directory
- **Change Work Dir** - Open modal to change working directory
- **Reset Session** - Clear conversation context and start fresh
- **Settings** - Configure message visibility preferences
- **How it Works** - Display help information

### Telegram Commands
Telegram supports traditional slash commands:
- `/start` - Show welcome message and available commands
- `/clear` - Reset conversation and start fresh
- `/cwd` - Show current working directory
- `/set_cwd <path>` - Change working directory
- `/settings` - Open personalization settings

## Configuration Requirements

### Essential Environment Variables
- `IM_PLATFORM`: Must be "telegram" or "slack"
- Platform-specific tokens (see `.env.example`)
- `CLAUDE_CWD`: Working directory for Claude Code execution

### Platform Switching
The system dynamically loads the appropriate IM client based on `IM_PLATFORM`. Configuration validation ensures required tokens are present for the selected platform.

## Adding New IM Platforms

To extend support to new platforms (Discord, Teams, etc.):

1. Create new client class inheriting from `BaseIMClient`
2. Implement all abstract methods (send_message, edit_message, etc.)
3. Create platform config class inheriting from `BaseIMConfig`
4. Add platform to `IMFactory.create_client()`
5. Update `AppConfig.from_env()` validation
6. Add environment variables to `.env.example`

Reference `modules/slack_bot.py` for a complete implementation example.

## Thread and Session Management

### Slack Thread Handling
Slack conversations are automatically organized into threads. Each user message creates a thread, and all bot responses are posted as replies in that thread for better organization.

### Session Management
- **Telegram**: Each chat/group maintains its own Claude session
- **Slack**: Each thread maintains its own Claude session
- Sessions persist across messages for continuous conversations
- Use `/clear` or Reset Session button to start fresh

### Callback System
The controller registers command handlers with IM clients through a callback system, allowing platform-agnostic command handling while preserving platform-specific features (inline keyboards, thread replies, etc.).

## Deployment Notes

### Process Management
- `start.sh` provides daemon-style execution with PID tracking
- Logs are automatically rotated and stored in `logs/` directory
- Virtual environment detection and activation is automatic

### Error Handling
- Graceful degradation for missing optional tokens
- Automatic retry with exponential backoff for network failures
- Comprehensive logging for debugging platform-specific issues

### Socket Mode vs Webhooks
For Slack, Socket Mode is preferred as it doesn't require public endpoints. The system automatically uses Socket Mode if `SLACK_APP_TOKEN` is provided.

## Technical Implementation Details

### ClaudeSDKClient vs query()
The project uses `ClaudeSDKClient` for persistent bidirectional communication instead of the simpler `query()` function. This provides:
- Persistent WebSocket-like connections per session
- Real-time message streaming without polling
- Proper session management with resume capability
- Better error handling and reconnection support

### Session ID Mapping
The system captures Claude's internal `session_id` from the `SystemMessage` and maintains a mapping:
- **Purpose**: Allows precise session restoration after bot restarts
- **Storage**: Maps IM session IDs (chat_id/thread_id) to Claude session_ids in `user_settings.json`
- **Usage**: Uses `resume` parameter with stored session_id instead of deprecated `continue_conversation`

### Concurrent Message Handling
Each session has:
- A dedicated `ClaudeSDKClient` instance
- A persistent message receiver task running in the background
- Proper cleanup mechanisms to prevent resource leaks

### Platform-Specific Markdown Formatting
Different platforms have unique markdown requirements:
- **Telegram**: Uses MarkdownV2 with extensive character escaping
- **Slack**: Uses mrkdwn format with different escaping rules
- Formatter pattern allows clean separation of concerns

## Common Development Challenges

### Async/Await Error: "read() called while another coroutine is already waiting"
**Problem**: Multiple coroutines trying to read from the same ClaudeSDKClient  
**Solution**: Implement a single persistent receiver per session that handles all messages

### Session Management Edge Cases
1. **Bot Restart**: Sessions are restored using stored session_id mappings
2. **Network Errors**: Broken sessions are cleaned up and recreated automatically
3. **User Switching Chats**: Each chat/thread maintains independent session

### Debugging Tips

1. **Enable Debug Logging**:
   ```python
   logging.basicConfig(level=logging.DEBUG)
   ```

2. **Check Session State**:
   - View active sessions in `user_settings.json`
   - Monitor session creation/cleanup in logs
   - Use `/cwd` command to verify session is active

3. **Platform-Specific Issues**:
   - **Telegram**: Check for markdown parsing errors in logs
   - **Slack**: Verify Socket Mode connection with app-level token
   - Both: Ensure proper environment variables are set

### Testing Strategies

1. **Session Persistence**:
   - Send message → Restart bot → Send follow-up message
   - Verify conversation context is maintained

2. **Concurrent Users**:
   - Test multiple users sending messages simultaneously
   - Verify sessions don't interfere with each other

3. **Error Recovery**:
   - Simulate network errors by disconnecting internet
   - Verify graceful error messages and recovery

## Performance Optimizations

1. **Message Batching**: Controller processes messages immediately without queuing
2. **Resource Cleanup**: Proper session cleanup prevents memory leaks
3. **Lazy Loading**: Claude clients created only when needed
4. **Persistent Connections**: Reduces overhead of creating new connections

## Security Considerations

1. **Token Storage**: Never commit tokens to git
2. **Session Isolation**: Each user's sessions are completely isolated
3. **Command Validation**: All user inputs are validated before execution
4. **File Path Security**: Working directory restrictions prevent unauthorized access

## Best Practices

- **Process Management**:
    - 不要直接执行杀进程来重启服务，使用./start.sh会自动杀掉对应进程并重启