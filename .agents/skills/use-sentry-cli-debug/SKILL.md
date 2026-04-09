---
name: use-sentry-cli-debug
slug: use-sentry-cli-debug
description: Use the official Sentry CLI to investigate issues, events, traces, and logs for Vibe Remote without committing Sentry credentials into the repository.
version: 0.1.1
---

# Use Sentry CLI Debug

Use this skill when the user asks to inspect Sentry issues, events, traces, logs, or authenticated Sentry API responses from the command line while working in the Vibe Remote repository.

## Core Rules

1. Prefer `sentry` CLI subcommands over raw REST calls. Use `sentry issue`, `sentry event`, `sentry trace`, and `sentry log` first. Fall back to `sentry api` only when the dedicated command cannot expose the needed field.
2. Do not commit credentials. Prefer `sentry auth login` interactive auth. If token auth is required, use a shell-scoped `SENTRY_AUTH_TOKEN` or another untracked local secret source. Never write tokens into tracked repo files.
3. Keep output small. Prefer `--json --fields ...`, `--limit ...`, and `jq` when filtering.
4. Stay read-only by default. Do not resolve, mute, delete, or otherwise mutate Sentry state unless the user explicitly asks.
5. Verify CLI context before debugging. Run `sentry auth status`, then confirm the target org/project if auto-detection looks ambiguous.

## Issue Status Guidance

When the user asks how to mark an issue that is already fixed on `master` but not yet released or validated in a real environment, recommend `Resolved in next release`.

Use that status for this specific state:

- code is merged to `master`
- the fix is not released yet, or not verified in the real target environment yet
- the user does not want to mark it as fully resolved now

Do not recommend plain `Resolved` for that case unless the user explicitly wants it.

If the current `sentry` CLI build cannot mutate issue state directly, say so plainly and recommend changing the status in the Sentry UI or via a targeted API call only when the user explicitly asks you to perform the mutation.

## Prerequisites

If `sentry` is not installed:

```bash
curl https://cli.sentry.dev/install -fsS | bash
```

Then verify:

```bash
sentry --help
sentry auth status
```

Authentication options that do not require committing secrets:

```bash
# Preferred: interactive login managed by the CLI
sentry auth login

# Alternative: shell-scoped token
export SENTRY_AUTH_TOKEN=...
sentry auth login --token "$SENTRY_AUTH_TOKEN"
```

Never add the token to tracked files such as `.env`, `.envrc`, `config/*.json`, `settings.json`, or skill files.

## Repo-Specific Context

Before querying Sentry, read [vibe-remote.md](references/vibe-remote.md) for the tags and environment metadata currently attached by this repository.

Important current signals:

- `component=service` for the main service
- `component=ui` for the Flask UI
- `mode=<config.mode>`
- `primary_platform=<config.platforms.primary>`
- `deployment_environment=<resolved environment>`
- `release=vibe-remote@<version>`

These are useful narrowing dimensions when many unrelated issues exist in the same Sentry org/project.

## Debug Workflow

### 1. Confirm auth and scope

```bash
sentry auth status
sentry org list
sentry project list <org>
```

If auto-detection is unreliable, use explicit `<org>/<project>` in every command.

### 2. Triage recent issues

```bash
sentry issue list <org>/<project> \
  --query 'is:unresolved' \
  --limit 10 \
  --json \
  --fields shortId,title,count,userCount,lastSeen,level,status
```

Common filters:

```bash
sentry issue list <org>/<project> --query 'is:unresolved environment:regression'
sentry issue list <org>/<project> --query 'component:service'
sentry issue list <org>/<project> --query 'component:ui'
sentry issue list <org>/<project> --query 'primary_platform:slack'
sentry issue list <org>/<project> --query 'release:vibe-remote@1.2.3'
```

### 3. Inspect one issue deeply

```bash
sentry issue view <short-id>
sentry issue explain <short-id>
```

Use `sentry issue explain` only after you have the raw issue details. Treat the AI explanation as a hint, not ground truth.

### 4. Inspect concrete event, trace, and logs

```bash
sentry event view <org>/<project>/<event-id>
sentry trace view <trace-id>
sentry trace logs <trace-id> --limit 100
sentry log list <org>/<project> --query 'level:error' --limit 100
```

Use trace and log commands when the issue points to timing, retries, upstream HTTP failures, or multi-step orchestration problems.

### 5. Use `sentry api` only as a fallback

```bash
sentry api /api/0/organizations/<org>/issues/
```

Prefer `--method`, `--data`, and `--header` only when the dedicated CLI command cannot express the operation.

## Vibe Remote Triage Hints

1. If the bug is user-visible in regression, first search `environment:regression`.
2. Split `component:service` and `component:ui` early. Do not mix backend startup errors with UI Flask failures.
3. When the report is platform-specific, try `primary_platform:<platform>` and cross-check local logs if Sentry detail is incomplete.
4. If no Sentry result appears, check whether the problem was only logged locally in `~/.vibe_remote/logs/vibe_remote.log`.
5. If Sentry output is too noisy, reduce fields instead of pasting full blobs back to the user.

## When Not to Use This Skill

- When the user wants release management or source map upload only, use generic `sentry` CLI commands directly without a full debug workflow.
- When the user explicitly wants dashboard/MCP-based investigation instead of CLI.
