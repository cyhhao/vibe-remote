# V2 Milestones and Decisions

## Confirmed Decisions

- Implementation language: Python (no TypeScript rewrite for V2).
- Install flow: single Bash command, prefer `uv` tooling, CLI-first.
- Configuration UI: local web server launched from CLI.
- Data model: JSON files only.
- Storage location: `~/.vibe_remote/`.
- Modes: Slack-only for V2 (platform abstraction remains for future Vibe app).
- Migration: no V1 migration; V2 starts clean.

## V2 Milestones

### M1: Local Data Directory + Config Model

- Establish `~/.vibe_remote/` as the single home for config/state/logs.
- Split settings and sessions into separate JSON files.
- Remove `.env` and legacy config paths from the V2 flow.

Proposed structure:

- `~/.vibe_remote/config/config.json`
- `~/.vibe_remote/state/settings.json`
- `~/.vibe_remote/state/sessions.json`
- `~/.vibe_remote/logs/vibe_remote.log`

### M2: CLI Install + Command Surface

- One-line install (Bash + `uv` tooling).
- CLI commands:
  - `vibe` (smart entrypoint for setup/start)
  - `vibe status` (runtime status)
  - `vibe stop` (stop runtime)
  - `vibe doctor` (self-check)

### M3: Local Web UI (Setup Wizard)

- UI starts from CLI and opens a browser.
- Two setup paths:
  - SaaS: OAuth flow, workspace binding, local gateway pairing.
  - Self-host: Slack app manifest guidance + token validation.

### M4: SaaS MVP (No Data Persistence)

- Official Slack App + OAuth install.
- Events API ingress.
- Relay to local gateway (WebSocket/gRPC).
- Cloud stores only workspace binding and connection status.

## Non-Goals (V2)

- Telegram support in V2 (explicitly out of scope).
- V1 migration or `.env` compatibility.
- Cloud-hosted execution/workspaces.
- TypeScript rewrite or Bun-based binaries.
