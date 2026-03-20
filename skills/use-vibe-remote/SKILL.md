---
name: use-vibe-remote
slug: use-vibe-remote
description: Safely inspect and modify local Vibe Remote configuration, routing, runtime settings, and operational state.
version: 0.1.0
---

# Use Vibe Remote

Use this skill when the user asks you to configure, repair, explain, or operate a local Vibe Remote installation.

Typical requests include:

- enable a Slack/Discord/Lark channel
- route one channel or DM user to OpenCode, Claude, or Codex
- set a working directory for a channel or DM
- choose a backend model, subagent, or reasoning level
- show or hide intermediate message types
- inspect logs, run doctor, restart services, or explain where Vibe Remote stores its state
- decide whether a requested change belongs in Vibe Remote config or in the host backend's own config

Follow this skill as an operations playbook for agents, not as end-user marketing copy.

## Core Rules

1. Read before editing. Inspect the current file first and preserve unrelated keys.
2. Make the smallest viable change. Do not rewrite the whole file unless necessary.
3. Treat secrets as opaque. Do not print, invent, rotate, or overwrite tokens unless the user explicitly provides replacements.
4. Back up `config.json` or `settings.json` before writing.
5. Validate JSON after editing.
6. Only touch the target platform scope in `settings.json`.
7. Do not hand-edit `sessions.json` unless the user explicitly asks for low-level recovery work.
8. Tell the user whether the change is global or scope-specific.
9. After config changes, recommend `vibe doctor` and usually `vibe stop && vibe`.

## Runtime Layout

Vibe Remote stores runtime data under `~/.vibe_remote/` by default. If `VIBE_REMOTE_HOME` is set, use that directory instead.

Important paths:

- `~/.vibe_remote/config/config.json`: global config
- `~/.vibe_remote/state/settings.json`: per-channel and per-user overrides
- `~/.vibe_remote/state/sessions.json`: runtime session state
- `~/.vibe_remote/logs/vibe_remote.log`: main application log
- `~/.vibe_remote/runtime/status.json`: runtime status file
- `~/.vibe_remote/runtime/doctor.json`: latest doctor result
- `~/.vibe_remote/attachments/`: attachment staging area

## Edit Workflow

When changing Vibe Remote config, use this order:

1. Determine the runtime home (`VIBE_REMOTE_HOME` or `~/.vibe_remote`).
2. Decide whether the request belongs in:
   - `config.json` for global defaults or platform/runtime config
   - `settings.json` for channel/user overrides
   - host backend config (`~/.config/opencode/opencode.json`, project `opencode.json` or `.opencode/`, `~/.claude/...`, `~/.codex/config.toml`) instead of Vibe Remote
3. Read the current target file.
4. Back it up, for example:

```bash
cp ~/.vibe_remote/config/config.json ~/.vibe_remote/config/config.json.bak.$(date +%s)
```

5. Apply a minimal edit.
6. Validate the file, for example:

```bash
VIBE_HOME="${VIBE_REMOTE_HOME:-$HOME/.vibe_remote}"
TARGET_FILE="$VIBE_HOME/state/settings.json"  # or the exact file you just edited
python3 -c "import json,sys; json.load(open(sys.argv[1]))" "$TARGET_FILE"
```

7. If the config change affects behavior, recommend:

```bash
vibe doctor
vibe stop && vibe
```

8. Summarize the exact keys changed.

## File Responsibilities

### `config.json`

This is the global config. It defines the active IM platform, runtime defaults, global backend defaults, UI settings, update policy, and global display toggles.

Current top-level structure:

```json
{
  "platform": "slack",
  "mode": "self_host",
  "version": "v2",
  "slack": {},
  "discord": null,
  "lark": null,
  "runtime": {},
  "agents": {},
  "gateway": null,
  "ui": {},
  "update": {},
  "ack_mode": "reaction",
  "show_duration": true,
  "include_user_info": true,
  "reply_enhancements": true,
  "language": "en"
}
```

Key responsibilities:

- `platform`: active IM transport; valid values are `slack`, `discord`, `lark`
- `mode`: `self_host` or `saas`
- `slack`, `discord`, `lark`: platform credentials and defaults
- `runtime.default_cwd`: global working directory fallback
- `runtime.log_level`: log verbosity
- `agents.default_backend`: default backend if no scope override applies
- `agents.opencode`, `agents.claude`, `agents.codex`: backend enablement and default model settings
- `ui`: local setup UI bind host/port
- `update`: auto-update behavior
- `ack_mode`: `reaction` or `message`
- `show_duration`, `include_user_info`, `reply_enhancements`, `language`: global UX toggles
- `gateway`: SaaS relay settings

Platform sections:

- `slack`: `bot_token`, `app_token`, `signing_secret`, `team_id`, `team_name`, `app_id`, `require_mention`
- `discord`: `bot_token`, `application_id`, `guild_allowlist`, `guild_denylist`, `require_mention`
- `lark`: `app_id`, `app_secret`, `require_mention`, `domain`

Secret-bearing fields that you should not print back unless the user explicitly asks:

- `slack.bot_token`
- `slack.app_token`
- `slack.signing_secret`
- `discord.bot_token`
- `lark.app_id` (treat as a sensitive identifier)
- `lark.app_secret`
- `gateway.workspace_token`
- `gateway.client_secret`

### `settings.json`

This is the main scope-level routing file. It stores channel overrides, DM-user overrides, and bind codes.

Current schema shape:

```json
{
  "schema_version": 3,
  "scopes": {
    "channel": {
      "slack": {},
      "discord": {},
      "lark": {}
    },
    "user": {
      "slack": {},
      "discord": {},
      "lark": {}
    }
  },
  "bind_codes": []
}
```

Channel scope entry:

```json
{
  "enabled": true,
  "show_message_types": ["assistant", "toolcall"],
  "custom_cwd": "/path/to/repo",
  "routing": {
    "agent_backend": "codex",
    "opencode_agent": null,
    "opencode_model": null,
    "opencode_reasoning_effort": null,
    "claude_agent": null,
    "claude_model": null,
    "codex_model": "gpt-5.4",
    "codex_reasoning_effort": "high"
  },
  "require_mention": null
}
```

User scope entry:

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
    "claude_agent": "reviewer",
    "claude_model": "sonnet"
  },
  "dm_chat_id": "D123456"
}
```

Meaning of important fields:

- `enabled`: for channel scopes, this is the enforceable on/off gate; for DM user scopes, do not treat it as the access-control source of truth
- `show_message_types`: which intermediate messages are visible; allowed values are `system`, `assistant`, `toolcall`
- `custom_cwd`: scope-level working directory override
- `routing`: backend choice and backend-specific overrides
- `require_mention`: channel-level override for mention gating; `null` means use platform global default
- `bind_codes`: DM authorization codes

Important DM caveat: current DM authorization checks whether the user is bound, not whether `scopes.user.<platform>.<user_id>.enabled` is `true`. If the user wants to revoke DM access, do not rely on flipping `enabled` to `false`; use the supported unbind/remove-user path instead.

If a user asks for "vault messages", "internal messages", or "tool execution messages", map that request to `show_message_types`. Current Vibe Remote does not expose a separate `vault` field.

### `sessions.json`

This file stores transient-but-persisted runtime state:

- session mappings
- active threads
- active OpenCode polls
- message dedup state
- last activity

Do not edit it during normal config work. Prefer restart, doctor, or log inspection first.

## Scope and Precedence Rules

Use these rules when deciding where to edit.

### Backend selection

Backend resolution priority is:

1. scope-level `settings.json` routing override
2. platform router fallback
3. global default backend

In practice, if the user names a specific channel or DM and wants a specific backend, edit `settings.json`, not `config.json`.

### Working directory

Working directory resolution is:

1. `custom_cwd` in the target channel/user scope
2. `runtime.default_cwd` in `config.json`

### Message visibility

`show_message_types` is scope-local. Preserve existing values unless the user wants an explicit replacement.

### Mention policy

`require_mention` works like this:

- `null`: inherit platform default from `config.json`
- `true`: require mention in that channel
- `false`: do not require mention in that channel

## Backend Capability Matrix

Current Vibe Remote routing support is:

| Backend | Channel/User backend select | Subagent | Model | Reasoning |
| --- | --- | --- | --- | --- |
| OpenCode | yes | yes | yes | yes |
| Claude | yes | yes | yes | not currently applied by Vibe Remote runtime |
| Codex | yes | no | yes | yes |

Behavior notes:

- OpenCode subagents are selected through `routing.opencode_agent` or through prefix routing such as `reviewer: ...`.
- Claude subagents are selected through `routing.claude_agent` or prefix routing.
- Codex subagents are not currently supported in Vibe Remote routing.
- Current Vibe Remote behavior does not apply channel-level Claude reasoning control. Do not promise that setting as an available per-scope feature.

## Subagent and Prefix Routing

If the user asks for subagents, remember:

- OpenCode and Claude support prefix-triggered subagent selection like `planner: draft a migration plan`
- when a subagent definition provides its own default model or reasoning setting, that subagent-level value overrides the channel default
- Claude subagents are discovered from markdown files under:
  - `~/.claude/agents/`
  - project `.claude/agents/`
- OpenCode subagent and model defaults come from the OpenCode runtime/config rather than only from Vibe Remote's own config

## Important Caveat: OpenCode Defaults

`config.json` defines these schema fields:

- `agents.opencode.default_agent`
- `agents.opencode.default_model`
- `agents.opencode.default_reasoning_effort`

However, current runtime behavior primarily resolves effective OpenCode defaults from:

- scope-level overrides in `settings.json`
- OpenCode's own config and agent definitions
- internal fallback logic, including a fallback default agent of `build`

So when the user wants a guaranteed OpenCode agent choice inside Vibe Remote, prefer `routing.opencode_agent` on the target channel or user scope.

Use OpenCode native config mainly for OpenCode-native concerns such as:

- default model and reasoning behavior
- providers and API credentials
- skills, plugins, MCP servers, tools, and project-local OpenCode behavior

When diagnosing OpenCode behavior, inspect both global config (`~/.config/opencode/opencode.json`) and any active project-level `opencode.json` or `.opencode/` overrides.

## How to Enable a New Channel

When the user asks to enable a new Slack/Discord/Lark channel:

1. Confirm the active platform or the requested platform.
2. Read `settings.json`.
3. Create or update `scopes.channel.<platform>.<channel_id>`.
4. Set `enabled` to `true`.
5. Add only the requested overrides.
6. Preserve other platforms and other channel IDs.

Example: enable a Slack channel and route it to Codex with GPT-5.4 high reasoning.

```json
{
  "scopes": {
    "channel": {
      "slack": {
        "C1234567890": {
          "enabled": true,
          "show_message_types": [],
          "custom_cwd": null,
          "routing": {
            "agent_backend": "codex",
            "opencode_agent": null,
            "opencode_model": null,
            "opencode_reasoning_effort": null,
            "claude_agent": null,
            "claude_model": null,
            "codex_model": "gpt-5.4",
            "codex_reasoning_effort": "high"
          },
          "require_mention": null
        }
      }
    }
  }
}
```

If the user also wants a scope-specific working directory, set `custom_cwd` and verify the directory exists.

## How to Enable or Adjust a DM User Scope

DM users are stored under `scopes.user.<platform>.<user_id>`.

Normal flow:

- generate a bind code via Web UI or API
- user sends `/bind <code>` in DM
- Vibe Remote creates the user scope entry

If you must adjust an existing DM user scope:

- read the existing user entry first
- preserve `display_name`, `is_admin`, `bound_at`, and `dm_chat_id`
- only change requested keys such as `custom_cwd`, `show_message_types`, or `routing`

If the user wants to revoke an existing DM binding, treat that as a bind-state change rather than a normal settings toggle. In the current implementation, removing the user entry is the reliable way to revoke bound DM access.

Avoid creating fake bind or admin state unless the user explicitly asks for manual recovery.

## Common Recipes

### Recipe 1: Route one Slack channel to Codex

User intent:

- enable Slack channel `C...`
- backend `codex`
- model `gpt-5.4`
- reasoning `high`

Action:

- edit `settings.json`
- target `scopes.channel.slack.<channel_id>`
- set `enabled: true`
- set `routing.agent_backend = "codex"`
- set `routing.codex_model = "gpt-5.4"`
- set `routing.codex_reasoning_effort = "high"`

### Recipe 2: Route one channel to OpenCode with a subagent

User intent:

- use OpenCode in one channel
- choose subagent `plan`
- choose model `anthropic/claude-sonnet-4-5`
- set reasoning `high`

Action:

- edit `settings.json`
- set `routing.agent_backend = "opencode"`
- set `routing.opencode_agent = "plan"`
- set `routing.opencode_model = "anthropic/claude-sonnet-4-5"`
- set `routing.opencode_reasoning_effort = "high"`

If the user instead wants to change OpenCode-native defaults such as model, reasoning, providers, or MCP behavior across projects, edit OpenCode config rather than Vibe Remote. If the user wants Vibe Remote to use a specific OpenCode agent in one scope, edit `routing.opencode_agent` in `settings.json`.

### Recipe 3: Change the global default working directory

User intent:

- use one default working directory unless a channel overrides it

Action:

- edit `config.json`
- set `runtime.default_cwd`
- do not overwrite scope-level `custom_cwd` entries in `settings.json`

### Recipe 4: Show tool execution messages in one channel

User intent:

- show tool or internal execution messages

Action:

- edit the target scope's `show_message_types`
- add `toolcall`
- preserve existing `system` and `assistant` values unless the user asked for a full replacement

### Recipe 5: Switch the whole installation from Slack to Discord

User intent:

- run Vibe Remote on Discord instead of Slack

Action:

- edit `config.json`
- set `platform = "discord"`
- ensure `discord` config is present and valid
- do not delete `slack` or `lark` settings unless the user asked
- recommend `vibe doctor` and restart

## Operations and Troubleshooting

Main commands:

- `vibe`: start or restart Vibe Remote; preserves the OpenCode server when possible
- `vibe status`: inspect runtime status
- `vibe stop`: stop Vibe Remote and the OpenCode server
- `vibe doctor`: validate config, CLI availability, and runtime health
- `vibe version`: print installed version
- `vibe check-update`: check for updates
- `vibe upgrade`: install latest version

Useful checks:

- config does not apply: run `vibe doctor`, then `vibe stop && vibe`
- backend missing: confirm the backend is enabled and the CLI path is executable
- channel does not respond: verify the right `settings.json` scope exists and `enabled` is `true`
- wrong repository/cwd: inspect `custom_cwd` and `runtime.default_cwd`
- startup failure: validate JSON and inspect `~/.vibe_remote/logs/vibe_remote.log`
- DM access denied: inspect bind-code state and user entry in `settings.json`

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

Remember: current Vibe Remote behavior supports Claude backend selection, model, and subagent routing, but not channel-level Claude reasoning control.

### Codex

Use Codex-native config when the user wants to change:

- personal default model
- global reasoning defaults
- MCP servers, approvals, or sandbox policy
- Codex CLI profiles and behavior outside Vibe Remote

Important locations:

- `~/.codex/config.toml`: global Codex config
- `.codex/config.toml`: project-local Codex config

Relevant docs:

- config basics: `https://developers.openai.com/codex/config-basic/`
- config reference: `https://developers.openai.com/codex/config-reference/`
- CLI overview: `https://developers.openai.com/codex/cli`

Inside Vibe Remote, Codex scope routing currently controls backend choice, model, and reasoning effort. It does not expose Codex subagent routing.

## Safety Boundaries

Always follow these constraints:

- never delete unrelated platform scopes from `settings.json`
- never blank out tokens or secrets as part of an unrelated config task
- never claim a backend feature exists if current Vibe Remote behavior does not actually support it
- never manually rewrite `sessions.json` for routine routing changes
- never expose bind codes unless the user explicitly asks
- always say when a requested change actually belongs in OpenCode, Claude Code, or Codex config instead of Vibe Remote

## Escalation

If the user still cannot solve a problem after normal config fixes, `vibe doctor`, restart, and log inspection, point them to the Vibe Remote repository:

- repo: `https://github.com/cyhhao/vibe-remote`

Use that link when:

- the behavior looks like a real bug rather than a local misconfiguration
- the user is asking for a feature Vibe Remote does not support yet
- backend integration behavior appears inconsistent with the documented configuration surface

If the user wants to contribute back, suggest opening an issue or a pull request in that repository.

## Response Pattern

When you complete a Vibe Remote maintenance task, report back with:

1. which file(s) changed
2. whether the change is global or scope-specific
3. which keys changed
4. any caveats, especially backend feature limitations
5. the recommended verification step, usually `vibe doctor` and/or restart
