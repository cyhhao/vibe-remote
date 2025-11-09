# Repository Guidelines

## Project Structure & Module Organization
`main.py` is the entry point that wires `config.AppConfig` into the runtime `Controller`. Core orchestration和 handlers live under `core/` (e.g., `core/controller.py`, `core/handlers/*`). Agent integrations now live in `modules/agents/` (shared base, Claude/Codex backends, registry), while IM transports stay in `modules/im/`. Configuration defaults live in `config/` (`agent_routes.example.yaml` + gitignored `agent_routes.yaml` at repo root control per-channel routing), reference docs in `docs/`, static assets in `assets/`, and `_tmp/` is the default remote working directory for agent sessions. Keep runtime logs inside `logs/claude_proxy.log` and persist chat state in `user_settings.json`.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` – create an isolated environment before installing dependencies.
- `pip install -r requirements.txt` – install the Slack/Telegram/Claude clients plus Codex routing deps (`PyYAML` for route parsing).
- `cp .env.example .env` – seed configuration; update IM platform tokens, `CLAUDE_DEFAULT_CWD`, and permission mode.
- `./start.sh` (or `python main.py`) – run the bot locally; prefers Slack when both tokens exist.
- `./status.sh` / `./stop.sh` – check or terminate the background process when using the helper scripts.

## Coding Style & Naming Conventions
Follow PEP 8 with four-space indentation, descriptive `snake_case` for functions, and `PascalCase` for classes/dataclasses (see `config/settings.py`). Type hints are expected for public functions, and inline logging should use the structured format in `main.py` for traceability. Keep modules small and cohesive; new handlers belong in `core/handlers/`, new IM transports under `modules/im/`. No repo-wide formatter is enforced—run your preferred tool (Black/Ruff) before opening a PR and document deviations.

## Testing Guidelines
There is no committed automated test suite yet; favor fast pytest-style modules named `test_<feature>.py` co-located with the code or under `tests/`. Exercise controller flows by faking IM payloads via lightweight fixtures and assert session state mutations. Run `pytest -q` (add it to your dev extras) before pushing; where live integrations are involved, stub Slack/Telegram clients and verify that outbound payloads match the documented schemas. Treat manual end-to-end checks (start bot, send `/start`) as mandatory until CI is in place.

## Commit & Pull Request Guidelines
Commits follow the `type(scope): summary` pattern visible in history (`fix(slack): …`, `feat(status): …`). Keep them small and focused on a single concern, referencing issues when relevant. Every PR must describe intent, list functional changes, attach logs or screenshots for UX-affecting updates, and call out config impacts (new env vars, migrations). Update README/docs whenever behavior or setup steps change, and ensure `pip install -r requirements.txt && ./start.sh` still works from a clean clone.

## Security & Configuration Tips
Never commit `.env`, tokens, or Slack/Telegram secrets; rely on `.env.example` plus local overrides. Validate IM scopes (`SLACK_REQUIRE_MENTION`, `TELEGRAM_TARGET_CHAT_ID`) through `config/settings.py` before shipping, and document any new flags. Keep `CLAUDE_DEFAULT_CWD` scoped to `_tmp/` or another sanitized path so that remote agents cannot escape the intended workspace. Logs may contain sensitive thread context—rotate `logs/claude_proxy.log` in production and scrub before sharing.

## Agent Routing & Codex Notes
- Enable Codex (on by default) via `CODEX_ENABLED=true` and ensure the CLI is reachable (`CODEX_CLI_PATH`); `CODEX_ENABLE_FULL_AUTO` propagates to `codex exec`.
- Copy `agent_routes.example.yaml` → `agent_routes.yaml` to map Slack channel / Telegram chat IDs to agents; see `docs/CODEX_SETUP.md` for a full walkthrough.
- Route Slack channel IDs or Telegram chat IDs in `AGENT_ROUTE_FILE` (`agent_routes.yaml` at repo root by default; copy from `agent_routes.example.yaml`). Keys map to agent names (`claude`, `codex`, future backends). Missing entries fall back to the platform default, then to the global default.
- Session mappings inside `user_settings.json` are now namespaced per agent; avoid manual edits unless you know the nested structure `{agent -> base_session_id -> cwd -> session_id}`.
