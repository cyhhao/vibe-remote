# Vibe Remote CLI Reference

## Quick Start

```bash
vibe              # Start Vibe Remote (opens web UI)
vibe status       # Check service status
vibe restart      # Restart all services (use --delay-seconds when agent-triggered)
vibe remote       # Guided Vibe Cloud remote-access setup
vibe screenshot   # Capture a local desktop screenshot
vibe stop         # Stop all services
```

## Commands

## Remote Web UI Access

By default, the Web UI binds to `127.0.0.1:5123` on the machine where Vibe Remote is running.

If you want to open the Web UI from another device, or you installed Vibe Remote on a remote server, use the guided remote-access setup:

```bash
vibe remote
```

The command walks you through signing in at `https://avibe.bot`, creating a remote-access bot, claiming your personal domain, pasting the one-time pairing key, and starting the secure tunnel.


### `vibe`

Start or restart Vibe Remote. Opens the web UI in your browser.

```bash
vibe
```

**Behavior:**
- Restarts the main service if already running
- Opens the setup wizard at `http://127.0.0.1:5123`
- **Preserves OpenCode server** — Running tasks are not interrupted

### `vibe stop`

Fully stop all Vibe Remote services.

```bash
vibe stop
```

**Behavior:**
- Stops the main service
- Stops the web UI server
- **Terminates OpenCode server** — Use this when you need to restart OpenCode

### `vibe restart`

Restart Vibe Remote (main service + Web UI). The OpenCode server is terminated as part of the restart.

```bash
vibe restart
vibe restart --delay-seconds 60
```

**Behavior:**
- Stops the main service and Web UI, then re-starts them
- Terminates the OpenCode server
- With `--delay-seconds N`, schedules the restart `N` seconds in the future so an active conversation can receive its reply before the restart lands. Prefer this form when an agent is triggering the restart from inside Slack, Discord, Telegram, Lark/Feishu, or WeChat.

### `vibe status`

Display current service status.

```bash
vibe status
```

**Output:**
```json
{
  "state": "running",
  "running": true,
  "pid": 12345
}
```

### `vibe doctor`

Run diagnostic checks on your configuration.

```bash
vibe doctor
```

**Checks:**
- Configuration file validity
- Slack token configuration
- Agent CLI availability (Claude Code, OpenCode, Codex)
- Runtime environment

### `vibe remote`

Start the guided Vibe Cloud remote-access setup.

```bash
vibe remote
```

**Flow:**
- The CLI explains what remote access does before asking for anything.
- Open `https://avibe.bot`, sign up or log in, create a new remote-access bot, claim your personal domain, and copy the one-time pairing key.
- Press Enter in the CLI, paste the pairing key, and Vibe Remote saves the config and starts the managed tunnel automatically.
- On success, the CLI prints your remote URL and the next commands for checking or stopping the tunnel. When you open the URL, sign in with the same avibe.bot account.

If you already have a pairing key and want to skip the guided copy, use:

```bash
vibe remote pair vrp_abc123
```

Useful follow-up commands:

```bash
vibe remote status
vibe remote start
vibe remote stop
```

Use `--json` on these subcommands for machine-readable output.

### `vibe screenshot`

Capture the local desktop as a PNG file.

```bash
vibe screenshot
vibe screenshot --output /tmp/screen.png
vibe screenshot --json
```

**Behavior:**
- Saves to `~/.vibe_remote/screenshots/` by default
- Prints the saved file path, or a JSON payload with `--json`
- Stays at the CLI layer only; it does not add IM commands, bot buttons, or agent prompt injection

### `vibe task`

Create, inspect, update, run, pause, resume, or remove scheduled tasks.

```bash
vibe task add --session-key 'slack::channel::C123' --cron '0 * * * *' --prompt 'Share the hourly summary.'
vibe task list --brief
vibe task update <task-id> --cron '*/30 * * * *'
vibe task run <task-id>
vibe task remove <task-id>
```

Use `vibe task add --help` and `vibe task update --help` for the full command surface, including:

- `--session-key` for session continuity
- `--post-to channel` to publish into the parent channel while keeping thread context
- `--deliver-key` for an explicit delivery target
- `--cron` and `--at` scheduling
- `--name`, `--timezone`, and prompt file support

### `vibe hook send`

Queue one asynchronous turn without storing a scheduled task definition.

```bash
vibe hook send --session-key 'slack::channel::C123' --prompt 'The export finished. Share the summary.'
vibe hook send --session-key 'slack::channel::C123::thread::171717.123' --post-to channel --prompt 'Share the benchmark result in the channel.'
```

Use this when you want one delayed or background follow-up without persisting a task in `scheduled_tasks.json`.

### `vibe watch`

Create, inspect, pause, resume, or remove a managed background watch. A watch
runs a long-lived waiter command (for example a build or a status poll) and,
when the command finishes successfully, prepends `--prefix` to the captured
stdout and delivers it through the chosen session as a follow-up message.

```bash
vibe watch add \
  --session-key 'slack::channel::C123' \
  --prefix 'Test run finished. Summarize the failures and propose next steps.' \
  -- ./scripts/run_tests.sh

# Alternative: pass the command through a shell with --shell
vibe watch add \
  --session-key 'slack::channel::C123' \
  --prefix 'Build done. Summarize.' \
  --shell 'make build && ./scripts/post_build.sh'

vibe watch list --brief
vibe watch show <watch-id>
vibe watch pause <watch-id>
vibe watch resume <watch-id>
vibe watch remove <watch-id>
```

The waiter command is passed positionally after `--` (or as a single shell
string via `--shell`). Use `vibe watch add --help` for the full surface,
including `--timeout` (per-cycle timeout in seconds), `--lifetime-timeout`
(total wall-clock limit), `--forever`, `--retry-exit-code`, `--retry-delay`,
`--post-to channel`, `--deliver-key`, and `--name`. Watches share
`--session-key`, `--post-to`, and `--deliver-key` semantics with `vibe task`
and `vibe hook send`. Prefer `vibe watch` over ad-hoc `nohup` jobs when the
user wants a managed background task with a guaranteed follow-up message.

### `vibe version`

Show the installed version.

```bash
vibe version
```

### `vibe check-update`

Check if a newer version is available.

```bash
vibe check-update
```

### `vibe upgrade`

Upgrade to the latest version.

```bash
vibe upgrade
```

## Service Lifecycle

### Understanding "Restart" vs "Stop"

Vibe Remote manages two types of processes:

| Process | Description |
|---------|-------------|
| **Main Service** | Handles chat platform communication and routes messages to agents |
| **OpenCode Server** | Backend server for OpenCode agent (if enabled) |

The key difference between commands:

| Command | Main Service | OpenCode Server |
|---------|--------------|-----------------|
| `vibe restart` | Restart | **Terminated** |
| `vibe stop` | Stop | **Terminated** |

### Why This Matters

When you run `vibe restart`:
- The main service restarts cleanly
- The UI restarts too
- The OpenCode server is terminated as part of the restart

When you run `vibe stop`:
- **Everything stops cleanly**
- OpenCode server is terminated
- Use this before updating OpenCode or its configuration

## Common Scenarios

### Daily Restart

If an agent is triggering the restart from an active conversation, prefer the delayed form for a better user experience:

```bash
vibe restart --delay-seconds 60
```

Just want to restart Vibe Remote immediately:

```bash
vibe restart
```

### Update OpenCode Configuration

After editing `~/.config/opencode/opencode.json`:

```bash
vibe restart --delay-seconds 60
```

### Update OpenCode Binary

After installing a new version of OpenCode:

```bash
vibe restart --delay-seconds 60
```

### Update Vibe Remote

```bash
vibe upgrade
# Then restart:
vibe restart --delay-seconds 60
```

### Troubleshooting

If something seems stuck:

```bash
# Check status
vibe status

# Run diagnostics
vibe doctor

# Prefer delayed restart when triggered by an agent
vibe restart --delay-seconds 60
```

## Web UI Controls

The web UI (`http://127.0.0.1:5123`) provides the same controls:

| Button | Equivalent CLI | OpenCode Behavior |
|--------|---------------|-------------------|
| **Start** | `vibe` | Starts on demand |
| **Restart** | `vibe restart` | Terminated |
| **Stop** | `vibe stop` | Terminated |

## File Locations

| Path | Description |
|------|-------------|
| `~/.vibe_remote/config/config.json` | Main configuration |
| `~/.vibe_remote/state/vibe.sqlite` | Primary persistent store (settings, users, sessions, discovered chats) |
| `~/.vibe_remote/state/settings.json` | Legacy JSON snapshot of channel routing settings |
| `~/.vibe_remote/state/scheduled_tasks.json` | Persisted scheduled task definitions |
| `~/.vibe_remote/state/watches.json` | Persisted managed watch definitions |
| `~/.vibe_remote/state/task_requests/` | Queued task run and hook execution requests |
| `~/.vibe_remote/state/user_preferences.md` | Shared long-term user preference notes |
| `~/.vibe_remote/state/backups/` | Automatic state backups taken before migrations |
| `~/.vibe_remote/runtime/remote-access-cloudflared.pid` | cloudflared tunnel PID for Vibe Cloud remote access |
| `~/.vibe_remote/screenshots/` | Default output directory for `vibe screenshot` |
| `~/.vibe_remote/logs/vibe_remote.log` | Application logs |
| `~/.vibe_remote/logs/opencode_server.json` | OpenCode server PID file |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENCODE_PORT` | Override OpenCode server port (default: 4096) |

## See Also

- [Slack Setup Guide](SLACK_SETUP.md)
- [Telegram Setup Guide](TELEGRAM_SETUP.md)
- [Codex Setup Guide](CODEX_SETUP.md)
