# Telegram Setup Guide

## TL;DR

```bash
vibe
```

Choose **Telegram** in the wizard, create a bot in **@BotFather**, paste the token, validate it, then finish the Telegram-side setup.

---

## Step 1: Create a Bot with BotFather

1. Open [@BotFather](https://t.me/BotFather) in Telegram
2. Send `/newbot`
3. Choose a display name
4. Choose a username ending in `bot`
5. Keep the generated token page open

If you already created the bot, you can skip straight to copying the token.

---

## Step 2: Paste the Token in Vibe Remote

1. Run `vibe`
2. In the setup wizard, choose **Telegram**
3. Paste the bot token from BotFather
4. Click **Validate Token**

If validation fails, make sure you copied the full token and did not include extra spaces.

---

## Step 3: Finish the BotFather Switches

Run these commands in **@BotFather** and select your bot each time:

- `/setprivacy`
- `/setjoingroups`
- `/setcommands`

Recommended settings:

- **`/setprivacy` -> `Disable`** if you want the bot to respond to normal group messages without explicit `@mentions`
- **`/setjoingroups` -> `Enable`** if the bot should be used in groups or forum-enabled supergroups
- **`/setcommands`** -> publish common commands such as `/start`, `/settings`, `/new`, `/resume`

The most common Telegram setup issue is leaving privacy mode enabled. In that state, the bot only receives commands, mentions, and replies.

---

## Step 4: Finish Setup, Bind, Then Discover Chats

Vibe Remote discovers Telegram chats from inbound messages. Telegram does not provide a generic "list every chat the bot is in" API.

Important: Telegram DMs are usable, but they stay hidden in the wizard chat-selection UI. The selectable list is for discovered groups and forum chats, not individual topics.

On first setup, do this in order:

1. Finish the Vibe Remote setup flow and start the service
2. On the final summary screen, copy the first bind command shown there
3. Open a DM with the bot and send `bind <code>` (or `/bind <code>`) to become the first admin
4. After binding, send `/start` in the DM to verify direct-message connectivity
5. If you want to use a group or forum, add the bot there and grant permission to send messages
6. For auto-created forum topics, also grant admin or topic-management rights
7. Send one message in each target group or forum chat; for forums, sending a message inside the forum helps Vibe Remote discover that chat and later use topic-related behavior there
8. In the dashboard group settings page, refresh the Telegram chat list and enable the discovered group or forum chat

If the bot only reacts to commands in groups, go back and verify `/setprivacy` is really set to `Disable`.

---

## Step 5: Choose Telegram Defaults

The wizard exposes two important Telegram defaults:

- **Require explicit bot targeting in groups**
  - When enabled, the bot responds only to commands, mentions, or replies
  - Good for busy groups
- **Forum auto-topic mode**
  - In forum-enabled supergroups, a new top-level message can create a new topic automatically
  - Requires admin or topic-management rights for the bot

---

## Using in Telegram

### Direct Messages

1. Open your bot in Telegram
2. First send `bind <code>` (or `/bind <code>`) using the bind code shown by Vibe Remote setup
3. Then send `/start`
4. Continue chatting normally

### Groups

1. Add the bot to the group
2. If `require_mention` is enabled, use `/start`, `@botname`, or reply-to-bot style messages
3. If `require_mention` is disabled, the bot can respond to normal group messages too

### Forum Topics

1. Add the bot to a forum-enabled supergroup
2. Ensure it has enough permissions
3. Send a message in the target topic so Vibe Remote discovers it

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Token validation fails | Re-copy the token from BotFather and validate again |
| Bot works in DM but not in groups | Run `/setjoingroups` and confirm it is `Enable` |
| Bot only reacts to commands or `@mentions` | Run `/setprivacy` and set it to `Disable` |
| Group or forum does not show up in the wizard | Send one message there first, then refresh the Telegram chat list |
| Forum auto-topic does not work | Grant admin or topic-management rights to the bot |

**Logs:** `~/.vibe_remote/logs/vibe_remote.log`

**Diagnostics:** `vibe doctor`
