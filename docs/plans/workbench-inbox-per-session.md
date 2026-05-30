# Workbench Inbox — Per-Session Feed · Design Doc

> **Branch**: `feat/workbench-inbox-per-session`
> **Status**: Implemented (2026-05-30) — backend query, typed persistence,
> Controller→UI realtime bridge, and frontend all landed.
> **Owner**: cyhhao

## 0. Prerequisite (confirmed 2026-05-30): unified, typed message persistence

**Finding (git archaeology):** avibe agent replies were *never* persisted — the mirror
(`920a513`) was built to "mirror **non-avibe** IM messages" and skips avibe; the avibe
adapter (`a7b87c0`) only pushes over SSE and never wrote to `messages`; `ui_server` only
ever wrote the user's own message. So the messages table holds avibe **user** rows but no
agent replies. Not a regression — an unfinished corner.

**Decision:** persist messages in the **Controller**, unified across all platforms incl.
avibe, **decoupled from IM delivery/muting** — i.e. `assistant` and `tool_call` messages are
persisted even when the IM side hides them (`emit_agent_message` currently drops them before
send). Add a first-class **`type` column** to `messages` (Alembic migration + backfill):

| `type` | meaning |
| --- | --- |
| `user` | human-sent |
| `assistant` | agent's intermediate text (before the final result) |
| `tool_call` | tool invocation |
| `notify` | progress/notification |
| `result` | the final user-facing answer (inbox preview = latest of these) |

Persistence hook lives in the Controller message flow *before* the IM mute filter, so every
type lands regardless of per-channel display preferences. The per-session inbox preview =
latest `type='result'` message — aligned with the avibe chat, which only renders results;
`assistant` / `tool_call` are the intermediate process log and never drive the preview.
Realtime therefore uses the Controller→UI events bridge (§4), since persistence now happens
Controller-side for all platforms.

**Read paths stay result-aware** (so persisting the full process log never leaks): the avibe
chat transcript (`/api/sessions/<id>/messages`) is scoped to `type IN ('user','result')`, and
the unread counts (`unread_counts` / `unread_counts_by_session`) count `type='result'` only —
matching the inbox card's `unread_count` so the sidebar badge and the card never disagree.

## 1. Why

The current Workbench Inbox is a flat **per-message** feed: `messages_service.list_inbox`
returns the most recent agent-authored message rows, so one busy session produces many
cards, cards show raw `scope_id`/`session_id` (no project name / session title), and there
is no realtime push for agent replies (only the web user-message write publishes an SSE
event; agent replies are written by the mirror in the **Controller** process and never reach
the UI server's browser SSE).

We are reshaping it into a Slack-like **per-session** feed: one card per conversation,
showing that conversation's latest agent reply, jumping to the top in real time on any new
message.

## 2. Confirmed product decisions

1. **Sort** by each session's last message of **any author**, time descending.
2. **Preview** text = that session's **latest agent reply** (distinct from the sort key).
3. **Tabs**: "Unread" lists only sessions with unread agent messages; "All" lists every
   session that has ≥1 agent reply.
4. **Open = mark the whole session read** (reuse existing `mark_session_read`).
5. **Scope**: avibe web sessions only (`platform = 'avibe'`).
6. **Realtime**: a new agent reply must bump its card to the top within ~1s, no manual
   refresh. Requires a Controller→UI cross-process event channel.
7. **"Replied" badge**: shown when the session's **last message is from the user**
   (`last_message_author == 'user'`) — i.e. you've responded and are awaiting the agent.
   NOT a sticky "ever replied" flag.

## 3. Data model — query-time aggregation, no new table

`agent_sessions` is the conversation aggregate root; `messages` stays the single source of
truth for read/unread. No schema migration.

Per-session inbox row (computed):

| Field | Source |
| --- | --- |
| `session_id` | `agent_sessions.id` |
| `scope_id` / `project_id` | `agent_sessions.scope_id` → `scopes.native_id` |
| `project_name` | `scopes.display_name` |
| `title` | `agent_sessions.title` |
| `last_activity_at` (sort key) | `MAX(messages.created_at)` over the session, any author |
| `last_message_author` | author of the message at `last_activity_at` (→ `replied` = `=='user'`) |
| `preview_text` / `preview_at` | latest `type='result'` message in the session |
| `unread_count` | count of `type='result' AND read_at IS NULL` in the session |

Eligibility: sessions with ≥1 `type='result'` message, `platform='avibe'`. "Unread" filter:
`unread_count > 0`. Sort: `last_activity_at DESC, session_id DESC`. Pagination: keyset cursor
on `(last_activity_at, session_id)` ("load more").

Implementation: `storage/messages_service.py::list_inbox_sessions(conn, *, unread_only,
limit, before)` using window functions / grouped subqueries over `messages` joined to
`agent_sessions` + `scopes`. `mark_session_read` is unchanged.

## 4. Realtime — Controller → UI events bridge

Reuses the existing internal Unix-socket infra (`core/internal_server.py` +
`vibe/internal_client.py`) and the per-process browser `SSEBroker`.

1. **Controller event bus** (`core/inbox_events.py`): a small thread-safe fan-out
   (`asyncio.Queue` per subscriber) owned by the Controller. `persist_agent_message`
   (`core/message_mirror.py`, runs in the Controller) emits an `inbox.session.updated` event
   after persisting an avibe **result** — carrying the recomputed per-session inbox row (so
   the browser can patch without a refetch). Intermediate `assistant`/`tool_call` persist
   silently (no result row yet → no event).
2. **`GET /internal/events`** (new, `core/internal_server.py`): long-lived SSE that subscribes
   to the event bus and streams events; mirrors the existing `/internal/dispatch` streaming
   shape.
3. **UI server subscriber**: a lifespan background task (`vibe/ui_server.py`) connects via a
   new `internal_client.stream_events()` and re-publishes each event to the browser `SSEBroker`
   (`broker.publish('inbox.session.updated', row)`), reconnecting on drop.
4. The existing in-process web user-message write also publishes `inbox.session.updated` so
   the user's own send updates the feed instantly without the round trip.

Browser: `WorkbenchInboxContext` handles `inbox.session.updated` by upserting the row and
re-sorting (bump to top); `inbox.unread.changed` / `markRead` zero the unread.

## 5. Frontend

- `WorkbenchInboxContext`: state becomes `inboxSessions: InboxSession[]` (+ `totalUnread`,
  pagination cursor). Realtime upsert + re-sort. `markRead(sessionId)` unchanged.
- `InboxPage`: one card per session — project name, title, **agent** preview, relative time
  (of last activity), unread badge, **已回复 / Replied** badge when `last_message_author=='user'`.
  Unread/All tabs; "Load more".
- `WorkbenchSidebar` `InboxHoverPopover`: show session title + project name (not IDs).
- i18n: `workbench.inbox.replied` etc. in `en.json` + `zh.json`.

## 6. Tests

- `tests/test_messages_service.py`: `list_inbox_sessions` — one card per session, sort by last
  activity (any author), preview = latest **result** (an intermediate `assistant` bumps the
  sort clock but never the preview), `unread_count`, `replied` when last message is the user's,
  pagination cursor.
- `tests/test_message_mirror.py`: `persist_agent_message` lands a typed agent row; an avibe
  `result` on a resolved session both persists **and** publishes `inbox.session.updated`; an
  intermediate `assistant` persists without publishing (no result yet).
- `tests/test_inbox_events.py`: `InboxEventBus` fan-out / unsubscribe / no-subscriber no-op.
- `tests/test_sqlite_state_migration.py`: HEAD bumped to the `messages.type` migration.
- Keep existing `unread_counts` / `mark_session_read` tests green.

## 7. Commit breakdown

1. Plan doc (this file).
2. Backend: `list_inbox_sessions` + `/api/inbox` shape change + tests.
3. Realtime: Controller event bus + `/internal/events` + UI subscriber + emit on writes.
4. Frontend: context + InboxPage + sidebar popover + i18n.
5. Polish + PR.
