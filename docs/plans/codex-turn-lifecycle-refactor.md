# Codex Turn Lifecycle Refactor

> Status: Completed

## Background

`modules/agents/codex/agent.py`, `modules/agents/codex/event_handler.py`, and `modules/agents/codex/session.py` currently split Codex turn lifecycle state across several structures:

- thread/session state in `CodexSessionManager`
- active request tracking in `CodexAgent`
- turn-to-request routing in `CodexAgent`
- pending assistant content in `CodexEventHandler`
- terminal error state in `CodexEventHandler`

This has led to repeated race-condition fixes around follow-up messages, interrupts, stale turn notifications, and duplicated or missing terminal outcomes.

## Goal

Introduce a single Codex turn lifecycle layer that:

1. owns turn-scoped request routing and per-turn state,
2. makes notification delivery rules explicit instead of scattered across handlers,
3. keeps thread/session persistence logic separate from in-memory turn lifecycle,
4. and reflects the current product preference for stale turns:
   - stale or interrupted turn errors should be logged, not shown to the user,
   - visible message types should still flow through the existing dispatcher rules,
   - and current-turn behavior should remain unchanged.

## Solution

1. Add a dedicated in-memory turn registry for Codex turn lifecycle state.
2. Move active-turn ownership, originating request mapping, pending assistant buffers, and terminal error bookkeeping into that registry.
3. Reduce `CodexSessionManager` back to persisted/session-level concerns (thread IDs and settings scope).
4. Update `CodexAgent` notification routing to resolve turn-scoped notifications from the registry first.
5. Update `CodexEventHandler` to use registry-backed state and centralized delivery policy helpers rather than ad-hoc dictionaries.
6. Keep stale/interrupted turn terminal errors as logs only; do not emit them into the conversation.
7. Add focused tests for routing, lifecycle cleanup, and stale-turn policy.

## Todo

- [x] Inspect the current Codex routing and lifecycle code paths.
- [x] Define the target product behavior for stale turns.
- [x] Implement a dedicated turn lifecycle registry.
- [x] Refactor agent/event handler/session manager to use the registry.
- [x] Add regression tests and run focused validation.
