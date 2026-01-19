<div align="center">

<img src="assets/logo.png" alt="Vibe Remote" width="40"/>

# Vibe Remote

[Quick Start](#quick-start) · [Configuration](#configuration) · [Usage](#usage) · [Setup Guides](#setup-guides) · [Roadmap](#roadmap)

[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platforms-Slack-4A90E2)](#setup-guides)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen)](CONTRIBUTING.md)

[English](README.md) | [中文](README_ZH.md)

![Banner](assets/banner.jpg)

</div>

_Remote vibe coding over chat — control AI coding agents (OpenCode, Claude Code, Codex, Cursor, etc.) from Slack._

Vibe Remote lets you operate coding agents via Slack. Type in Slack to start and steer agents; describe intent and constraints, receive streaming results, and ship without being tied to a local IDE.

## Why Vibe Remote

- **Vibe coding, not micromanaging**: Let AI drive based on your intent and constraints; focus on outcomes.
- **Work from anywhere**: Control coding sessions over Slack; no IDE tether.
- **Extensible by design**: OpenCode-first, and also supports Claude Code + Codex; built to support additional coding agents/CLIs.
- **Multi-agent routing**: Route each Slack channel to OpenCode, Claude Code, or Codex via Slack UI.
- **Session persistence by thread + path**: Each Slack thread maintains its own agent session and working dir; auto‑resume via saved mappings.
- **Interactive Slack UX**: `/start` menu + Settings/CWD modals; buttons over commands for faster flow.

> Recommendation: Prefer Slack as the primary platform. Threaded conversations enable parallel subtasks and keep channel history tidy — each subtask stays in its own thread.

## Core Features

- **Platform-first**: Slack-first today; platform abstraction retained for future Vibe app
- **Hands‑free flow**: Minimal review; messages stream back in real time
- **Persistent sessions**: Per chat/thread sessions, easy resume
- **Threaded Slack UX**: Clean, per‑conversation threads
- **Working dir control**: Inspect and change `cwd` on the fly
- **Personalization**: Toggle per-channel show message types (defaults hide all)

## Architecture (Brief)

- `BaseIMClient` + platform implementations (`slack.py`)
- `IMFactory` to construct clients by platform config
- `Controller` orchestrates sessions, formatting, and command routing

## Prerequisites

- At least one coding agent CLI installed. Recommended: OpenCode (`opencode`). Also supported: Claude Code CLI and Codex CLI.

### OpenCode (Recommended)

Install (Homebrew):

```bash
brew install opencode
```

Install (script):

```bash
curl -fsSL https://opencode.ai/install | bash
```

Verify:

```bash
opencode --help
```

Enable in Vibe Remote:

- `OPENCODE_ENABLED=true`
- Optional: `OPENCODE_CLI_PATH=opencode`, `OPENCODE_PORT=4096`

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

1. Install

```bash
curl -fsSL https://vibe.remote/install.sh | bash
```

2. Run

```bash
vibe
```

## Configuration

### Slack

- `SLACK_BOT_TOKEN` (xoxb-...)
- `SLACK_APP_TOKEN` (xapp-..., Socket Mode)
- `SLACK_TARGET_CHANNELS` optional whitelist of allowed channel IDs (channels only, start with `C`). Leave empty or omit to accept all channels. DMs are not supported currently.

### Claude Code

- `AGENT_DEFAULT_CWD` e.g. `./_tmp` (recommended)
- Legacy alias: `CLAUDE_DEFAULT_CWD` (still supported)
- `CLAUDE_PERMISSION_MODE` e.g. `bypassPermissions`
- `CLAUDE_SYSTEM_PROMPT` optional
- `ANTHROPIC_API_KEY` if required by your SDK setup

### Codex

- Install the [Codex CLI](https://github.com/openai/codex) (e.g., `brew install codex`) and sign in (`codex --help`).
- `CODEX_ENABLED=true` (default) enables the agent; set to false only if the Codex CLI is unavailable. `CODEX_CLI_PATH` overrides the binary path.
- `CODEX_DEFAULT_MODEL` / `CODEX_EXTRA_ARGS` customize the underlying model or flags.

### OpenCode

- OpenCode is enabled by `OPENCODE_ENABLED=true` (default: false). Ensure `opencode` is installed.
- OpenCode runs as a local HTTP server started by Vibe Remote (`opencode serve --hostname=127.0.0.1 --port=4096`).
- Default agent/model settings are read from `~/.config/opencode/opencode.json`, and can be overridden per Slack channel via the Agent Settings dialog.

### Agent routing

- Slack: use the built-in **Agent Settings** dialog to select the backend per channel (recommended).
- Slack routes use channel IDs.
- See [docs/CODEX_SETUP.md](docs/CODEX_SETUP.md) for Codex install and routing notes.
- If OpenCode is enabled, the bot defaults to OpenCode; otherwise it falls back to Claude.

### App

- `LOG_LEVEL` default `INFO`

## Usage

### Commands

- `/start` open menu / welcome
- `/clear` reset conversation/session
- `/cwd` show working directory
- `/set_cwd <path>` change working directory
- `/settings` configure message visibility
- `/stop` force-stop the active agent session (Claude interrupt / Codex process kill)

### Subagent Prefix Routing

Use `SubagentName:` or `SubagentName：` at the start of a message (leading spaces/newlines allowed) to invoke a subagent for the channel’s current agent backend.

- Example: `Plan: outline the steps`
- Matching is case-insensitive; only the channel’s bound agent is searched.
- The subagent’s default model/reasoning are used automatically.
- The bot adds a 🤖 reaction to your message when a subagent is matched.

### Slack

- In channels, run `/start` to open the interactive menu (Current Dir, Change Work Dir, Reset Session, Settings, How it Works)
- The bot organizes each conversation as a thread; reply in the thread to continue
- Slack DMs are not supported currently
- Slash commands are limited in threads; to stop in a thread, type `stop` directly

## Setup Guides

- Slack: [English](docs/SLACK_SETUP.md) | [中文](docs/SLACK_SETUP_ZH.md)

## Releases

See GitHub Releases: https://github.com/cyhhao/vibe-remote/releases

## Roadmap

- Additional coding CLIs/agents beyond the current built-in set
- More IM platforms (Discord, Teams)
- File upload/attachments piping to coding sessions
- Advanced session policies & permissions

## Contributing

See `CONTRIBUTING.md`. By participating you agree to `CODE_OF_CONDUCT.md`.

## License

MIT. See `LICENSE`.

## Security & Ops

- **Secrets**: Never commit tokens. Store in `~/.vibe_remote/config/config.json` or your secret manager.
- **Whitelists**: Restrict access via `SLACK_TARGET_CHANNELS` (channels only, `C…`). Leave empty to accept all channels (Slack DMs currently unsupported).
- **Logs**: Runtime logs at `~/.vibe_remote/logs/vibe_remote.log`.
- **Session persistence**: `~/.vibe_remote/state/sessions.json` stores per‑thread session mappings; persist this file in production.
- **Cleanup**: Set `CLEANUP_ENABLED=true` to safely prune completed receiver tasks during message handling for long‑running processes.
