# Chat Bootstrap API Plan

## Goal

Reduce Workbench Chat first-screen latency over remote links by replacing the current fan-out of session/messages/queue/draft/turn-state/config/agents requests with one chat bootstrap request for the ChatPage initial load.

## Scope

- Add a read-only `GET /api/sessions/<session_id>/bootstrap` endpoint.
- Return the same shapes ChatPage already consumes: session, recent visible messages, queue, draft, turn state, enabled agents, default agent name, and config.
- Keep reconnect/gap recovery endpoints separate; bootstrap is for initial session load only.
- Preserve turn-state timeout semantics: timeout is `in_flight: null`, not idle.

## Validation

- Backend route test for success and timeout shape.
- Frontend production build.
- Ruff on touched Python files.
