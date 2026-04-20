# Processing Indicator Lifecycle

## Problem

Processing indicators were split across layers:

- `MessageHandler` started ack messages, reactions, and typing indicators.
- `AgentRequest` carried several loosely related mutable fields.
- Each agent backend decided when to delete or clear those fields.
- OpenCode persisted active poll state separately for restart recovery.

That made cleanup depend on every backend remembering the same platform-specific
details. WeChat exposed the flaw because clearing typing requires the original
conversation context token, and restored OpenCode polls did not own that state.

## Direction

`core.processing_indicator.ProcessingIndicatorService` is the lifecycle owner.

- Start indicators through one service.
- Select indicator strategy from platform registry capabilities, not platform
  string checks.
- Store a single handle on `AgentRequest`.
- Keep legacy request fields in sync for compatibility while callers migrate.
- Finish and ack-message deletion go through the same service for OpenCode,
  Codex, Claude, and handler-level errors.
- Persist a serializable handle snapshot for restored OpenCode polls.

## Platform Capability Source

The registry owns ACK/typing differences:

- which platforms can use typing, reaction, or message indicators
- whether typing requires an explicit clear operation
- whether typing support is best-effort rather than a stable platform path
- whether message ACKs can be deleted
- whether a platform has a preferred or forced indicator mode

This keeps WeChat's explicit cancel requirement, Lark's reaction preference, and
Telegram's sendChatAction/message-delete support out of backend-specific code.
Slack is marked as typing-capable but best-effort because it relies on legacy
RTM availability.

## Compatibility Boundary

This PR keeps the public request fields as a compatibility layer. They mirror the
handle, but lifecycle ownership and platform policy live in
`ProcessingIndicatorService` plus `config.platform_registry`.
