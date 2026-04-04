# Regression Testing

`回归测试` is the manual regression workflow for this repository. It starts a single unified container with all four IM platforms (Slack, Discord, Feishu, WeChat) running simultaneously, each with per-channel backend routing pre-configured.

The container state is persistent by default. Changes you make through the UI or inside the running service, such as channel routing and other saved settings, stay under `_tmp/three-regression/` across normal restarts.

It complements the existing automated `E2E` flow instead of replacing it:

- `E2E testing` keeps using scripts and pytest for automatable scenarios.
- capability scenario metadata now lives under `tests/scenarios/`
- multi-step auth/setup journeys should add or update `tests/scenarios/auth_setup/catalog.yaml` and `tests/scenarios/auth_setup/test_auth_setup_scenarios.py`
- `docs/regression/` is now a human-facing entry layer, not the canonical source of truth for scenario metadata
- `Regression testing` is for human-triggered checks on real IM platforms.

## Scenario Metadata Navigation

Start here only if you are doing manual regression or need the human-readable index.

For deterministic scenario metadata, read:

1. `tests/scenarios/INDEX.yaml`
2. `tests/scenarios/<capability>/catalog.yaml`
3. `tests/scenarios/<capability>/observations.yaml`
4. `tests/scenarios/<capability>/test_*.py`

## Setup

1. Copy the local template:

   ```bash
   cp .env.three-regression.example .env.three-regression
   ```

2. Fill in `.env.three-regression` with:

- shared LLM credentials: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
- optional API base URLs: `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, `OPENAI_API_BASE`
- optional UI host override: `THREE_REGRESSION_UI_HOST` (useful when exposing the UI on a LAN host)
- platform-specific bot credentials for Slack, Discord, Feishu, and WeChat
- the target regression channel for each platform, if you want channel routing preseeded at startup
- the backend that each platform's channel should pin to by default

Channel IDs are optional. If you leave them empty, the container still starts and you can configure channels later from the Web UI.

3. Keep these local-only files out of git:

- `.env.three-regression`
- `_tmp/three-regression/`

## Usage

The default command rebuilds the image, preserves your current regression state, and recreates the container:

```bash
./scripts/run_three_regression.sh
```

Common commands:

```bash
./scripts/run_three_regression.sh --no-build
./scripts/run_three_regression.sh --reset-config
./scripts/run_three_regression.sh --reset-all
./scripts/run_three_regression.sh --status
./scripts/run_three_regression.sh --logs
./scripts/run_three_regression.sh --down
```

If you run the script over SSH on macOS and `docker` is not in the non-interactive `PATH`, the script now auto-detects common Docker CLI locations such as `/usr/local/bin/docker` and `/opt/homebrew/bin/docker`. You can also override it explicitly:

```bash
DOCKER_BIN=/usr/local/bin/docker ./scripts/run_three_regression.sh
```

Reset modes:

- `--reset-config`: re-seed `config/`, `state/`, and `runtime/` from `.env.three-regression`, while preserving `workdir/`, `attachments/`, and `logs/`
- `--reset-all`: wipe the full state directory, including `workdir/`, then re-seed from `.env.three-regression`

`--reset-state` remains available as a backward-compatible alias for `--reset-all`.

## What You Get

On success, the script prints one local UI URL together with the configured per-platform channel/backend mapping:

```text
Unified regression environment is ready:
  URL: http://127.0.0.1:15130
  Default backend: opencode

  Platform routing:
  - Slack:   channel=C123SLACK  backend=opencode
  - Discord: channel=1234567890  backend=codex
  - Feishu:  channel=oc_xxx     backend=claude
  - WeChat:  channel=(QR login)  backend=opencode
```

If you set `THREE_REGRESSION_UI_HOST=192.168.2.3`, the printed URL and generated UI config will use that host instead.

On first startup, or when you run with `--reset-config` / `--reset-all`, the runner seeds these files under `_tmp/three-regression/vibe/`:

- `config/config.json`
- `state/settings.json`
- `state/sessions.json`

The generated state lives under `_tmp/three-regression/`, which keeps the regression environment isolated while preserving your later modifications by default.

Persistence rules:

- `./scripts/run_three_regression.sh` preserves UI changes, sessions, and files under `workdir/`
- `--reset-config` preserves `workdir/` files but resets service config/state
- `--reset-all` clears everything under the state directory

## Architecture

The unified container leverages the multi-platform IM support to run all four platforms in one process:

- **Config**: A single `config.json` with `platforms.enabled: ["slack", "discord", "lark", "wechat"]` and all four platform credential blocks populated.
- **Routing**: Per-channel backend routing via `settings.json` scoped by platform, so each platform's test channel resolves to its designated backend.
- **Agents**: All three backend agents (OpenCode, Claude, Codex) are enabled and installed in the container image.
- **State**: A single `_tmp/three-regression/vibe/` directory holds config, state, logs, and the agent workdir.
- **Shared agent home configs**: Generated under `_tmp/three-regression/shared-home/` and mounted read-only into the container.

## Configuration Rules

- All four IM platforms (Slack, Discord, Feishu, WeChat) run in a single container process.
- The `platforms.primary` is set to `slack` by default (controls fallback behavior).
- `THREE_REGRESSION_DEFAULT_BACKEND` sets the global default backend (default: `opencode`).
- Per-platform backend vars (`THREE_REGRESSION_SLACK_BACKEND`, etc.) control per-channel routing in `settings.json`.
- All three agent CLIs receive shared credentials via `_tmp/three-regression/shared-home/`.
- The default working directory is `/data/vibe_remote/workdir`, a writable sandbox under the generated state.

## Secret Safety

- Never commit `.env.three-regression`.
- Never commit generated files under `_tmp/three-regression/`.
- Share `.env.three-regression.example` if you only need to show the structure.
