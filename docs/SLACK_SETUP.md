# Slack Setup (5 minutes)

## TL;DR

1. Create Slack App with manifest below
2. Get two tokens (`xoxb-` and `xapp-`)
3. Run `vibe` and paste them in the web UI

---

## Step 1: Create App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. **Create New App** → **From an app manifest**
3. Select workspace
4. Paste this YAML:

```yaml
display_information:
  name: Vibe Remote
  description: AI coding agent for Slack
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
      - chat:write.public
      - app_mentions:read
      - users:read
      - commands
      - groups:read
      - groups:history
      - groups:write
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

5. Click **Create**

---

## Step 2: Get Tokens

### Bot Token (`xoxb-`)

1. **OAuth & Permissions** → **Install to Workspace** → **Allow**
2. Copy the **Bot User OAuth Token**

### App Token (`xapp-`)

1. **Basic Information** → **App-Level Tokens** → **Generate Token**
2. Name: `socket-mode`
3. Add scope: `connections:write`
4. **Generate** → Copy the token

---

## Step 3: Configure

```bash
vibe
```

Web UI opens. Paste your tokens. Click validate. Done.

---

## Step 4: Use

1. Invite bot to channel: `/invite @Vibe Remote`
2. Type `/start`
3. Start coding

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Bot not responding | Check `vibe status`, ensure bot is invited |
| Permission error | Reinstall app to workspace |
| Socket error | Verify `xapp-` token has `connections:write` |

Logs: `~/.vibe_remote/logs/vibe_remote.log`

Diagnostics: `vibe doctor`
