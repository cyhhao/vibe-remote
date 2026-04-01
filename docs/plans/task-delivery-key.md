# Task Delivery Target Split

## Background

`vibe task add` and `vibe hook send` currently use one `session_key` for both:

- session continuity
- outbound delivery target

That breaks cases where the agent should keep a thread-backed session for memory, but publish each result to the parent channel.

## Goal

Keep command usage simple while allowing delivery to be decoupled from session reuse:

- keep `--session-key` as the memory/session target
- add `--post-to thread|channel` for the common delivery choice
- add `--deliver-key <session-key>` for advanced delivery overrides

`--deliver-key` should be documented in CLI help and skill docs, but not in injected prompt guidance.

## Solution

1. Extend stored task/request payloads with delivery fields.
2. Resolve two targets at runtime:
   - session target from `session_key`
   - delivery target from `deliver_key` or `post_to`
3. Preserve the original thread-backed base session when running the turn.
4. Apply delivery override only in dispatcher send contexts.
5. When a scheduled result is delivered as a top-level message, alias that sent message back to the same native session so follow-up replies continue the session.

## Todo

- Add `post_to` / `deliver_key` to task models, queue requests, CLI, and JSON output.
- Implement delivery override context building in `ScheduledTaskService`.
- Update dispatcher/session finalization to use delivery override without breaking session reuse.
- Add focused tests for:
  - CLI parsing and persistence
  - delivery override context building
  - scheduled finalization alias behavior for top-level deliveries
- Update help text and skill docs.
