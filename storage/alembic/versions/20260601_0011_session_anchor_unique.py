"""pin sessions to (scope, anchor): strip cwd from anchors, dedup, unique index

Realises the new session model where a thread is one session pinned to one
backend, keyed by ``(scope_id, session_anchor)`` (see
docs/plans/session-anchor-backend-pin.md):

1. OpenCode used to fold the working directory into ``session_anchor``
   (``base:/path``) to rotate a session per cwd. The cwd now lives only in the
   ``workdir`` column (it is a per-request OpenCode param), so strip the
   ``:<workdir>`` suffix back to the bare base anchor.
2. With anchors normalised, an IM thread that toggled backends (or had a cwd
   suffix) can have several rows for one ``(scope_id, session_anchor)``. Keep the
   most-recently-active row per group and delete the rest — a thread is now ONE
   session.
3. Add a UNIQUE index on ``(scope_id, session_anchor)`` so the invariant holds
   going forward.

NOTE: steps 1-2 rewrite/remove rows and are NOT reversible; ``downgrade`` only
drops the unique index.

Revision ID: 20260601_0011
Revises: 20260531_0010
Create Date: 2026-06-01
"""

from __future__ import annotations

from alembic import op

revision = "20260601_0011"
down_revision = "20260531_0010"
branch_labels = None
depends_on = None

_UNIQUE_INDEX = "uq_agent_sessions_scope_anchor"


def _tables(bind) -> set[str]:
    return {row[0] for row in bind.exec_driver_sql("select name from sqlite_master where type = 'table'")}


def upgrade() -> None:
    bind = op.get_bind()
    if "agent_sessions" not in _tables(bind):
        return

    # 1. Strip the ``:<workdir>`` suffix OpenCode appended to the anchor. Match by
    #    exact suffix (no LIKE wildcards, since paths contain ``_`` etc.): the
    #    anchor must end with ``':' || workdir`` and have a non-empty base before
    #    it. claude/codex anchors (no path) and avibe anchors (bare session id)
    #    never match, so only OpenCode composite anchors are rewritten. Loop: a
    #    prior bug could append the suffix more than once (``base:/p:/p``); each
    #    pass removes one trailing ``:<workdir>`` and shortens the value, so this
    #    terminates (the bound is just a safety cap).
    for _ in range(20):
        result = bind.exec_driver_sql(
            """
            update agent_sessions
            set session_anchor = substr(session_anchor, 1, length(session_anchor) - length(workdir) - 1)
            where workdir is not null
              and workdir != ''
              and length(session_anchor) > length(workdir) + 1
              and substr(session_anchor, length(session_anchor) - length(workdir)) = ':' || workdir
            """
        )
        if not result.rowcount:
            break

    # 2. Dedup: keep the most-recently-active row per (scope_id, session_anchor),
    #    delete the rest. A thread is now ONE session regardless of how many
    #    backends it cycled through.
    bind.exec_driver_sql(
        """
        delete from agent_sessions
        where id in (
            select id from (
                select id,
                       row_number() over (
                           partition by scope_id, session_anchor
                           order by coalesce(last_active_at, '') desc, id desc
                       ) as rn
                from agent_sessions
            )
            where rn > 1
        )
        """
    )

    # 3. Enforce uniqueness going forward. (NULL scope_id rows stay distinct under
    #    SQLite's NULL semantics — they're orphaned/legacy and not load-bearing.)
    bind.exec_driver_sql(
        f"create unique index if not exists {_UNIQUE_INDEX} "
        "on agent_sessions (scope_id, session_anchor)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "agent_sessions" in _tables(bind):
        bind.exec_driver_sql(f"drop index if exists {_UNIQUE_INDEX}")
