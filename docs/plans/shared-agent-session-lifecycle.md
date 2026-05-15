# Shared Agent Session Lifecycle

## Background

Vibe Remote exposes a public `ses...` agent-session id to AI backends so they
can create tasks, watches, and hooks targeting the current conversation. That
id is Vibe-owned and should not depend on whether a backend has already created
its native session.

Today each backend handles this sequence locally. OpenCode usually creates its
native session before prompt injection, while Claude Code and Codex can build
their first prompt before the native session id exists. That leaves
`Current session id` unavailable on first turn for some backends.

## Goal

Make the Vibe-owned agent-session lifecycle shared:

1. Ensure a stable Vibe agent-session row before prompt injection.
2. Put the `ses...` id into `MessageContext.platform_specific`.
3. Bind the backend native session id into the same row once it exists.
4. Keep backend-specific code responsible only for native session creation and
   resume behavior.

## Plan

- Add shared storage/facade APIs for `ensure_agent_session_id` and
  `bind_agent_session`.
- Add a shared `BaseAgent` helper that writes the resolved `ses...` id into the
  request context.
- Use the helper from Codex, Claude Code, and OpenCode before their prompt
  injection points and after native session creation.
- Cover first-turn prompt injection for all three backend paths.
