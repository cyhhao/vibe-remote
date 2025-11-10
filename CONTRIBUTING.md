# Contributing

Thanks for your interest in contributing!

## Getting Started

- Fork the repo and create a feature branch
- Create and activate a virtualenv
- Install deps: `pip install -r requirements.txt`
- Copy `.env.example` to `.env` and fill values

## Development

- Run locally: `python main.py`
- Lint before PR (add your linter if used)
- Write clear commit messages
- Codex-specific changes: install the Codex CLI, copy `agent_routes.example.yaml` → `agent_routes.yaml`, and route one Slack channel / Telegram chat to `codex` for manual testing (see `docs/CODEX_SETUP.md`).

## Pull Requests

- One logical change per PR
- Include description, screenshots/logs if UX/behavior changes
- Update docs/README if config or behavior changes

## Code of Conduct

By participating, you agree to the CODE_OF_CONDUCT.md
