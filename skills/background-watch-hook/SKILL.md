---
name: background-watch-hook
slug: background-watch-hook
description: Use `vibe watch` to run a background waiter that returns to the same conversation later. Best for reviews, CI, files, logs, and other wait-now-continue-later workflows.
version: 0.5.0
---

# Background Watch Hook

Use this skill when the job is "wait now, continue later in the same conversation".

What it gives the agent:

- a managed background task instead of manual polling
- a clean way to come back to the same channel or thread later
- a reusable pattern that works for reviews, CI, files, logs, and process completion

Good trigger scenarios:

- PR reviews or comments may arrive later
- CI, deployments, or exports need time to finish
- a file, log line, or process exit should wake the agent up later

Prefer `vibe watch` when the wait should be inspectable, pausable, resumable, or removable later.

## Main Tools

- `vibe watch add`
  Main entrypoint. Starts a managed background watch and sends a follow-up hook after the waiter succeeds or times out.
- `vibe watch list`, `vibe watch show`, `vibe watch pause`, `vibe watch resume`, `vibe watch remove`
  Use these to inspect and manage the watch after creation.
- `scripts/wait_for_github_pr_activity.py`
  Bundled waiter example for one common case: GitHub PR review activity.

## Core Pattern

Use `vibe watch add` first. Most tasks only need:

1. the target `session_key`
2. a short action-oriented prefix
3. a blocking waiter command

Generic shape:

```bash
vibe watch add \
  --session-key "<session-key>" \
  --prefix "<what the next turn should do>" \
  --name "<optional label>" \
  -- \
  <waiter command ...>
```

Default behavior:

- returns immediately
- keeps the waiter managed by Vibe Remote
- lets the agent inspect or stop the watch later
- sends a follow-up after the waiter succeeds or times out

Use `--forever` when the same waiter should re-arm after each detected event instead of exiting after one follow-up.

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
vibe watch add \
  --session-key "slack::channel::C123::thread::171717.123" \
  --name "Delay callback" \
  --prefix "The delayed check completed. Continue from the result below." \
  -- \
  bash -lc 'sleep 120; echo "Timer finished after 120 seconds."'
```

File appears:

```bash
vibe watch add \
  --session-key "slack::channel::C123::thread::171717.123" \
  --name "Wait for export file" \
  --prefix "The export file is ready. Inspect it and continue." \
  -- \
  bash -lc 'while [ ! -f /tmp/export.json ]; do sleep 10; done; echo "Detected /tmp/export.json"'
```

Log match:

```bash
vibe watch add \
  --session-key "slack::channel::C123::thread::171717.123" \
  --name "Watch app log" \
  --prefix "The expected log pattern appeared. Inspect the event and continue." \
  --forever \
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

For `vibe watch add`:

- `--timeout` is the waiter timeout for one cycle
- default is `21600` seconds
- `0` means no per-cycle timeout
- `--forever` means re-arm after each detected event
- `--lifetime-timeout` limits the whole long-running watch; default is `0` meaning run until killed

This separation matters: a forever watch can still use a bounded timeout for each cycle.

## GitHub Example Waiter

Use the bundled GitHub waiter only when the watched thing is PR review activity.

One-shot watch:

```bash
vibe watch add \
  --session-key "slack::channel::C123::thread::171717.123" \
  --name "Watch PR 151 reviews" \
  --prefix "PR #151 has new review activity. Fetch the latest review state, summarize actionable items, and continue handling them if needed." \
  -- \
  python3 skills/background-watch-hook/scripts/wait_for_github_pr_activity.py \
    --repo cyhhao/vibe-remote \
    --pr 151 \
    --interval 60
```

Catch up on existing activity first:

```bash
vibe watch add \
  --session-key "slack::channel::C123::thread::171717.123" \
  --name "Catch up PR 151 reviews" \
  --prefix "PR #151 already has review activity. Fetch the latest review state and continue handling it if needed." \
  -- \
  python3 skills/background-watch-hook/scripts/wait_for_github_pr_activity.py \
    --repo cyhhao/vibe-remote \
    --pr 151 \
    --catch-up
```

Stay armed for future activity:

```bash
vibe watch add \
  --session-key "slack::channel::C123::thread::171717.123" \
  --name "Monitor PR 151 reviews" \
  --forever \
  --timeout 21600 \
  --lifetime-timeout 86400 \
  --prefix "PR #151 has new review activity. Fetch the latest review state, summarize actionable items, and continue handling them if needed." \
  -- \
  python3 skills/background-watch-hook/scripts/wait_for_github_pr_activity.py \
    --repo cyhhao/vibe-remote \
    --pr 151 \
    --interval 60
```

GitHub-specific notes:

- `--catch-up` reports activity that already exists at startup
- without `--catch-up`, the waiter snapshots current PR activity as the baseline
- PR activity also includes the special case where `chatgpt-codex-connector[bot]` leaves a `+1` reaction on the PR body instead of posting a comment
- authentication is preferred; unauthenticated polling is slower and more fragile

## Practical Advice

- Keep prefixes action-oriented. Tell the next turn what to do with the waiter result.
- Prefer `vibe watch` over ad-hoc detached shells when the wait should survive the current turn cleanly.
- Treat GitHub as just one example waiter, not the main point of the skill.
- If a watch is no longer useful, remove it instead of leaving stale background work behind.
