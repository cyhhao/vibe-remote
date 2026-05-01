# SQLite State Migration Plan

## Status

- Branch: `feature/sqlite-state-migration-plan`
- Status: Planning
- Scope: Settings, sessions, and future session message storage

## Background

Vibe Remote currently stores product state in JSON files under
`~/.vibe_remote/`. This has worked well while the state model was small:

- `config/config.json`: global runtime, platform, backend, and UI config
- `state/settings.json`: channel/user/guild settings, routing, bind codes
- `state/sessions.json`: agent session mappings, active polls, active
  threads, and processed-message deduplication
- `state/scheduled_tasks.json`, `state/watches.json`, and
  `state/task_requests/`: task/watch orchestration
- `state/discovered_chats.json`: discovered IM chat metadata
- `runtime/*.json`: pid/status/doctor/watch runtime files

The next product direction changes the pressure on persistence:

- Web UI Chat will need session listing, pagination, message replay, and
  eventually search.
- Sub-agent support will add richer session/message relationships and more
  routing metadata.
- Session state is no longer only a small recovery blob; it is becoming a
  product data model.
- Project-level user-editable configuration should remain file-based, similar
  to Claude Code and Codex, rather than becoming opaque database rows.

The migration should therefore separate **configuration** from **data**:

- User-editable project configuration remains file-based.
- Operational/product state that benefits from indexing, querying, and
  recovery moves to SQLite.

## Goals

1. Migrate existing users automatically and safely on upgrade.
2. Make SQLite the source of truth for settings, sessions, and future message
   history.
3. Preserve current public Python APIs during the first implementation pass so
   platform adapters and agent backends do not all change at once.
4. Keep hot reload behavior simple and robust across the UI process and service
   process.
5. Add real migration management with Alembic rather than ad hoc schema setup.
6. Keep project-level user configuration readable and editable on disk.
7. Avoid long-lived ORM sessions or ORM-object caching in runtime code.

## Non-Goals

- Do not move `config/config.json` into SQLite in the first migration. It
  contains user-facing global configuration and secrets, already has atomic
  writes, and is edited through a mature API path.
- Do not migrate task/watch persistence in the first pass. The current volume is
  low and file-based claim/recovery semantics are already well-scoped.
- Do not move pid/status/doctor runtime files into SQLite. They are process
  liveness artifacts, not durable product data.
- Do not add a revision table in the first pass. Use SQLite's built-in
  `PRAGMA data_version` for coarse cache invalidation.
- Do not make project-level agent/skill config database-backed. Keep those files
  under the user's project so they can be reviewed, edited, and versioned.

## Proposed Storage Boundary

### Remain File-Based

| Data | Reason |
| --- | --- |
| `config/config.json` | User-visible global config and secrets; existing API already validates and atomically writes it. |
| Project-level `.vibe/*` config | Should be reachable and versionable by users in project repos. |
| `scheduled_tasks.json` | Low volume; not urgent; can migrate later if task querying grows. |
| `watches.json` | Low volume; operational control rather than chat/session product data. |
| `task_requests/` | Directory queue semantics are already clear; defer DB queue until needed. |
| `runtime/*.json` | Process state, pid files, status snapshots; DB adds little value. |
| `user_preferences.md` | Human-readable durable notes, not structured app state. |

### Move to SQLite

| Data | Reason |
| --- | --- |
| Settings | Needs scoped lookup, future filtering, admin/user management, and UI views. |
| Sessions | Needs robust session identity, session listing, restore behavior, and agent binding queries. |
| Session messages/events | Needed for Web UI Chat, replay, pagination, search, and sub-agent activity. |
| Discovered chats | Useful for searchable UI selection; small but naturally relational. |

## Dependency Choice

Use:

- SQLAlchemy 2.x for database access.
- Alembic for schema migrations.

Rationale:

- Alembic is the mature migration path for SQLAlchemy and avoids inventing a
  local migration runner.
- SQLAlchemy lets the implementation use ORM where it improves clarity and Core
  where explicit SQL is better.
- The app already uses Python dataclasses heavily; service methods can return
  plain dataclasses instead of leaking ORM objects across layers.

Avoid:

- Peewee as the primary ORM: lighter, but migration management is weaker for
  this product's upgrade-safety requirement.
- SQLModel as the first choice: it adds Pydantic coupling without solving
  migrations better than SQLAlchemy plus Alembic.
- Long-lived SQLAlchemy `Session` objects as application caches.

## Database Layout

Default path:

```text
~/.vibe_remote/state/vibe.sqlite
```

Migration and backup paths:

```text
~/.vibe_remote/state/vibe.sqlite
~/.vibe_remote/state/vibe.sqlite-shm
~/.vibe_remote/state/vibe.sqlite-wal
~/.vibe_remote/state/migration.lock
~/.vibe_remote/state/backups/<timestamp>/
```

Connection setup:

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;
```

Notes:

- WAL allows the UI process and service process to read concurrently while a
  writer commits changes.
- `busy_timeout` avoids brittle immediate failures when the two processes race.
- `foreign_keys` should be enabled on every connection because SQLite does not
  enforce it globally.

## Layering

The new persistence stack should have four layers:

```text
Business code
  -> SettingsService / SessionService / MessageStore
      -> repositories
          -> SQLAlchemy engine + short-lived Session
              -> SQLite
```

Rules:

- Business code should not import SQLAlchemy models directly.
- Public service methods return dataclasses, frozen dataclasses, or simple
  dictionaries that match current domain concepts.
- A SQLAlchemy `Session` is request/operation scoped, not process scoped.
- ORM objects are not cached and are not passed into platform adapters.
- Stores keep the old method names where practical during the first migration
  so existing call paths can move gradually.

## Why Not Cache ORM Objects

SQLAlchemy ORM objects are in-process Python objects associated with one
SQLAlchemy `Session`. They do not represent shared live state across processes.

The UI process and service process can share the SQLite database file, but they
cannot share the same ORM object or identity map. A long-lived ORM session would
also risk stale reads after the other process updates the database.

If caching is needed, cache resolved domain values such as:

```python
EffectiveSettings(
    platform="slack",
    scope_type="channel",
    scope_id="C123",
    enabled=True,
    show_message_types=("assistant", "toolcall"),
    custom_cwd="/repo",
    routing=RoutingSettings(...),
    require_mention=False,
)
```

This object is independent of the DB session lifecycle and can be discarded
whenever the underlying DB changes.

## Hot Reload Strategy

First pass:

- SQLite is the source of truth.
- Each process keeps its own small in-memory cache only where there is a proven
  hot path.
- Same-process writes clear that process's relevant cache immediately.
- Cross-process writes are detected through `PRAGMA data_version`.
- If the data version changes, clear the cache and reload on the next access.
- Compare `PRAGMA data_version` values on one long-lived probe connection per
  process. Do not compare values from arbitrary pooled connections; SQLite only
  guarantees useful comparisons across repeated calls on the same connection.

Implementation sketch:

```python
class SqliteInvalidationProbe:
    def __init__(self, engine):
        self.connection = engine.connect()
        self.last_data_version: int | None = None

    def has_external_write(self) -> bool:
        version = self.connection.exec_driver_sql("PRAGMA data_version").scalar_one()
        changed = self.last_data_version is not None and version != self.last_data_version
        self.last_data_version = version
        return changed

    def close(self) -> None:
        self.connection.close()
```

Settings resolution:

- At message ingress, resolve effective settings once for the context.
- Store the resolved value on the message/request context.
- Downstream routing, handlers, and formatters use that resolved context value.
- CLI and UI API reads query through the same service layer.

This preserves the current "changes show up shortly after saving" behavior
without building a custom revision table. If a future Web UI needs more precise
invalidation, add a `change_log` or domain revision table later.

## Schema Draft

The first schema should be explicit enough to support future querying, but not
over-normalized. JSON columns are acceptable for stable, nested settings where
SQL filtering is not valuable yet.

### `schema_meta`

Optional app-level metadata separate from Alembic's own version table:

| Column | Type | Notes |
| --- | --- | --- |
| `key` | text primary key | Metadata key |
| `value` | text | Metadata value |
| `updated_at` | text | ISO timestamp |

Use for import markers such as `json_import_completed_at`.

### `scopes`

Common identity for channel/user/guild scopes.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer primary key | Internal key |
| `platform` | text not null | `slack`, `discord`, `telegram`, `lark`, `wechat` |
| `scope_type` | text not null | `channel`, `user`, `guild` |
| `scope_id` | text not null | Native IM ID |
| `display_name` | text | Optional user/chat display name |
| `created_at` | text not null | ISO timestamp |
| `updated_at` | text not null | ISO timestamp |

Constraints and indexes:

- Unique: `(platform, scope_type, scope_id)`
- Index: `(platform, scope_type)`

### `channel_settings`

| Column | Type | Notes |
| --- | --- | --- |
| `scope_id` | integer primary key references `scopes(id)` | Must be `channel` scope |
| `enabled` | integer not null | Boolean |
| `show_message_types_json` | text not null | Serialized list |
| `custom_cwd` | text | Optional |
| `routing_json` | text not null | Serialized routing settings |
| `require_mention` | integer | Null means inherit global default |
| `created_at` | text not null | ISO timestamp |
| `updated_at` | text not null | ISO timestamp |

### `guild_settings`

| Column | Type | Notes |
| --- | --- | --- |
| `scope_id` | integer primary key references `scopes(id)` | Must be `guild` scope |
| `enabled` | integer not null | Boolean |
| `created_at` | text not null | ISO timestamp |
| `updated_at` | text not null | ISO timestamp |

### `guild_policies`

| Column | Type | Notes |
| --- | --- | --- |
| `platform` | text primary key | Usually `discord`, but keep generic |
| `default_enabled` | integer not null | Boolean |
| `created_at` | text not null | ISO timestamp |
| `updated_at` | text not null | ISO timestamp |

### `user_settings`

| Column | Type | Notes |
| --- | --- | --- |
| `scope_id` | integer primary key references `scopes(id)` | Must be `user` scope |
| `is_admin` | integer not null | Boolean |
| `bound_at` | text | ISO timestamp |
| `enabled` | integer not null | Boolean |
| `show_message_types_json` | text not null | Serialized list |
| `custom_cwd` | text | Optional |
| `routing_json` | text not null | Serialized routing settings |
| `dm_chat_id` | text | Lark/Feishu DM delivery binding |
| `created_at` | text not null | ISO timestamp |
| `updated_at` | text not null | ISO timestamp |

### `bind_codes`

| Column | Type | Notes |
| --- | --- | --- |
| `code` | text primary key | Existing code string |
| `type` | text not null | `one_time` or `expiring` |
| `created_at` | text not null | ISO timestamp |
| `expires_at` | text | ISO timestamp |
| `is_active` | integer not null | Boolean |
| `used_by_json` | text not null | Preserve existing list semantics first |

Future refinement: normalize used-by rows only if UI/query requirements need it.

### `agent_session_bindings`

Replaces `session_mappings`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer primary key | Internal key |
| `scope_key` | text not null | Existing key such as `slack::C123` |
| `agent_name` | text not null | `opencode`, `claude`, `codex`, or sub-agent name |
| `thread_id` | text not null | Existing thread/base key |
| `session_id` | text not null | Backend-native session ID |
| `created_at` | text not null | ISO timestamp |
| `updated_at` | text not null | ISO timestamp |

Constraints and indexes:

- Unique: `(scope_key, agent_name, thread_id)`
- Index: `(session_id)`
- Index: `(scope_key, agent_name)`

### `active_threads`

Replaces `active_slack_threads`, but keeps the model platform-generic.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer primary key | Internal key |
| `scope_key` | text not null | Existing user/session scope key |
| `channel_id` | text not null | Native channel/chat ID |
| `thread_id` | text not null | Native thread/topic ID |
| `last_active_at` | real not null | Preserve current float timestamp semantics |

Constraints:

- Unique: `(scope_key, channel_id, thread_id)`

### `active_polls`

| Column | Type | Notes |
| --- | --- | --- |
| `opencode_session_id` | text primary key | Existing key |
| `base_session_id` | text not null | Existing value |
| `platform` | text not null | Backfilled during import |
| `channel_id` | text not null | Existing value |
| `thread_id` | text not null | Existing value |
| `settings_key` | text not null | Raw settings key |
| `working_path` | text not null | Existing value |
| `started_at` | real not null | Existing value |
| `baseline_message_ids_json` | text not null | Existing list |
| `seen_tool_calls_json` | text not null | Existing list |
| `emitted_assistant_messages_json` | text not null | Existing list |
| `ack_reaction_message_id` | text | Existing value |
| `ack_reaction_emoji` | text | Existing value |
| `typing_indicator_active` | integer not null | Boolean |
| `context_token` | text not null | Existing value |
| `processing_indicator_json` | text not null | Existing dict |
| `user_id` | text not null | Existing value |
| `updated_at` | text not null | ISO timestamp |

### `processed_messages`

Replaces `processed_message_ts`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | integer primary key | Internal key |
| `channel_id` | text not null | Native channel/chat ID |
| `thread_id` | text not null | Native thread/topic ID |
| `message_id` | text not null | Native message ID |
| `processed_at` | text not null | ISO timestamp |

Constraints and cleanup:

- Unique: `(channel_id, thread_id, message_id)`
- Index: `(channel_id, thread_id, processed_at)`
- Keep only the latest 200 rows per `(channel_id, thread_id)` to preserve
  current bounded-dedup behavior.

### `chat_sessions`

New product model for Web UI Chat and sub-agent activity.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | text primary key | Stable Vibe Remote session ID |
| `platform` | text not null | IM platform |
| `scope_type` | text not null | `channel` or `user` |
| `scope_id` | text not null | Native channel/user ID |
| `thread_id` | text | Native thread/topic ID |
| `agent_backend` | text | Effective backend |
| `agent_name` | text | Effective agent/sub-agent |
| `working_path` | text | Effective cwd |
| `title` | text | Optional display title |
| `status` | text not null | `active`, `idle`, `archived`, `error` |
| `created_at` | text not null | ISO timestamp |
| `updated_at` | text not null | ISO timestamp |
| `last_message_at` | text | ISO timestamp |

Indexes:

- `(platform, scope_type, scope_id, updated_at)`
- `(agent_backend, agent_name)`
- `(working_path)`

### `session_messages`

Append-only message/event stream for Web UI Chat and replay.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | text primary key | Stable event/message ID |
| `session_id` | text not null references `chat_sessions(id)` | Vibe session |
| `parent_message_id` | text | For threaded/sub-agent relationships |
| `role` | text not null | `user`, `assistant`, `system`, `tool`, `event` |
| `source` | text not null | `im`, `agent`, `scheduled`, `hook`, `system` |
| `content_text` | text | Renderable text |
| `content_json` | text not null | Full structured payload |
| `agent_backend` | text | Backend that produced it |
| `agent_name` | text | Agent/sub-agent that produced it |
| `native_message_id` | text | IM or backend-native ID |
| `created_at` | text not null | ISO timestamp |

Indexes:

- `(session_id, created_at)`
- `(native_message_id)`
- `(agent_backend, agent_name, created_at)`

Future search:

- Add SQLite FTS only after Web UI Chat needs full-text search. Do not include
  FTS in the first migration.

### `discovered_chats`

| Column | Type | Notes |
| --- | --- | --- |
| `platform` | text not null | IM platform |
| `chat_id` | text not null | Native chat/channel ID |
| `name` | text not null | Display name |
| `username` | text not null | Optional username |
| `chat_type` | text not null | Native chat type |
| `is_private` | integer not null | Boolean |
| `is_forum` | integer not null | Boolean |
| `supports_topics` | integer not null | Boolean |
| `last_seen_at` | text not null | ISO timestamp |

Primary key:

- `(platform, chat_id)`

## JSON Compatibility Mapping

The first implementation should preserve the current dataclass domain models
and method signatures where possible.

### Settings Import

Current JSON `settings.json` supports both current and legacy schemas:

- Current: `scopes.channel`, `scopes.guild`, `scopes.guild_policy`,
  `scopes.user`, `bind_codes`
- Legacy: flat `channels` and `users`

Import should reuse the current parser in `config.v2_settings.SettingsStore`
first, then persist the normalized `SettingsState` into SQLite. This avoids
duplicating legacy inference rules in the DB importer.

### Sessions Import

Current JSON `sessions.json` includes:

- `session_mappings`
- `active_slack_threads`
- `active_polls`
- `processed_message_ts`
- `last_activity`

Import should reuse existing migration helpers first:

- `migrate_active_polls(primary_platform)`
- `migrate_session_mappings(primary_platform)`

Then import the normalized in-memory state into SQLite.

### Message Store Bootstrap

Existing installs do not have historical message content in Vibe Remote state.
On first migration:

- Create empty `chat_sessions` and `session_messages`.
- Existing agent session bindings remain available through
  `agent_session_bindings`.
- Web UI Chat only shows messages captured after the new message store ships.

## First-Run Migration Flow

Migration must be automatic when a user installs a new version and starts Vibe
Remote. It must also be idempotent.

1. Ensure `~/.vibe_remote/state/` exists.
2. Acquire an exclusive migration lock:
   - Use a `migration.lock` file with OS-level locking.
   - The UI process and service process must share this lock.
   - If another process is migrating, wait with a timeout and then re-check DB
     health.
3. Open or create `vibe.sqlite`.
4. Run Alembic migrations to the latest head.
5. If `schema_meta.json_import_completed_at` exists, skip JSON import.
6. If JSON import is needed, create a timestamped backup directory:
   - Copy `settings.json` if present.
   - Copy `sessions.json` if present.
   - Copy `discovered_chats.json` if present.
   - Copy a small manifest with source file sizes and mtimes.
7. Parse current JSON through existing stores:
   - Use current `SettingsStore` parser for settings.
   - Use current `SessionsStore` loader plus current migration helpers for
     sessions.
   - Use current `DiscoveredChatsStore` parser for discovered chats.
8. Import all parsed rows in one SQLite transaction.
9. Validate imported data:
   - `PRAGMA integrity_check` returns `ok`.
   - Imported counts match parsed counts for channels, users, guilds, bind
     codes, session mappings, active polls, processed messages, and discovered
     chats.
   - Critical uniqueness checks have no conflicts.
10. Mark `json_import_completed_at` in `schema_meta` within the same
    transaction.
11. Leave original JSON files in place as backups. Do not delete or rewrite them
    during the first release.
12. Release the migration lock.

Failure behavior:

- If migration fails before `json_import_completed_at`, leave the DB either
  empty or partially written only inside a rolled-back transaction.
- Keep JSON files untouched.
- Log the failure and continue with JSON fallback only for the first migration
  release if fallback is explicitly implemented.
- If fallback is not implemented, fail startup with a clear doctor/actionable
  error rather than running against ambiguous state.

Recommended first-release approach:

- Implement a JSON fallback read path behind a temporary `StorageProvider`
  boundary for one release.
- Once migration stability is proven, remove fallback writes and keep JSON only
  as recovery backups.

## Runtime Access Pattern

### Settings

Introduce `SettingsService` as the canonical runtime API:

- `get_effective_settings(platform, scope_type, scope_id)`
- `get_channel_settings(platform, channel_id)`
- `update_channel_settings(platform, channel_id, settings)`
- `get_user(platform, user_id)`
- `bind_user_with_code(platform, user_id, display_name, code, dm_chat_id)`
- `list_channels(platform, filters...)`
- `list_users(platform, filters...)`

The existing `SettingsStore` and `SettingsManager` can become compatibility
wrappers around this service during migration.

Cache:

- Start without caching except for per-message resolved settings.
- Add a small effective-settings cache only if profiling shows need.
- Invalidate by same-process write and `PRAGMA data_version`.

### Sessions

Introduce `SessionService`:

- Agent session binding methods used by `SessionsFacade`.
- Active-thread methods used by Slack and scheduled delivery.
- Active-poll restore/update methods used by OpenCode.
- Processed-message dedup methods.
- New chat-session/message APIs for Web UI Chat.

The existing `SessionsStore` can become a compatibility wrapper around
`SessionService`. `SessionsFacade` should keep its public method names during
the first pass.

### Messages

Add append-only capture from the common message handling path, not from each
platform adapter. The right long-term integration point is near core handlers
or dispatcher boundaries, where both IM inbound messages and agent outbound
events have normalized context.

First pass message capture can be limited to:

- Inbound user message.
- Final assistant response.
- Tool-call summaries if already normalized.

Do not block the SQLite migration on perfect event capture coverage.

## Implementation Phases

### Phase 0: Storage Skeleton

- Add dependencies: `sqlalchemy` and `alembic`.
- Add `storage/` package:
  - engine/session factory
  - SQLite pragmas
  - migration runner
  - migration lock
  - invalidation probe
- Add Alembic environment and first schema migration.
- Add path helpers for `vibe.sqlite` and `migration.lock`.

Validation:

- Unit tests for engine pragmas, migration lock, and idempotent migration
  runner.
- A test that creates an empty DB and verifies Alembic head.

### Phase 1: Importer and Read-Only Verification

- Implement JSON-to-SQLite importer.
- Reuse current JSON parsers and compatibility migrations.
- Add count and integrity validation.
- Add a CLI/internal doctor command to inspect migration status.

Validation:

- Fixtures for current settings schema.
- Fixtures for legacy flat settings schema.
- Fixtures for legacy session mappings and active polls.
- Corrupt JSON handling.
- Existing JSON remains untouched.

### Phase 2: Settings Store Switch

- Implement `SettingsService` and repositories.
- Convert `SettingsStore` to SQLite-backed behavior while preserving current
  public methods.
- Convert `SettingsManager` to resolve from service instead of JSON mtime.
- Replace `_file_mtime` checks with `data_version` invalidation semantics.
- Keep dataclass serialization behavior stable at API boundaries.

Validation:

- Existing settings, auth, user-scope, Discord guild, update-checker, and UI API
  tests should pass with minimal changes.
- Add cross-process simulation: one connection writes settings, another process
  detects `data_version` change and reloads.

### Phase 3: Sessions Store Switch

- Implement `SessionService`.
- Convert `SessionsStore` and `SessionsFacade` to SQLite-backed behavior.
- Preserve active poll restore behavior.
- Preserve processed-message bounded dedup behavior.
- Preserve agent session mapping semantics across OpenCode, Claude, and Codex.

Validation:

- Existing `test_v2_sessions.py` coverage should pass through the compatibility
  wrapper.
- Add concurrent write tests for processed-message dedup and active-poll
  updates.

### Phase 4: Message Store

- Add `chat_sessions` and `session_messages` write APIs.
- Capture normalized inbound and outbound message records.
- Expose read APIs for future Web UI Chat:
  - list sessions
  - get session
  - list messages with cursor/pagination
- Do not build Web UI Chat in this migration PR unless explicitly scoped.

Validation:

- Focused tests for append-only ordering, pagination, and session metadata
  updates.
- Contract tests around message record shape.

### Phase 5: Cleanup and Documentation

- Update CLI docs to mention SQLite-backed settings/sessions.
- Update operational docs with backup and recovery steps.
- Keep JSON backups documented.
- Decide when to remove JSON fallback code after one stable release window.

## Test Plan

Minimum automated tests:

- Empty install creates SQLite DB and reaches Alembic head.
- Install with no JSON files starts cleanly.
- Current `settings.json` imports exactly once.
- Legacy flat `settings.json` imports through current parser.
- Current `sessions.json` imports exactly once.
- Legacy session mappings are normalized before DB import.
- Corrupt JSON causes clear migration failure and leaves JSON untouched.
- Second startup after import does not duplicate rows.
- UI process write invalidates service process cache using `PRAGMA data_version`.
- Same-process write clears local cache immediately.
- `processed_messages` enforces bounded dedup behavior.
- Active polls survive restart and preserve platform/settings-key semantics.

Regression checks:

- Focused pytest for settings and sessions.
- CLI task/watch tests should not need migration changes in the first pass.
- Docker regression after settings/sessions switch because platform adapters
  rely heavily on settings lookups.

Manual checks:

- Upgrade a real-ish `~/.vibe_remote` copy with Slack, Discord, Telegram, Lark,
  and WeChat settings.
- Verify Web UI settings edits are visible to the running service without
  restart.
- Verify `/clear`, resume, and active thread behavior across OpenCode, Claude,
  and Codex.

## Rollout Plan

First release with SQLite:

- Auto-migrate on startup.
- Keep source JSON files untouched as recovery backups.
- Keep a clear doctor/status output showing:
  - DB path
  - Alembic revision
  - JSON import completed timestamp
  - backup path
- Keep temporary JSON fallback only if migration fails before DB becomes source
  of truth.

Second release:

- Keep JSON backup but stop reading JSON fallback in normal startup.
- Add explicit recovery command if needed.

Future:

- Consider moving task/watch persistence only when there is a real querying or
  queueing requirement.
- Add FTS for message search only after Web UI Chat needs it.

## Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| UI and service migrate at the same time | Shared migration lock and idempotent import marker. |
| Partial import corrupts source of truth | Single SQLite transaction plus untouched JSON backups. |
| Legacy JSON edge cases are reimplemented incorrectly | Reuse current parsers and current session migration helpers. |
| Stale settings after UI edit | `PRAGMA data_version` invalidation plus local write cache clear. |
| Invalid cache checks due to pooled connections | Keep one dedicated probe connection per process for `PRAGMA data_version`. |
| ORM objects leak into business code | Service/repository boundary returns dataclasses/dicts only. |
| SQLite write contention | WAL, short transactions, `busy_timeout`, and operation-scoped sessions. |
| Message store scope grows too large | Ship session/message APIs separately from UI Chat UI. |
| Existing tests depend on file paths | Compatibility wrappers keep method names; tests migrate gradually. |

## Self Review

This plan is intentionally conservative. The main strength is that it avoids a
big-bang rewrite: current JSON parsers perform compatibility normalization, then
SQLite becomes the source of truth behind mostly stable public store APIs.

Issues to watch before implementation:

1. **Fallback policy needs a product decision.** Running with JSON fallback after
   a failed migration is friendlier, but it risks extending dual-source
   complexity. Failing startup loudly is simpler and safer once migration has
   good diagnostics.
2. **`SettingsManager` currently owns runtime caches.** The first code PR should
   reduce that cache surface rather than translating `_file_mtime` directly into
   another long-lived cache.
3. **`PRAGMA data_version` is subtle.** The implementation must keep a dedicated
   probe connection per process; comparing values from different pooled
   connections would be a false simplification.
4. **Message capture should not be bolted onto every platform adapter.** It
   belongs in shared core paths, otherwise multi-platform behavior will drift.
5. **Alembic packaging must be verified.** Migrations need to be included in
   wheels and sdists, and `vibe` must be able to locate them after installation.
6. **SQLite path and backup policy need doctor visibility.** Users must be able
   to tell whether they are using DB-backed state and where backups live.
7. **Schema may need one adjustment before coding:** `chat_sessions.id` should be
   defined from the product's future session identity model. If that identity is
   not settled, the first implementation can create the table but delay writes
   until the session identity contract is clear.

Recommended next step:

- Implement Phase 0 and Phase 1 first, behind compatibility wrappers, and stop
  before switching live Settings/Sessions reads. That gives a safe migration
  artifact that can be tested against real state copies before changing runtime
  behavior.
