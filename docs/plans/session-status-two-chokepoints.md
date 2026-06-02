# Session status dot — collapse to two chokepoints

## Problem

`agent_sessions.agent_status` (the sidebar dot: `running` / `idle` / `failed`) is
currently driven by instrumentation scattered across **every turn-entry path** and
**every backend error branch**:

- `mark_turn_running` called from `internal_server._run_turn` (interactive) and
  `ScheduledTaskService._execute_request` (scheduled).
- a failure latch (`Controller._sessions_turn_failed` via `note_turn_failed` /
  `pop_turn_failed`, plus `BaseAgent._note_turn_failed` / `MessageHandler._note_turn_failed`)
  poked from `opencode/agent.py`, `codex/agent.py`, `base.py`, `agent_auth_service.py`,
  `message_handler.py`.

Every new turn path (scheduled, Show Page) or backend error branch (codex missing
CLI) is a place that can be forgotten, so the dot goes stale. Successive reviews
keep finding "another path that wasn't wired" — the symptom of accidental
complexity, not an inherent property of session status.

## The two chokepoints (the only allowed instrumentation)

Every turn already funnels through one inbound and one outbound function:

- **Inbound — `AgentService.handle_message(agent_name, request)`**: the single
  dispatch point every turn (interactive / scheduled / Show Page, every backend)
  passes through to reach a backend. → set `running`.
- **Outbound — `MessageDispatcher.emit_agent_message(...)` on a TERMINAL message**:
  every agent reply/result/error is persisted here as a typed message. A terminal
  `result` → `idle`; a terminal `error` → `failed`.

The dot is gated to avibe via `Controller._session_id_from_context` (only avibe
workbench turns carry `agent_session_id`).

## Convergence (instead of a third instrumentation point)

The outbound only covers everything if **every terminal outcome emits a terminal
message**. Today terminal *failures* are emitted as a `notify` plus a manual
`_note_turn_failed()` plus a manual `_release_stream_turn()` (because a `notify`
does not fire `_signal_turn_complete`). That is three manual steps for the same
event.

Fix: introduce a terminal **`error`** message type (sibling of `result`):

- delivered immediately, persisted with `type="error"`, fires `_signal_turn_complete`.
- The outbound chokepoint sets the dot `failed` on it.

Then every terminal-failure path emits `emit_agent_message(context, "error", text)`
and drops its manual latch + manual stream-release. This converges failures onto
the outbound chokepoint AND fixes the latent "SSE stream hangs until the 600s
timeout on a failure" issue.

Terminal outcomes that produce no agent text (user **Stop**, dispatch **timeout**)
must also settle the dot — they converge by emitting a terminal signal through the
same outbound path (or, where they already run inside the dispatcher's
result/clear path, by reusing `_signal_turn_complete`). Verified case-by-case
during implementation; no new instrumentation point is added.

## Delete

- `Controller._sessions_turn_failed`, `note_turn_failed`, `pop_turn_failed`,
  `mark_turn_running`.
- `BaseAgent._note_turn_failed`, `MessageHandler._note_turn_failed` and all callers.
- The dot lifecycle block in `internal_server._run_turn`'s runner `finally`.
- The r7 dot wrapper in `ScheduledTaskService._execute_request`.

## Keep

- `set_agent_status` (the DB writer — the two chokepoints call it).
- `_signal_turn_complete` / `mark_turn_complete` / the turn-sink + `turn_token`
  (the live SSE stream completion — a separate concern; the dot now rides the same
  terminal-message point but does not replace it).
- `_reset_stale_agent_status` on startup (crash recovery: `running` → `idle`).

## Evidence layers

- unit: controller (only `set_agent_status` + reset remain), message_dispatcher
  (terminal `result`→idle / `error`→failed sets the dot), AgentService inbound.
- contract: `core.services.sessions` surface unchanged except removed latch.
- scenario/manual: avibe interactive, scheduled, Show-Page-dispatched, and a
  backend failure (e.g. codex missing CLI) all drive the dot from the two points.
