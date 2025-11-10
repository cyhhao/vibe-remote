# Slack Bot Setup Guide

This guide will walk you through setting up a Slack bot for Vibe Remote.

## Prerequisites

- Admin access to a Slack workspace
- Python 3.6 or higher installed
- Vibe Remote cloned and dependencies installed

## Step 1: Create a Slack App

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps)
2. Click **"Create New App"** → **"From scratch"**
3. Enter an app name (e.g., "Vibe Remote")
4. Select your workspace
5. Click **"Create App"**

## Step 2: Configure Bot Token Scopes

1. In your app's settings, navigate to **"OAuth & Permissions"** in the sidebar
2. Scroll down to **"Scopes"** → **"Bot Token Scopes"**
3. Add ALL the following OAuth scopes to avoid permission issues:

### Essential Scopes (Required)

- `channels:history` - View messages in public channels
- `channels:read` - View basic information about public channels
- `chat:write` - Send messages as bot
- `app_mentions:read` - View messages that mention your bot
- `users:read` - View basic information about users
- `commands` - Use slash commands (automatically added)

### Private Channel Support

- `groups:read` - View basic information about private channels
- `groups:history` - View messages in private channels
- `groups:write` - Send messages to private channels

### Enhanced Features

- `chat:write.public` - Send messages to channels without joining
- `chat:write.customize` - Send messages with custom username and avatar
- `files:read` - View files shared in channels (for future file handling)
- `files:write` - Upload files (for future file upload support)
- `reactions:read` - View emoji reactions
- `reactions:write` - Add emoji reactions
- `users:read.email` - View email addresses (for enhanced user info)
- `team:read` - View team/workspace information

**Note**: It's better to add all permissions now to avoid reinstalling the app multiple times later. Unused permissions won't affect the bot's performance.

### Quick Permission Checklist

To ensure full functionality, make sure you've added:

- ✅ All Essential Scopes (6 scopes)
- ✅ All Private Channel scopes (3 scopes)
- ✅ Any Enhanced Features you want (up to 8 additional scopes)

**Total recommended scopes: ~17 scopes** for full functionality.

## Step 3: Install App to Workspace

1. Still in **"OAuth & Permissions"**, scroll to the top
2. Click **"Install to Workspace"**
3. Review the permissions and click **"Allow"**
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`)
   - Save this as `SLACK_BOT_TOKEN` in your `.env` file

## Step 4: Enable Socket Mode (Recommended)

Socket Mode allows your bot to connect without exposing a public URL.

1. Go to **"Socket Mode"** in the sidebar
2. Toggle **"Enable Socket Mode"** to On
3. You'll be prompted to generate an app-level token:
   - Token Name: "Socket Mode Token"
   - Add scope: `connections:write`
   - Click **"Generate"**
4. Copy the **App-Level Token** (starts with `xapp-`)
   - Save this as `SLACK_APP_TOKEN` in your `.env` file

## Step 5: Configure Event Subscriptions

1. Go to **"Event Subscriptions"** in the sidebar
2. Toggle **"Enable Events"** to On
3. Under **"Subscribe to bot events"**, add ALL these events:

### Message Events

- `message.channels` - Messages in public channels
- `message.groups` - Messages in private channels
- `app_mention` - When someone mentions your bot

### Additional Events (Optional but Recommended)

- `member_joined_channel` - When bot joins a channel
- `member_left_channel` - When bot leaves a channel
- `channel_created` - When a new channel is created
- `channel_renamed` - When a channel is renamed
- `team_join` - When a new member joins the workspace

4. Click **"Save Changes"**

**Note**: Adding all events now prevents the need to reconfigure later when expanding bot functionality.

## Step 6: Configure Native Slash Commands (Recommended)

Native Slack slash commands provide the best user experience with autocomplete and help text. Here's a detailed step-by-step guide:

### 6.1 Access Slash Commands Configuration

1. Navigate to [https://api.slack.com/apps](https://api.slack.com/apps)
2. Log in to your Slack account (if not already logged in)
3. Find your Vibe Remote app in the "Your Apps" list
4. Click on your app name to enter the management interface
5. In the left sidebar, find **"Features"** section and click **"Slash Commands"**
6. You'll see the "Slash Commands" page showing any existing commands

**Page Overview**:

- New apps will show "You haven't created any slash commands yet"
- Green **"Create New Command"** button is in the top right corner
- Existing commands appear in a list with edit options

### 6.2 Create Commands

**Steps for each command**:

1. Click **"Create New Command"** button
2. Fill in the command form (see specific configurations below)
3. Click **"Save"** to save the command
4. Wait for confirmation - Slack will show "Your slash command was saved!" message
5. Return to command list to verify creation

**Important Notes**:

- Commands must be created one at a time
- Command names cannot be duplicated or contain spaces
- All fields are case-sensitive

### 6.3 Required Commands

**Configuration Guidelines**:

- Leave **Request URL** empty for all commands (we use Socket Mode)
- Check **"Escape channels, users, and links"** for all commands
- Copy and paste the configurations below

---

#### Command 1: `/start`

```
Command: /start
Request URL: (leave empty)
Short Description: Open main menu with interactive buttons
Usage Hint: Start Vibe Remote and show menu
```

**Steps**:

1. Click **"Create New Command"**
2. Fill in **"Command"** field: `/start`
3. Leave **"Request URL"** blank
4. Fill in **"Short Description"**: `Open main menu with interactive buttons`
5. Fill in **"Usage Hint"**: `Start Vibe Remote and show menu`
6. ✅ Check "Escape channels, users, and links"
7. Click **"Save"**
8. Return to Slash Commands page

#### Command 2: `/stop`

```
Command: /stop
Request URL: (leave empty)
Short Description: Stop Vibe Remote session
Usage Hint: Stop the current bot session
```

**Steps**:

1. Click **"Create New Command"**
2. Fill in **"Command"** field: `/stop`
3. Leave **"Request URL"** blank
4. Fill in **"Short Description"**: `Stop Vibe Remote session`
5. Fill in **"Usage Hint"**: `Stop the current bot session`
6. ✅ Check "Escape channels, users, and links"
7. Click **"Save"**

**✅ Complete**: After creating both commands, you should see them listed on the Slash Commands page.

### 6.4 Important Configuration Notes

- **Request URL**: Always leave this empty when using Socket Mode (recommended)
- **Socket Mode Required**: These commands only work when Socket Mode is enabled (Step 4)
- **Case Sensitive**: Command names are case-sensitive
- **No Automation**: Currently, Slack doesn't provide an API to create slash commands programmatically - they must be created manually through the web interface
- **3-Second Response**: The bot has 3 seconds to acknowledge slash commands (handled automatically)

### 6.5 Verification

After creating both commands:

1. Go back to the main "Slash Commands" page
2. You should see both `/start` and `/stop` commands listed
3. Each command should show "✅ Configured" status

### 6.6 Setup Checklist

**Verify your configuration**:

- [ ] Accessed [https://api.slack.com/apps](https://api.slack.com/apps) and selected your app
- [ ] Navigated to "Features" > "Slash Commands" page
- [ ] Created `/start` command
- [ ] Created `/stop` command
- [ ] All commands have empty **Request URL**
- [ ] All commands have **"Escape channels, users, and links"** checked
- [ ] Both commands appear on the Slash Commands page

### 6.7 Troubleshooting Slash Commands

If commands not showing in autocomplete:

- Reinstall app to workspace via "OAuth & Permissions" page
- Verify Socket Mode is enabled
- Ensure bot is invited to channel: `/invite @YourBotName`

If commands execute but no response:

- Check bot process is running
- Verify `SLACK_APP_TOKEN` and `SLACK_BOT_TOKEN` are correctly configured

## Step 7: Configure Your Environment

Update your `.env` file with the following:

```env
# Set platform to slack
IM_PLATFORM=slack

# Slack configuration
SLACK_BOT_TOKEN=xoxb-your-bot-token-here
SLACK_APP_TOKEN=xapp-your-app-token-here

# Optional: Whitelist of allowed channel IDs (null = all channels)
SLACK_TARGET_CHANNEL=[C1234567890,C0987654321]
```

### Finding Channel IDs

To find a channel ID:

1. Right-click on the channel name in Slack
2. Select **"View channel details"**
3. Scroll to the bottom
4. The Channel ID starts with `C` for public channels

## Step 8: Invite Bot to Channels

Before the bot can interact with a channel, you need to invite it:

1. In Slack, go to the channel
2. Type `/invite @YourBotName`
3. Press Enter

## Step 9: Start the Bot

```bash
python main.py
```

## Usage

### Command Methods

The bot supports multiple ways to interact:

#### 1. Slash Commands (Recommended)

```
/start
/stop
```

- Provides autocomplete and help text
- Works in any channel where the bot is invited
- Most user-friendly experience

#### 2. In Channels

- Use `/start` to begin
- The bot creates threads to organize conversations
- All responses appear in the thread

### Using the Bot

#### Primary Command

- `/start` - Opens the main menu with interactive buttons:
  - **Current Dir** - Display current working directory
  - **Change Work Dir** - Open modal to change working directory
  - **Reset Session** - Clear conversation context and start fresh
  - **Settings** - Configure message visibility preferences
  - **How it Works** - Display help information

#### Additional Command

- `/stop` - Stop the current Vibe Remote session

#### Sending Messages to Your Agent

After using `/start`, simply type your message in the channel. The bot will:

1. Send your message to the configured coding agent
2. Stream the response back in real-time
3. Maintain conversation context for follow-ups

## Thread Support

The Slack bot automatically uses threads to organize conversations:

- Each user's messages are grouped in a thread
- Responses from the configured agent appear in the same thread
- This keeps channel history clean and organized

## Troubleshooting

### Bot not responding

1. Check that the bot is online (green dot in Slack)
2. Verify the bot was invited to the channel
3. Check logs for any error messages

### Permission errors

1. Ensure all required scopes are added (including `groups:read` for private channels)
2. **Important**: After adding new scopes, you MUST reinstall the app to workspace:
   - Go to **"OAuth & Permissions"** page
   - Click **"Reinstall to Workspace"** button at the top
   - Review and approve the new permissions
3. Verify tokens are correctly set in `.env`

### Private Channel Access Issues

If you see `missing_scope` errors with `groups:read`:

1. Add ALL private channel scopes: `groups:read`, `groups:history`, `groups:write`
2. Click **"Reinstall to Workspace"** (this is mandatory!)
3. Copy the new Bot Token and update your `.env` file
4. Restart the bot
5. Ensure bot is invited to the private channel: `/invite @YourBotName`

### Thread Reply Issues

If you see `cannot_reply_to_message` error:

1. This usually means the bot is trying to reply to a message that doesn't exist or in wrong context
2. Ensure the bot has `channels:history` or `groups:history` permission for the channel type
3. Check that the message timestamp (thread_ts) is valid
4. Verify the bot is a member of the channel where it's trying to reply

### Socket Mode issues

1. Ensure `SLACK_APP_TOKEN` is set correctly
2. Check that the app-level token has `connections:write` scope
3. Verify Socket Mode is enabled in app settings

### Slash Command Issues

If slash commands not working:

1. Verify commands created in app settings
2. Check Socket Mode is enabled
3. Ensure bot is invited to channel: `/invite @YourBotName`
4. Restart bot if needed

Check bot logs for startup confirmation:

```
INFO - Starting Slack bot in Socket Mode...
INFO - A new session has been established
```

## Alternative: Webhook Mode

If you prefer to use webhooks instead of Socket Mode:

1. Set up a public HTTPS endpoint
2. Configure the Request URL in Event Subscriptions
3. Add `SLACK_SIGNING_SECRET` to your `.env`
4. Remove or leave empty `SLACK_APP_TOKEN`

Note: Webhook mode requires a publicly accessible HTTPS endpoint, which is more complex to set up.

## Security Considerations

- Never commit tokens to version control
- Use environment variables for all sensitive data
- Regularly rotate your tokens
- Only grant necessary permissions to the bot

## Additional Resources

- [Slack API Documentation](https://api.slack.com/)
- [Python Slack SDK](https://slack.dev/python-slack-sdk/)
- [Socket Mode Guide](https://api.slack.com/apis/connections/socket)
