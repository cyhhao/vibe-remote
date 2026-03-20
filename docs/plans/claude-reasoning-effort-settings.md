# Claude Reasoning Effort Settings

## Background

- Vibe Remote already exposes reasoning effort controls for OpenCode and Codex in Agent Settings and the Web UI.
- Claude backend routing currently stores `claude_agent` and `claude_model`, but not Claude reasoning effort.
- The current code still contains an outdated assumption that Claude Code does not support a reasoning-effort parameter.
- Latest Claude Code / Agent SDK now supports `effort` in the Python SDK and `--effort` in the CLI.

## Goal

- Add Claude reasoning effort control end-to-end using the current Claude SDK integration path.
- Keep UX aligned with existing OpenCode/Codex settings and reuse shared option-building logic where practical.

## Proposed Solution

1. Extend shared routing data structures with `claude_reasoning_effort`.
2. Add a shared Claude reasoning-option helper that returns allowed values per selected model.
3. Pass the selected Claude effort into `ClaudeAgentOptions(effort=...)`.
4. Expose Claude reasoning effort in Slack, Discord, Feishu Agent Settings.
5. Expose Claude reasoning effort in the Web UI channel/user settings.
6. Validate with targeted Python/UI checks and update the three-end regression environment.

## Todo

- [x] Extend routing/config/state models with Claude reasoning effort.
- [x] Add shared Claude reasoning option builder and backend wiring.
- [x] Add Claude reasoning selectors to Slack/Discord/Feishu Agent Settings.
- [x] Add Claude reasoning selectors to Web UI channel/user settings.
- [x] Validate changes and sync them to the three-end regression environment.
