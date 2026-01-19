# V2 Milestones and Decisions

## Confirmed Decisions

- Implementation language: Python (no TypeScript rewrite for V2).
- Install flow: single Bash command, prefer `uv` tooling, CLI-first.
- Configuration UI: local web server launched from CLI.
- Data model: JSON files only.
- Storage location: `~/.vibe_remote/`.
- Modes: Slack-only for V2 (platform abstraction remains for future Vibe app).
- Migration: no V1 migration; V2 starts clean.

## Progress Legend

- [x] Done
- [~] In progress
- [ ] Not started

## V2 Milestones

### M1: Local Data Directory + Config Model (Status: Done)

- [x] Establish `~/.vibe_remote/` as the single home for config/state/logs.
- [x] Split settings and sessions into separate JSON files.
- [x] Remove `.env` and legacy config paths from the V2 flow.
- [x] Align defaults with `docs/plans/v2/05_config_model.md` (settings defaults and enablement).
- [x] Drop V1 compatibility paths/aliases in settings and routing.

Implemented structure:

- `~/.vibe_remote/config/config.json`
- `~/.vibe_remote/state/settings.json`
- `~/.vibe_remote/state/sessions.json`
- `~/.vibe_remote/logs/vibe_remote.log`
- `~/.vibe_remote/runtime/vibe.pid`
- `~/.vibe_remote/runtime/status.json`

### M2: CLI Install + Command Surface (Status: Done)

- [x] One-line install (`curl ... | bash` for macOS/Linux, PowerShell for Windows).
- [x] `pyproject.toml` for `uv tool install vibe-remote` and `pip install vibe-remote`.
- [x] CLI commands: `vibe`, `vibe status`, `vibe stop`, `vibe doctor` (all fully implemented).

### M3: Local Web UI (Setup Wizard) (Status: Done for Self-host)

- [x] UI starts from CLI and opens a browser.
- [x] Self-host token flow (Socket Mode) fully implemented.
- [~] SaaS OAuth + relay pairing (UI exists but disabled; backend not implemented).
- [x] Channel-level settings (enable/disable + routing + agent backend selection).
- [x] Validation + finish step (summary + start implemented).
- [x] Doctor panel (UI + backend checks implemented with groups/summary format).

### M4: SaaS MVP (No Data Persistence) (Status: Not started)

- [ ] Official Slack App + OAuth install.
- [ ] Events API ingress.
- [ ] Relay to local gateway (WebSocket/gRPC).
- [ ] Cloud stores only workspace binding and connection status.

## Non-Goals (V2)

- Telegram support in V2 (explicitly out of scope).
- V1 migration or `.env` compatibility.
- Cloud-hosted execution/workspaces.
- TypeScript rewrite or Bun-based binaries.
