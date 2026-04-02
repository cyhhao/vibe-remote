---
name: background-watch-hook
slug: background-watch-hook
description: Run a blocking watcher in the background and send a `vibe hook` back to the current IM session when it finishes. Use for GitHub reviews, CI, file completion, log matches, and process-exit follow-ups.
version: 0.1.0
---

# Background Watch Hook

Use this skill when the user wants the agent to:

- wait for an external event in the background
- stop polling manually
- automatically wake up the current IM session when the event happens

This is a reusable pattern, not a product feature. Do not build a new scheduler or event subsystem when this pattern is enough.

## Core Idea

The pattern has three parts:

1. A blocking waiter command that exits only when the watched condition becomes true.
2. A detached shell wrapper that runs the waiter asynchronously.
3. A `vibe hook send` call that fires only after the waiter exits successfully.

The waiter prints its final event summary to `stdout`. The wrapper captures that output, builds a prompt file, and queues one hook back into the current session.

Use this pattern for any one-off "wait in the background, then come back here" task, for example:

- GitHub PR reviews or comments
- CI finishing
- a file or export being generated
- a log line appearing
- a long-running process exiting

## Hard Rules

1. Do not use `watcher & && vibe hook send ...`. That runs the hook immediately after background launch, not after the watcher finishes.
2. Put both the waiter and the hook inside the same detached shell.
3. Keep waiter `stdout` clean. Final event summary goes to `stdout`; debug logs go to `stderr`.
4. Prefer `--prompt-file` over inline `--prompt` for hook payloads.
5. Use a timeout-aware waiter. Infinite waits are fine only when the user explicitly wants them.

## Files In This Skill

- `scripts/watch_then_hook.sh`
  General wrapper. Runs a waiter command, captures `stdout`, then calls `vibe hook send`.
- `scripts/wait_for_github_pr_activity.py`
  Included example waiter. Polls a GitHub PR until new review activity appears, then prints a concise summary.

## Session Targeting

You usually already know the current delivery target from Vibe Remote's prompt context:

- use the current `session_key` if the follow-up should return to the same channel
- append `::thread::<thread_id>` when the follow-up must land in the current thread
- use `--post-to channel` only when the user wants to preserve thread context but publish in the parent channel

If the current turn does not expose a usable session key, stop and ask for the exact delivery target instead of guessing.

## Wrapper First

`scripts/watch_then_hook.sh` is the main reusable tool in this skill. Most of the time you do not need to invent a new shell pattern. You only need:

1. a correct `session_key`
2. a short action-oriented prefix
3. a waiter command that follows the contract below

The wrapper interface is:

```bash
scripts/watch_then_hook.sh \
  --session-key "<session-key>" \
  [--prefix "<hook prefix>"] \
  [--post-to channel] \
  [--deliver-key "<delivery-session-key>"] \
  [--hook-bin vibe] \
  [--hook-cmd "uv run python -m vibe"] \
  [--timeout-exit-code 124] \
  -- <waiter command ...>
```

- `--session-key`
  Required. Delivery target for the follow-up hook.
- `--prefix`
  Optional. Prepended to the waiter output inside the generated prompt file.
- `--post-to`
  Optional. Use only when the follow-up should post to the parent channel while keeping thread context.
- `--deliver-key`
  Optional. Explicit delivery target key. Use this only when the follow-up should be delivered to a different target.
- `--hook-bin`
  Optional. Explicit hook executable override.
- `--hook-cmd`
  Optional. Full hook command override. Useful when the local `vibe` on `PATH` is a stub and the real CLI should run through something like `uv run python -m vibe`.
- `--timeout-exit-code`
  Optional. The wrapper treats this exit code as a silent timeout.

`--post-to` and `--deliver-key` are mutually exclusive. Fail fast if both are set.

## Preferred Execution Pattern

Run the wrapper itself in the background with `nohup bash -lc '...'`:

```bash
nohup bash -lc '
  scripts/watch_then_hook.sh \
    --session-key "slack::channel::C123::thread::171717.123" \
    --prefix "GitHub PR #151 has new review activity. Fetch the latest review state, summarize unresolved items, and continue handling them if needed." \
    -- \
    scripts/wait_for_github_pr_activity.py \
      --repo cyhhao/vibe-remote \
      --pr 151 \
      --interval 45
' >/tmp/watch-pr-151.log 2>&1 &
```

That command returns immediately, leaves the watcher running, and sends the hook only after the waiter exits with success.

When running inside the Vibe Remote repo or worktree, the wrapper will auto-fallback to `uv run python -m vibe` if the `vibe` executable on `PATH` is not the real CLI.

## Generic Workflow

### 1. Pick the waiter

For other use cases, either:

- write a new small waiter in the task workspace, or
- reuse another existing blocking command that prints a final summary to `stdout`

Use `scripts/wait_for_github_pr_activity.py` only when the thing being watched is GitHub PR review activity.

If the watcher should immediately surface activity that already exists at startup, add `--catch-up`. Without it, the included GitHub waiter snapshots current activity as the baseline and waits only for newer events.

### 2. Decide the session target

Use the current thread when the user wants the result to continue in context.

### 3. Build a short hook prefix

The prefix should tell the next turn what to do with the watcher result. Good prefixes are action-oriented:

- `GitHub PR has new review activity. Fetch the latest comments and continue resolving them.`
- `CI finished. Inspect the failure and propose the next fix.`
- `The export completed. Summarize the result and share the artifact details.`

### 4. Start the detached wrapper

Run `nohup bash -lc 'scripts/watch_then_hook.sh ...' >/tmp/<name>.log 2>&1 &`.

### 5. Tell the user what was started

Report:

- what is being watched
- where the hook will be delivered
- where the background log is going

## Waiter Contract

Design waiters to follow this contract:

- `exit 0`: event detected; final summary printed to `stdout`
- `exit 124`: timeout; no hook should be sent
- any other non-zero exit: failure; wrapper should exit without sending a hook

Good waiter output is compact and already useful to the next turn, for example:

```text
GitHub PR activity detected for cyhhao/vibe-remote#151
- review_comment #3025433621 by chatgpt-codex-connector[bot] on modules/agents/opencode/server.py
  Avoid terminating active OpenCode server during auth refresh
  https://github.com/cyhhao/vibe-remote/pull/151#discussion_r3025433621
```

## Writing New Waiters

Keep new waiters small and boring. A good waiter:

- blocks until a single condition becomes true
- prints one final summary to `stdout`
- keeps polling logs and diagnostics on `stderr`
- exits with `124` on timeout when a timeout matters

The wrapper is the reusable part. Waiters should stay disposable and task-specific.

## Included Example: GitHub PR Activity

`scripts/wait_for_github_pr_activity.py` snapshots the current PR state on startup when no explicit cursor is provided, then blocks until something newer appears. Use it as:

```bash
nohup bash -lc '
  scripts/watch_then_hook.sh \
    --session-key "slack::channel::C123::thread::171717.123" \
    --prefix "PR review changed. Check the latest GitHub review results and continue the review loop." \
    -- \
    scripts/wait_for_github_pr_activity.py \
      --repo cyhhao/vibe-remote \
      --pr 151 \
      --interval 60 \
      --timeout 14400
' >/tmp/watch-pr-151.log 2>&1 &
```

To catch up on comments or reviews that already exist before the watcher starts, add `--catch-up`:

```bash
nohup bash -lc '
  scripts/watch_then_hook.sh \
    --session-key "slack::channel::C123::thread::171717.123" \
    --prefix "PR review already has activity. Pull the current review state and continue the thread." \
    -- \
    scripts/wait_for_github_pr_activity.py \
      --repo cyhhao/vibe-remote \
      --pr 151 \
      --catch-up
' >/tmp/watch-pr-151-catch-up.log 2>&1 &
```

## Failure Handling

- If the waiter requires credentials, verify them before starting the background process.
- If `vibe hook send` is not on `PATH`, stop and fix the environment instead of launching a broken watcher.
- If the user wants a durable recurring watch shared across sessions, this pattern is not enough by itself; explain that it is best for one-off background waiting tied to the current task.

## Common Mistakes

- launching the waiter in the background and the hook in the foreground
- letting the waiter print noisy polling logs to `stdout`
- hardcoding the wrong session target
- forgetting that a channel follow-up and a thread follow-up use different `session_key` values
- omitting a timeout when the user expects eventual cleanup
