<div align="center">

<img src="assets/logo.png" alt="Vibe Remote" width="120"/>

# Vibe Remote

### Your AI agent army, commanded from Slack, Discord, WeChat & Lark.

**No laptop. No IDE. Just vibes.**

[![GitHub Stars](https://img.shields.io/github/stars/cyhhao/vibe-remote?color=ffcb47&labelColor=black&style=flat-square)](https://github.com/cyhhao/vibe-remote/stargazers)
[![Python](https://img.shields.io/badge/python-3.9%2B-3776AB?labelColor=black&style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green?labelColor=black&style=flat-square)](LICENSE)

[English](README.md) | [中文](README_ZH.md)

**Supported Platforms**

![Slack](https://img.shields.io/badge/Slack-4A154B?style=flat-square&logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACMAAAAiCAYAAADVhWD8AAACT0lEQVR42s2VQW7bMBBFfQQdoDa47CIiBBSuu/QRvCpid1HdwNrHjnUDH4Ww0irdCWjtZukjMF0ViGOM0wuoZJRkCFkUJZmIMsAHLXNAPo5Gn506Mdg8TD9uDlwofRxv/n3N59Afl1Maz0EoFeI0vsQcW9Ffgy8h8hr8huAF5Hq2kBB5WQfCihwJnnNkJYphZlvbMKlOXgKOdx2SYwiUbRjQwXy6AfKqMIM1BO3DqNX5BSH2TqswGHJzVfI/6zC4kVkdJZrAEBY6HguJ1tD6a2zSMqHpPSyawNB4sdQYIxpafaHpmWEQpGj+7OpimMFsDtvaICioCuOx0NHNu/E8QUMzyIbpeeL0JTn86TUdkhNguHIdgO46QBhDZT78hFFjGHlzI0yguyhLYbBnsIn761q9w6Urd3Lhfp/7eGHK8SLL0cC48SxBkLca3Bk5nJwTVbqcsnWAEecv80gFFa/zpzeZ3va+wG1vkqqSGys5CzVHPufX2Ud0eR9REEqraBdRvr+iuA7vTnwEOIZBkIL53jjIgaSN9I1mH4I47dYEI35zTQ48wzSBQLlJBoMLF8Jw4jtlOXIN+f5PgRFV5RlMd5yUnVoClcJgZeDkyvB358PifpiEdWDuIho0hdmxs6HSxOORUiGOjVkZJgNirn+/otuKECArgiDmMMNYCOswO+YNVQF7T1qA0X9N+8hdtg6jareiYfswqofYChum17EZYlPNlTHmBtNDQ7MQ6ENFxtj9PDKZ3h3zPJss6NSZMXI5ymecRdOTldivKBcjqwPyH3MZonz6I6ghAAAAAElFTkSuQmCC)
![Discord](https://img.shields.io/badge/Discord-5865F2?style=flat-square&logo=discord&logoColor=white)
![WeChat](https://img.shields.io/badge/WeChat-07C160?style=flat-square&logo=wechat&logoColor=white)
![Lark](https://img.shields.io/badge/Lark%20%2F%20Feishu-3370FF?style=flat-square&logo=bytedance&logoColor=white)

**Supported Agents**

![Claude Code](https://img.shields.io/badge/Claude%20Code-D4A27F?style=flat-square&logo=anthropic&logoColor=white)
![OpenCode](https://img.shields.io/badge/OpenCode-00B4D8?style=flat-square&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAzMCIgZmlsbD0ibm9uZSI+PHBhdGggZD0iTTE4IDZINlYyNEgxOFY2Wk0yNCAzMEgwVjBIMjRWMzBaIiBmaWxsPSIjZmZmIi8+PC9zdmc+)
![Codex](https://img.shields.io/badge/Codex-412991?style=flat-square&logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAIAAAD8GO2jAAAAAXNSR0IArs4c6QAAAERlWElmTU0AKgAAAAgAAYdpAAQAAAABAAAAGgAAAAAAA6ABAAMAAAABAAEAAKACAAQAAAABAAAAIKADAAQAAAABAAAAIAAAAACshmLzAAAEHklEQVRIDe2VWSitbRTHDyGZZ5EhQyJTxgwhKVOZM4QLuSCSqXBDUS5wQZkuSUSZbkw3KKSMhSJCCZmnEJnP7+s9Obt3s/f39XXuzlN7t571rLX+z7PWf61X4ePj48efXIp/Mvg/sf8CyM2wklwLDI6Ojubm5k5PTy0sLPz8/HR1df+N1y8bWCRjvb29VVdX6+npGRoaWltba2trW1patrW1HR4eLi8vb21tPT8/y3Dn6Ifs44KCAgMDg66urqurq/f39/Pz86SkJK6mo6OjoaGhpqbm6OjY2dkpI4gsgJmZGVVV1YWFBcGfW+fm5urr6ycnJ4+Pj+/u7pK3wsJCJSWl8vLy7zC+Bbi5uYmNjU1MTMTz/v6+vr6ep7i5uY2NjYli9fT08CYgRXph+zVAb2+vubk5bg0NDdjl5+draWk1NTW9vLywXVlZCQsLc3BwGBgYEKJERERERUUJsuj/i0br7+9PTU1NSUmxt7enyMDs7OyQlry8vOvr69LS0pCQEJT+/v7p6emRkZEbGxsJCQlra2vAoxcvESDFNDY2LisrQ09C6urqEKKjozMyMtrb201MTPDnsgJ5lpaWQkNDoZaXl5eTkxO3EUVjK05RX18fDpeXl5y5uroKALxGWVnZyMiotraWYpiZmfn6+k5MTAjhcLGzsyOlJycn8gEqKyu5uGAHAOGQ4+Li6K/9/X1BjxAQEMBTAN7e3kZ5cXHh6enJy6QfIa4Bxby7u4Py+L++vpJZoQyACWVHj8AlnJ2d6W0fH5+Wlha429raCsHoPgwklxggMDBwb29vcXERI8gzPDwcFBTENUmRpBv1tLW1JUvwuLGxkSMPDw8rKytaR9IMWQzg7u7OS3Nycs7OzrKysubn53FbX1+nngALzggwFVlBQYFOFrAVFRXpbV4v2Pz+F9Iq+U9ohhqThw4S9FNTUyTd1NS0pqaG0cRcUlFRgbic0ihgIMAL2AHFJUMhi1mEiqTDOW9vbwZOcHAw8wAlvORl3IsCMJoyMzPhLnoAKAZCSUkJN6BRkCWXOEWEoMLMhuzsbPJDzcGgCcLDw0GqqKhYXV1NS0sjIVAAY4THx8fi4mKQaHXu9Ds5giSJ9inHxMTQosJ2ZGQEzjCX6Fg0T09PxNLU1CwqKmLb3NxMHOo0ODj46S4pfJEijicnJ7lad3e3pCny0NAQCaHVuezDwwMaWiE+Pp7aiiw/t18DcFxVVQU9mM/T09Obm5ujo6MEYqDC3ePjY8GfSc4nYXZ29jOctPAtAKawyMXFRV1dHf4Jn0mGGvOAJuDL09HRAR7Zlw4qqZEFgB2VZJRSWOJCHhsbG8rOV5PWhaywVno2SEZHVuAnrvv3+9vbW7h0cHDA4IPHFON7218n/w1Abjhpgy/6QNro/2j+AsjN3k91TuJWs4eHugAAAABJRU5ErkJggg==)

---

![Banner](assets/banner.jpg)

</div>

## The Pitch

You're at the beach. Phone buzzes — production's on fire.

**Old you:** Panic. Find WiFi. Open laptop. Wait for IDE. Lose your tan.

**Vibe Remote you:** Open Slack, Discord, or WeChat. Type "Fix the auth bug in login.py". Watch Claude Code fix it in real-time. Approve. Sip margarita.

```
AI works. You live.
```

---

## Install in 10 Seconds

```bash
curl -fsSL https://raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh | bash && vibe
```

That's it. Browser opens -> Follow the wizard -> Done.

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
| Claude Code is amazing but needs a terminal | Slack/Discord/WeChat/Lark IS your terminal now |
| Context-switching kills flow | Stay in one app |
| Can't code from phone | Yes you can |
| Multiple agents, multiple setups | One chat app, any agent |

**Supported Agents:**
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) — Deep reasoning, complex refactors
- [OpenCode](https://opencode.ai) — Fast, extensible, community favorite  
- [Codex](https://github.com/openai/codex) — OpenAI's coding model

---

## Why Vibe Remote over OpenClaw?

| | Vibe Remote | OpenClaw |
|---|---|---|
| **Setup** | One command + web wizard. Done in 2 minutes. | Gateway + channels + JSON config. Expect an afternoon. |
| **Security** | Local-first. Socket Mode / WebSocket only. No public endpoints, no inbound ports, minimal attack surface. | Gateway exposes ports. More moving parts, more attack surface. |
| **Token cost** | Thin transport layer — relays messages between your IM and agent. Zero LLM overhead from the middleware itself. | Every message carries a long system context for maintaining agent persona, IM tooling, and orchestration plumbing. Tokens burn on overhead before your actual task even starts. |

OpenClaw is a personal AI assistant — great for casual chat, but its always-on agent loop makes it expensive for real productivity workloads. Vibe Remote is not an agent framework. It's a **remote control** — a minimal bridge between your chat app and whatever AI agent you already use. It adds no extra intelligence layer, no extra token spend, and no extra attack surface. Every token goes straight to your task.

---

## Highlights

<table>
<tr>
<td width="33%">

### Setup Wizard

One-command install, guided configuration. No manual token juggling.

![Setup Wizard](assets/screenshots/setup-slack-en.png)

</td>
<td width="33%">

### Dashboard

Real-time status, health monitoring, and quick controls.

![Dashboard](assets/screenshots/dashboard-en.png)

</td>
<td width="33%">

### Channel Routing

Per-channel agent configuration. Different projects, different agents.

![Channels](assets/screenshots/channels-en.png)

</td>
</tr>
</table>

### Instant Notifications

Get notified the moment your AI finishes. Like assigning tasks to employees — delegate, go do something else, and come back when the work is done. No need to babysit.

### Thread = Session

Each Slack/Discord/WeChat/Lark thread is an isolated workspace. Open 5 threads, run 5 parallel tasks. Context stays separate.

### Interactive Prompts

When your agent needs input — file selection, confirmation, options — your chat app pops up buttons or a modal. Full CLI interactivity, zero terminal required.

![Interactive Prompts](assets/screenshots/question-en.jpg)

---

## How It Works

```
┌──────────────┐             ┌──────────────┐             ┌──────────────┐
│     You      │   Slack     │              │   stdio     │  Claude Code │
│  (anywhere)  │   Discord   │ Vibe Remote  │ ──────────▶ │  OpenCode    │
│              │   WeChat    │  (your Mac)  │ ◀────────── │  Codex       │
│              │   Lark      │              │             │              │
└──────────────┘             └──────────────┘             └──────────────┘
```

1. **You type** in Slack/Discord/WeChat/Lark: *"Add dark mode to the settings page"*
2. **Vibe Remote** routes to your configured agent
3. **Agent** reads your codebase, writes code, streams back
4. **You review** in your chat app, iterate in thread

**Your code never leaves your machine.** Vibe Remote runs locally and connects via Slack Socket Mode, Discord Gateway, WeChat polling, or Lark WebSocket.

---

## Commands

| In chat | What it does |
|----------|--------------|
| `@Vibe Remote /start` | Open control panel |
| `/stop` | Kill current session |
| Just type | Talk to your agent |
| Reply in thread | Continue conversation |

**Pro tip:** Each thread = isolated session. Start multiple threads for parallel tasks.

---

## Instant Agent Switching

Need a different agent mid-conversation? Just prefix your message:

```
Plan: Design a new caching layer for the API
```

That's it. No menus, no commands. Type `AgentName:` and your message routes to that agent instantly.

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
<summary><b>OpenCode</b> (Recommended)</summary>

```bash
curl -fsSL https://opencode.ai/install | bash
```

**Required:** Add to `~/.config/opencode/opencode.json` to skip permission prompts:

```json
{
  "permission": "allow"
}
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
npm install -g @openai/codex
```
</details>

---

## Security

- **Local-first** — Vibe Remote runs on your machine
- **Socket Mode / WebSocket** — No public URLs, no webhooks
- **Your tokens** — Stored in `~/.vibe_remote/`, never uploaded
- **Your code** — Stays on your disk, sent only to your chosen AI provider

---

## Uninstall

```bash
vibe stop && uv tool uninstall vibe-remote && rm -rf ~/.vibe_remote
```

---

## Roadmap

- [x] Slack support
- [x] Discord support
- [x] WeChat support
- [x] Lark (Feishu) support
- [x] Web UI setup wizard & dashboard
- [x] Per-channel agent routing
- [x] Interactive prompts (buttons, modals)
- [x] File attachments
- [ ] SaaS Mode
- [ ] Vibe Remote Coding Agent (one agent to rule them all)
- [ ] Skills Manager
- [ ] Best practices & multi-workspace guide

---

## Docs

- **[CLI Reference](docs/CLI.md)** — Command-line usage and service lifecycle
- **[Slack Setup Guide](docs/SLACK_SETUP.md)** — Detailed setup with screenshots
- **[Discord Setup Guide](docs/DISCORD_SETUP.md)** — Detailed setup with screenshots
- **WeChat Setup Guide** — Follow the in-app wizard (`vibe` → choose WeChat)
- **Lark Setup Guide** — Follow the in-app wizard (`vibe` → choose Lark)

## Remote Server Tip (SSH)

If you run Vibe Remote on a remote server, keep the Web UI bound to `127.0.0.1:5123` and access it via SSH port forwarding:

```bash
ssh -NL 5123:localhost:5123 user@server-ip
```

See: **[CLI Reference](docs/CLI.md)** (search for "Remote Web UI Access")

---

<div align="center">

**Stop context-switching. Start vibe coding.**

[Install Now](#install-in-10-seconds) · [Setup Guide](docs/SLACK_SETUP.md) · [Report Bug](https://github.com/cyhhao/vibe-remote/issues) · [Follow @alex_metacraft](https://x.com/alex_metacraft)

---

*Built for developers who code from anywhere.*

</div>
