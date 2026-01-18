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

### M1: Local Data Directory + Config Model (Status: In progress)

- [x] Establish `~/.vibe_remote/` as the single home for config/state/logs.
- [x] Split settings and sessions into separate JSON files.
- [~] Remove `.env` and legacy config paths from the V2 flow.

Proposed structure:

- `~/.vibe_remote/config/config.json`
- `~/.vibe_remote/state/settings.json`
- `~/.vibe_remote/state/sessions.json`
- `~/.vibe_remote/logs/vibe_remote.log`

### M2: CLI Install + Command Surface (Status: In progress)

- [ ] One-line install (Bash + `uv` tooling).
- [~] CLI commands (`vibe`, `vibe status`, `vibe stop` done; `vibe doctor` basic checks only).

### M3: Local Web UI (Setup Wizard) (Status: In progress)

- [x] UI starts from CLI and opens a browser.
- [~] Two setup paths (Self-host token flow present; SaaS OAuth + relay pairing pending).
- [~] Channel-level settings (enable/disable + routing present; validation/target-channel semantics pending).
- [~] Validation + finish step (summary + start exists; full validation pending).
- [~] Doctor panel (UI exists; backend checks are minimal).

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
