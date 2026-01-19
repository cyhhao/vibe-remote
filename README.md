<div align="center">

<img src="assets/logo.png" alt="Vibe Remote" width="80"/>

# Vibe Remote

### Code from your couch. Ship from the beach.

**Control AI coding agents from Slack — no IDE required.**

[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?labelColor=black&style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green?labelColor=black&style=flat-square)](LICENSE)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen?labelColor=black&style=flat-square)](CONTRIBUTING.md)

[English](README.md) | [中文](README_ZH.md)

---

![Banner](assets/banner.jpg)

</div>

## Why Vibe Remote?

You're on vacation. Your phone buzzes — a production bug. 

With Vibe Remote, you don't scramble for your laptop. You open Slack, type what needs fixing, and watch the AI agent stream back the solution in real-time. Review, approve, done. Back to your margarita.

**That's vibe coding.**

- 🛋️ **Work from anywhere** — Slack is your IDE now
- 🤖 **Multi-agent support** — OpenCode, Claude Code, Codex — switch per channel
- 🧵 **Thread-based sessions** — Each conversation is isolated, resumable
- ⚡ **Real-time streaming** — Watch your agent think and code live
- 🔒 **Local-first** — Your code stays on your machine

---

## 30-Second Install

**macOS / Linux:**
```bash
curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash
```

**Windows:**
```powershell
irm https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.ps1 | iex
```

Then run:
```bash
vibe
```

A web UI opens. Add your Slack tokens. Enable channels. Start vibing.

---

## How It Works

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│    Slack    │────▶│ Vibe Remote │────▶│  AI Agent   │
│  (You type) │     │  (Routes)   │     │  (Codes)    │
└─────────────┘     └─────────────┘     └─────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │ Your Local  │
                    │  Codebase   │
                    └─────────────┘
```

1. **You** type in Slack: *"Fix the login bug in auth.py"*
2. **Vibe Remote** routes to your chosen AI agent (OpenCode/Claude/Codex)
3. **Agent** analyzes, writes code, streams back results
4. **You** review in Slack, iterate with follow-ups

All execution happens locally. Your code never leaves your machine.

---

## Quick Commands

| Command | What it does |
|---------|--------------|
| `/start` | Open the main menu |
| `/stop` | Stop current agent session |
| `/cwd` | Show working directory |
| `/settings` | Configure message visibility |

**Pro tip:** Use threads! Each thread maintains its own session and working directory.

---

## Per-Channel Agent Routing

Different projects need different agents. Route them per channel:

| Channel | Agent | Why |
|---------|-------|-----|
| `#frontend` | OpenCode | Fast, great for UI work |
| `#backend` | Claude Code | Deep reasoning for complex logic |
| `#experiments` | Codex | Quick prototyping |

Configure via the web UI at `http://localhost:5173/channels`.

---

## Prerequisites

You need at least one coding agent CLI installed:

<details>
<summary><b>OpenCode</b> (Recommended)</summary>

```bash
brew install opencode
# or
curl -fsSL https://opencode.ai/install | bash
```
</details>

<details>
<summary><b>Claude Code</b></summary>

```bash
npm install -g @anthropic-ai/claude-code
```
</details>

<details>
<summary><b>Codex</b></summary>

```bash
brew install codex
```
</details>

---

## CLI Reference

```bash
vibe          # Start service + open web UI
vibe status   # Check if service is running
vibe stop     # Stop everything
vibe doctor   # Diagnose issues
```

---

## Uninstall

```bash
vibe stop
uv tool uninstall vibe-remote   # or: pip uninstall vibe-remote
rm -rf ~/.vibe_remote           # Remove config (optional)
```

---

## Documentation

- **[Slack Setup Guide](docs/SLACK_SETUP.md)** — Create your Slack app in 5 minutes
- **[中文安装指南](docs/SLACK_SETUP_ZH.md)** — Chinese setup guide

---

## Security

- 🔐 Tokens stored locally in `~/.vibe_remote/config/config.json`
- 🏠 Web UI runs on localhost only
- 💻 All code execution happens on your machine
- 🚫 No data sent to third parties (except your chosen AI provider)

---

## Roadmap

- [ ] More IM platforms (Discord, Teams)
- [ ] File attachments support
- [ ] Multi-workspace Slack support
- [ ] SaaS mode with cloud relay

---

## Contributing

PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md).

---

<div align="center">

**Stop context-switching. Start vibe coding.**

[Install Now](#30-second-install) · [Setup Slack](docs/SLACK_SETUP.md) · [Report Bug](https://github.com/cyhhao/vibe-remote/issues)

</div>
