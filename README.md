<div align="center">

<img src="assets/logo.png" alt="Vibe Remote" width="40"/>

# Vibe Remote

[Quick Start](#quick-start) · [Configuration](#configuration) · [Usage](#usage) · [Setup Guides](#setup-guides) · [Roadmap](#roadmap)

[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platforms-Slack%20%7C%20Telegram-8A2BE2)](#setup-guides)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen)](CONTRIBUTING.md)

[English](README.md) | [中文](README_ZH.md)

![Banner](assets/banner.jpg)

</div>

_Remote vibe coding over chat — control AI coding agents (Claude Code, Codex, Cursor, etc.) from Slack/Telegram._

Vibe Remote lets you operate coding agents via IM. Type in Slack or Telegram to start and steer agents; describe intent and constraints, receive streaming results, and ship without being tied to a local IDE.

## Why Vibe Remote

- **Vibe coding, not micromanaging**: Let AI drive based on your intent and constraints; focus on outcomes.
- **Work from anywhere**: Control coding sessions over Slack/Telegram; no IDE tether.
- **Extensible by design**: Starts with Claude Code and Codex, built to support additional coding agents/CLIs.
- **Multi-agent routing**: Route each Slack channel / Telegram chat to Claude Code or Codex by editing `agent_routes.yaml`.
- **Session persistence by thread + path**: Each Slack thread/Telegram chat maintains its own Claude session and working dir; auto‑resume via saved mappings.
- **Interactive Slack UX**: `/start` menu + Settings/CWD modals; buttons over commands for faster flow.

> Recommendation: Prefer Slack as the primary platform. Threaded conversations enable parallel subtasks and keep channel history tidy — each subtask stays in its own thread.

## Core Features

- **Multi‑platform**: First‑class Slack & Telegram support
- **Hands‑free flow**: Minimal review; messages stream back in real time
- **Persistent sessions**: Per chat/thread sessions, easy resume
- **Threaded Slack UX**: Clean, per‑conversation threads
- **Working dir control**: Inspect and change `cwd` on the fly
- **Personalization**: Toggle which message types to display

## Architecture (Brief)

- `BaseIMClient` + platform implementations (`slack.py`, `telegram.py`)
- `IMFactory` to construct clients by `IM_PLATFORM`
- `Controller` orchestrates sessions, formatting, and command routing

## Prerequisites

- At least one coding agent CLI installed (Claude Code CLI or Codex CLI). You can install both to mix and match per channel.

### Claude Code

Install:

```bash
npm install -g @anthropic-ai/claude-code
```

Verify:

```bash
claude --help
```

## Quick Start

1. Install dependencies

```bash
pip install -r requirements.txt
```

2. Create and edit `.env`

```bash
cp .env.example .env
# Set IM_PLATFORM and tokens
```

3. Run

```bash
./start.sh
# or
python main.py
```

## Configuration

### Platform selection

- `IM_PLATFORM=slack` or `IM_PLATFORM=telegram`

### Slack

- `SLACK_BOT_TOKEN` (xoxb-...)
- `SLACK_APP_TOKEN` (xapp-..., Socket Mode)
- `SLACK_TARGET_CHANNEL` optional whitelist of allowed channel IDs (channels only, start with `C`). Leave empty or omit to accept all channels. DMs are not supported currently.

### Telegram

- `TELEGRAM_BOT_TOKEN` from @BotFather
- `TELEGRAM_TARGET_CHAT_ID` optional whitelist: `[123,...]` | `[]` only DMs | `null` all

### Claude Code

- `CLAUDE_DEFAULT_CWD` e.g. `./_tmp`
- `CLAUDE_PERMISSION_MODE` e.g. `bypassPermissions`
- `CLAUDE_SYSTEM_PROMPT` optional
- `ANTHROPIC_API_KEY` if required by your SDK setup

### Codex

- Install the [Codex CLI](https://github.com/openai/codex) (e.g., `brew install codex`) and sign in (`codex --help`).
- `CODEX_ENABLED=true` (default) enables the agent; set to false only if the Codex CLI is unavailable. `CODEX_CLI_PATH` overrides the binary path.
- `CODEX_DEFAULT_MODEL` / `CODEX_EXTRA_ARGS` customize the underlying model or flags.

### Agent routing

- Copy `agent_routes.example.yaml` → `agent_routes.yaml` (repository root) to configure per-channel routing, or point `AGENT_ROUTE_FILE` to any YAML/JSON file.
- File schema:

```yaml
default: claude
slack:
  default: claude
  overrides:
    C01EXAMPLE: codex
telegram:
  default: claude
  overrides:
    "123456789": codex
```

- Slack routes use channel IDs; Telegram routes use chat IDs. Unlisted channels fall back to the per-platform default (then to the global `default`). `agent_routes.yaml` is gitignored so each environment can customize it safely.

- See [docs/CODEX_SETUP.md](docs/CODEX_SETUP.md) for a step‑by‑step guide to installing Codex CLI and configuring routing.
- No file? Everything routes to Claude.

### App

- `LOG_LEVEL` default `INFO`

## Usage

### Commands (all platforms)

- `/start` open menu / welcome
- `/clear` reset conversation/session
- `/cwd` show working directory
- `/set_cwd <path>` change working directory
- `/settings` configure message visibility
- `/stop` force-stop the active agent session (Claude interrupt / Codex process kill)

### Slack

- In channels, run `/start` to open the interactive menu (Current Dir, Change Work Dir, Reset Session, Settings, How it Works)
- The bot organizes each conversation as a thread; reply in the thread to continue
- Slack DMs are not supported currently
- Slash commands are limited in threads; to stop in a thread, type `stop` directly

### Telegram

- DM or group; run `/start` then type naturally
- Real‑time streaming; long outputs are split and code blocks are formatted

## Setup Guides

- Slack: [English](docs/SLACK_SETUP.md) | [中文](docs/SLACK_SETUP_ZH.md)
- Telegram: [English](docs/TELEGRAM_SETUP.md) | [中文](docs/TELEGRAM_SETUP_ZH.md)

## Roadmap

- Additional coding CLIs/agents beyond Claude Code
- More IM platforms (Discord, Teams)
- File upload/attachments piping to coding sessions
- Advanced session policies & permissions

## Contributing

See `CONTRIBUTING.md`. By participating you agree to `CODE_OF_CONDUCT.md`.

## License

MIT. See `LICENSE`.

## Security & Ops

- **Secrets**: Never commit tokens. Use `.env`. Rotate regularly.
- **Whitelists**: Restrict access via `SLACK_TARGET_CHANNEL` (channels only, `C…`) or `TELEGRAM_TARGET_CHAT_ID`. `null` accepts all; empty list limits to DMs/groups accordingly (Slack DMs currently unsupported).
- **Logs**: Runtime logs at `logs/vibe_remote.log`.
- **Session persistence**: `user_settings.json` stores per‑thread/chat session mappings and preferences; persist this file in production.
- **Cleanup**: Set `CLEANUP_ENABLED=true` to safely prune completed receiver tasks during message handling for long‑running processes.
