"""pin sessions to (scope, anchor): strip cwd from anchors, dedup, unique index

Realises the new session model where a thread is one session pinned to one
backend, keyed by ``(scope_id, session_anchor)`` (see
docs/plans/session-anchor-backend-pin.md):

1. OpenCode used to fold the working directory into ``session_anchor``
   (``base:/path``) to rotate a session per cwd. The cwd now lives only in the
   ``workdir`` column (it is a per-request OpenCode param), so strip the
   ``:<cwd>`` suffix back to the bare base anchor. Only ABSOLUTE-path suffixes
   are stripped, so claude/codex ``base:<subagent>`` anchors are preserved.
2. With anchors normalised, an IM thread that toggled backends (or had a cwd
   suffix) can have several rows for one ``(scope_id, session_anchor)``. Reattach
   the loser rows' transcripts to the survivor, then keep the most-recently-active
   row per group and delete the rest — a thread is now ONE session.
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


# Window expression selecting, for every agent_sessions row, the id that survives
# dedup for its (scope_id, session_anchor) group: the most-recently-active row
# (``last_active_at`` desc, then ``id`` desc as a stable tiebreak). Reused by both
# the message-reattach and the delete below so they agree on the winner.
_DEDUP_ANCHORED = """
    select id,
           row_number() over (
               partition by scope_id, session_anchor
               order by coalesce(last_active_at, '') desc, id desc
           ) as rn,
           first_value(id) over (
               partition by scope_id, session_anchor
               order by coalesce(last_active_at, '') desc, id desc
           ) as keep_id
    from agent_sessions
"""


def upgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    if "agent_sessions" not in tables:
        return

    # 1. Strip the ``:<cwd>`` suffix OpenCode appended to the anchor. Match by exact
    #    suffix (no LIKE wildcards in the suffix itself, since paths contain ``_``
    #    etc.): the anchor must end with ``':' || workdir`` and have a non-empty
    #    base before it. The ``workdir like '/%'`` gate is the load-bearing guard —
    #    OpenCode cwds are always absolute (get_cwd -> os.path.abspath), so the
    #    suffix starts with ``/``; claude/codex SUBAGENT rows store the anchor as
    #    ``base:<name>`` (e.g. ``base:reviewer``) and backfill workdir to that bare
    #    name, which does NOT start with ``/`` and so is preserved here. Without the
    #    gate this would collapse a subagent's anchor to the main thread and the
    #    dedup below would delete the subagent's native binding (lost context).
    #    avibe anchors (bare session id, no ``:``) never match. Loop: a prior bug
    #    could append the suffix more than once (``base:/p:/p``); each pass removes
    #    one trailing ``:<cwd>`` and shortens the value, so this terminates (the
    #    bound is just a safety cap).
    for _ in range(20):
        result = bind.exec_driver_sql(
            """
            update agent_sessions
            set session_anchor = substr(session_anchor, 1, length(session_anchor) - length(workdir) - 1)
            where workdir is not null
              and workdir like '/%'
              and length(session_anchor) > length(workdir) + 1
              and substr(session_anchor, length(session_anchor) - length(workdir)) = ':' || workdir
            """
        )
        if not result.rowcount:
            break

    # 2. Reattach transcripts before dedup deletes the loser rows. messages.session_id
    #    is FK ``ondelete=SET NULL`` and alembic runs with ``PRAGMA foreign_keys=ON``,
    #    so deleting a duplicate session row would orphan its messages and drop them
    #    from ``/api/sessions/<kept>/messages``. Point every loser row's messages at
    #    the row that survives dedup for the same (scope, anchor) first, so the
    #    consolidated session keeps the full visible history.
    if "messages" in tables:
        bind.exec_driver_sql(
            f"""
            update messages
            set session_id = (
                select w.keep_id from ({_DEDUP_ANCHORED}) as w
                where w.id = messages.session_id
            )
            where messages.session_id in (
                select id from ({_DEDUP_ANCHORED}) where rn > 1
            )
            """
        )

    # 3. Dedup: keep the most-recently-active row per (scope_id, session_anchor),
    #    delete the rest. A thread is now ONE session regardless of how many
    #    backends it cycled through. (Messages were reattached above.)
    bind.exec_driver_sql(
        f"""
        delete from agent_sessions
        where id in (
            select id from ({_DEDUP_ANCHORED}) where rn > 1
        )
        """
    )

    # 4. Enforce uniqueness going forward. (NULL scope_id rows stay distinct under
    #    SQLite's NULL semantics — they're orphaned/legacy and not load-bearing.)
    bind.exec_driver_sql(
        f"create unique index if not exists {_UNIQUE_INDEX} "
        "on agent_sessions (scope_id, session_anchor)"
    )


def downgrade() -> None:
    bind = op.get_bind()
    if "agent_sessions" in _tables(bind):
        bind.exec_driver_sql(f"drop index if exists {_UNIQUE_INDEX}")
