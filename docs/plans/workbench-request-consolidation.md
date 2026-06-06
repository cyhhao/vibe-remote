# Workbench request consolidation

## Background

The prior performance work reduced the hottest chat and inbox paths:

- `/api/sessions/<id>/bootstrap` folds the Chat first-screen reads into one request.
- `/api/config` is cached longer on the client.
- `/api/inbox` has query-specific SQLite indexes.

The remaining avoidable cost is request fan-out around secondary Workbench
surfaces: the project tree reconnect path and the Harness page. These hurt most
over Cloudflare Tunnel because each small request pays extra round-trip cost.

## Goals

- Keep first-screen payloads bounded.
- Collapse initial Harness reads into one request.
- Rebuild already-loaded project session windows with fewer requests after SSE
  reconnects.
- Add lightweight API timing headers/logs so future slow-path reports can be
  separated into network, handler, DB, and payload-size questions.

## Non-goals

- No new DB migration unless query-plan evidence shows it is needed.
- No change to paging contracts or visible UI behavior.
- No restart of the local Vibe Remote service for verification.

## Plan

1. Add a Workbench projects bootstrap endpoint that returns projects plus
   optional per-project session pages for requested expanded projects.
2. Add a Harness bootstrap endpoint that returns counts and the current tab's
   first page in one payload.
3. Update the Workbench projects provider to use the bootstrap endpoint for
   project refresh/reconnect recovery, while preserving 8-row first-page loads.
4. Update the Harness page to use bootstrap for refreshes, then keep dedicated
   endpoints for mutations, pagination, and run details.
5. Add request timing response headers and slow-request logs for `/api/*`
   routes.

## Validation

- Focused FastAPI tests for new bootstrap routes.
- Existing Harness route tests.
- Frontend type/build check.
- Ruff on changed Python files.
