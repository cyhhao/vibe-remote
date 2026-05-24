# Disable OpenCode Question Tool

## Background

OpenCode exposes interactive user questions through its built-in `question`
tool. Vibe Remote currently adapts that tool into IM buttons/modals and keeps a
poll loop waiting for answers. That bridge is expensive to maintain across IM
platforms and is not needed for the preferred chat workflow: if the agent needs
clarification, it should ask in normal assistant text and let the user reply in
the next turn.

## Goal

Disable OpenCode `question` tool calls for all Vibe Remote-started OpenCode
turns and remove the OpenCode-specific question polling / answer submission
path.

## Scope

- Always send the per-prompt OpenCode `tools={"question": False}` override.
- Remove OpenCode-specific `opencode_question:*` callback handling from the
  agent coordinator.
- Remove question-tool branches from normal and restored OpenCode poll loops.
- Remove OpenCode `/question` list/reply client methods and tests.
- Delete the OpenCode-specific question handler/tests.

## Non-Goals

- Do not remove the shared question UI primitives yet; Claude's disabled
  AskUserQuestion code still imports them and platform modal methods are shared
  surface area.
- Do not alter Claude Code `AskUserQuestion` policy in this change.
- Do not change user-facing IM routing or general tool-call rendering.

## Safety Notes

- Existing restored polls that still contain an unfinished `question` tool call
  should no longer try to re-open UI. They will be treated as a regular tool
  call in the poll stream, and new OpenCode turns should not produce new
  `question` calls because the tool is denied at prompt start.
- Error retry prompts must use the same `tools={"question": False}` override so
  retries do not re-enable the question tool.
