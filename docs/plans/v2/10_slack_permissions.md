# Slack Permissions and Event Template (V2)

This template consolidates the recommended Slack scopes and event subscriptions based on `docs/SLACK_SETUP.md`.

## OAuth Scopes (Bot Token)

### Essential (Required)

- `channels:history` (public channel message history)
- `channels:read` (public channel metadata)
- `chat:write` (send messages)
- `app_mentions:read` (mentions)
- `users:read` (basic user info)
- `commands` (slash commands)

### Private Channel Support

- `groups:read` (private channel metadata)
- `groups:history` (private channel message history)
- `groups:write` (send messages to private channels)

### Enhanced Features

- `chat:write.public` (send without joining)
- `chat:write.customize` (custom username/avatar)
- `files:read` (read shared files)
- `files:write` (upload files)
- `reactions:read` (read reactions)
- `reactions:write` (add/remove reactions)
- `users:read.email` (read user emails)
- `team:read` (workspace metadata)

## Socket Mode (Self-host Only)

- App-level token scope: `connections:write`

## Event Subscriptions

### Required Bot Events

- `message.channels`
- `message.groups`
- `app_mention`

### Optional/Recommended Bot Events

- `member_joined_channel`
- `member_left_channel`
- `channel_created`
- `channel_renamed`
- `team_join`

## Slash Commands (Self-host Socket Mode)

Create the following commands with empty Request URL:

- `/start` (Open main menu)
- `/stop` (Stop current session)

## Notes

- Add all scopes up front to avoid reinstall loops.
- Socket Mode is used for self-host; SaaS uses Events API.
- Slash command creation is manual in Slack UI.
