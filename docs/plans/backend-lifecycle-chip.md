# Backend Lifecycle Chip Plan

## Background

The Settings → Backends page currently shows a tiny "Found" / "Missing" pill per backend (`AgentDetection.tsx` → `StatusBadge`). It cannot tell the user:

- whether the installed CLI is up to date
- whether the backend's persistent server process (OpenCode / Codex) needs a restart to pick up a new Provider / MCP / env change
- how to recover when a backend isn't running

The unified `design.pen` (`qUpin` / `NrTO1` / `x53H1P`) replaces that pill with a chip + popover. The popover surfaces five visual states (Ready / Updating / Disabled / Error / Update Available) and the small set of operations the user is allowed to perform.

## Goal

Wire the Settings → Backends chip + popover to live backend state and provide three user-facing operations:

1. **Upgrade** — for all three backends (Claude / OpenCode / Codex), re-run the installer to pull the latest CLI release.
2. **Restart** — only for OpenCode and Codex (persistent server processes). Used after Provider / MCP / env config changes. Claude is a one-shot CLI so this action is hidden.
3. **Reinstall** — when the backend's CLI is missing or fails to start, run the installer.

Out of scope:

- Stop / Start buttons. The user explicitly said Vibe Remote should manage these processes; only Restart is exposed.
- PID / uptime / exit code / auto-restart timers. None of this leaks into the UI.
- Per-cwd restart granularity for Codex. Restart resets all in-process Codex transports.

## Solution

### Process-model reminder

- **OpenCode** — singleton server (`OpenCodeServerManager`), lazy-started on first message, PID persisted to `~/.vibe_remote/logs/opencode_server.json`. UI server can kill it via the existing pid-file path (same pattern as `vibe/cli.py::_stop_opencode_server`).
- **Codex** — one persistent `codex app-server` subprocess **per working directory**, held in-memory inside `CodexAgent._transports`. No pid file. UI server enumerates `codex app-server` processes owned by the current user and sends SIGTERM; the controller's lazy-start path respawns on the next message.
- **Claude** — one-shot CLI invocation per request; no daemon, no restart action.

### Backend (Python)

1. `vibe/api.py`
   - `get_backend_runtime(name)` returns
     ```python
     {
       "name": "opencode" | "claude" | "codex",
       "enabled": bool,                       # from config
       "cli_path": str,                       # configured path
       "installed": bool,                     # resolves on PATH
       "current_version": str | None,         # `<cli> --version`, 5s timeout, 30s cache
       "latest_version": str | None,          # registry probe, 1h cache, best-effort
       "has_update": bool,
       "supports_restart": bool,              # True for opencode / codex
       "process_status": "running" | "stopped" | "unknown",
       "error": str | None,
     }
     ```
   - `restart_backend(name)` → `{ok, message}`. Reuses `_stop_opencode_server`-style pid-file kill for OpenCode; enumerates `codex app-server` processes via `psutil` for Codex; returns `{ok: False, message: "..."}` for Claude.
   - Upgrade reuses `install_agent(name)` (already runs the upstream installer; same code path for install + upgrade).
   - Caching uses two TTL dicts at module scope (`_VERSION_CACHE`, `_LATEST_CACHE`), guarded by short timeouts and clean fallback (`latest_version=None, has_update=False`) on network failure.

2. `vibe/ui_server.py`
   - `GET /backend/<name>/runtime` → returns `api.get_backend_runtime(name)`.
   - `POST /backend/<name>/restart` → returns `api.restart_backend(name)`. Allowlists `{opencode, codex}`.
   - Upgrade keeps using existing `POST /agent/<name>/install`.

### Frontend

3. `ui/src/components/settings/BackendLifecycleChip.tsx` (new)
   - Props: `{ name, enabled, cliStatus, onUpgraded? }`.
   - Owns its own popover state, version probe (lazy via `api.getBackendRuntime`), upgrade + restart calls.
   - Visual state derivation (priority order):
     1. `enabled === false` → **Disabled** (muted)
     2. internal `phase === 'upgrading'` → **Updating** (cyan)
     3. `cliStatus === 'missing'` or `runtime.installed === false` → **Error / Not running** (red)
     4. `runtime.has_update` → **Update available** (amber)
     5. else → **Ready** (mint)
   - Popover footer:
     - **Update available** → `Upgrade` button.
     - **Ready** → `Restart` button only when `runtime.supports_restart` (i.e. opencode / codex).
     - **Error** → `Restart` (when supported) + `Reinstall` buttons.
     - **Disabled** → muted hint, no buttons.
     - **Updating** → progress, no buttons.
   - Disable the Restart button if the backend is currently upgrading.

4. `ui/src/context/ApiContext.tsx`
   - Add `getBackendRuntime(name)` and `restartBackend(name)` with typed return shapes.

5. `ui/src/components/steps/AgentDetection.tsx`
   - Replace `<StatusBadge>` with `<BackendLifecycleChip>` inside each backend card header.
   - Keep the existing first-time install hint (`isMissing(agent)` block) so the Setup Wizard's first-run guidance remains inline; the chip handles ongoing lifecycle.

6. i18n: add `backendLifecycle.*` keys in `ui/src/i18n/en.json` + `ui/src/i18n/zh.json`.

## Latest-version sources

- **OpenCode** — `https://api.github.com/repos/sst/opencode/releases/latest` → `tag_name` (strip leading `v`).
- **Codex** — `https://registry.npmjs.org/@openai/codex/latest` → `version`.
- **Claude** — `https://registry.npmjs.org/@anthropic-ai/claude-code/latest` → `version`.

All probes use a 5s timeout and short-circuit to `latest_version=None` on any error; the chip silently falls back to **Ready** instead of **Update available** in that case.

## TODO

- [x] Plan document.
- [x] Implement `api.get_backend_runtime` + cache + version probes.
- [x] Implement `api.restart_backend` — controller IPC via marker file `~/.vibe_remote/state/runtime_commands/restart-<backend>.cmd`, with UI-side direct-kill fallback when the controller doesn't ack within 4s.
- [x] Wire HTTP routes `GET /backend/<name>/runtime` + `POST /backend/<name>/restart`.
- [x] Add `getBackendRuntime` / `restartBackend` to `ApiContext`.
- [x] Build `BackendLifecycleChip` + popover.
- [x] Swap into `AgentDetection.tsx`.
- [x] i18n strings for both languages.
- [x] `npm run build` in `ui/`, fix typescript / lint warnings.
- [x] `ruff check` on touched Python files.
- [x] Reviewer subagent + manual sanity check.

## Implementation notes (delta vs original plan)

- Restart is **not** a direct UI-side kill anymore. The UI server writes a marker file and the controller's `RuntimeCommandWatcher` runs `agent_auth_service._refresh_backend_runtime(backend)` — the existing controller-side cleanup that already covers Codex transports + OpenCode singleton, so cached state in the controller process stays consistent. Direct kill remains only as a fallback after a 4 s ack timeout.
- `packaging>=23.0` was added as a runtime dependency for proper PEP 440 version comparison; a tuple-based fallback handles parses that `packaging.version.Version` rejects.
- Failed `latest_version` probes are cached for 120 s (vs 3600 s for success) so transient outages don't lock in a stale "no update" reading for an hour.
- All version + latest caches are keyed by `(name, cli_path)` so editing the configured CLI path invalidates the cache.

## Evidence

- Unit: add focused pytest for version-string parsing + `restart_backend` Claude-path no-op.
- Contract: `psutil` lookup is best-effort; no scenario catalog entry needed.
- Manual: in local Incus regression, open Settings → Backends, verify each chip renders the right state for installed / missing / disabled and that Upgrade + Restart succeed.
