## TL;DR

```bash
vibe
```

Choose **Discord** in the wizard, paste your bot token, validate, pick a guild, enable channels.

---

## Step 1: Create a Discord App

1. Open the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application** and name it
3. Go to **Bot** → **Add Bot**
4. Copy the **Bot Token**

---

## Step 2: Enable Intents

In **Bot → Privileged Gateway Intents**, enable:

- Message Content Intent
- Server Members Intent (optional)

---

## Step 3: Invite the Bot

1. Go to **OAuth2 → URL Generator**
2. Select **bot** scope
3. Add permissions:
   - Read Messages/View Channels
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Add Reactions
   - Attach Files
4. Open the generated URL and invite the bot to your guild

---

## Step 4: Configure in Wizard

1. Run `vibe`
2. Select **Discord**
3. Paste bot token → **Validate Token**
4. Select your guild
5. Enable channels in the **Channels** step

---

## Notes

- Vibe Remote creates a new Discord thread for each new user message to match Slack session behavior.
- For DMs, Discord does not support threads; sessions are still isolated per message.
