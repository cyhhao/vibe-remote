---
name: background-watch-hook
slug: background-watch-hook
description: Run a blocking watcher in the background and send a `vibe hook` back to the current IM session when it finishes. Use for GitHub reviews, CI, file completion, log matches, and process-exit follow-ups.
version: 0.2.0
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
2. An async wrapper that starts the waiter in the background and returns immediately.
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
2. Let the wrapper own detaching. Do not make every caller hand-roll `nohup`.
3. Keep waiter `stdout` clean. Final event summary goes to `stdout`; debug logs go to `stderr`.
4. Prefer `--prompt-file` over inline `--prompt` for hook payloads.
5. Use a timeout-aware waiter. Infinite waits are fine only when the user explicitly wants them.

## Files In This Skill

- `scripts/watch_then_hook.sh`
  Main entrypoint. Starts a waiter in the background by default, captures `stdout`, then calls `vibe hook send`.
- `scripts/watch_github_pr_then_hook.sh`
  Thin convenience wrapper for the common GitHub PR case. It forwards GitHub-specific flags to the bundled waiter and generic delivery flags to the main wrapper.
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
  [--log-file "/tmp/watch.log"] \
  [--foreground] \
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
- `--log-file`
  Optional. Background log destination. If omitted, the wrapper picks a file under `/tmp/`.
- `--foreground`
  Optional. Run inline. Use this only when you explicitly want the low-level synchronous primitive for testing or composition.
- `--post-to`
  Optional. Use only when the follow-up should post to the parent channel while keeping thread context.
- `--deliver-key`
  Optional. Explicit delivery target key. Use this only when the follow-up should be delivered to a different target.
- `--hook-bin`
  Optional. Explicit hook executable override.
- `--hook-cmd`
  Optional. Full hook command override. Useful when the local `vibe` on `PATH` is a stub and the real CLI should run through something like `uv run python -m vibe`.
- `--timeout-exit-code`
  Optional. The wrapper treats this exit code as a timeout and still sends a timeout hook summary.

`--post-to` and `--deliver-key` are mutually exclusive. Fail fast if both are set.

## Preferred Execution Pattern

The core reusable move is still the generic wrapper. Start there:

```bash
scripts/watch_then_hook.sh \
  --session-key "slack::channel::C123::thread::171717.123" \
  --prefix "The background condition was met. Inspect the result and continue the task." \
  -- \
  bash -lc 'sleep 120; echo "Waiter finished after 120 seconds."'
```

That command returns immediately, leaves the watcher running in the background, and sends the hook only after the waiter exits with success.

If you need the old low-level synchronous primitive, add `--foreground`.

When running inside the Vibe Remote repo or worktree, the wrapper will auto-fallback to `uv run python -m vibe` if the `vibe` executable on `PATH` is not the real CLI.

Use a thin convenience wrapper like `watch_github_pr_then_hook.sh` only when it materially shortens a common case.

## Generic Workflow

### 1. Pick the waiter

For other use cases, either:

- write a new small waiter in the task workspace, or
- reuse another existing blocking command that prints a final summary to `stdout`

Use `scripts/wait_for_github_pr_activity.py` only when the thing being watched is GitHub PR review activity. Use `scripts/watch_github_pr_then_hook.sh` if you want that common path pre-wired.

If the watcher should immediately surface activity that already exists at startup, add `--catch-up`. Without it, the included GitHub waiter snapshots current activity as the baseline and waits only for newer events.

The included GitHub waiter expects GitHub authentication by default. It will use `GITHUB_TOKEN`, `GH_TOKEN`, or `gh auth token`. Only use `--allow-unauthenticated` for slower best-effort polling when authentication is not available.

### 2. Decide the session target

Use the current thread when the user wants the result to continue in context.

### 3. Build a short hook prefix

The prefix should tell the next turn what to do with the watcher result. Good prefixes are action-oriented:

- `GitHub PR has new review activity. Fetch the latest comments and continue resolving them.`
- `CI finished. Inspect the failure and propose the next fix.`
- `The export completed. Summarize the result and share the artifact details.`

### 4. Start the wrapper

Run `scripts/watch_then_hook.sh ...`. It detaches by default.

### 5. Tell the user what was started

Report:

- what is being watched
- where the hook will be delivered
- where the background log is going

## Waiter Contract

Design waiters to follow this contract:

- `exit 0`: event detected; final summary printed to `stdout`
- `exit 124`: timeout; the wrapper sends a timeout hook with an error summary
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

## Generic Examples

### Delayed Follow-up

```bash
scripts/watch_then_hook.sh \
  --session-key "slack::channel::C123::thread::171717.123" \
  --prefix "The delayed check completed. Continue from the waiter output below." \
  -- \
  bash -lc 'sleep 120; echo "Timer finished after 120 seconds."'
```

### File Appears

```bash
scripts/watch_then_hook.sh \
  --session-key "slack::channel::C123::thread::171717.123" \
  --prefix "The export file is ready. Inspect it and continue." \
  -- \
  bash -lc 'while [ ! -f /tmp/export.json ]; do sleep 10; done; echo "Detected /tmp/export.json"'
```

### Log Pattern

```bash
scripts/watch_then_hook.sh \
  --session-key "slack::channel::C123::thread::171717.123" \
  --prefix "The expected log pattern appeared. Inspect the event and continue." \
  -- \
  bash -lc 'tail -Fn0 /tmp/app.log | while read -r line; do case "$line" in *READY*) echo "$line"; break;; esac; done'
```

## GitHub Example
`scripts/watch_github_pr_then_hook.sh` is a convenience wrapper for one specific waiter pair: `watch_then_hook.sh` plus `wait_for_github_pr_activity.py`.

Its default timeout is 6 hours. Override it with `--timeout <seconds>` when the watch should end sooner or run longer.

```bash
scripts/watch_github_pr_then_hook.sh \
  --session-key "slack::channel::C123::thread::171717.123" \
  --repo cyhhao/vibe-remote \
  --pr 151 \
  --prefix "PR review changed. Check the latest GitHub review results and continue the review loop." \
  --interval 60 \
  --timeout 14400
```

To catch up on comments or reviews that already exist before the watcher starts, add `--catch-up`:

```bash
scripts/watch_github_pr_then_hook.sh \
  --session-key "slack::channel::C123::thread::171717.123" \
  --repo cyhhao/vibe-remote \
  --pr 151 \
  --prefix "PR review already has activity. Pull the current review state and continue the thread." \
  --catch-up
```

If authentication is unavailable and a quick one-off best-effort watch is still acceptable, you may opt in explicitly:

```bash
  scripts/watch_github_pr_then_hook.sh \
    --session-key "slack::channel::C123::thread::171717.123" \
    --repo cyhhao/vibe-remote \
    --pr 151 \
    --prefix "Best-effort unauthenticated GitHub watch fired. Inspect the latest PR state." \
    --allow-unauthenticated \
    --interval 180
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
