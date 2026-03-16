# Three-End Regression

`三端回归测试` is the manual regression workflow for this repository. It starts three isolated containers in parallel, one for Slack, one for Discord, and one for Feishu, with each environment pre-bound to its own channel and backend agent.

The container state is persistent by default. Changes you make through the UI or inside the running service, such as channel routing and other saved settings, stay under `_tmp/three-regression/` across normal restarts.

It complements the existing automated `E2E` flow instead of replacing it:

- `E2E testing` keeps using scripts and pytest for automatable scenarios.
- `Three-end regression` is for human-triggered checks on real IM platforms.

## Setup

1. Copy the local template:

   ```bash
   cp .env.three-regression.example .env.three-regression
   ```

2. Fill in `.env.three-regression` with:

- shared LLM credentials: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
- optional API base URLs: `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, `OPENAI_API_BASE`
- platform-specific bot credentials for Slack, Discord, and Feishu
- the target regression channel for each platform, if you want channel routing preseeded at startup
- the backend that each platform should pin to by default

Channel IDs are optional. If you leave them empty, the containers still start and you can configure channels later from the Web UI.

3. Keep these local-only files out of git:

- `.env.three-regression`
- `_tmp/three-regression/`

## Usage

The default command rebuilds the image, resets generated state, and recreates all three containers:

```bash
./scripts/run_three_regression.sh
```

Common commands:

```bash
./scripts/run_three_regression.sh --no-build
./scripts/run_three_regression.sh --reset-state
./scripts/run_three_regression.sh --status
./scripts/run_three_regression.sh --logs
./scripts/run_three_regression.sh --logs slack
./scripts/run_three_regression.sh --down
```

Use `--reset-state` only when you want to wipe the generated container state and re-seed it from `.env.three-regression`.

## What You Get

On success, the script prints three local UI URLs together with the configured channel/backend mapping, for example:

```text
Slack   -> http://127.0.0.1:15131
Discord -> http://127.0.0.1:15132
Feishu  -> http://127.0.0.1:15133
```

On first startup, or when you run with `--reset-state`, the runner seeds these files for every service:

- `config/config.json`
- `state/settings.json`
- `state/sessions.json`

The generated state lives under `_tmp/three-regression/`, which keeps each regression environment isolated while preserving your later modifications by default.

## Configuration Rules

- The Slack container always runs Slack platform config.
- The Discord container always runs Discord platform config.
- The Feishu container uses the internal `lark` platform config while keeping `feishu` naming in the regression workflow.
- All three containers receive the shared credentials needed by Claude, Codex, and OpenCode.
- Shared Claude / Codex / OpenCode home configs are generated under `_tmp/three-regression/shared-home/` and mounted into all three containers.
- Each container preloads channel routing in `settings.json`, so the target channel resolves to the expected backend immediately after startup.
- The default working directory is `/data/vibe_remote/workdir`, a writable per-container sandbox under the generated regression state.

## Secret Safety

- Never commit `.env.three-regression`.
- Never commit generated files under `_tmp/three-regression/`.
- Share `.env.three-regression.example` if you only need to show the structure.
