---
name: use-vibe-remote
slug: use-vibe-remote
description: Safely inspect and modify local Vibe Remote configuration, routing, runtime settings, watches, scheduled tasks, and operational state.
version: 0.2.2
---

# Use Vibe Remote

Use this skill when the user asks you to configure, repair, explain, or operate a local Vibe Remote installation.

Typical requests include:

- enable a Slack, Discord, Telegram, Lark/Feishu, or WeChat scope
- route one channel or DM user to OpenCode, Claude, or Codex
- set a working directory for a channel or DM user
- choose a backend model, subagent, or reasoning level
- show or hide intermediate message types
- create, inspect, pause, resume, or remove a managed background watch with `vibe watch`
- create, inspect, run, pause, resume, or remove a scheduled task with `vibe task`
- queue a one-shot asynchronous hook with `vibe hook send`
- inspect logs, run doctor, check service status, or explain where Vibe Remote stores state
- decide whether a requested change belongs in Vibe Remote config or in the host backend's own config

Follow this skill as an operations playbook for agents, not as end-user marketing copy.

## Core Rules

1. Prefer the Web UI API for Vibe Remote configuration changes. Do not hand-edit config files for routine work.
2. Read current API state before mutating. Merge the user's requested change into the current payload.
3. Preserve unrelated scopes, platforms, users, and secrets.
4. Treat secrets as opaque. Do not print, invent, rotate, or overwrite tokens unless the user explicitly provides replacements.
5. Use the smallest viable API call and verify by reading back the API response.
6. For `POST /settings`, preserve every existing channel for that platform; the endpoint replaces the platform's channel map.
7. For `POST /api/users`, merge each edited user with its current user payload first; missing user fields are not a patch.
8. Do not hand-edit `sessions.json` unless the user explicitly asks for low-level recovery work.
9. Do not restart the service by default. Use `POST /doctor`, `GET /status`, and read-back checks first.
10. Only start, stop, restart, or reload Vibe Remote when the user explicitly asks or when a change cannot take effect otherwise; explain why before doing it.
11. Tell the user whether the change is global or scope-specific.

## API First Workflow

Use this order when changing Vibe Remote configuration:

1. Determine the Web UI base URL.
   - Default is `http://127.0.0.1:5123`.
   - If the user has a custom UI host or port, use that exact origin.
   - Check liveness with `GET /health` or `GET /status`.
2. Decide whether the request belongs in:
   - `POST /config` for global defaults, platform credentials, runtime config, agent defaults, UI config, or global display toggles
   - `POST /settings` for channel-level routing, working directory, visibility, enablement, and mention policy
   - `/api/users` and `/api/bind-codes` for DM user binding and user-scope settings
   - host backend config instead of Vibe Remote when the request is OpenCode, Claude Code, or Codex native behavior
3. Fetch the current state from the matching GET endpoint.
4. Merge the requested change in memory.
5. Send the mutating request through the Web UI API with CSRF protection.
6. Read back the changed resource and verify the effective payload.
7. Run `POST /doctor` only when the change affects runtime health, platform credentials, or backend availability.
8. Report the changed scope or global keys and whether a restart was avoided or still required.

## Calling the Web UI API

Mutating API calls require:

- same-origin `Origin` or `Referer` header
- CSRF cookie named `vibe_csrf_token`
- matching `X-Vibe-CSRF-Token` header

Use this local curl pattern:

```bash
BASE="http://127.0.0.1:5123"
COOKIE_JAR="$(mktemp)"
CSRF="$(
  curl -fsS -c "$COOKIE_JAR" "$BASE/api/csrf-token" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])'
)"

curl -fsS -b "$COOKIE_JAR" -c "$COOKIE_JAR" \
  -H "Origin: $BASE" \
  -H "X-Vibe-CSRF-Token: $CSRF" \
  -H "Content-Type: application/json" \
  -X POST "$BASE/doctor" \
  --data '{}'
```

For `DELETE`, use the same cookie jar, `Origin`, and CSRF header.

Do not log full request bodies when they contain tokens or secrets.

### Reusable local API helper

For multi-step maintenance, use the bundled helper at `scripts/vibe_api.py` instead of hand-writing curl commands. The helper handles CSRF, same-origin headers, cookies, JSON encoding, and readable error output.

Resolve paths relative to this skill directory. If the skill is installed at `skills/use-vibe-remote`, run:

Usage examples:

```bash
export VIBE_UI_BASE="http://127.0.0.1:5123"

python3 skills/use-vibe-remote/scripts/vibe_api.py GET /health
python3 skills/use-vibe-remote/scripts/vibe_api.py GET '/settings?platform=slack'
python3 skills/use-vibe-remote/scripts/vibe_api.py POST /doctor '{}'
python3 skills/use-vibe-remote/scripts/vibe_api.py POST /config '{"show_duration":true}'
python3 skills/use-vibe-remote/scripts/vibe_api.py DELETE '/api/users/U123?platform=slack'
```

Payload can be passed as inline JSON, as `@payload.json`, or as `-` to read JSON from stdin.

For scope updates, still fetch and merge first:

```bash
API_HELPER="skills/use-vibe-remote/scripts/vibe_api.py"

python3 "$API_HELPER" GET '/settings?platform=slack' > /tmp/slack_settings.json
python3 - <<'PY'
import json
from pathlib import Path

settings = json.loads(Path("/tmp/slack_settings.json").read_text())
channels = settings.get("channels") or {}
channels["C123"] = {
    **channels.get("C123", {}),
    "enabled": True,
    "show_message_types": channels.get("C123", {}).get("show_message_types") or ["assistant"],
    "custom_cwd": channels.get("C123", {}).get("custom_cwd"),
    "require_mention": channels.get("C123", {}).get("require_mention"),
    "routing": {
        **(channels.get("C123", {}).get("routing") or {}),
        "agent_backend": "codex",
        "codex_model": "gpt-5.4",
        "codex_reasoning_effort": "high",
    },
}
Path("/tmp/slack_payload.json").write_text(json.dumps({"platform": "slack", "channels": channels}))
PY
python3 "$API_HELPER" POST /settings @/tmp/slack_payload.json
python3 "$API_HELPER" GET '/settings?platform=slack'
```

## Runtime Layout

Vibe Remote stores runtime data under `~/.vibe_remote/` by default. If `VIBE_REMOTE_HOME` is set, use that directory instead.

Important paths:

- `~/.vibe_remote/config/config.json`: global config persisted by `POST /config`
- `~/.vibe_remote/state/settings.json`: scope settings persisted by `/settings`, `/api/users`, and `/api/bind-codes`
- `~/.vibe_remote/state/scheduled_tasks.json`: persisted scheduled tasks created by `vibe task`
- `~/.vibe_remote/state/watches.json`: persisted managed watches
- `~/.vibe_remote/state/task_requests/`: queued task-run and hook-send requests plus completion receipts
- `~/.vibe_remote/state/user_preferences.md`: shared long-term preference file
- `~/.vibe_remote/state/sessions.json`: runtime session state; do not edit during normal config work
- `~/.vibe_remote/logs/vibe_remote.log`: main application log
- `~/.vibe_remote/runtime/status.json`: runtime status file
- `~/.vibe_remote/runtime/doctor.json`: latest doctor result
- `~/.vibe_remote/runtime/watch_runtime.json`: live watch runtime state, including active PIDs
- `~/.vibe_remote/attachments/`: attachment staging area

Only use direct file editing as a recovery fallback when the Web UI API is unavailable or the user explicitly asks for low-level repair. If you must edit files directly, back up the file first, validate JSON, and explain why the API path was not usable.

## API Endpoint Reference

### Health and inspection

- `GET /health`
  - returns `{"status":"ok"}` when the Web UI server is reachable
- `GET /status`
  - returns runtime status, running state, PID metadata, and last action
- `GET /doctor`
  - reads the latest persisted doctor result
- `POST /doctor`
  - runs doctor immediately and returns the result
- `POST /logs`
  - payload: `{"lines": 500, "source": "service"}`
  - `source` can be `service` or another source listed in the response; use `all` for aggregated logs
- `GET /version`
  - returns current version and update metadata

### Global config

- `GET /config`
  - returns the current V2 config payload
- `POST /config`
  - accepts a partial object, deep-merges it with current config, validates it through `V2Config.from_payload`, then persists it
  - use for platform credentials, enabled platforms, primary platform, runtime defaults, agent defaults, UI config, update policy, and global toggles

Important config payload shape:

```json
{
  "platform": "slack",
  "platforms": {
    "enabled": ["slack", "discord", "telegram", "lark", "wechat"],
    "primary": "slack"
  },
  "mode": "self_host",
  "version": "v2",
  "slack": {
    "bot_token": "xoxb-...",
    "app_token": "xapp-...",
    "signing_secret": "...",
    "team_id": "T...",
    "team_name": "...",
    "app_id": "A...",
    "require_mention": false
  },
  "discord": {
    "bot_token": "...",
    "application_id": "...",
    "guild_allowlist": [],
    "guild_denylist": [],
    "require_mention": false
  },
  "telegram": {
    "bot_token": "123:abc",
    "require_mention": true,
    "forum_auto_topic": true,
    "use_webhook": false,
    "webhook_url": null,
    "webhook_secret_token": null,
    "allowed_chat_ids": null,
    "allowed_user_ids": null
  },
  "lark": {
    "app_id": "...",
    "app_secret": "...",
    "require_mention": false,
    "domain": "feishu"
  },
  "wechat": {
    "bot_token": "...",
    "base_url": "https://ilinkai.weixin.qq.com",
    "cdn_base_url": "https://novac2c.cdn.weixin.qq.com/c2c",
    "proxy_url": null,
    "require_mention": false
  },
  "runtime": {
    "default_cwd": "/path/to/workdir",
    "log_level": "INFO"
  },
  "agents": {
    "default_backend": "opencode",
    "opencode": {
      "enabled": true,
      "cli_path": "opencode",
      "default_agent": null,
      "default_model": null,
      "default_reasoning_effort": null,
      "error_retry_limit": 1
    },
    "claude": {
      "enabled": true,
      "cli_path": "claude",
      "default_model": null,
      "idle_timeout_seconds": 600
    },
    "codex": {
      "enabled": true,
      "cli_path": "codex",
      "default_model": null,
      "idle_timeout_seconds": 600
    }
  },
  "ack_mode": "typing",
  "language": "en",
  "show_duration": false,
  "include_user_info": true,
  "reply_enhancements": true
}
```

When switching the active platform, update `platforms.primary` and make sure `platforms.enabled` contains the new primary. Keep the legacy `platform` field aligned for readability, but `platforms.primary` is the real multi-platform source of truth.

Secret-bearing config fields that you should not print:

- `slack.bot_token`
- `slack.app_token`
- `slack.signing_secret`
- `discord.bot_token`
- `telegram.bot_token`
- `telegram.webhook_secret_token`
- `lark.app_id` (treat as a sensitive identifier)
- `lark.app_secret`
- `wechat.bot_token`
- `gateway.workspace_token`
- `gateway.client_secret`

### Channel settings

- `GET /settings?platform=<platform>`
  - returns channel settings, user settings, and bind codes for one platform
- `POST /settings`
  - payload: `{"platform": "<platform>", "channels": {...}}`
  - validates message visibility and routing, normalizes Claude reasoning, persists the full channel map for that platform

Important: `POST /settings` replaces the entire `channels` map for the selected platform. To change one channel:

1. `GET /settings?platform=<platform>`
2. copy `response.channels`
3. merge or add one channel entry
4. `POST /settings` with the full merged `channels` object
5. `GET /settings?platform=<platform>` again and verify

Channel entry shape:

```json
{
  "enabled": true,
  "show_message_types": ["assistant"],
  "custom_cwd": "/path/to/repo",
  "require_mention": null,
  "routing": {
    "agent_backend": "codex",
    "opencode_agent": null,
    "opencode_model": null,
    "opencode_reasoning_effort": null,
    "claude_agent": null,
    "claude_model": null,
    "claude_reasoning_effort": null,
    "codex_agent": "reviewer",
    "codex_model": "gpt-5.4",
    "codex_reasoning_effort": "high"
  }
}
```

Field meanings:

- `enabled`: whether this channel is allowed to use Vibe Remote
- `show_message_types`: visible intermediate messages; allowed values are `system`, `assistant`, `toolcall`
- `custom_cwd`: scope-level working directory override; empty string or `null` means use global default
- `require_mention`: `null` inherits the platform default, `true` requires mention, `false` disables mention gating for that channel
- `routing.agent_backend`: `opencode`, `claude`, `codex`, or `null` to inherit default
- `routing.<backend>_agent`: backend-specific subagent
- `routing.<backend>_model`: backend-specific model
- `routing.<backend>_reasoning_effort`: backend-specific reasoning effort

### DM users and bind codes

- `GET /api/users?platform=<platform>`
  - returns bound DM users for one platform
- `POST /api/users`
  - payload: `{"platform": "<platform>", "users": {...}}`
  - merges included users into existing users and preserves each existing user's `dm_chat_id`
- `POST /api/users/<user_id>/admin`
  - payload: `{"platform": "<platform>", "is_admin": true}`
- `DELETE /api/users/<user_id>?platform=<platform>`
  - removes a bound user; this is the reliable way to revoke DM access
- `GET /api/bind-codes`
  - returns all bind codes
- `POST /api/bind-codes`
  - payload: `{"type": "one_time"}` or `{"type": "expiring", "expires_at": "2026-04-18"}`
- `DELETE /api/bind-codes/<code>`
  - deactivates a bind code
- `GET /api/setup/first-bind-code`
  - returns an existing valid setup bind code or creates a new one-time code

Important: user updates are not field patches. Before changing a user's routing, cwd, visibility, or enabled flag, read the current user object and send the merged full user entry.

User entry shape:

```json
{
  "display_name": "Alice",
  "is_admin": false,
  "bound_at": "2026-03-20T12:34:56+00:00",
  "enabled": true,
  "show_message_types": ["assistant"],
  "custom_cwd": "/path/to/repo",
  "routing": {
    "agent_backend": "claude",
    "opencode_agent": null,
    "opencode_model": null,
    "opencode_reasoning_effort": null,
    "claude_agent": "reviewer",
    "claude_model": "claude-sonnet-4-6",
    "claude_reasoning_effort": "high",
    "codex_agent": null,
    "codex_model": null,
    "codex_reasoning_effort": null
  }
}
```

DM caveat: current DM authorization checks whether the user is bound, not whether `enabled` is true. If the user wants to revoke DM access, use `DELETE /api/users/<user_id>?platform=<platform>` instead of only setting `enabled` to false.

### Platform discovery and validation

- `GET /slack/manifest`
  - returns Slack app manifest JSON for setup
- `POST /slack/auth_test`
  - payload: `{"bot_token": "xoxb-..."}`
- `POST /slack/channels`
  - payload: `{"bot_token": "xoxb-...", "browse_all": false}`
- `POST /discord/auth_test`
  - payload: `{"bot_token": "..."}`
- `POST /discord/guilds`
  - payload: `{"bot_token": "..."}`
- `POST /discord/channels`
  - payload: `{"bot_token": "...", "guild_id": "..."}`
- `POST /telegram/auth_test`
  - payload: `{"bot_token": "123:abc"}`
- `POST /telegram/chats`
  - payload: `{"include_private": false}`
- `POST /lark/auth_test`
  - payload: `{"app_id": "...", "app_secret": "...", "domain": "feishu"}`
- `POST /lark/chats`
  - payload: `{"app_id": "...", "app_secret": "...", "domain": "feishu"}`
- `POST /lark/temp_ws/start`
  - payload: `{"app_id": "...", "app_secret": "...", "domain": "feishu"}`
- `POST /lark/temp_ws/stop`
  - payload: `{}`
- `POST /wechat/qr_login/start`
  - payload: `{"base_url": "https://ilinkai.weixin.qq.com"}` or `{}`
- `POST /wechat/qr_login/poll`
  - payload: `{"session_key": "..."}`

WeChat QR login is special: when login is confirmed and a token is returned, the API auto-binds the WeChat user and schedules an internal service restart so the new token can take effect. Do not add an extra restart unless the user asks.

### Backend and local helper endpoints

- `GET /cli/detect?binary=<name-or-path>`
  - detects a CLI binary path
- `POST /agent/<name>/install`
  - `name` must be `opencode`, `claude`, or `codex`
- `POST /opencode/options`
  - payload: `{"cwd": "/path/to/repo"}`
  - returns OpenCode model, agent, and reasoning option data for that cwd
- `POST /opencode/setup-permission`
  - intentionally writes OpenCode native config to set `permission` to `allow`
- `GET /claude/agents?cwd=/path/to/repo`
- `GET /codex/agents?cwd=/path/to/repo`
- `GET /claude/models`
- `GET /codex/models`
- `POST /browse`
  - payload: `{"path": "~", "show_hidden": false}`

### Control endpoints

- `POST /control`
  - payload: `{"action": "start"}`, `{"action": "stop"}`, or `{"action": "restart"}`
- `POST /ui/reload`
  - payload: `{"host": "127.0.0.1", "port": 5123}`

Avoid these for routine configuration. `POST /control` starts, stops, or restarts the service. `POST /ui/reload` restarts only the Web UI server to apply host or port changes. Use them only with explicit user intent or a concrete need.

## Scope and Precedence Rules

### Backend selection

Backend resolution priority is:

1. scope-level routing override from `/settings` or `/api/users`
2. platform router fallback
3. global default backend from `/config`

If the user names a specific channel or DM and wants a specific backend, use the scope API, not global `/config`.

### Working directory

Working directory resolution is:

1. `custom_cwd` on the target channel or user scope
2. `runtime.default_cwd` from `/config`

### Message visibility

`show_message_types` is scope-local. Preserve existing values unless the user wants an explicit replacement.

If a user asks for "vault messages", "internal messages", or "tool execution messages", map that request to `show_message_types`. Current Vibe Remote does not expose a separate `vault` field.

### Mention policy

`require_mention` works like this:

- `null`: inherit platform default from `/config`
- `true`: require mention in that channel
- `false`: do not require mention in that channel

## Recipes

### Route one Slack channel to Codex

Goal:

- enable Slack channel `C123`
- route it to Codex
- use Codex subagent `reviewer`
- use model `gpt-5.4`
- set reasoning `high`

API flow:

1. `GET /settings?platform=slack`
2. merge `channels.C123`
3. `POST /settings` with all Slack channels
4. read back `GET /settings?platform=slack`

Merged channel entry:

```json
{
  "enabled": true,
  "show_message_types": ["assistant"],
  "custom_cwd": null,
  "require_mention": null,
  "routing": {
    "agent_backend": "codex",
    "opencode_agent": null,
    "opencode_model": null,
    "opencode_reasoning_effort": null,
    "claude_agent": null,
    "claude_model": null,
    "claude_reasoning_effort": null,
    "codex_agent": "reviewer",
    "codex_model": "gpt-5.4",
    "codex_reasoning_effort": "high"
  }
}
```

### Route one channel to OpenCode with a subagent

Use `/settings` and set:

- `routing.agent_backend = "opencode"`
- `routing.opencode_agent = "<agent>"`
- `routing.opencode_model = "<model>"` if requested
- `routing.opencode_reasoning_effort = "<effort>"` if requested

If the user wants OpenCode-native defaults, providers, MCP servers, skills, plugins, or API credentials, use OpenCode config instead of Vibe Remote scope routing.

### Route one scope to Claude with model and reasoning

Use `/settings` for a channel or `/api/users` for a DM user and set:

- `routing.agent_backend = "claude"`
- `routing.claude_agent = "<agent>"` if requested
- `routing.claude_model = "<model>"`
- `routing.claude_reasoning_effort = "<effort>"`

The API normalizes Claude reasoning for incompatible model combinations; verify by reading back the saved payload.

### Change the global default working directory

Use `POST /config`:

```json
{
  "runtime": {
    "default_cwd": "/path/to/workdir"
  }
}
```

Do not overwrite scope-level `custom_cwd` entries.

### Show tool execution messages in one channel

Use `/settings` and add `toolcall` to the target channel's `show_message_types`.

Preserve existing `system` and `assistant` values unless the user asked for a full replacement.

### Switch primary platform

Use `POST /config` and keep `platforms.enabled` complete:

```json
{
  "platform": "discord",
  "platforms": {
    "enabled": ["slack", "discord"],
    "primary": "discord"
  }
}
```

Make sure the target platform config section exists and validates. Do not delete old platform config unless the user explicitly asks.

### Generate a DM bind code

Use `POST /api/bind-codes`:

```json
{
  "type": "one_time"
}
```

For an expiring code:

```json
{
  "type": "expiring",
  "expires_at": "2026-04-18"
}
```

Do not expose bind codes unless the user explicitly asks for them.

## Scheduled Tasks

Use scheduled tasks when the user wants Vibe Remote to inject a prompt later or repeatedly into an existing chat scope.

Preferred CLI shape:

- recurring: `vibe task add --session-key '<key>' --cron '<expr>' --prompt '...'`
- one-off: `vibe task add --session-key '<key>' --at '<ISO-8601>' --prompt '...'`
- immediate rerun: `vibe task run <id>`
- one-shot async hook: `vibe hook send --session-key '<key>' --prompt '...'`

Delivery controls:

- `session_key` controls which session Vibe Remote continues using
- when you want to keep the current session, keep using the current `session_key`
- when you do not want to keep the current thread session and instead want to start or reuse the higher-level session, switch to the higher-level key
- example: `slack::channel::C123::thread::171717.123` keeps the current thread session, while `slack::channel::C123` creates or reuses the channel-scoped session
- use `--post-to channel` when the task or hook should keep the session chosen by `session_key` but publish to the parent channel
- use `--deliver-key '<key>'` only when delivery must go to a different explicit target than `session_key`
- do not combine `--post-to` and `--deliver-key` in the same command
- `vibe task add` stores the text from `--prompt` or `--prompt-file` and injects it each time the task runs
- `vibe hook send` queues the text from `--prompt` or `--prompt-file` once without storing a task
- `vibe watch add` uses `--prefix` as follow-up instruction text; on a successful cycle Vibe Remote prepends it before waiter stdout, joined with a blank line when both exist

Session key format:

- channel scope: `<platform>::channel::<channel_id>`
- DM scope: `<platform>::user::<user_id>`
- exact thread target: append `::thread::<thread_id>` when the user explicitly wants the task bound to that thread

Operational guidance:

- use `vibe task list` before editing or deleting an existing task
- if this is the first time using `vibe task add`, `vibe watch add`, or `vibe hook send`, read the matching `--help` output first
- use `vibe task update <id>` to keep the same task ID while changing name, schedule, prompt, or target
- use `vibe task list --brief` for scheduling-focused summaries
- `vibe task list` hides completed one-shot tasks by default; use `vibe task list --all` when you need full history
- use `vibe task show <id>` to inspect stored fields and derived scheduling state such as `next_run_at`
- treat `warnings` from task or hook commands as delivery-risk hints to fix proactively

## Backend Capability Matrix

Current Vibe Remote routing support is:

| Backend | Channel/User backend select | Subagent | Model | Reasoning |
| --- | --- | --- | --- | --- |
| OpenCode | yes | yes | yes | yes |
| Claude | yes | yes | yes | yes |
| Codex | yes | yes | yes | yes |

Behavior notes:

- OpenCode subagents are selected through `routing.opencode_agent` or through prefix routing such as `reviewer: ...`.
- Claude subagents are selected through `routing.claude_agent` or prefix routing.
- Codex subagents are selected through `routing.codex_agent` or prefix routing.
- Claude reasoning is selected through `routing.claude_reasoning_effort`; common values are `low`, `medium`, and `high`, and some models also allow `max`.
- If a Claude reasoning value is invalid for the chosen model, the API normalizes or drops that override and falls back to the backend default.

## Subagent and Prefix Routing

If the user asks for subagents, remember:

- OpenCode, Claude, and Codex support prefix-triggered subagent selection like `planner: draft a migration plan`
- when a subagent definition provides its own default model or reasoning setting, that subagent-level value overrides the channel default
- Claude subagents are discovered from markdown files under:
  - `~/.claude/agents/`
  - project `.claude/agents/`
- Codex custom agents are discovered from TOML files under:
  - `~/.codex/agents/`
  - project `.codex/agents/`
- OpenCode subagent and model defaults come from the OpenCode runtime/config rather than only from Vibe Remote's own config

## Host Backend Guidance

When the request belongs to the host backend, do not force it into Vibe Remote config.

### OpenCode

Use OpenCode-native config when the user wants to change:

- personal default model
- global reasoning behavior
- provider and API keys
- MCP servers
- skills, plugins, tools, or project-local OpenCode behavior

Important locations:

- `~/.config/opencode/opencode.json`: global OpenCode config
- project `opencode.json`: project-level OpenCode config file
- `.opencode/`: project-local OpenCode config directory
- `~/.config/opencode/agents/`: global OpenCode agents
- `.opencode/agents/`: project-local OpenCode agents
- `~/.config/opencode/skills/`: global OpenCode skills
- `.opencode/skills/`: project-local OpenCode skills

Relevant docs:

- config: `https://opencode.ai/docs/config/`
- skills: `https://opencode.ai/docs/skills`
- plugins: `https://opencode.ai/docs/plugins/`
- MCP servers: `https://opencode.ai/docs/mcp-servers/`

Inside Vibe Remote, OpenCode scope routing controls backend choice, subagent, model, and reasoning effort. Use `POST /opencode/setup-permission` only for the specific permission helper.

### Claude Code

Use Claude-native config when the user wants to change:

- Claude subagent definitions
- Claude skills
- CLAUDE instructions and project rules

Important locations:

- `~/.claude/agents/`: global Claude subagents
- `.claude/agents/`: project subagents
- `~/.claude/skills/`: global Claude skills
- `.claude/skills/`: project skills

Relevant docs:

- subagents: `https://docs.anthropic.com/en/docs/claude-code/sub-agents`

Inside Vibe Remote, Claude scope routing controls backend choice, model, subagent, and reasoning effort.

### Codex

Use Codex-native config when the user wants to change:

- personal default model
- global reasoning defaults
- MCP servers, approvals, or sandbox policy
- Codex CLI profiles and behavior outside Vibe Remote

Important locations:

- `~/.codex/config.toml`: global Codex config
- `.codex/config.toml`: project-local Codex config
- `~/.codex/agents/`: global Codex custom agents
- `.codex/agents/`: project-local Codex custom agents

Relevant docs:

- config basics: `https://developers.openai.com/codex/config-basic/`
- config reference: `https://developers.openai.com/codex/config-reference/`
- CLI overview: `https://developers.openai.com/codex/cli`
- subagents: `https://developers.openai.com/codex/subagents`

Inside Vibe Remote, Codex scope routing controls backend choice, subagent, model, and reasoning effort.

## Troubleshooting

Start with evidence:

1. `GET /status`
2. `POST /doctor`
3. `POST /logs` with a small line count and focused source
4. read back `/config`, `/settings`, or `/api/users` for the affected scope

Common cases:

- config does not apply: verify the API read-back first; only restart if the changed field is startup-only
- backend missing: confirm backend is enabled, CLI path is executable, and `/cli/detect` finds it
- channel does not respond: verify `/settings?platform=<platform>` contains the channel and `enabled` is true
- wrong repository/cwd: inspect `custom_cwd` and `runtime.default_cwd`
- DM access denied: inspect `/api/users?platform=<platform>` and bind-code state
- startup failure: use `GET /status`, `POST /doctor`, then inspect logs

Do not use `vibe restart`, `POST /control {"action":"restart"}`, or `POST /ui/reload` as a first response to config problems.

## Direct File Recovery Fallback

Use file edits only when:

- the Web UI API is down and cannot be recovered through normal service start
- the config file is malformed and prevents the API from starting
- the user explicitly asks for low-level file repair

Recovery rules:

1. Identify `VIBE_REMOTE_HOME` or default to `~/.vibe_remote`.
2. Back up the target file before writing.
3. Preserve unrelated keys and secrets.
4. Validate JSON after editing.
5. Start or restart only when needed to bring the API back.
6. After recovery, return to the API workflow for further changes.

## Safety Boundaries

Always follow these constraints:

- never delete unrelated platform scopes
- never blank out tokens or secrets as part of an unrelated config task
- never claim a backend feature exists if current Vibe Remote behavior does not support it
- never manually rewrite `sessions.json` for routine routing changes
- never expose bind codes unless the user explicitly asks
- always say when a requested change actually belongs in OpenCode, Claude Code, or Codex config instead of Vibe Remote

## Escalation

If the user still cannot solve a problem after API read-back checks, doctor, and log inspection, point them to the Vibe Remote repository:

- repo: `https://github.com/cyhhao/vibe-remote`

Use that link when:

- the behavior looks like a real bug rather than a local misconfiguration
- the user is asking for a feature Vibe Remote does not support yet
- backend integration behavior appears inconsistent with the documented configuration surface

If the user wants to contribute back, suggest opening an issue or a pull request in that repository.

## Response Pattern

When you complete a Vibe Remote maintenance task, report back with:

1. which API endpoint changed the state
2. whether the change is global or scope-specific
3. which keys changed
4. the read-back or doctor evidence
5. whether a restart was avoided, deferred, or still required and why
