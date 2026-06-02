"""Unit tests for the workbench chat-media proxy spine.

Covers ``storage.media_service`` (token mint + readback, derived content-type /
ext / size) and ``core.workbench_media.rewrite_agent_media`` (in-place file://
rewrite, image vs file kind, external URLs untouched). Uses an isolated temp
SQLite migrated to head, so it never touches real ``~/.vibe_remote`` state.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from core.workbench_media import rewrite_agent_media
from storage import media_service, settings_service
from storage.db import create_sqlite_engine
from storage.migrations import run_migrations
from storage.models import agent_sessions, media_objects


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_scope_and_session(conn) -> str:
    scope_id = settings_service.upsert_scope(
        conn,
        platform="avibe",
        scope_type="project",
        native_id="proj-1",
        now=_now(),
        supports_threads=False,
    )
    conn.execute(
        agent_sessions.insert().values(
            id="sess_x",
            scope_id=scope_id,
            agent_backend="claude",
            agent_variant="default",
            session_anchor="anchor",
            native_session_id="native",
            status="active",
            metadata_json="{}",
            created_at=_now(),
            updated_at=_now(),
        )
    )
    return scope_id


def test_register_and_get_by_token(tmp_path):
    db = tmp_path / "vibe.sqlite"
    run_migrations(db)
    engine = create_sqlite_engine(db)

    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\n")

    with engine.begin() as conn:
        scope_id = _seed_scope_and_session(conn)
        token = media_service.register(
            conn,
            scope_id=scope_id,
            session_id="sess_x",
            kind="image",
            source="agent_reply",
            local_path=str(shot),
            file_name="shot.png",
        )

    with engine.connect() as conn:
        row = media_service.get_by_token(conn, token)
        assert media_service.get_by_token(conn, "does-not-exist") is None

    assert row is not None
    assert row["kind"] == "image"
    assert row["source"] == "agent_reply"
    assert row["content_type"] == "image/png"
    assert row["file_ext"] == "png"
    assert row["size_bytes"] == 8
    assert row["local_path"] == str(shot)
    assert row["session_id"] == "sess_x"


def test_rewrite_in_place_image_file_and_external(tmp_path):
    db = tmp_path / "vibe.sqlite"
    run_migrations(db)
    engine = create_sqlite_engine(db)

    img = tmp_path / "a.png"
    img.write_bytes(b"x")
    doc = tmp_path / "r.pdf"
    doc.write_bytes(b"y")

    text = (
        f"See ![chart](file://{img}) and the [report](file://{doc}); "
        f"also [docs](https://example.com/x)."
    )

    with engine.begin() as conn:
        scope_id = _seed_scope_and_session(conn)
        out = rewrite_agent_media(conn, scope_id=scope_id, session_id="sess_x", text=text)

    # Both file:// links are rewritten in place to same-origin proxy URLs.
    assert "file://" not in out
    assert out.startswith("See ![chart](/api/sessions/sess_x/media/")
    assert "the [report](/api/sessions/sess_x/media/" in out
    # External URL is left untouched (no token, no rewrite).
    assert "[docs](https://example.com/x)" in out

    with engine.connect() as conn:
        rows = conn.execute(select(media_objects)).mappings().all()
    assert sorted(r["kind"] for r in rows) == ["file", "image"]
    assert all(r["source"] == "agent_reply" for r in rows)


def test_resolve_attachment_specs(tmp_path):
    db = tmp_path / "vibe.sqlite"
    run_migrations(db)
    engine = create_sqlite_engine(db)
    doc = tmp_path / "doc.pdf"
    doc.write_bytes(b"%PDF-1.4 hello")

    from core.workbench_media import resolve_attachment_specs

    with engine.begin() as conn:
        scope_id = _seed_scope_and_session(conn)
        token = media_service.register(
            conn,
            scope_id=scope_id,
            session_id="sess_x",
            kind="file",
            source="user_upload",
            local_path=str(doc),
            file_name="doc.pdf",
        )

    with engine.connect() as conn:
        specs = resolve_attachment_specs(
            conn,
            session_id="sess_x",
            attachments=[{"token": token}, {"token": "bad"}, {"nope": 1}],
        )
        cross = resolve_attachment_specs(conn, session_id="other", attachments=[{"token": token}])

    assert len(specs) == 1
    assert specs[0]["path"] == str(doc)
    assert specs[0]["mimetype"] == "application/pdf"
    assert specs[0]["name"] == "doc.pdf"
    # A token from another session must not resolve (defense in depth).
    assert cross == []


def test_message_context_accepts_files():
    # internal_server builds MessageContext(files=...) for web turns; guard the
    # contract that the dataclass takes a files kwarg.
    from modules.im.base import FileAttachment, MessageContext

    ctx = MessageContext(
        user_id="u",
        channel_id="c",
        platform="avibe",
        files=[FileAttachment(name="a.png", mimetype="image/png", local_path="/tmp/a.png")],
    )
    assert ctx.files and ctx.files[0].local_path == "/tmp/a.png"


def test_rewrite_noop_without_file_links(tmp_path):
    db = tmp_path / "vibe.sqlite"
    run_migrations(db)
    engine = create_sqlite_engine(db)
    text = "Plain reply with a [link](https://example.com) and no files."
    with engine.begin() as conn:
        scope_id = _seed_scope_and_session(conn)
        out = rewrite_agent_media(conn, scope_id=scope_id, session_id="sess_x", text=text)
    assert out == text
    with engine.connect() as conn:
        assert conn.execute(select(media_objects)).first() is None


def test_process_reply_keep_file_links():
    # The avibe result path persists with keep_file_links=True so the proxy
    # rewrite can still see the file:// links; the IM default strips them.
    from core.reply_enhancer import process_reply

    raw = "Here ![chart](file:///tmp/c.png) and [doc](file:///tmp/d.pdf)\n\n---\n[OK]"

    default = process_reply(raw)
    assert "file://" not in default.text
    assert "![chart]" not in default.text
    assert len(default.files) == 2

    kept = process_reply(raw, keep_file_links=True)
    assert "![chart](file:///tmp/c.png)" in kept.text
    assert "[doc](file:///tmp/d.pdf)" in kept.text
    assert "[OK]" not in kept.text  # trailing quick-reply block still stripped
    assert len(kept.files) == 2


def test_rewrite_refuses_paths_outside_safe_roots(tmp_path):
    db = tmp_path / "vibe.sqlite"
    run_migrations(db)
    engine = create_sqlite_engine(db)

    from core.workbench_media import rewrite_agent_media

    # A path outside temp / uploads / workdir / Codex roots (here /etc/hosts) must
    # NOT mint a token, so untrusted agent output can't exfiltrate secrets.
    text = "see [hosts](file:///etc/hosts)"
    with engine.begin() as conn:
        scope_id = _seed_scope_and_session(conn)
        out = rewrite_agent_media(
            conn, scope_id=scope_id, session_id="sess_x", text=text, workdir=str(tmp_path / "proj")
        )
    assert out == text  # left untouched, no proxy URL
    with engine.connect() as conn:
        assert conn.execute(select(media_objects)).first() is None


def test_rewrite_allows_paths_under_workdir(tmp_path):
    db = tmp_path / "vibe.sqlite"
    run_migrations(db)
    engine = create_sqlite_engine(db)

    from core.workbench_media import rewrite_agent_media

    workdir = tmp_path / "proj"
    workdir.mkdir()
    img = workdir / "out.png"
    img.write_bytes(b"x")
    text = f"![out](file://{img})"
    with engine.begin() as conn:
        scope_id = _seed_scope_and_session(conn)
        out = rewrite_agent_media(
            conn, scope_id=scope_id, session_id="sess_x", text=text, workdir=str(workdir)
        )
    assert "/api/sessions/sess_x/media/" in out
    with engine.connect() as conn:
        rows = conn.execute(select(media_objects)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["local_path"] == str(img.resolve())
