# SubAgent Prefix Routing

## Background

Users want to invoke OpenCode or ClaudeCode subagents by prefixing a message with `XXX:` (or `XXX：`). The system should detect the prefix, resolve it against the current channel's bound agent, and route the request to the matching subagent. When a subagent is used, its default `model` and `reasoning_effort` must take precedence over channel overrides.

## Goal

- Support prefix-triggered subagent routing with case-insensitive matching.
- Only search within the channel's bound agent (OpenCode or ClaudeCode).
- If the prefix matches a subagent name and the message body is non-empty:
  - Explicitly select that subagent.
  - Use the subagent's default `model` and `reasoning_effort`.
  - Add a `:robot_face:` reaction to the user's original message.
- If no match or empty body, fall back to existing channel logic.
- ClaudeCode subagent lookup reads installed agent definitions from the local Claude Code plugin directories.

## Proposed Solution

- Parse user messages by trimming leading whitespace, then match `^([^\s:：]+)\s*[:：]\s*(.*)$`.
- Normalize the prefix to lower-case for matching against subagent names.
- Resolve the current channel agent backend, then look up subagent names for that backend only.
- On match, pass the subagent identifier explicitly and override model/reasoning fields with subagent defaults.
- On match, add a `:robot_face:` reaction to the user's original message and remove the ack reaction when applicable.

## To-do

1. Add prefix parsing and match logic at the message entry point.
2. Implement subagent lookup for OpenCode and ClaudeCode backends, sourcing ClaudeCode agents from local agent definitions.
3. Apply subagent overrides (agent + default model/reasoning) when matched.
4. Add a `:robot_face:` reaction to the original user message on match.
5. Add tests covering:
   - Case-insensitive matching.
   - Chinese/English colon support.
   - Empty-body fallback behavior.
   - Non-match fallback behavior.
6. Update user documentation with usage instructions for the prefix routing feature.
