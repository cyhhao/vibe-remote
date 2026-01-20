# Slack Setup Guide

## TL;DR

```bash
vibe
```

Browser opens -> Follow the wizard -> Done!

---

## Step 1: Welcome

Run `vibe` to launch the setup wizard. Your browser opens automatically:

![Setup Welcome](../assets/screenshots/setup-welcome-en.png)

Click **Get started** to begin.

---

## Step 2: Slack Configuration

The wizard guides you through creating a Slack App:

![Slack Configuration](../assets/screenshots/setup-slack-en.png)

1. Click **Create Slack App** - opens Slack with manifest pre-filled
2. Select your workspace and click **Create**
3. Follow the accordion steps to get **Bot Token** (`xoxb-`) and **App Token** (`xapp-`)
4. Click **Validate Tokens** to verify

<details>
<summary><b>Manual Setup (if needed)</b></summary>

Go to [api.slack.com/apps](https://api.slack.com/apps) and create app with this manifest:

```json
{
  "_metadata": {
    "major_version": 1,
    "minor_version": 1
  },
  "display_information": {
    "name": "Vibe Remote",
    "description": "AI coding agent runtime for Slack",
    "background_color": "#262626"
  },
  "features": {
    "bot_user": {
      "display_name": "Vibe Remote",
      "always_online": true
    },
    "app_home": {
      "home_tab_enabled": true,
      "messages_tab_enabled": true,
      "messages_tab_read_only_enabled": false
    }
  },
  "oauth_config": {
    "scopes": {
      "bot": [
        "channels:history",
        "channels:read",
        "chat:write",
        "app_mentions:read",
        "users:read",
        "commands",
        "groups:read",
        "groups:history",
        "im:history",
        "im:read",
        "im:write",
        "mpim:history",
        "mpim:read",
        "mpim:write",
        "files:read",
        "files:write",
        "reactions:read",
        "reactions:write"
      ]
    }
  },
  "settings": {
    "event_subscriptions": {
      "bot_events": [
        "message.channels",
        "message.groups",
        "message.im",
        "message.mpim",
        "app_mention",
        "reaction_added",
        "reaction_removed"
      ]
    },
    "interactivity": {
      "is_enabled": true
    },
    "org_deploy_enabled": true,
    "socket_mode_enabled": true,
    "token_rotation_enabled": false
  }
}
```
</details>

---

## Step 3: Review & Finish

Review your configuration and click **Finish & Start**:

![Review & Finish](../assets/screenshots/setup-finish-en.png)

The wizard shows quick tips on how to use Vibe Remote.

---

## Step 4: Dashboard

Once setup is complete, you'll see the Dashboard:

![Dashboard](../assets/screenshots/dashboard-en.png)

From here you can:
- Start/stop the service
- Configure message handling options
- Manage channel settings

---

## Using in Slack

1. Invite bot to channel: `/invite @Vibe Remote`
2. Type `@Vibe Remote /start` to open control panel
3. Start coding!

**Tips:**
- Each Slack thread = isolated session
- Start multiple threads for parallel tasks
- Use `AgentName: message` to route to specific agent

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Bot not responding | Check `vibe status`, ensure bot is invited to channel |
| Permission error | Reinstall app to workspace |
| Socket error | Verify `xapp-` token has `connections:write` scope |

**Logs:** `~/.vibe_remote/logs/vibe_remote.log`

**Diagnostics:** `vibe doctor`
