# Slack Bot Setup Guide

This guide will walk you through setting up a Slack bot for Vibe Remote.

## Prerequisites

- Admin access to a Slack workspace
- Python 3.9 or higher installed
- Vibe Remote installed (`curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash`)

## Step 1: Create a Slack App

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps)
2. Click **"Create New App"** → **"From an app manifest"**
3. Select your workspace
4. Paste the following manifest (YAML):

```yaml
display_information:
  name: Vibe Remote
  description: Local-first agent runtime for Slack
  background_color: "#0B1B2B"
features:
  bot_user:
    display_name: Vibe Remote
    always_online: false
  slash_commands:
    - command: /start
      description: Open main menu
      should_escape: false
    - command: /stop
      description: Stop current session
      should_escape: false
oauth_config:
  scopes:
    bot:
      - channels:history
      - channels:read
      - chat:write
      - app_mentions:read
      - users:read
      - commands
      - groups:read
      - groups:history
      - groups:write
      - chat:write.public
      - files:read
      - files:write
      - reactions:read
      - reactions:write
      - users:read.email
      - team:read
settings:
  event_subscriptions:
    bot_events:
      - message.channels
      - message.groups
      - app_mention
      - member_joined_channel
      - member_left_channel
      - channel_created
      - channel_renamed
      - team_join
  socket_mode_enabled: true
  interactivity:
    is_enabled: true
```

5. Click **"Next"**, review the configuration, and click **"Create"**

## Step 2: Generate Tokens

### Bot Token

1. Go to **"OAuth & Permissions"** in the sidebar
2. Click **"Install to Workspace"**
3. Review permissions and click **"Allow"**
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

### App Token (Socket Mode)

1. Go to **"Basic Information"** in the sidebar
2. Scroll to **"App-Level Tokens"**
3. Click **"Generate Token and Scopes"**
4. Name: `socket-mode`
5. Add scope: `connections:write`
6. Click **"Generate"**
7. Copy the **App-Level Token** (starts with `xapp-`)

## Step 3: Configure Vibe Remote

Run Vibe Remote to open the setup wizard:

```bash
vibe
```

The web UI will open at `http://localhost:5173`. Follow the setup wizard:

1. **Choose Mode**: Select "Self-host (Socket Mode)"
2. **Agent Detection**: Configure your coding agent backends (OpenCode, Claude, Codex)
3. **Slack Configuration**: 
   - Paste your **Bot Token** (`xoxb-...`)
   - Paste your **App Token** (`xapp-...`)
   - Click **"Validate"** to test the connection
4. **Channel Settings**: Enable the channels where you want the bot to work
5. **Summary**: Review and start the service

## Step 4: Invite Bot to Channels

Before the bot can interact with a channel, invite it:

1. Go to the channel in Slack
2. Type `/invite @Vibe Remote`
3. Press Enter

## Step 5: Test the Bot

1. In an enabled channel, type `/start`
2. You should see the Vibe Remote menu
3. Type a message to start a coding session

## Usage

### Slash Commands

- `/start` - Open main menu with interactive buttons
- `/stop` - Stop the current session

### Interactive Menu Options

After `/start`, you'll see buttons for:
- **Current Dir** - Display current working directory
- **Change Work Dir** - Open modal to change working directory
- **Reset Session** - Clear conversation context
- **Settings** - Configure message visibility
- **How it Works** - Display help information

### Thread-based Conversations

- The bot creates threads for each conversation
- Reply in the thread to continue the session
- Each thread maintains its own agent session

## Troubleshooting

### Bot not responding

1. Check that the service is running: `vibe status`
2. Check logs: `~/.vibe_remote/logs/vibe_remote.log`
3. Run diagnostics: `vibe doctor`
4. Verify the bot is invited to the channel

### Permission errors

1. Verify tokens are correctly set (check via web UI)
2. Ensure all required scopes are in the app manifest
3. Reinstall the app to workspace if scopes were changed

### Socket Mode issues

1. Verify App Token (`xapp-`) is set correctly
2. Check that Socket Mode is enabled in app settings
3. Ensure the app-level token has `connections:write` scope

### Channel access issues

1. Ensure bot is invited to the channel: `/invite @Vibe Remote`
2. For private channels, verify `groups:read`, `groups:history` scopes
3. Check channel is enabled in the web UI

## Security Considerations

- Tokens are stored locally in `~/.vibe_remote/config/config.json`
- Never commit tokens to version control
- The web UI runs on localhost only
- Regularly rotate your tokens via Slack app settings

## Manual Configuration (Alternative)

If you prefer to edit config files directly instead of using the web UI:

Edit `~/.vibe_remote/config/config.json`:

```json
{
  "mode": "self_host",
  "slack": {
    "bot_token": "xoxb-your-bot-token",
    "app_token": "xapp-your-app-token"
  },
  "runtime": {
    "default_cwd": "/path/to/your/project"
  },
  "agents": {
    "default_backend": "opencode",
    "opencode": {"enabled": true, "cli_path": "opencode"},
    "claude": {"enabled": true, "cli_path": "claude"},
    "codex": {"enabled": false, "cli_path": "codex"}
  }
}
```

Then start the service:

```bash
vibe
```

## Additional Resources

- [Slack API Documentation](https://api.slack.com/)
- [Socket Mode Guide](https://api.slack.com/apis/connections/socket)
- [Vibe Remote GitHub](https://github.com/cyhhao/vibe-remote)
