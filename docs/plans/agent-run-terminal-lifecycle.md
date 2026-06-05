# Agent Run Terminal Lifecycle

## Background

`vibe agent run` currently records an `agent_run` row and marks it succeeded as
soon as the harness request submits the prompt to the backend path. For async
backends such as Codex, submitting the prompt only starts the native turn; tool
events and the terminal result arrive later through `emit_agent_message`.

This makes `run.status=succeeded` and `completed_at` appear before the run's
final `result_text` is available.

## Goal

For direct `agent_run` requests, the run lifecycle must model one concrete
Agent turn:

- `queued` before claim
- `running` while the backend turn is active
- terminal only after the backend emits its real result

The fix should use the existing terminal-result signal. It must not add sleeps,
post-completion polling, or Codex-specific behavior.

## Design

Keep the existing request queue and run storage. Change only the direct
`agent_run` lifecycle:

1. Build the same scheduled/harness `MessageContext` with `task_execution_id`.
2. For private/non-avibe runs, run the turn through
   `dispatch_turn(..., source=SOURCE_SCHEDULED, on_chunk=noop)` so dispatch
   returns only after the terminal result.
3. For avibe/workbench sessions, keep routing through the session turn gate so
   queueing, Stop, and browser lifecycle remain unchanged.
4. When `emit_agent_message(..., "result", ...)` carries
   `task_trigger_kind="agent_run"`, update the run record with `result_text`,
   message id, terminal status, and `completed_at`.
5. Treat terminal delivery and run completion as separate concerns: even when
   every IM send/upload fails, the terminal result still releases the turn
   waiter and records the run result.
6. When an avibe agent run is queued behind an active workbench turn, return
   the run row to `queued` and let the workbench queue hold it until flush time.
   The background drain skips those held rows so the same run is not claimed
   twice.

This makes the outbound terminal-result chokepoint the source of truth for
direct Agent Run completion, while preserving the existing avibe gate for
workbench sessions.

## Scope

This change is intentionally limited to `request_type == "agent_run"`.
Stored tasks and watches may still mean "trigger/follow-up submitted"; changing
that semantic together would be a broader product migration.

## Validation

- Unit test that a direct private `agent_run` stays `running` until a delayed
  terminal result is emitted.
- Unit tests that suppressed and visible `agent_run` terminal output update the
  run status.
- Unit test that avibe `agent_run` still routes through the session gate and
  does not complete at submit time.
- Unit test that failed terminal results remain failed and are not overwritten
  by request-submission success.
- Unit test that synchronous dispatch errors are recorded as failed runs.
- Unit test that terminal delivery failure still releases the waiting turn.
- Unit test that busy avibe agent runs remain queued until the workbench queue
  actually starts them.
- Existing scheduled task and dispatcher tests remain green.
