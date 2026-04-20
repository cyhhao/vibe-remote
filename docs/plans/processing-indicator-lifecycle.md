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
- Store a single handle on `AgentRequest`.
- Keep legacy request fields in sync for compatibility while callers migrate.
- Finish and ack-message deletion go through the same service for OpenCode,
  Codex, Claude, and handler-level errors.
- Persist a serializable handle snapshot for restored OpenCode polls.

## Follow-Up Boundary

This PR keeps the public request fields as a compatibility layer. A later cleanup
can remove direct reads of `ack_reaction_message_id`, `typing_indicator_active`,
and related fields once all tests and helper code use the handle directly.
