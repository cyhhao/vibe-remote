# Feishu DM Thread Sessions And DM Thread Research

## Background

- Discord DMs were intentionally changed to avoid thread-based session routing because Discord DMs do not support threads.
- Feishu DMs currently still carry thread metadata, but the shared session base-id logic treats all DMs as a single session.
- That mismatch causes Feishu DM conversations to open threads while still collapsing into one session.
- We also need to verify whether Slack and Discord DMs can support thread-based session semantics.

## Goal

- Make Feishu DMs follow the same session model as normal channel threads: one thread equals one session.
- Keep Discord DMs on the existing single-DM-session behavior.
- Document the current product/platform reality for Slack and Discord DM thread capability.

## Proposed Solution

1. Add a platform capability hook for whether DMs support thread-based sessions.
2. Use that hook in the shared `SessionHandler` so DM session base IDs can be thread-based only on platforms that support it.
3. Set Slack and Feishu to support DM threads, and keep Discord disabled.
4. Add targeted tests for DM session base-id behavior.
5. Research Slack and Discord DM thread support from platform documentation and summarize the result.

## Research Findings

- Slack supports threads in direct messages. Slack Help explicitly says threads help avoid clutter in "a channel or direct message (DM) conversation" and documents replying in a DM thread.
- Discord threads are guild-channel features, not DM features. Discord's Threads FAQ and developer docs describe threads as sub-channels inside existing server channels, with parent types like `GUILD_TEXT` and `GUILD_ANNOUNCEMENT`.
- Product decision for Vibe Remote should therefore be:
  - Slack DM: thread-based sessions are supported.
  - Feishu DM: thread-based sessions are supported.
  - Discord DM: keep single-DM-session behavior.

## Todo

- [x] Add shared IM capability for DM thread sessions.
- [x] Update session base-id logic to respect platform DM thread capability.
- [x] Add regression tests for Slack/Discord/Feishu DM session behavior.
- [x] Research Slack and Discord DM thread support and summarize the findings.
- [x] Validate changes and sync them to the three-end regression environment.
