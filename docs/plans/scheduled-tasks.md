# Scheduled Tasks And Turn Pipeline

## Background
- Vibe Remote already has a mature session mapping model: `session_scope -> agent -> base_session_id -> native_session_id`.
- The current inbound path mixes two concerns inside `handle_user_message()`:
  - Session semantics: which conversation this input belongs to.
  - IM behavior: whether replies should go to a thread, a newly created Discord thread, or a flat DM channel.
- Product requirement for scheduled tasks is stricter than a generic background prompt runner:
  - A scheduled task must behave like "the user said this prompt under this session key".
  - The product logic must remain equivalent to manual usage.
  - Thread-capable and flat conversations intentionally behave differently.

## Goal
Add `vibe task ...` so users or agents can create one-off or cron-based scheduled tasks that inject prompts into existing Vibe Remote conversations without creating a parallel scheduler-only conversation model.

## Status
- Implemented in this worktree:
  - `vibe task add/list/show/pause/resume/rm`
  - persisted `scheduled_tasks.json` store
  - controller-owned `ScheduledTaskService` with cron and one-off triggers
  - shared `human` / `scheduled` turn pipeline
  - source-aware Discord turn preparation
  - scheduled top-level anchor aliasing into the existing session map
  - Slack/Lark scheduled-thread reply allowance
- Explicitly not implemented in this slice:
  - `vibe task run <id>`
  - UI management pages for scheduled tasks

## Product Rules

### External Session Key
The CLI receives one opaque `session_key` parameter. This is an external CLI contract only; it does not replace the current internal `_get_session_key()` implementation.

Recommended wire format:

```text
<platform>::channel::<channel_id>
<platform>::channel::<channel_id>::thread::<thread_id>
<platform>::user::<user_id>
<platform>::user::<user_id>::thread::<thread_id>
```

Examples:

```text
slack::channel::C123
slack::channel::C123::thread::1712345678.123
discord::user::123456789012345678
```

### Equivalent Manual Behavior
For a scheduled task with prompt `P` and session key `K`, the effect must be equivalent to the user manually sending message `P` under `K`.

That equivalence is defined by product behavior, not by blindly reusing the current webhook code path.

### Session Behavior By Surface Type
- Thread-capable surfaces without explicit thread:
  - Each scheduled trigger is a new session.
  - The bot sends results directly to the top-level channel/DM surface.
  - Future human replies to those bot messages must continue that session.
- Flat surfaces without thread support:
  - The same session key keeps using the same session across triggers.
- Explicit thread:
  - Scheduled triggers always continue that exact thread/session.

## Design

### 1. Split Turn Processing
Refactor `MessageHandler.handle_user_message()` into a shared turn-processing pipeline with explicit turn source.

Proposed source types:
- `human`
- `scheduled`

Proposed stages:
1. `ingest_human_turn()` / `ingest_scheduled_turn()`
2. `prepare_turn_context()`
3. `resolve_turn_session()`
4. `dispatch_turn_to_agent()`
5. `finalize_turn_delivery()`

The shared pipeline owns session semantics. IM-specific reply topology decisions move behind IM hooks instead of remaining hard-coded in adapters.

### 2. Move Reply Topology Out Of Discord Inbound Adapter
Today Discord eagerly creates a thread in the inbound adapter for non-thread guild messages. That is too early because the correct behavior depends on turn source.

New rule:
- IM adapters only normalize inbound context.
- IM clients may expose a hook such as `prepare_turn_context(context, source)` for source-aware reply topology.
- Discord uses that hook to create a thread only for `human` top-level guild messages.
- Scheduled top-level turns on Discord do not create a thread before agent execution.

Slack and Lark already encode top-level user messages as thread-root contexts; they can continue to do so for real user messages.

### 3. Preserve Existing Session Store, Add Alias Operations
Do not introduce a separate scheduler conversation namespace.

Instead, extend the existing session mapping facade with alias-oriented operations:
- detect whether any stored session exists for a given `session_scope + base_session_id`
- create alias mappings from one `base_session_id` prefix to another
- remove provisional aliases after final anchoring

Alias behavior must preserve suffixes used by existing backends:
- Claude subagents: `base:subagent`
- OpenCode cwd-scoped keys: `base:/abs/path`

Prefix replacement rule:
- exact match: `old_base -> new_base`
- prefixed match: `old_base:<suffix> -> new_base:<suffix>`

### 4. Scheduled Turn Anchoring
Scheduled top-level turns on thread-capable surfaces need a provisional base before the first bot message exists.

Flow:
1. Build a provisional `base_session_id` for this scheduled run.
2. Run the agent using that provisional base.
3. Every top-level bot message emitted by that run is collected as a potential reply anchor.
4. Once the backend session mapping exists, alias those emitted message IDs to the same native session in the existing store.
5. After the turn is finalized, clear the provisional aliases and keep only the real anchors.

Why:
- Slack/Lark replies will later use the bot message ID as thread root directly.
- Discord top-level replies require an extra bridge:
  - a human may reply to a scheduled bot message in-channel
  - the system creates a new Discord thread for that human turn
  - if the replied-to message ID is already aliased, the new thread ID can inherit that session

### 5. Scheduled Turn Delivery
Scheduled turns without explicit thread should deliver top-level messages on thread-capable surfaces.

That is intentionally different from real human top-level messages, where some platforms create or use a thread automatically.

To support this, outbound targeting must become source-aware instead of assuming `context.thread_id` alone defines the correct reply location.

### 6. Session-Key Resolution
Add a resolver that parses the external CLI session key into:
- platform
- scope type: `channel` or `user`
- scope ID
- optional explicit thread ID

Then resolve it into an execution context:
- `session_scope`: current internal scope key used by settings/session maps
- `delivery_channel_id`: actual channel/chat ID used to send messages
- `context.user_id`: actual DM user ID for user-scoped conversations, synthetic scheduler user for channel-scoped turns
- `is_dm`
- `thread_id`

DM resolution requirements:
- Slack / Discord can open or send to DM by user ID.
- Lark must resolve the bound user's `dm_chat_id`.
- WeChat stays flat and uses the chat/user target already persisted by current settings.

### 7. Mention-Gating Compatibility
Scheduled-created sessions must remain replyable in channels where mention gating is enabled.

Implemented approach:
- mark scheduled-created Slack/Lark thread roots under synthetic user `scheduled`
- allow thread replies when either the real user or `scheduled` has an active thread mark
- for Discord guild channels, treat a reply to a known scheduled anchor as a valid conversation continuation even without a mention
- once Discord creates or reuses the thread for that reply, alias the scheduled anchor base onto the real thread base

## CLI Scope
Initial subcommands:
- `vibe task add`
- `vibe task list`
- `vibe task show <id>`
- `vibe task pause <id>`
- `vibe task resume <id>`
- `vibe task rm <id>`
- `vibe task run <id>`

Initial scheduling modes:
- `--cron "<expr>"`
- `--at "<iso8601>"`

Task inputs:
- `--session-key "<key>"`
- `--prompt "..."`
- `--prompt-file path`
- `--timezone Asia/Shanghai` (default: local timezone)

## Persistence And Runtime
- Persist scheduled tasks in `~/.vibe_remote/state/scheduled_tasks.json`.
- Introduce a `ScheduledTaskStore` with atomic writes and `maybe_reload()` support.
- Introduce a controller-owned `ScheduledTaskService` started after IM readiness.
- Use a scheduler library for cron support and task reconciliation on store changes.

## Planned Code Changes
- `core/handlers/message_handler.py`
  - split turn ingestion and shared processing
  - add scheduled turn entrypoint
- `core/handlers/session_handler.py`
  - source-aware base session resolution
- `core/controller.py`
  - wire scheduled task service and top-level turn entrypoints
- `core/message_dispatcher.py`
  - expose sent message IDs to scheduled anchor tracking
- `modules/im/base.py`
  - add source-aware turn-context preparation hook
- `modules/im/discord.py`
  - remove eager thread creation from raw inbound adapter
  - move Discord thread creation into the new hook
  - bridge replied-to scheduled bot message -> new thread session alias
- `modules/im/slack.py`
  - mention-gating fallback for stored session anchors
- `modules/im/feishu.py`
  - mention-gating fallback if needed for scheduled thread roots
- `modules/sessions_facade.py`
  - alias helpers and stored-session existence helpers
- `vibe/cli.py`
  - `task` command group
- `config/` or `core/`
  - scheduled task store / session key parsing module
- `tests/`
  - session-key parsing
  - scheduled turn session behavior
  - Discord source-aware thread behavior
  - alias migration and mention-gating fallback

## Implementation Todos
- [x] Add scheduled-task persistence and session-key parser.
- [x] Introduce source-aware turn pipeline and scheduled turn entrypoint.
- [x] Move Discord eager thread creation into handler-driven preprocessing.
- [x] Add alias operations to existing session facade.
- [x] Add scheduled anchor tracking and alias finalization.
- [x] Add `vibe task` CLI.
- [x] Add scheduled runtime service with cron and one-off triggers.
- [x] Add focused tests.
- [x] Run relevant lint/tests/build checks.
