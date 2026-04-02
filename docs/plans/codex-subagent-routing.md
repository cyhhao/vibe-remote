# Codex Subagent Routing Plan

## Background

Vibe Remote already supports subagent routing for OpenCode and Claude, but Codex routing still only exposes backend, model, and reasoning effort. Codex CLI now supports custom agents via `~/.codex/agents/` and project `.codex/agents/`, so the Vibe Remote capability gap is stale.

## Goal

Add end-to-end Codex subagent support that matches the existing OpenCode/Claude experience:

- persisted `routing.codex_agent`
- prefix-triggered subagent selection such as `reviewer: ...`
- model/reasoning fallback from Codex custom agent definitions
- agent selection in Slack, Discord, Feishu, Web UI, and API
- remove stale documentation that claims Codex subagents are unsupported

## Approach

1. Extend routing data models and serialization to include `codex_agent`.
2. Add Codex custom-agent discovery and parsing from global/project agent directories.
3. Reuse the existing generic `subagent_*` request fields so message handling stays backend-agnostic.
4. Update the Codex backend to load selected custom-agent instructions and defaults when starting a thread.
5. Add tests for parsing, routing payloads, UI/API listing, and Codex turn-start overrides.

## Validation

- targeted pytest for subagent parsing, routing modal selection, UI API, and Codex agent payloads
- `npm run build` in `ui/`
