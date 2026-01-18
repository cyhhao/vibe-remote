# Codex Agent Setup

Vibe Remote can route individual Slack channels to Codex instead of Claude Code. (OpenCode is also supported and recommended; see README for quick enablement.) This guide walks through enabling Codex end-to-end.

## 1. Install and authenticate Codex CLI

```bash
brew install codex     # or follow https://github.com/openai/codex
codex --help           # verify installation
codex                  # sign in when prompted
```

Codex CLI must be available on the PATH of the host running Vibe Remote. The bot automatically runs Codex with `--json` and `--dangerously-bypass-approvals-and-sandbox`, so make sure you trust the workspace it operates in.

## 2. Configure environment variables

In `~/.vibe_remote/config/config.json`:

```json
{
  "agents": {
    "codex": {
      "enabled": true,
      "cli_path": "codex",
      "default_model": "gpt-5-codex"
    }
  }
}
```

No additional flag is required to bypass approvals—the bot always adds `--dangerously-bypass-approvals-and-sandbox`.

## 3. Route channels to Codex

Configure routing (two options):

1) Slack: use the **Agent Settings** dialog to switch a channel to Codex.

2) Legacy (file-based): create `agent_routes.yaml` (repo root) or set `AGENT_ROUTE_FILE`.

Example contents:

```yaml
default: claude
slack:
  default: claude
  overrides:
    C01ABCD2EFG: codex          # Slack channel ID
```

Each Slack channel ID (starts with `C`) gets its own agent. Routes fall back to the platform default, then to the global `default`.

`agent_routes.yaml` is gitignored so you can maintain different mappings per environment. Alternatively, point `AGENT_ROUTE_FILE` to a custom path.

## 4. Restart the bot and test

```bash
vibe
```

In a routed Slack channel run `@VibeRemote status` or any question—you should see the bot react with 👀 (default) or an acknowledgement like `📨 Codex received, processing...` (when `ACK_MODE=message`), followed by Codex’s reply. If the CLI is missing, the bot will reply with “Agent `codex` is not configured”.

## 5. Troubleshooting

- **“Agent `codex` is not configured”**: ensure `codex` CLI is installed and on PATH; check `CODEX_ENABLED`.
- **`codex exec` errors**: inspect the Slack stderr snippet or tail the latest `~/.vibe_remote/logs/vibe_remote.log`.
- **Routing not applied**: confirm the channel ID matches Slack’s `C...` value (copy from channel details), and restart the bot after editing `agent_routes.yaml`.
