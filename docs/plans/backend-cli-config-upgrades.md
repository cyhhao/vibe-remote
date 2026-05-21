# Backend CLI config and upgrade isolation

## Problem

Two backend-management paths have drifted away from the actual backend ownership boundaries:

1. Claude Code API-key auth is stored in Vibe Remote V2Config and injected as environment variables. Claude Code also reads `~/.claude/settings.json` and layers `env` on top of the inherited process env, so the saved Vibe Remote key may not be the effective key. The correct source of truth is Claude Code's own settings file.
2. Backend CLI install/upgrade runs synchronously in the UI request path. A slow or stuck CLI update can block a server worker. Backend CLI updates should be independent backend jobs, not main Vibe Remote lifecycle operations.

## Design

### Claude auth source of truth

- Make `vibe.claude_config` able to atomically upsert/remove `env.ANTHROPIC_API_KEY`, `env.ANTHROPIC_AUTH_TOKEN`, and `env.ANTHROPIC_BASE_URL` in Claude's `settings.json`.
- Change Claude auth read/write APIs so `settings.json` is authoritative for key/base-url state.
- Keep V2Config fields only as migration cleanup / UI intent metadata. New saves must not persist the key in V2Config.
- Keep OAuth mode explicit: remove Anthropic key/base-url entries from `settings.json`, set V2Config intent to OAuth, and keep existing OAuth credentials untouched.
- Since Claude is per-request, saving Claude auth only needs controller/runtime config refresh, not Vibe Remote main-service restart.

### Other backends

- Codex remains correct structurally: auth writes go to `~/.codex/auth.json` plus `~/.codex/config.toml`; V2Config is only an intent/cache layer.
- OpenCode remains correct structurally: provider keys go through OpenCode auth APIs / auth store; baseURL lives in OpenCode user config because OpenCode has no auth API field for it.

### Backend CLI upgrades

- Introduce an in-process backend install/upgrade job manager with job IDs, status, output, timeout handling, and child-process cleanup.
- `POST /agent/<backend>/install` starts a background job and returns immediately. Existing install/upgrade command planning remains shared so behavior stays consistent.
- Add `GET /agent/<backend>/install/<job_id>` for polling and optional latest-job lookup by backend.
- On success, invalidate version cache and persist any newly detected CLI path. Do not restart Vibe Remote main service.
- For backend runtime adoption after upgrade:
  - Codex/OpenCode: request backend runtime refresh only, because they have persistent backend daemons.
  - Claude: invalidate/probe version cache only; there is no backend daemon to restart.

## Validation

- Unit tests for Claude settings.json read/write, including overwriting stale env values and clearing OAuth mode.
- API tests for Claude save behavior proving V2Config no longer stores the key.
- API tests for async backend install job lifecycle, success, timeout cleanup, and non-blocking initial response shape.
- Focused lint/test run on touched Python files and affected UI build/type check if frontend API shape changes.
