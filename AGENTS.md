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
- Before starting complex work, capture background, goal, solution, and todo items in a Markdown plan under `docs/plans/`.
- Implementations must follow the plan and its todo items; update the plan document when tasks or scope change.
- If requirements are unclear during planning, ask the user early and proceed only after confirmation.
- If a plan-related subagent exists, prefer calling it to draft/refine the plan.

### Code Review

- If a review-related subagent exists, call it when the code is ready for review.
- Address review findings as appropriate until no must-fix issues remain.

### Documentation Updates

- When adding user-visible features, update the user documentation with usage guidance alongside the code changes.
- For complex features, bug investigations, or refactorings that require detailed documentation (e.g., plans, testing guides, summaries), create a dedicated subfolder under `docs/` with a descriptive name (e.g., `docs/opencode-poll-loop-refactor/`). Place all related documentation files in that subfolder.
- Do NOT place project-specific documentation in the root directory. Keep the root clean.

### Quality Bar

- Prefer root-cause fixes over defensive patches.
- Run the smallest relevant checks first (unit tests, targeted scripts), then broader checks when needed.
- Add tests when there is an existing test pattern; do not introduce a brand-new testing framework unless requested.

### Git Hygiene & Security

- Commit messages: use `type(scope): summary`.
- Never commit secrets (tokens, credentials files).
- Avoid destructive git operations unless explicitly requested (e.g., `reset --hard`, force-push).

### Pre-push Lint Check

Before every `git push`, run the lint check to avoid CI failures:

```bash
# Install ruff if not available
pip install ruff

# Run lint check on changed Python files
ruff check <changed-files>

# Or run on all Python files
ruff check .
```

Fix any errors before pushing. The CI runs `pre-commit run --all-files` with ruff, so local lint must pass first.

### Worktree Development Flow

For complex features or bug fixes that may take extended time and block the main workspace, use git worktree:

**When to use worktree:**
- Task is complex and may span multiple sessions
- Need to keep main workspace clean for quick fixes
- Working on multiple independent features in parallel

**How to use:** Load the worktree skill for detailed setup, workflow, and cleanup instructions.

## 2) Project-Specific (Vibe Remote)

### Structure

- Entry point: `main.py` wires `config.V2Config` into `core/controller.py`.
- Core orchestration and handlers: `core/` (notably `core/handlers/`).
- Agent backends: `modules/agents/` (shared base, OpenCode/Claude/Codex backends, registry).
- IM transports: `modules/im/` (Slack-first; platform abstraction retained).
- Config:
  - Defaults and validation: `config/` (see `config/v2_config.py`).
  - Agent routing: configured via Slack Agent Settings.
- Runtime data:
- Logs: `~/.vibe_remote/logs/vibe_remote.log`.
- Persisted state: `~/.vibe_remote/state/`.
- Default remote working dir: `_tmp/`.


### Common Commands

- Setup:
  - `uv tool install vibe`
- Run:
  - `vibe`
  - `vibe status` / `vibe stop`
  - Restart: run `vibe`

### Frontend (UI)

- Location: `ui/` (React + Vite + TypeScript)
- Build: `npm run build` (from `ui/` directory)
- i18n: `ui/src/i18n/en.json` and `zh.json`
- Output: `ui/dist/` (served by `vibe/ui_server.py`)

**Development workflow:**
1. Make changes to `ui/src/`
2. Run `npm run build` in `ui/` directory
3. For local preview, install in editable mode: `uv tool install --force --editable .`
4. Restart `vibe` to load new assets

**Important:** The installed `vibe` command uses bundled UI assets from the package, not `ui/dist/`. To test local UI changes, either:
- Use editable install (`uv tool install --force --editable .`)
- Or reinstall the package after building

### Release Notes

- Tags follow the latest version number +1 (e.g., `v1.0.1` -> `v1.0.2`) and should be pushed; releases are published automatically by workflow.

### Coding Conventions

- Follow PEP 8, 4-space indentation.
- Naming: `snake_case` for functions, `PascalCase` for classes/dataclasses.
- Use type hints for public functions.
- Keep modules cohesive; add new handlers under `core/handlers/`, new IM transports under `modules/im/`.
- No repo-wide formatter is enforced; use Black/Ruff if you want, keep diffs focused.

### Testing

- No committed automated suite yet.
- Prefer fast `pytest`-style tests (`test_<feature>.py`) colocated or under `tests/`.
- For IM integrations, stub Slack clients and validate outbound payload schemas.
- Do a manual E2E sanity check (start bot, send `/start`) until CI exists.

### Agent Routing

- OpenCode enablement: `OPENCODE_ENABLED=true` (default: false) and `OPENCODE_CLI_PATH` points to the CLI.
- Codex enablement: `CODEX_ENABLED=true` (default: true) and `CODEX_CLI_PATH` points to the CLI.
- Routing is configured per channel via Slack **Agent Settings**.

### Safety Notes

- Keep `AGENT_DEFAULT_CWD` scoped to `_tmp/` (or another sanitized directory).
- Logs can contain sensitive context; scrub before sharing.
