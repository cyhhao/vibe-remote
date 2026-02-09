# Discord IM Support Plan

## Background
Vibe Remote currently supports Slack as the only IM transport. The goal is to add Discord as a first-class IM platform, matching Slack feature coverage while preserving existing Slack behavior. This includes full onboarding/configuration flow, UI copy, documentation, and operational parity.

## Goals
- Add Discord as a supported IM platform alongside Slack (no regression to Slack).
- Align all Slack features in Discord with equivalent UX and behavior (commands, buttons, modals, reactions, file uploads, routing, settings, resume session, update notifications, etc.).
- Provide end-to-end setup flow (wizard + docs + copy) for Discord, in both English and Chinese.
- Keep configuration backward compatible and safe for existing Slack users.

## Non-Goals
- Voice, stage, or message threading beyond Discord's native capabilities.
- Multi-workspace Slack redesign or SaaS mode.
- New agent backends or new CLI commands unrelated to Discord.

## Key Parity Surface (Slack -> Discord)
- Message intake: mentions, direct messages, channel messages, and thread messages.
- Commands: `/start`, `/clear`, `/cwd`, `/set_cwd`, `/settings`, `/stop`, `/resume`, `/routing`.
- Interactive UI: buttons, select menus, and modals for settings/routing/change cwd/resume session.
- Message formatting (markdown): bold/italic/links/code blocks/quotes/lists.
- Ack mode: reaction (eyes) and ack message.
- Inline actions: callback handling for buttons/menus and question modals.
- File handling: upload result as markdown, download attachments, and persistent storage under `~/.vibe_remote/attachments`.
- Update notifications and "update now" action.
- Per-channel routing and settings storage.

## Proposed Architecture Changes
1) Config model
   - Add `platform` to `V2Config` with allowed values: `slack` | `discord`.
   - Introduce `DiscordConfig` in `config/v2_config.py` with required fields (bot token, app ID, public key if needed, guild allowlist/denylist, intents flags).
   - Update `to_app_config` in `config/v2_compat.py` to pass platform and discord config into `AppCompatConfig`.

2) IM abstraction
   - Add `modules/im/discord.py` implementing `BaseIMClient` for Discord.
   - Add `DiscordFormatter` in `modules/im/formatters/discord_formatter.py`.
   - Update `IMFactory` to create Slack or Discord client based on `config.platform`.
   - Extend optional IM client capabilities for:
     - `open_settings_modal`, `open_change_cwd_modal`, `open_routing_modal`, `update_routing_modal`, `open_resume_session_modal`.
     - `delete_message`, `download_file`, `upload_markdown`, reactions, and interaction handling.

3) Controller & session semantics
   - Replace Slack-only assumptions:
     - `SessionHandler.get_base_session_id` should be platform-aware (`{platform}_{thread_or_channel_or_message_id}`).
     - `Controller._get_settings_key` should remain channel-based but handle Discord DM/guild channel IDs.
   - Update any Slack-only logic in handlers to branch on `config.platform`.

4) Update notifications
   - Generalize update checker to send notification via IM client, not Slack WebClient.
   - Implement Discord equivalent of "Update Now" using a button interaction.

5) API + Web UI
   - Add Discord setup endpoints in `vibe/api.py`:
     - token validation, bot identity, guild list, and channel list (where possible).
   - Update Web UI wizard to include platform selection and a Discord configuration step.
   - Update i18n keys in `ui/src/i18n/en.json` + `zh.json` and server i18n in `vibe/i18n/*`.

## Discord Implementation Details (Proposed)
- Library: `discord.py` 2.x (add dependency in `pyproject.toml`).
- Connection: bot token + gateway; run with `discord.Client` or `commands.Bot` and `app_commands` for slash commands.
- Intents: enable `GUILD_MESSAGES`, `DIRECT_MESSAGES`, `MESSAGE_CONTENT`, `GUILDS`, and `REACTIONS`.
- Interactions:
  - Buttons and select menus via `discord.ui.View`.
  - Modals via `discord.ui.Modal` (text input only). Use follow-up menus to emulate Slack select in routing/settings.
- Threading:
  - Always create a real Discord thread per new user message (Slack parity: per-message session).
  - Use the created thread ID as `thread_id` and bind the session to that thread.
  - If message already arrives in a thread, reuse that thread.
- Mentions:
  - Respect `require_mention` by checking `bot.user.mentioned_in(message)` in guild channels.

## Setup Flow & Copy
- Wizard:
  - Add platform selector (Slack/Discord) before platform-specific config.
  - Discord config step: bot token, optional guild allowlist, and permissions/intents guidance.
  - Channel list step should support Discord channels (guild selection + channel checkboxes).
- Docs:
  - Add `docs/DISCORD_SETUP.md` and `docs/DISCORD_SETUP_ZH.md`.
  - Update `README.md` and `README_ZH.md` to mention Discord support and setup links.
- Slack docs remain unchanged.

## Data Model & Migration
- Backward compatible: existing configs without `platform` default to `slack`.
- Discord config section optional unless platform=discord.
- Settings store remains channel-based; no migration needed beyond platform prefix in session IDs.

## Testing Plan
- Unit-style checks:
  - Config load/validate for Discord and Slack.
  - Formatter parity (markdown examples).
- Manual E2E:
  - Discord bot setup and OAuth invite flow.
  - DM and channel interactions (commands, buttons, modals).
  - File attachments upload/download.
  - Routing + resume session flows.
  - Update notification button flow.

## Milestones
1) Config + IM factory + formatter + minimal Discord client (send/receive messages).
2) Commands + settings + routing + resume session interactions on Discord.
3) Attachments, reactions, update notifications, and parity polish.
4) Wizard + docs + i18n copy.

## Open Questions
- For routing/settings UI on Discord, do we accept multi-step ephemeral menus instead of a single modal (Discord limitation)?
