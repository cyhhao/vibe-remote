# Agent Guidelines

This file defines how coding agents should work in this repository.

## 1) General (Reusable)

### Language

- Default to English for all comments, docs, user-facing copy, and logs.
- Use non-English text only when required for i18n/localization.

### Workflow (Branches + PRs)

- Always branch from the latest `master` when starting a new feature or bug fix.
- Implement work on a new branch, validate changes, then open a PR to `master` for review.
- Keep commits small and focused; avoid mixing unrelated changes.

### Planning (When Work Is Non-trivial)

- If the task is complex or ambiguous, propose a short plan and confirm it with the user before large changes.
- If a plan-related subagent exists, prefer calling it to draft/refine the plan.

### Code Review

- If a review-related subagent exists, call it when the code is ready for review.
- Address review findings as appropriate until no must-fix issues remain.

### Quality Bar

- Prefer root-cause fixes over defensive patches.
- Run the smallest relevant checks first (unit tests, targeted scripts), then broader checks when needed.
- Add tests when there is an existing test pattern; do not introduce a brand-new testing framework unless requested.

### Git Hygiene & Security

- Commit messages: use `type(scope): summary`.
- Never commit secrets (e.g., `.env`, tokens, credentials files).
- Avoid destructive git operations unless explicitly requested (e.g., `reset --hard`, force-push).

## 2) Project-Specific (Vibe Remote)

### Structure

- Entry point: `main.py` wires `config.AppConfig` into `core/controller.py`.
- Core orchestration and handlers: `core/` (notably `core/handlers/`).
- Agent backends: `modules/agents/` (shared base, Claude/Codex backends, registry).
- IM transports: `modules/im/` (Slack/Telegram).
- Config:
  - Defaults and validation: `config/` (see `config/settings.py`).
  - Agent routing: `agent_routes.example.yaml` (template) and local `agent_routes.yaml` (gitignored).
- Runtime data:
  - Logs: `logs/vibe_remote.log`.
  - Persisted state: `user_settings.json`.
  - Default remote working dir: `_tmp/`.

### Common Commands

- Setup:
  - `python -m venv .venv && source .venv/bin/activate`
  - `pip install -r requirements.txt`
  - `cp .env.example .env`
- Run:
  - `./start.sh` (preferred) or `python main.py`
  - `./status.sh` / `./stop.sh`

### Coding Conventions

- Follow PEP 8, 4-space indentation.
- Naming: `snake_case` for functions, `PascalCase` for classes/dataclasses.
- Use type hints for public functions.
- Keep modules cohesive; add new handlers under `core/handlers/`, new IM transports under `modules/im/`.
- No repo-wide formatter is enforced; use Black/Ruff if you want, keep diffs focused.

### Testing

- No committed automated suite yet.
- Prefer fast `pytest`-style tests (`test_<feature>.py`) colocated or under `tests/`.
- For IM integrations, stub Slack/Telegram clients and validate outbound payload schemas.
- Do a manual E2E sanity check (start bot, send `/start`) until CI exists.

### Agent Routing / Codex

- Codex enablement: `CODEX_ENABLED=true` and `CODEX_CLI_PATH` points to the CLI.
- Routing file:
  - Copy `agent_routes.example.yaml` to `agent_routes.yaml` (local).
  - Controlled by `AGENT_ROUTE_FILE` (defaults to repo-root `agent_routes.yaml`).
  - Keys are Slack channel IDs / Telegram chat IDs; values are agent names (e.g., `claude`, `codex`).
  - Missing entries fall back to platform default, then to global default.

### Safety Notes

- Keep `CLAUDE_DEFAULT_CWD` scoped to `_tmp/` (or another sanitized directory).
- Logs can contain sensitive context; scrub before sharing.
