<div align="center">

<img src="assets/logo.png" alt="Vibe Remote" width="120"/>

# Vibe Remote

### Your AI coding army, commanded from Slack.

**No laptop. No IDE. Just vibes.**

[![GitHub Stars](https://img.shields.io/github/stars/cyhhao/vibe-remote?color=ffcb47&labelColor=black&style=flat-square)](https://github.com/cyhhao/vibe-remote/stargazers)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?labelColor=black&style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green?labelColor=black&style=flat-square)](LICENSE)

[English](README.md) | [中文](README_ZH.md)

---

![Banner](assets/banner.jpg)

</div>

## The Pitch

You're at the beach. Phone buzzes — production's on fire.

**Old you:** Panic. Find WiFi. Open laptop. Wait for IDE. Lose your tan.

**Vibe Remote you:** Open Slack. Type "Fix the auth bug in login.py". Watch Claude Code fix it in real-time. Approve. Sip margarita.

```
That's it. That's the product.
```

---

## Install in 10 Seconds

```bash
curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash && vibe
```

That's it. Browser opens. Paste your Slack tokens. Done.

<details>
<summary><b>Windows?</b></summary>

```powershell
irm https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.ps1 | iex
```
</details>

---

## Why This Exists

| Problem | Solution |
|---------|----------|
| Claude Code is amazing but needs a terminal | Slack IS your terminal now |
| Context-switching kills flow | Stay in one app |
| Can't code from phone | Yes you can |
| Multiple agents, multiple setups | One Slack, any agent |

**Supported Agents:**
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Deep reasoning, complex refactors
- [OpenCode](https://opencode.ai) — Fast, extensible, community favorite  
- [Codex](https://github.com/openai/codex) — OpenAI's coding model

---

## How It Works

```
┌──────────────┐         ┌──────────────┐         ┌──────────────┐
│     You      │  Slack  │ Vibe Remote  │  stdio  │  AI Agent    │
│  (anywhere)  │ ──────▶ │  (your Mac)  │ ──────▶ │ (your code)  │
└──────────────┘         └──────────────┘         └──────────────┘
```

1. **You type** in Slack: *"Add dark mode to the settings page"*
2. **Vibe Remote** routes to your configured agent
3. **Agent** reads your codebase, writes code, streams back
4. **You review** in Slack, iterate in thread

**Your code never leaves your machine.** Vibe Remote runs locally and connects via Slack's Socket Mode.

---

## Quick Start

### 1. Install
```bash
curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash
```

### 2. Run
```bash
vibe
```

### 3. Setup Slack (5 min)
The web UI guides you through everything. Or read the [detailed guide](docs/SLACK_SETUP.md).

### 4. Vibe
```
/start → Pick your agent → Start typing
```

---

## Commands

| In Slack | What it does |
|----------|--------------|
| `/start` | Open control panel |
| `/stop` | Kill current session |
| Just type | Talk to your agent |
| Reply in thread | Continue conversation |

**Pro tip:** Each Slack thread = isolated session. Start multiple threads for parallel tasks.

---

## Per-Channel Routing

Different projects, different agents:

```
#frontend    → OpenCode (fast iteration)
#backend     → Claude Code (complex logic)  
#prototypes  → Codex (quick experiments)
```

Configure in web UI → Channels.

---

## CLI

```bash
vibe          # Start everything
vibe status   # Check if running
vibe stop     # Stop everything
vibe doctor   # Diagnose issues
```

---

## Prerequisites

You need at least one coding agent installed:

<details>
<summary><b>Claude Code</b> (Recommended)</summary>

```bash
npm install -g @anthropic-ai/claude-code
```
</details>

<details>
<summary><b>OpenCode</b></summary>

```bash
curl -fsSL https://opencode.ai/install | bash
```
</details>

<details>
<summary><b>Codex</b></summary>

```bash
npm install -g @openai/codex
```
</details>

---

## Security

- **Local-first** — Vibe Remote runs on your machine
- **Socket Mode** — No public URLs, no webhooks
- **Your tokens** — Stored in `~/.vibe_remote/`, never uploaded
- **Your code** — Stays on your disk, sent only to your chosen AI provider

---

## Uninstall

```bash
vibe stop && uv tool uninstall vibe-remote && rm -rf ~/.vibe_remote
```

---

## Roadmap

- [ ] Discord & Teams support
- [ ] File attachments in Slack
- [ ] Multi-workspace
- [ ] Cloud relay mode (optional)

---

## Docs

- **[Slack Setup Guide](docs/SLACK_SETUP.md)** — Create your Slack app
- **[中文安装指南](docs/SLACK_SETUP_ZH.md)** — Chinese guide

---

<div align="center">

**Stop context-switching. Start vibe coding.**

[Install Now](#install-in-10-seconds) · [Setup Slack](docs/SLACK_SETUP.md) · [Report Bug](https://github.com/cyhhao/vibe-remote/issues)

---

*Built for developers who code from anywhere.*

</div>
