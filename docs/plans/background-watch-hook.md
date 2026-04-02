# Background Watch Hook

## Background

We want a reusable pattern and bundled guide that teaches agents how to wait for an external condition in the background and automatically send a `vibe hook` back into the current IM session when the condition is met.

The user explicitly does not want a new event subsystem inside Vibe Remote. The solution should stay lightweight and be built from:

1. a blocking waiter script
2. a detached shell wrapper
3. `vibe hook send`

## Goal

Add a reusable guide under `skills/` that:

- explains the detached execution pattern as a generic technique
- provides a general wrapper script for `wait -> capture stdout -> vibe hook send`
- provides a GitHub PR activity waiter as a concrete example

## Design

- `skills/background-watch-hook/SKILL.md`
  - explains when to use the pattern
  - explains how to derive the current `session_key`
  - explains the detached-shell pattern, wrapper interface, and common pitfalls
- `skills/background-watch-hook/scripts/watch_then_hook.sh`
  - runs an arbitrary waiter command
  - captures stdout
  - builds a prompt file
  - invokes `vibe hook send`
- `skills/background-watch-hook/scripts/wait_for_github_pr_activity.py`
  - waits until a PR gets new review activity
  - prints a concise activity summary to stdout

## Validation

- `bash -n` on the shell script
- `python3 -m py_compile` on the Python waiter
- basic `--help` smoke checks
