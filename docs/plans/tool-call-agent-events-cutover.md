# Tool Call Agent Events Cutover Plan

## Status

- Scope: move new `tool_call` persistence out of `messages` and into an
  execution-trace table.
- Product decision: historical `messages.type = 'tool_call'` rows are
  disposable trace data. They do not require lossless backfill before deletion.
- Non-goal: redesign all assistant/intermediate events in this pass. This plan
  intentionally starts with `tool_call` only.

## Background

The local SQLite `messages` table currently stores both user-visible transcript
rows and backend execution trace rows. The current observed distribution is
dominated by `tool_call` rows, which are execution details and should not be
part of the canonical chat transcript.

`messages` should represent user-facing conversation facts. `tool_call` rows
should instead be stored as agent trace events that can be retained, displayed,
or deleted independently from the transcript.

## Target Boundary

Long-term, `messages` should converge on the canonical transcript:

- `user`
- `result`
- `notify`
- `error`
- user-visible `assistant` text

PR 1 only moves `tool_call`. Existing intermediate `assistant` process-log rows
remain in `messages` for now so this change does not bundle a broader event
taxonomy decision into the first cleanup.

`agent_events` owns execution trace:

- first supported event type: `tool_call`
- future event types may include `tool_result`, `thinking`, `sdk_error`,
  `intermediate_assistant`, and backend raw events

The first implementation should only move new `tool_call` writes. Broader event
taxonomy can be added later without blocking this cleanup.

## Proposed Table

Create a new `agent_events` table:

```sql
create table agent_events (
    id varchar primary key,
    scope_id varchar not null,
    session_id varchar,
    turn_id varchar,
    run_id varchar,
    platform varchar not null,
    agent_name varchar,
    backend varchar,
    event_type varchar not null,
    visibility varchar not null default 'trace',
    sequence integer,
    content_text text,
    content_json text not null,
    metadata_json text not null,
    source varchar,
    created_at varchar not null,
    updated_at varchar not null,
    foreign key(scope_id) references scopes(id) on delete cascade,
    foreign key(session_id) references agent_sessions(id) on delete set null
);
```

Initial indexes:

```sql
create index ix_agent_events_session_created_id
    on agent_events (session_id, created_at, id);

create index ix_agent_events_session_type_created_id
    on agent_events (session_id, event_type, created_at, id);

create index ix_agent_events_scope_created_id
    on agent_events (scope_id, created_at, id);

create index ix_agent_events_turn_sequence_id
    on agent_events (turn_id, sequence, id);
```

Notes:

- `turn_id` is nullable because current write paths may not consistently expose
  a stable turn identifier yet. The schema should reserve the boundary now.
- `visibility = 'trace'` should be the default for `tool_call`.
- `content_json` should preserve the structured payload currently mirrored into
  `messages.content_json`.
- `metadata_json` should carry backend, formatter, model, tool name, duration,
  and any source details that are not query dimensions.

## Phase 1: New Writes Only

Goal: stop new `tool_call` rows from entering `messages`.

Implementation tasks:

1. Add the `agent_events` migration and model/store helpers.
2. Add a shared persistence API, for example `persist_agent_event(...)`.
3. Route outbound `tool_call` persistence through `agent_events`.
4. Keep transport/UI behavior stable: IM adapters may still send visible
   tool-call previews if configured, but durable transcript persistence should
   not write `messages.type = 'tool_call'`.
5. Add focused tests:
   - emitting a `tool_call` creates an `agent_events` row;
   - emitting a `tool_call` does not create a `messages` row;
   - normal `user`, `result`, `notify`, `error`, and visible `assistant`
     messages still persist to `messages`.

Read-path checks before merging:

- Workbench chat transcript should not require `messages.tool_call`.
- Inbox/session list queries should not count `tool_call` as transcript
  activity unless explicitly intended.
- Export/debug paths that previously read `messages.tool_call` should either
  accept the loss or be changed to read `agent_events`.

## Phase 2: Historical Cleanup

Goal: remove historical `tool_call` rows from `messages`.

Product decision:

- Lossless backfill is not required.
- Historical `tool_call` data may be deleted because it is disposable trace, not
  user-visible transcript.

Preferred cleanup:

```sql
delete from messages
where type = 'tool_call';
```

For the current local scale, one transaction is acceptable. If this later ships
to much larger databases, use batched deletion.

Validation:

```sql
select count(*) from messages where type = 'tool_call';
select type, count(*) from messages group by type order by count desc;
```

Expected outcome:

- `messages.type = 'tool_call'` count is `0`.
- Total `messages` count drops by approximately the previous `tool_call` count.
- Workbench chat and inbox views still behave normally.

## Phase 3: Optional Trace UI

This is intentionally separate from the cutover.

Potential future UI/API work:

- session/turn execution details panel reading `agent_events`;
- event pagination by `(session_id, created_at, id)`;
- trace retention or compaction policy;
- optional deletion of trace events independent of transcript messages.

## Risks

- Hidden read dependency: some debug/export path may treat `messages` as a full
  execution stream. Phase 1 should search and test those paths before changing
  persistence.
- Naming drift: code may use both `toolcall` and `tool_call`. The migration
  should pick one canonical event type, with `tool_call` preferred for database
  consistency.
- Turn association: without a stable `turn_id`, early trace grouping will rely
  on `session_id` and timestamps. This is acceptable for the first cutover but
  should not become the long-term model.

## Suggested PR Split

### PR 1: `agent_events` table and new `tool_call` write path

- Add schema and store helper.
- Cut new `tool_call` persistence from `messages` to `agent_events`.
- Add unit coverage around the persistence split.
- Do not delete historical data.

### PR 2: Delete historical `messages.tool_call`

- Run/delete historical `messages.type = 'tool_call'` rows.
- Add or update a migration/maintenance command depending on the chosen
  deployment policy.
- Validate count is zero and transcript views still work.

### Later PR: richer event model

- Add `tool_result`, `thinking`, `sdk_error`, and intermediate assistant events
  only after the `tool_call` boundary has proven stable.
