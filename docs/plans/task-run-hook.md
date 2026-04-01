# Task Run And Hook Send

## Background

Vibe Remote already supports persisted scheduled tasks through `vibe task add`, but it lacks:

- an immediate rerun command for an existing task definition
- a one-shot asynchronous hook command for scripts and long-running workflows

The service runtime and CLI are separate processes, so these new commands need a lightweight IPC path instead of direct controller access from the CLI.

## Goal

Add two user-facing commands:

- `vibe task run <id>`: queue an immediate execution of an existing stored task
- `vibe hook send --session-key ... --prompt ...`: queue a one-shot asynchronous turn without storing a task

Keep prompt guidance concise and keep session behavior identical to the current scheduled-turn pipeline.

## Solution

Introduce a small request queue under `~/.vibe_remote/state/task_requests/`:

- CLI commands write JSON requests into `pending/`
- `ScheduledTaskService` polls and claims requests
- claimed requests move to `processing/`
- finished requests write receipts to `completed/`

Both new commands reuse the existing scheduled-turn execution path, so they inherit the same session and IM behavior.

## Notes

- `task run` updates `last_run_at` and `last_error` but does not disable one-shot tasks
- `hook send` does not persist anything into `scheduled_tasks.json`
- prompt guidance should mention `task add`, `task run`, and `hook send`, but defer full syntax details to `--help`
