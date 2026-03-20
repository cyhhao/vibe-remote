# Use Vibe Remote Skill Plan

## Background

- We want a reusable skill at `skills/use-vibe-remote/` that teaches agents how to maintain a local Vibe Remote installation.
- The skill should be usable as a default skill for agents that operate inside or alongside Vibe Remote.
- The highest-value use case is translating natural-language requests such as "enable this Slack channel and route it to Codex with GPT-5.4 high reasoning" into safe, accurate config edits.

## Goal

- Document the current Vibe Remote runtime file model and config-editing workflow.
- Cover both global defaults (`config.json`) and per-channel/per-user overrides (`settings.json`).
- Explain operational commands, validation, restart flow, and troubleshooting.
- Explain how host backends such as OpenCode, Claude Code, and Codex should be configured when a requested change belongs to the backend rather than Vibe Remote itself.

## Solution

- Add `skills/use-vibe-remote/SKILL.md` with agent-oriented instructions.
- Keep the skill self-contained: include the runtime path map, config schema overview, precedence rules, backend capability matrix, operational guardrails, and concrete request-to-edit recipes.
- Anchor OpenCode/Codex/Claude backend notes to official docs where practical.
- Call out implementation caveats where the repo schema exposes a field that is not the main runtime source of truth.

## Todo

- [x] Inspect Vibe Remote config/state/runtime source files and existing docs.
- [x] Create a task worktree from `origin/master`.
- [x] Draft this plan document.
- [x] Author `skills/use-vibe-remote/SKILL.md`.
- [x] Validate the skill with `askill validate`.
- [x] Run a reviewer pass and address findings.
