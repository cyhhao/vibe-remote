# Vibe Remote Command Reference

This document is the exhaustive command reference for Vibe Remote.

It covers:

- in-chat commands users send to the bot from Slack, Discord, Telegram, WeChat, and Lark
- command aliases and parser normalization rules
- platform differences
- permission requirements
- host-side CLI commands exposed by the `vibe` executable

For installation, setup, and operations background, also see:

- [CLI Reference](./CLI.md)
- [Slack Setup Guide](./SLACK_SETUP.md)
- [Telegram Setup Guide](./TELEGRAM_SETUP.md)

## 1. Command Surface Overview

Vibe Remote exposes two command families:

1. In-chat commands
   - Sent from an IM platform to the bot.
   - Examples: `/start`, `/resume`, `/setcwd ~/repo`, `/setup`.
2. Host CLI commands
   - Run on the machine where Vibe Remote is installed.
   - Examples: `vibe`, `vibe status`, `vibe task add ...`.

The two families solve different problems:

- in-chat commands control conversations, working directories, session resume, DM binding, and backend auth repair
- host CLI commands control the local service process, diagnostics, upgrades, scheduled tasks, and async hooks

## 2. In-Chat Commands

### 2.1 Supported in-chat commands

These commands are registered by the controller today:

| Command | Purpose |
| --- | --- |
| `/start` | Show the welcome panel / control entry |
| `/new` | Start a fresh session |
| `/clear` | Alias of `/new` |
| `/cwd` | Show current working directory |
| `/setcwd <path>` | Set working directory |
| `/set_cwd <path>` | Internal-style alias that also works |
| `/resume` | Resume a recent session |
| `/setup` | Repair backend login/auth |
| `/settings` | Open settings UI |
| `/stop` | Interrupt the active backend execution |
| `/bind <code>` | Bind a DM user to this Vibe Remote instance |
| `bind <code>` | Plain-text DM alias for unbound users only |

### 2.2 Permission model

Authorization is centralized in `core/auth.py`.

#### Available to any authorized channel user

- `/start`
- `/new`
- `/clear`
- `/cwd`
- `/resume`
- `/stop`

#### Admin-only commands

- `/setcwd <path>`
- `/set_cwd <path>`
- `/settings`
- `/setup`

Notes:

- `/setup` button callbacks such as `Reset OAuth` are also admin-protected.
- If a workspace has at least one admin configured, non-admins cannot run admin-only commands.

#### DM binding exception

- `/bind <code>` is allowed for unbound DM users.
- Plain `bind <code>` is also allowed for unbound DM users on platforms where plain-text bind is enabled as a workaround.

### 2.3 Platform behavior

#### Slack

- Native Slack slash commands are currently exposed only for `/start` and `/stop`.
- Other commands are typically sent as normal bot-directed messages, for example:
  - `@Vibe Remote /resume`
  - `@Vibe Remote /setcwd ~/work/repo`
- In DM, plain `bind <code>` is accepted for unbound users.

#### Discord

- Commands are parsed from normal messages that start with `/`.
- `/resume` opens a Discord-native resume picker flow when the platform interaction context is available.

#### Telegram

- Commands are parsed from normal messages that start with `/`.
- `/resume` and `/settings` prefer inline button flows in the current chat.

#### Lark / Feishu

- Commands are parsed from normal messages that start with `/`.
- `/resume` and `/settings` prefer native cards/modals when available.

#### WeChat

- Commands are parsed from normal text messages that start with `/`.
- `/resume` uses a text-first flow instead of modals.
- `/resume 1`, `/resume more`, `/resume latest ...`, and manual resume syntax are especially relevant on WeChat.

### 2.4 Parser normalization and aliases

The shared parser in `modules/im/base.py` applies these rules:

- `/setcwd /tmp/work` is normalized to internal action `set_cwd`
- `/set_cwd /tmp/work` also works because `set_cwd` is a registered command name
- `bind abc123` is accepted only when plain bind is allowed for that DM user

Backend aliases used by `/resume` and `/setup`:

| Alias | Backend |
| --- | --- |
| `oc` | `opencode` |
| `open-code` | `opencode` |
| `cc` | `claude` |
| `claude-code` | `claude` |
| `cx` | `codex` |

## 3. In-Chat Command Reference

### `/start`

Show the welcome message and the main control entry for the current channel or DM scope.

#### Syntax

```text
/start
```

#### What it does

- shows the current platform
- shows the currently resolved backend for the scope
- shows the current channel when relevant
- lists the main text commands
- opens an interactive menu on platforms that support buttons

#### Typical usage

```text
@Vibe Remote /start
```

#### Notes

- This is the safest discovery command when a user is unsure what is currently configured.
- On some platforms the response appears in-channel rather than inside the thread.

### `/new`

Reset the current session state so the next user message starts a fresh conversation.

#### Syntax

```text
/new
```

#### What it does

- clears active session state for the current scope
- does not delete your repository
- does not change routing or working directory

#### Typical usage

```text
/new
```

### `/clear`

Alias of `/new`.

#### Syntax

```text
/clear
```

#### What it does

- dispatches to the same handler as `/new`

#### Recommendation

- Prefer `/new` in user-facing docs and examples.
- Keep `/clear` in mind for compatibility or old habits.

### `/cwd`

Show the working directory currently associated with the channel or DM scope.

#### Syntax

```text
/cwd
```

#### What it does

- prints the absolute working directory
- reports whether the directory exists
- reminds the user that this is where the backend executes commands

#### Typical usage

```text
/cwd
```

#### Example scenario

- Before asking the agent to edit code, confirm that the current scope points at the correct repository.

### `/setcwd <path>`

Set the working directory for the current channel or DM scope.

#### Syntax

```text
/setcwd <path>
```

Also accepted:

```text
/set_cwd <path>
```

#### What it does

- expands `~`
- converts the path to an absolute path
- creates the directory if it does not exist yet
- saves the custom working directory to the current settings scope

#### Examples

```text
/setcwd ~/projects/myapp
/setcwd /srv/repos/api
/set_cwd ../another-repo
```

#### Permission

- admin-only

#### Notes

- The scope is the current channel for channel chats.
- The scope is the current user for DMs.

### `/resume`

Resume a recent native agent session from the current working directory.

#### Core syntax

```text
/resume
```

#### Text-mode subcommands

```text
/resume 1
/resume more
/resume latest
/resume latest oc
/resume latest cc
/resume latest cx
/resume <backend> <session_id>
```

#### Backend names accepted in text-mode resume

- `oc`
- `opencode`
- `open-code`
- `cc`
- `claude`
- `claude-code`
- `cx`
- `codex`

#### What it does by platform

##### Slack

- `/resume` opens the resume picker when invoked from an interaction-capable context.
- If no modal trigger is available, the bot sends guidance telling the user to open the picker from the menu.

##### Discord

- `/resume` opens a native resume picker flow when the interaction context exists.

##### Telegram

- `/resume` opens an inline-button picker in the current chat.

##### Lark

- `/resume` prefers a native card/modal flow.

##### WeChat

- `/resume` is text-driven.
- `/resume 1` restores item 1 from the current shown list.
- `/resume more` paginates.
- `/resume latest [backend]` restores the newest session.
- `/resume <backend> <session_id>` restores a session manually.

#### Typical usage

```text
/resume
```

Then, on WeChat text flow:

```text
/resume 1
/resume latest cc
/resume codex 123e4567-thread-id
```

#### Notes

- Resume only looks at sessions under the current working directory.
- If the working directory changed since the last list snapshot, selection-by-number expires.

### `/setup`

Repair backend login or provider auth through the IM flow.

#### Syntax

```text
/setup
/setup claude
/setup codex
/setup opencode
/setup cc
/setup cx
/setup oc
/setup code <value>
/setup code <backend> <value>
```

#### What it does

- resolves the backend for the current scope, unless an explicit backend is given
- starts a backend-specific auth recovery flow
- sends browser links, device codes, or follow-up prompts into chat
- waits for completion and verifies login status

#### Backend-specific behavior

##### Claude

- starts the Claude login flow
- sends the browser authorization URL into chat
- if Claude later asks for a pasted code, the user submits:

```text
/setup code <value>
```

##### Codex

- starts device auth
- sends a browser URL plus one-time code into chat
- the bot waits for completion and verifies `codex login status`

##### OpenCode

- infers the provider from current OpenCode routing/model when possible
- `openai` uses headless device-style auth
- other common providers such as `opencode` or `anthropic` use key-entry flows
- when OpenCode asks for a key, the user submits:

```text
/setup code <value>
```

#### Permission

- admin-only

#### Notes

- `/setup code <backend> <value>` is useful when multiple setup flows are open and the backend must be explicit.
- Only the user who started the setup flow can submit its follow-up code or key.

### `/settings`

Open the settings UI for the current scope.

#### Syntax

```text
/settings
```

#### What it does

- opens or routes to the settings menu for the current platform
- allows changing settings such as routing and other scope-level controls through UI flows

#### Permission

- admin-only

#### Notes

- This command is registered even though the exact UI differs by platform.
- Prefer it when users want a guided configuration flow instead of raw commands.

### `/stop`

Interrupt the active backend execution for the current scope.

#### Syntax

```text
/stop
```

#### What it does

- builds a stop request for the currently resolved backend
- asks the backend adapter to interrupt the active task
- if there is no active session, returns an informational response

#### Typical usage

```text
/stop
```

#### Notes

- In threaded platforms, a plain `stop` or `/stop` in-thread may also be recognized by the message path.
- This command does not change routing or clear historical state by itself.

### `/bind <code>`

Bind a DM user to this Vibe Remote instance using a bind code generated from the UI or admin workflow.

#### Syntax

```text
/bind <code>
bind <code>
```

#### What it does

- validates the bind code
- records the DM user as bound
- stores the DM chat ID
- may grant admin on the initial bootstrap bind, depending on the bind-code workflow

#### Permission and context

- DM-only
- allowed even before the user is bound

#### Examples

```text
/bind vr-a3x9k2
bind vr-a3x9k2
```

#### Notes

- Plain `bind <code>` is the compatibility path for platforms or contexts where leading `/` is awkward.
- If the user is already bound, the command returns an already-bound response instead of rebinding.

## 4. What Is Not a Command

These are important user-facing controls, but they are not text commands:

- `AgentName: your message`
  - Example: `Plan: Design a new caching layer`
  - This is the subagent prefix flow, not a command.
- `/start` menu buttons
  - Examples: `Settings`, `Resume Session`, `Change Work Dir`
  - These are button callbacks or modal flows, not slash commands.

## 5. Host CLI Commands

The `vibe` executable controls the local service and async automation features.

## 5.1 Top-level CLI commands

| Command | Purpose |
| --- | --- |
| `vibe` | Start or restart the service and Web UI |
| `vibe stop` | Stop the service and UI; also terminates OpenCode server |
| `vibe restart` | Stop then start again |
| `vibe status` | Print runtime status JSON |
| `vibe doctor` | Run diagnostics |
| `vibe screenshot` | Capture a local desktop screenshot |
| `vibe version` | Show installed version |
| `vibe check-update` | Check for new version |
| `vibe upgrade` | Upgrade to latest version |
| `vibe task ...` | Manage scheduled tasks |
| `vibe hook send ...` | Queue one async hook without persisting a task |

### `vibe`

```bash
vibe
```

- starts or restarts the main service
- opens the Web UI
- preserves the running OpenCode server when possible

### `vibe stop`

```bash
vibe stop
```

- stops the main service
- stops the UI server
- terminates the OpenCode server too

### `vibe restart`

```bash
vibe restart
```

- stops the main service
- stops the UI server
- terminates the OpenCode server too
- starts the service again after a brief wait

Optional async scheduling:

```bash
vibe restart --delay-seconds 60
```

- prints a confirmation immediately
- exits without waiting
- runs the restart in the background after the specified delay

Recommended usage:

- prefer `vibe restart --delay-seconds 60` when an agent triggers the restart from an active conversation
- use plain `vibe restart` when the user explicitly wants the restart to happen immediately

### `vibe status`

```bash
vibe status
```

- prints runtime status JSON

### `vibe doctor`

```bash
vibe doctor
```

- validates config
- checks platform credentials
- checks backend CLI availability
- checks runtime environment

### `vibe screenshot`

```bash
vibe screenshot
vibe screenshot --output /tmp/screen.png
vibe screenshot --json
```

- captures the local desktop as a PNG file
- saves to `~/.vibe_remote/screenshots/` by default
- prints the saved file path by default
- prints a machine-readable payload with `--json`
- stays at the host CLI layer; it does not expose in-chat commands, bot buttons, or agent prompt injection

### `vibe version`

```bash
vibe version
```

- prints the installed package version

### `vibe check-update`

```bash
vibe check-update
```

- checks PyPI for a newer version

### `vibe upgrade`

```bash
vibe upgrade
```

- upgrades Vibe Remote using the selected upgrade plan
- usually recommends `vibe restart --delay-seconds 60` after success

## 5.2 `vibe task`

`vibe task` manages persisted scheduled tasks.

### Supported subcommands

| Subcommand | Purpose |
| --- | --- |
| `vibe task add` | Create a task |
| `vibe task update` | Update a task |
| `vibe task list` | List tasks |
| `vibe task ls` | Hidden alias of `list` |
| `vibe task show <task_id>` | Show one task |
| `vibe task pause <task_id>` | Pause a task |
| `vibe task resume <task_id>` | Resume a task |
| `vibe task run <task_id>` | Run immediately |
| `vibe task remove <task_id>` | Delete a task |
| `vibe task rm <task_id>` | Hidden alias of `remove` |

### `vibe task add`

```bash
vibe task add --session-key <key> (--cron <expr> | --at <timestamp>) (--prompt <text> | --prompt-file <file>) [options]
```

Important options:

- `--name`
- `--session-key` required
- `--post-to {thread,channel}`
- `--deliver-key`
- `--cron`
- `--at`
- `--prompt`
- `--prompt-file`
- `--timezone`

### `vibe task update`

```bash
vibe task update <task_id> [options]
```

Important options:

- `--name`
- `--clear-name`
- `--session-key`
- `--post-to {thread,channel}`
- `--deliver-key`
- `--reset-delivery`
- `--cron`
- `--at`
- `--prompt`
- `--prompt-file`
- `--timezone`

### `vibe task list`

```bash
vibe task list [--all] [--brief]
```

### `vibe task show`

```bash
vibe task show <task_id>
```

### `vibe task pause`

```bash
vibe task pause <task_id>
```

### `vibe task resume`

```bash
vibe task resume <task_id>
```

### `vibe task run`

```bash
vibe task run <task_id>
```

### `vibe task remove`

```bash
vibe task remove <task_id>
```

## 5.3 `vibe hook send`

Queue one asynchronous turn without creating a stored task definition.

### Syntax

```bash
vibe hook send --session-key <key> (--prompt <text> | --prompt-file <file>) [options]
```

Important options:

- `--session-key` required
- `--post-to {thread,channel}`
- `--deliver-key`
- `--prompt`
- `--prompt-file`

## 6. Recommended Mental Model

Use the right command family for the job:

- Want to control a conversation from chat:
  - use `/start`, `/resume`, `/setcwd`, `/setup`, `/stop`
- Want to bootstrap or recover DM access:
  - use `/bind <code>` in DM
- Want to control the local daemon or troubleshoot installation:
  - use `vibe`, `vibe status`, `vibe doctor`, `vibe upgrade`
- Want asynchronous automation:
  - use `vibe task ...` or `vibe hook send ...`

## 7. Quick Examples

### In chat

```text
@Vibe Remote /start
@Vibe Remote /cwd
@Vibe Remote /setcwd ~/projects/backend
@Vibe Remote /setup
@Vibe Remote /setup codex
@Vibe Remote /setup code 123456
@Vibe Remote /stop
```

### On the host machine

```bash
vibe
vibe status
vibe doctor
vibe task list --brief
vibe hook send --session-key 'slack::channel::C123' --prompt 'Share the latest build summary.'
```
