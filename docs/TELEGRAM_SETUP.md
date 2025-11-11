# Telegram Bot Setup Guide

This guide will walk you through setting up a Telegram bot for Vibe Remote.

## Prerequisites

- Telegram account
- Python 3.6 or higher installed
- Vibe Remote cloned and dependencies installed

## Step 1: Create a Telegram Bot

1. Open Telegram and search for **@BotFather**
2. Start a conversation with BotFather
3. Send the command `/newbot`
4. Follow the prompts:
   - Choose a name for your bot (e.g., "Vibe Remote")
   - Choose a username for your bot (must end in `bot`, e.g., `claude_code_bot`)
5. BotFather will provide you with a **bot token** that looks like:
   ```
   1234567890:ABCdefGHIjklMNOpqrsTUVwxyz1234567890
   ```
6. **Save this token** - you'll need it for configuration

## Step 2: Configure Bot Settings (Optional)

While still chatting with BotFather, you can configure additional settings:

### Set Bot Description

```
/setdescription
```

Then select your bot and provide a description like:
"Vibe Remote - trigger AI coding from Telegram"

### Set About Text

```
/setabouttext
```

Select your bot and add:
"Vibe Remote - AI-powered remote coding assistant"

### Set Bot Commands

```
/setcommands
```

Select your bot and paste these commands:

```
start - Show welcome message and available commands
clear - Reset conversation and start fresh
cwd - Show current working directory
set_cwd - Change working directory
settings - Open personalization settings
```

### Enable Inline Mode (Optional)

```
/setinline
```

This allows the bot to be used inline in other chats.

## Step 3: Configure Your Environment

Update your `.env` file with the following:

```env
# Set platform to telegram
IM_PLATFORM=telegram

# Telegram configuration
TELEGRAM_BOT_TOKEN=your-bot-token-here

# Optional: Whitelist specific chat IDs (null = all chats allowed)
TELEGRAM_CHAT_ID=[-1001234567890,987654321]

# Default working directory for the coding agent
CLAUDE_CWD=/path/to/your/project
```

### Finding Chat IDs

To find a chat ID:

#### For Personal Chats

1. Send any message to your bot
2. Check the bot logs - the chat ID will be displayed
3. Personal chat IDs are positive numbers

#### For Groups

1. Add the bot to the group
2. Send a message in the group
3. Check the bot logs for the chat ID
4. Group chat IDs are negative numbers starting with `-100`

#### Using IDBot (Alternative)

1. Add **@username_to_id_bot** to your group
2. It will automatically display the chat ID
3. Remove the bot after getting the ID

## Step 4: Bot Permissions for Groups

If you want to use the bot in groups:

1. Talk to **@BotFather**
2. Send `/mybots`
3. Select your bot
4. Click **Bot Settings**
5. Click **Group Privacy**
6. **Disable** privacy mode

This allows the bot to see all messages in groups, not just commands.

## Step 5: Start the Bot

```bash
python main.py
```

The bot should now be running and ready to accept commands.

## Usage

### Commands

- `/start` - Display welcome message and available commands
- `/clear` - Reset the conversation context
- `/cwd` - Show current working directory
- `/set_cwd <path>` - Change working directory
- `/settings` - Configure message visibility and preferences

### Sending Messages to Your Agent

After starting the bot, simply send any message and it will be forwarded to your configured agent. The bot will:

1. Send your message to the configured agent
2. Stream the response back in real-time
3. Maintain conversation context for follow-ups

### In Groups

When using the bot in groups:

- Messages must start with `/` to be processed as commands
- Regular messages are processed as agent queries
- The bot maintains separate sessions for each chat

## Features

### Message Formatting

The bot supports Telegram's MarkdownV2 format:

- **Bold text** with `**text**`
- _Italic text_ with `*text*`
- `Code` with backticks
- `Code blocks` with triple backticks

### Inline Keyboards

The settings command provides inline keyboard buttons for:

- Show/hide raw agent output
- Show/hide thinking process
- Reset session
- Return to main menu

### Real-time Streaming

Messages from your agent are streamed in real-time:

- Long messages are automatically split
- Code blocks are properly formatted
- Progress is shown during processing

## Troubleshooting

### Bot not responding

1. Check the bot token is correct in `.env`
2. Ensure the bot is running (check logs)
3. Verify your chat ID is whitelisted (if using whitelist)

### "Unauthorized" errors

1. Double-check the bot token
2. Make sure you're using the token from BotFather
3. Regenerate the token if necessary:
   - Talk to @BotFather
   - Send `/revoke`
   - Select your bot
   - Get the new token

### Bot not seeing messages in groups

1. Ensure privacy mode is disabled (Step 4)
2. Check that the bot is an admin in the group (optional but recommended)
3. Verify the group chat ID is whitelisted if using `TELEGRAM_CHAT_ID`

### Message formatting issues

1. The bot uses MarkdownV2 which requires escaping special characters
2. If messages appear broken, check logs for parsing errors
3. Special characters like `_`, `*`, `[`, `]`, `(`, `)`, `~`, `` ` ``, `>`, `#`, `+`, `-`, `=`, `|`, `{`, `}`, `.`, `!` need escaping

### Rate limiting

Telegram has rate limits:

- 30 messages per second to different users
- 20 messages per minute to the same group
- 1 message per second to the same user

If you hit rate limits, the bot will automatically retry with delays.

## Security Considerations

### Token Security

- Never commit the bot token to version control
- Keep the token private - anyone with it can control your bot
- Regenerate the token if it's ever exposed

### Chat Whitelisting

- Use `TELEGRAM_CHAT_ID` to restrict bot access to specific chats
- This prevents unauthorized users from using your bot
- Leave it as `null` only for development/testing

### Group Security

- Consider making the bot admin with restricted permissions
- Monitor bot usage in groups
- Use chat whitelisting for production deployments

## Advanced Configuration

### Webhook Mode (Optional)

For production deployments, you can use webhooks instead of polling:

1. Set up an HTTPS endpoint
2. Configure the webhook URL with Telegram API
3. Update your bot code to handle webhook requests

### Custom Keyboards

You can implement custom keyboard layouts for frequently used commands:

- Reply keyboards for quick command access
- Inline keyboards for interactive menus
- Remove keyboards when not needed

### Persistent Settings

User settings are stored in `user_settings.json`:

- Per-user preferences
- Session management
- Message visibility options

## Additional Resources

- [Telegram Bot API Documentation](https://core.telegram.org/bots/api)
- [Python Telegram Bot Library](https://python-telegram-bot.org/)
- [BotFather Commands](https://core.telegram.org/bots#botfather)
- [Telegram Bot Best Practices](https://core.telegram.org/bots/faq)

## Tips

1. **Testing**: Create a test bot first to experiment with settings
2. **Logging**: Enable debug logging to troubleshoot issues
3. **Updates**: Keep the python-telegram-bot library updated
4. **Monitoring**: Use bot analytics to track usage
5. **Backups**: Regularly backup your `user_settings.json` file
