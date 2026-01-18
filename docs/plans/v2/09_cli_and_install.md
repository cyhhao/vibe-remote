# CLI and Install Flow (V2)

This document defines the install script, CLI command surface, and runtime behavior.

## Install Script

Entry point:

```
curl -fsSL https://vibe.remote/install.sh | bash
```

Behavior:

- Auto-install `uv` without user confirmation.
- Version check:
  - If `uv` exists and meets minimum version, keep it.
  - If `uv` exists but is outdated, upgrade automatically.
  - If `uv` is missing, install automatically.
- Install CLI via `uv tool install vibe`.

## CLI Command Surface

### `vibe`

Single smart entrypoint.

- If config directory or files do not exist, initialize and launch the setup UI.
- If config exists, start the service.
- Always starts the service in the background.
- Automatically opens the Web UI, then exits the CLI.

### `vibe stop`

- Stop the background service by PID.
- Clears runtime state.

### `vibe status`

- Show service state (running/stopped), mode, workspace, and relay status.

### `vibe doctor`

- Full diagnostics with detailed output:
  - Config validation
  - Slack token checks (`auth.test`)
  - CLI executables detection
  - Relay connectivity (SaaS)
- The same diagnostic results are visible in the Web UI.

## Runtime Behavior

- Service runs in the background by default.
- Web UI is opened automatically on start.
- Web UI provides start/stop/restart controls.
- Runtime data stored under `~/.vibe_remote/runtime/` (pid, ports, status).
