# Vibe Remote CLI Reference

## Quick Start

```bash
vibe              # Start Vibe Remote (opens web UI)
vibe status       # Check service status
vibe stop         # Stop all services
```

## Commands

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
| **Main Service** | Handles Slack communication, routes messages to agents |
| **OpenCode Server** | Backend server for OpenCode agent (if enabled) |

The key difference between commands:

| Command | Main Service | OpenCode Server |
|---------|--------------|-----------------|
| `vibe` | Restart | **Preserved** |
| `vibe stop` | Stop | **Terminated** |

### Why This Matters

When you run `vibe` to restart:
- Any **running OpenCode tasks continue uninterrupted**
- The new Vibe Remote instance "adopts" the existing OpenCode server
- Session state is preserved

When you run `vibe stop`:
- **Everything stops cleanly**
- OpenCode server is terminated
- Use this before updating OpenCode or its configuration

## Common Scenarios

### Daily Restart

Just want to restart Vibe Remote without interrupting work:

```bash
vibe
```

### Update OpenCode Configuration

After editing `~/.config/opencode/opencode.json`:

```bash
vibe stop && vibe
```

### Update OpenCode Binary

After installing a new version of OpenCode:

```bash
vibe stop && vibe
```

### Update Vibe Remote

```bash
vibe upgrade
# Then restart:
vibe stop && vibe
```

### Troubleshooting

If something seems stuck:

```bash
# Check status
vibe status

# Run diagnostics
vibe doctor

# Full restart (stops everything including OpenCode)
vibe stop && vibe
```

## Web UI Controls

The web UI (`http://127.0.0.1:5123`) provides the same controls:

| Button | Equivalent CLI | OpenCode Behavior |
|--------|---------------|-------------------|
| **Start** | `vibe` | Preserved |
| **Restart** | `vibe` | Preserved |
| **Stop** | `vibe stop` | Terminated |

## File Locations

| Path | Description |
|------|-------------|
| `~/.vibe_remote/config/config.json` | Main configuration |
| `~/.vibe_remote/state/settings.json` | Channel routing settings |
| `~/.vibe_remote/logs/vibe_remote.log` | Application logs |
| `~/.vibe_remote/logs/opencode_server.json` | OpenCode server PID file |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `OPENCODE_PORT` | Override OpenCode server port (default: 4096) |

## See Also

- [Slack Setup Guide](SLACK_SETUP.md)
- [Codex Setup Guide](CODEX_SETUP.md)
