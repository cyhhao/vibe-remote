# Unified Channel Inventory Implementation Plan

> Status: Implementation plan
> Branch: `feature/unified-channel-inventory-impl`
> Created: 2026-05-13

## Problem

The `/groups` page is slower than `/users` because Slack, Discord, and Lark
fetch live platform APIs on every page open. Telegram has the opposite shape:
it cannot actively list chats, so it keeps a legacy `discovered_chats.json`
file populated from inbound messages.

The repository already has the right persistent model:

- `scopes`: all known runtime scopes, including channels, users, guilds, and
  platform-level records.
- `scope_settings`: optional user configuration for a scope.
- `state_meta`: small key/value records for migration markers and runtime
  bookkeeping.

The implementation should finish that migration instead of adding a new cache
table.

## Product Goal

Make `scopes` the single source of truth for channel inventory. The UI should
load cached channels immediately, then refresh stale platform data in the
background. User configuration remains in `scope_settings`.

Expected behavior:

1. `/groups` is fast after the first successful refresh.
2. Telegram-discovered chats are stored in SQLite, not the legacy JSON store.
3. Slack, Discord, and Lark refreshes update `scopes` rather than returning
   live API results directly.
4. Configured rows are never auto-deleted just because a platform refresh no
   longer returns them.

## Data Model

No new tables and no schema-level columns.

### `scopes`

Use the existing columns as the canonical identity and inventory fields:

- `id`: stable `"{platform}::{scope_type}::{native_id}"`.
- `platform`: `slack`, `discord`, `telegram`, `lark`, etc.
- `scope_type`: `channel`, `guild`, `user`, or `platform`.
- `native_id`: platform-native identifier.
- `parent_scope_id`: parent relationship, especially Discord guild -> channel.
- `display_name`: best-known display name.
- `native_type`: platform-native channel/chat type.
- `is_private`: common privacy flag.
- `supports_threads`: common thread/topic capability.
- `metadata_json`: controlled platform-specific metadata.
- `first_seen_at`, `last_seen_at`, `updated_at`: inventory timestamps.

### `scope_settings`

Keep this table strictly for user configuration:

- enabled/disabled state
- role/workdir/routing/model settings
- require-mention behavior
- opaque settings JSON for existing compatibility

Do not add discovery, cache, refresh, or visibility fields here.

### `metadata_json`

Use `metadata_json` for channel inventory fields that are either
platform-specific or not stable enough to deserve formal columns yet.

Known keys for `scope_type = "channel"`:

```jsonc
{
  "username": "string|null",
  "topic": "string|null",
  "platform_archived": "bool|null",
  "visibility_status": "visible|not_returned|unknown",
  "is_member": "bool|null",
  "last_refreshed_at": "ISO 8601|null",
  "last_missing_at": "ISO 8601|null",

  "is_forum": "bool|null",
  "supports_topics": "bool|null",

  "channel_position": "number|null",
  "channel_category_id": "string|null",

  "chat_mode": "string|null"
}
```

`visibility_status` is not the same as archive:

- `visible`: the latest successful refresh for that refresh scope returned the
  row.
- `not_returned`: a successful refresh did not return a previously known row.
- `unknown`: no active refresh has established current visibility.

`platform_archived` is only true when the platform explicitly reports an
archive/deleted state. Missing from a response is not enough evidence.

All metadata writes go through the new inventory service. Adapters must not
write arbitrary blobs directly.

### `state_meta`

Add conventional keys:

```jsonc
{
  "channel_refresh.slack": {
    "last_attempt_at": "ISO 8601|null",
    "last_success_at": "ISO 8601|null",
    "last_error": "string|null"
  },
  "channel_refresh.discord": {
    "last_attempt_at": "ISO 8601|null",
    "last_success_at": "ISO 8601|null",
    "last_error": "string|null"
  },
  "channel_refresh.lark": {
    "last_attempt_at": "ISO 8601|null",
    "last_success_at": "ISO 8601|null",
    "last_error": "string|null"
  },
  "migrations.discovered_chats_to_scopes": "done"
}
```

Telegram has no active refresh state because the Bot API cannot list all chats.

## Service Boundary

Add `core/chat_discovery.py` as the only write/read service for channel
inventory.

Public surface:

- `remember_chat(...)`: passive discovery path. Used by Telegram immediately
  and optionally by Slack/Discord/Lark message handlers later.
- `list_chats(platform, ...)`: cached read path. Returns `scopes` rows joined
  with `scope_settings` so the UI can know whether a row is configured.
- `refresh_platform(platform, force=False, ...)`: active refresh path for
  Slack, Discord, and Lark.
- `refresh_state(platform)`: returns `state_meta` refresh bookkeeping.
- `migrate_legacy_discovered_chats()`: one-shot compatibility import from the
  legacy JSON file into `scopes`.

Important boundary: existing `upsert_scope()` is a low-level identity helper.
It does not merge `metadata_json`. `chat_discovery` must read existing
metadata, normalize incoming metadata, preserve unknown keys, then write the
merged blob.

## Refresh Behavior

### Cached-first endpoints

Change these endpoints to read from SQLite first:

- `POST /slack/channels`
- `POST /discord/channels`
- `POST /lark/chats`
- `POST /telegram/chats`

Cold cache behavior:

- If no cached rows exist for a requested active-refresh platform, do one
  synchronous refresh so first-run behavior is no worse than today.

Warm cache behavior:

- Return cached rows immediately.
- If stale or explicitly forced, schedule refresh in the background.
- Return `refreshing: true` and the last known refresh state.

Errors:

- Keep returning stale cached rows when refresh fails.
- Record `last_attempt_at` and `last_error`.
- Do not update `last_success_at` on failure.

### Slack

Use existing Slack API helper behavior as the refresh source.

On successful refresh:

- Upsert returned channels as `scope_type = "channel"`.
- Set `visibility_status = "visible"` and `last_refreshed_at`.
- Preserve user settings by leaving `scope_settings` untouched.
- Mark previously known Slack channel rows absent from the response as
  `visibility_status = "not_returned"` and set `last_missing_at`.

### Discord

Discord refresh must be guild-aware.

On successful refresh:

1. Refresh guild scope rows where available.
2. Determine refresh guild set from configured/allowed guilds and the current
   endpoint request.
3. Refresh channels for those guilds.
4. Upsert channel rows with `parent_scope_id` pointing to the guild scope.
5. Mark absent rows as `not_returned` only within the refreshed guild scope.

Do not treat channels from unrefreshed guilds as missing.

### Lark

Use the joined chat list as the active refresh source.

On successful refresh:

- Upsert joined chats as channel scopes.
- Set `visibility_status = "visible"`.
- Mark previously known Lark rows absent from the response as `not_returned`.

### Telegram

Telegram has no active refresh.

Replace the legacy `_remember_discovered_chat()` write path with
`chat_discovery.remember_chat(platform="telegram", ...)`.

Use a two-layer write mitigation:

1. Process-local debounce keyed by `(platform, chat_id)` checks the normalized
   payload before opening SQLite.
2. After debounce expiry, read the existing row and skip the SQLite write if
   only a too-recent `last_seen_at` would change.

Target default: at most one write per unchanged chat per 60 seconds.

## Legacy Migration

The existing JSON-to-SQLite importer already imports `discovered_chats.json`
when SQLite state is first created. Still add a compatibility migration because
some users may have continued writing Telegram discoveries to the JSON file
after SQLite was created.

Migration rules:

- Guard with `state_meta["migrations.discovered_chats_to_scopes"] == "done"`.
- If the file is absent, mark done.
- If present, import every valid chat into `scopes` through `remember_chat()`.
- Rename the legacy file to `.json.migrated` after successful import.
- Preserve the renamed file for one release as a fallback.

## UI Behavior

Update `ChannelList.tsx` to understand cached-first responses:

- Render returned rows immediately.
- Show last sync and refresh-in-progress state when present.
- Manual refresh passes `force=1`.
- After first paint, do one delayed re-fetch to pick up background refresh
  results.
- Keep current enable/disable and routing behavior backed by `scope_settings`.

Existing response compatibility matters. If the UI currently expects
`channels`, endpoints should either continue returning `channels` or return both
`channels` and the new richer envelope during the transition.

## Tests

Add focused tests before broad regression:

- Metadata normalization preserves unknown keys and merges known partial
  updates.
- `remember_chat()` creates rows, preserves existing settings, and debounces
  unchanged payloads.
- Legacy discovered chats migration is idempotent.
- `list_chats()` returns configured state via LEFT JOIN.
- Refresh state separates attempts, success, and errors.
- Active refresh marks absent rows as `not_returned` without deleting settings.
- Discord missing marking is scoped to refreshed guilds.
- Endpoint cold-cache path performs synchronous refresh.
- Endpoint warm-cache path returns cached rows and schedules refresh.

Validation:

- Focused pytest for storage/service/API paths.
- `ruff check` on changed Python files.
- `npm run build` for UI changes.
- local Incus regression only after core behavior is stable, preserving the
  selected regression worktree state.

## Implementation Order

1. Add this plan.
2. Add `core/chat_discovery.py` with metadata helpers, state_meta helpers,
   `remember_chat()`, and `list_chats()`.
3. Add unit tests for metadata merge, passive discovery, list join, and legacy
   migration.
4. Wire Telegram passive discovery to `chat_discovery`.
5. Add active refresh functions for Slack, Discord, and Lark.
6. Migrate channel endpoints to cached-first responses while preserving
   frontend compatibility.
7. Update `ChannelList.tsx` for refresh state and delayed re-fetch.
8. Expand endpoint and refresh tests.
9. Run focused backend tests, lint, and UI build.
10. Review the final diff for cross-platform consistency before PR.
