# Background Tasks With Stable Agent Sessions

## Background

Scheduled tasks, hooks, and watches currently target conversations through an
external `session_key` such as:

```text
slack::channel::C123::thread::171717.123
```

That key is useful for bootstrapping a turn from an IM surface, but it is not
the durable identity of the agent session. Vibe Remote already has an
`agent_sessions` table whose `id` was explicitly designed as the short handle
that can be shown to agents, passed to CLI commands, and referenced by future
Web UI task management.

The intended `agent_sessions.id` format is:

```text
ses + 10 lowercase base32-like characters
```

Example:

```text
sesk8m4q2p7x
```

It is short, symbol-free, non-numeric, and copyable in chat. The current
implementation has a critical bug: `SQLiteSessionsService.save_state()` rebuilds
`agent_sessions` from the legacy in-memory session mappings and generates new
row IDs on every save. That breaks the original contract. Before any task/watch
feature references `agent_sessions.id`, the ID must become stable.

## Goals

- Make `agent_sessions.id` stable across saves and service restarts.
- Inject the current `session_id` into agent prompts as the primary target for
  background commands.
- Add `--session-id` to `vibe task`, `vibe hook`, and `vibe watch`.
- Keep `--session-key` as a compatibility path for old scripts and persisted
  tasks, but do not teach it in new agent-facing prompt text.
- Design the future SQLite background task tables around `session_id`, not
  external `session_key`.

## Non-Goals

- Do not solve the future "create a brand-new session on each run" product
  model in this change. That will get a separate design.
- Do not remove `--session-key` yet.
- Do not require the Web UI to manage tasks in this first slice.

## Session Identity Rules

### Stable Agent Session IDs

`agent_sessions.id` is the user-facing Vibe Remote session handle. It must not
change when:

- a session mapping is saved again;
- active thread/poll/dedup runtime state changes;
- the service restarts;
- task/watch state is updated.

The persistence layer should preserve existing IDs by matching the durable
business identity of a session:

| Component | Meaning |
| --- | --- |
| `scope_id` | IM scope resolved from the legacy session scope key |
| `agent_variant` | agent or subagent namespace |
| `session_anchor` | conversation anchor / base session id / composite key |
| `native_session_id` | backend-native session/thread id |

`workdir` is still stored and indexed, but is not sufficient by itself as a
unique identity. OpenCode already embeds workdir in the `session_anchor`, while
Claude/Codex use backend-native session ids to distinguish resumed sessions.

### Prompt Injection

The prompt should use `session_id` as the primary background target:

```text
Current conversation targeting:
- Current session id: sesk8m4q2p7x
```

Rules shown to agents should prefer:

```bash
vibe task add --session-id sesk8m4q2p7x ...
vibe hook send --session-id sesk8m4q2p7x ...
vibe watch add --session-id sesk8m4q2p7x ...
```

The injected prompt must not mention the legacy session key or show
`--session-key` examples. Old targeting belongs only in compatibility code and
low-prominence CLI/help text for existing scripts.

## CLI Compatibility

The new public flag is `--session-id`.

| Command | New primary flag | Legacy flag |
| --- | --- | --- |
| `vibe task add` | `--session-id` | `--session-key` |
| `vibe task update` | `--session-id` | `--session-key` |
| `vibe hook send` | `--session-id` | `--session-key` |
| `vibe watch add` | `--session-id` | `--session-key` |

Validation:

- Require exactly one of `--session-id` or `--session-key` for new add/send
  commands.
- Reject commands that pass both.
- Keep `--deliver-key` and `--post-to` for explicit delivery overrides.
- When a stored object has `session_id`, resolve through `agent_sessions`.
- When a stored object only has `session_key`, use the legacy resolver.

## Resolving `--session-id`

Resolving a session id should load the `agent_sessions` row and its `scope`.

| Target field | Source |
| --- | --- |
| platform | `scopes.platform` |
| scope type | `scopes.scope_type` |
| scope native id | `scopes.native_id` |
| session anchor | `agent_sessions.session_anchor` |
| agent backend | `agent_sessions.agent_backend` |
| agent variant | `agent_sessions.agent_variant` |
| native session id | `agent_sessions.native_session_id` |
| workdir | `agent_sessions.workdir` |

The execution path should reconstruct a scheduled `MessageContext` that routes
to the same scope and anchor that the session row represents. The agent backend
then resumes the same native session through the normal session mapping.

## Background Tables

Keep the schema small: two tables cover persisted definitions, queued work,
runtime snapshots, and execution history.

### `background_tasks`

| Field | Type | Purpose |
| --- | --- | --- |
| `id` | TEXT PK | background task id |
| `task_type` | TEXT | `scheduled` or `watch` |
| `session_id` | TEXT | primary Agent Session target |
| `legacy_session_key` | TEXT | compatibility target for old tasks and old CLI |
| `name` | TEXT | user-visible name |
| `enabled` | INTEGER | enabled flag |
| `post_to` | TEXT | delivery shortcut |
| `deliver_key` | TEXT | explicit delivery override |
| `prompt` | TEXT | scheduled task prompt |
| `schedule_type` | TEXT | `cron` or `at` |
| `cron` | TEXT | cron expression |
| `run_at` | TEXT | one-off run timestamp |
| `timezone` | TEXT | schedule timezone |
| `command_json` | TEXT | watch argv array |
| `shell_command` | TEXT | watch shell command |
| `prefix` | TEXT | watch follow-up instruction prefix |
| `cwd` | TEXT | watch working directory |
| `mode` | TEXT | `once` or `forever` |
| `timeout_seconds` | REAL | per-cycle timeout |
| `lifetime_timeout_seconds` | REAL | overall forever-watch timeout |
| `retry_exit_codes_json` | TEXT | retryable exit codes |
| `retry_delay_seconds` | REAL | retry delay |
| `last_started_at` | TEXT | latest start time |
| `last_finished_at` | TEXT | latest finish time |
| `last_event_at` | TEXT | latest detected event time |
| `last_run_at` | TEXT | latest scheduled-task run time |
| `last_error` | TEXT | latest error |
| `last_exit_code` | INTEGER | latest exit code |
| `metadata_json` | TEXT | extension payload |
| `created_at` | TEXT | create time |
| `updated_at` | TEXT | update time |

### `background_runs`

| Field | Type | Purpose |
| --- | --- | --- |
| `id` | TEXT PK | run id |
| `task_id` | TEXT | optional `background_tasks.id` |
| `run_type` | TEXT | `scheduled`, `task_run`, `hook_send`, or `watch_runtime` |
| `status` | TEXT | `pending`, `processing`, `completed`, `failed`, or `running` |
| `session_id` | TEXT | actual target session |
| `legacy_session_key` | TEXT | compatibility target |
| `post_to` | TEXT | delivery shortcut snapshot |
| `deliver_key` | TEXT | explicit delivery snapshot |
| `prompt` | TEXT | actual prompt sent |
| `pid` | INTEGER | running process id for watch runtime rows |
| `started_at` | TEXT | start time |
| `completed_at` | TEXT | finish time |
| `exit_code` | INTEGER | process exit code |
| `error` | TEXT | error summary |
| `stdout` | TEXT | bounded stdout when recorded |
| `stderr` | TEXT | bounded stderr when recorded |
| `metadata_json` | TEXT | extension result/runtime payload |
| `created_at` | TEXT | create time |
| `updated_at` | TEXT | update time |

## Migration Strategy

1. Fix `agent_sessions.id` stability first.
2. Add `--session-id` while keeping `--session-key`.
3. Update prompt injection to prefer `--session-id`.
4. Add SQLite background tables and import old JSON state once.
5. Switch task/watch stores to SQLite as the source of truth while preserving
   explicit-path JSON stores for tests and compatibility.

This order keeps old CLI scripts working while making every new task/watch use
the durable agent session handle.

## Implementation Slices

### Slice 1: Session ID Targeting Bridge

Completed bridge before moving task/watch persistence itself to SQLite:

- preserve `agent_sessions.id` across `save_state()` rebuilds;
- attach `agent_session_id` to Claude, Codex, and OpenCode prompt contexts when
  the row already exists or is created;
- inject `Current session id` as the only agent-facing background target;
- add `--session-id` to task, hook, and watch CLI commands;
- resolve `session_id` through SQLite `agent_sessions` at runtime, while still
  accepting old `session_key`-only records.

### Slice 2: SQLite Background Store

This slice adds `background_tasks` and `background_runs`, imports existing JSON
tasks/watches/request state once, and makes SQLite the default source of truth.
It does not change the public targeting contract again: new rows store
`session_id` directly and keep `legacy_session_key` only for compatibility
display/import.
