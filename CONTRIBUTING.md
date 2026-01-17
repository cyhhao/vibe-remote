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
- Agent-specific changes: install the relevant CLI (recommended: OpenCode `opencode`; also supported: Codex), then route one Slack channel via Slack **Agent Settings**, or (legacy) create `agent_routes.yaml`, to manually test the backend (`opencode` or `codex`).

## Pull Requests

- One logical change per PR
- Include description, screenshots/logs if UX/behavior changes
- Update docs/README if config or behavior changes

## Code of Conduct

By participating, you agree to the CODE_OF_CONDUCT.md
