# Inbox Query Indexes Plan

## Goal

Speed up `GET /api/inbox`, which drives the Workbench inbox feed and sidebar
badges. The feed computes latest activity, latest terminal agent reply, latest
user send, and unread counts from the `messages` table.

## Evidence

`EXPLAIN QUERY PLAN` on a seeded SQLite store showed the inbox query reading
`messages` through the generic `(platform, session_id, created_at, id)` index
and building temporary B-trees for the window-function orderings.

## Scope

- Add inbox-specific SQLite indexes for the three ranked message scans:
  conversation activity, terminal agent replies, and user sends.
- Add the indexes to SQLAlchemy metadata, Alembic migration, and the head-schema
  repair helper used before stamping existing schemas.
- Keep query semantics unchanged.

## Validation

- Migration/index tests.
- Message service inbox tests.
- Ruff on touched Python files.
