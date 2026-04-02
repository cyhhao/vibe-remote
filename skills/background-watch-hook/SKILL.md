---
name: background-watch-hook
slug: background-watch-hook
description: Start a background waiter that returns to the same conversation later. Use when the agent needs to wait for reviews, CI, files, logs, or process completion without blocking the current turn.
version: 0.4.0
---

# Background Watch Hook

Use this skill when the job is "wait now, continue later in the same conversation".

What it gives the agent:

- the ability to stop manual polling
- a reusable way to come back to the same channel or thread later
- a small pattern that works for many external events, not just GitHub

Good trigger scenarios:

- PR reviews or comments may arrive later
- CI, deployments, or exports need time to finish
- a file, log line, or process exit should wake the agent up later

Do not build a new scheduler or event system when this pattern is enough.

## Main Tools

- `scripts/watch_then_hook.sh`
  Main entrypoint. Starts a waiter in the background by default, captures its final `stdout`, and sends one follow-up back to the target session.
- `scripts/watch_github_pr_then_hook.sh`
  Thin convenience wrapper for one common case: GitHub PR review activity.
- `scripts/wait_for_github_pr_activity.py`
  Bundled waiter example used by the GitHub wrapper.

## Core Pattern

Use the generic wrapper first. Most tasks only need:

1. the target `session_key`
2. a short action-oriented prefix
3. a blocking waiter command

Generic shape:

```bash
scripts/watch_then_hook.sh \
  --session-key "<session-key>" \
  --prefix "<what the next turn should do>" \
  -- \
  <waiter command ...>
```

Default behavior:

- returns immediately
- keeps the waiter in the background
- sends a follow-up only after the waiter succeeds
- sends a timeout follow-up if the waiter times out

Add `--foreground` only when you explicitly want the low-level synchronous primitive.

## Waiter Contract

Write waiters to follow this contract:

- `exit 0`: event detected; final summary printed to `stdout`
- `exit 124`: timeout; still send a timeout follow-up
- other non-zero: failure; no follow-up

Keep the output split clean:

- `stdout`: final summary for the next turn
- `stderr`: polling logs and diagnostics

## Generic Examples

Delay:

```bash
scripts/watch_then_hook.sh \
  --session-key "slack::channel::C123::thread::171717.123" \
  --prefix "The delayed check completed. Continue from the result below." \
  -- \
  bash -lc 'sleep 120; echo "Timer finished after 120 seconds."'
```

File appears:

```bash
scripts/watch_then_hook.sh \
  --session-key "slack::channel::C123::thread::171717.123" \
  --prefix "The export file is ready. Inspect it and continue." \
  -- \
  bash -lc 'while [ ! -f /tmp/export.json ]; do sleep 10; done; echo "Detected /tmp/export.json"'
```

Log match:

```bash
scripts/watch_then_hook.sh \
  --session-key "slack::channel::C123::thread::171717.123" \
  --prefix "The expected log pattern appeared. Inspect the event and continue." \
  -- \
  bash -lc 'tail -Fn0 /tmp/app.log | while read -r line; do case "$line" in *READY*) echo "$line"; break;; esac; done'
```

## Session Targeting

Use the current Vibe Remote context:

- current channel if the follow-up can return there
- current thread if the follow-up must stay in thread context
- `--post-to channel` only when the user wants to keep thread context but publish in the parent channel

If the current turn does not expose a usable target, ask instead of guessing.

## Timeout And Lifecycle

For the generic wrapper:

- `--timeout` is the waiter timeout
- default is `21600` seconds
- `0` means no timeout

For the GitHub convenience wrapper:

- `--timeout` is still the timeout for one waiter cycle
- `--forever` means re-arm after each detected event
- `--lifetime-timeout` limits the whole long-running watch; default is `0` meaning run until killed

This separation matters: a forever watcher can still use a bounded timeout for each polling cycle.

## GitHub Convenience Wrapper

Use the GitHub wrapper only when the watched thing is PR review activity.

One-shot watch:

```bash
scripts/watch_github_pr_then_hook.sh \
  --session-key "slack::channel::C123::thread::171717.123" \
  --repo cyhhao/vibe-remote \
  --pr 151 \
  --interval 60
```

Catch up on existing activity first:

```bash
scripts/watch_github_pr_then_hook.sh \
  --session-key "slack::channel::C123::thread::171717.123" \
  --repo cyhhao/vibe-remote \
  --pr 151 \
  --catch-up
```

Stay armed for future activity:

```bash
scripts/watch_github_pr_then_hook.sh \
  --session-key "slack::channel::C123::thread::171717.123" \
  --repo cyhhao/vibe-remote \
  --pr 151 \
  --forever \
  --timeout 21600 \
  --lifetime-timeout 86400
```

GitHub-specific notes:

- `--catch-up` reports activity that already exists at startup
- without `--catch-up`, the waiter snapshots current PR activity as the baseline
- authentication is preferred; unauthenticated polling is slower and more fragile

## Practical Advice

- Keep prefixes action-oriented. Tell the next turn what to do with the waiter result.
- Prefer the generic wrapper in the skill body. Treat GitHub as just one example.
- If the execution host reaps detached children, run `--foreground` inside a long-lived terminal session instead of relying on background detaching.
