from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from sqlalchemy import insert, select, update

from config import paths
from config.v2_config import V2Config
from storage.db import create_sqlite_engine
from storage.importer import ensure_sqlite_state, resolve_primary_platform_from_config
from storage.models import show_pages

VISIBILITY_PRIVATE = "private"
VISIBILITY_PUBLIC = "public"
VISIBILITY_OFFLINE = "offline"
VISIBILITIES = {VISIBILITY_PRIVATE, VISIBILITY_PUBLIC, VISIBILITY_OFFLINE}
SHARE_ID_BYTES = 8
_SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class ShowPageError(ValueError):
    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ShowPage:
    session_id: str
    visibility: str
    share_id: str | None
    offline_at: str | None
    created_at: str
    updated_at: str

    @property
    def offline(self) -> bool:
        return self.visibility == VISIBILITY_OFFLINE


def validate_session_id(session_id: str) -> str:
    value = (session_id or "").strip()
    if not value:
        raise ShowPageError("Session ID is required.", code="missing_session_id")
    if not _SESSION_ID_PATTERN.fullmatch(value):
        raise ShowPageError(
            "Session ID may contain only letters, numbers, underscore, dash, dot, and colon.",
            code="invalid_session_id",
        )
    return value


def show_page_dir(session_id: str) -> Path:
    return paths.get_show_page_dir(validate_session_id(session_id))


def ensure_show_page_dir(session_id: str) -> Path:
    page_dir = show_page_dir(session_id)
    page_dir.mkdir(parents=True, exist_ok=True)
    index_path = page_dir / "index.html"
    if not index_path.exists():
        index_path.write_text(_default_index_html(validate_session_id(session_id)), encoding="utf-8")
    return page_dir


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_share_id() -> str:
    return secrets.token_urlsafe(SHARE_ID_BYTES).rstrip("_-")


def _base_public_url(config: V2Config | None = None) -> str | None:
    try:
        cfg = config or V2Config.load()
    except Exception:
        return None
    public_url = (cfg.remote_access.vibe_cloud.public_url or "").strip()
    return public_url.rstrip("/") if public_url else None


def private_url(session_id: str, *, config: V2Config | None = None) -> str | None:
    base = _base_public_url(config)
    if not base:
        return None
    return urljoin(base + "/", f"show/{validate_session_id(session_id)}/")


def public_url(share_id: str | None, *, config: V2Config | None = None) -> str | None:
    if not share_id:
        return None
    base = _base_public_url(config)
    if not base:
        return None
    return urljoin(base + "/", f"p/{share_id}/")


class ShowPageStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or paths.get_sqlite_state_path()
        if db_path is None:
            ensure_sqlite_state(primary_platform=resolve_primary_platform_from_config(paths.get_state_dir()))
        else:
            from storage.migrations import run_migrations

            run_migrations(self.db_path)
        self.engine = create_sqlite_engine(self.db_path)

    def close(self) -> None:
        self.engine.dispose()

    def get(self, session_id: str) -> ShowPage | None:
        session_id = validate_session_id(session_id)
        with self.engine.connect() as conn:
            row = conn.execute(select(show_pages).where(show_pages.c.session_id == session_id).limit(1)).mappings().first()
            return _page_from_row(row) if row else None

    def get_by_share_id(self, share_id: str) -> ShowPage | None:
        share_id = (share_id or "").strip()
        if not share_id:
            return None
        with self.engine.connect() as conn:
            row = (
                conn.execute(select(show_pages).where(show_pages.c.share_id == share_id).limit(1)).mappings().first()
            )
            return _page_from_row(row) if row else None

    def ensure(self, session_id: str) -> ShowPage:
        session_id = validate_session_id(session_id)
        existing = self.get(session_id)
        if existing is not None:
            return existing
        now = _utc_now_iso()
        page = ShowPage(
            session_id=session_id,
            visibility=VISIBILITY_PRIVATE,
            share_id=None,
            offline_at=None,
            created_at=now,
            updated_at=now,
        )
        with self.engine.begin() as conn:
            conn.execute(
                insert(show_pages).values(
                    session_id=page.session_id,
                    visibility=page.visibility,
                    share_id=page.share_id,
                    offline_at=page.offline_at,
                    created_at=page.created_at,
                    updated_at=page.updated_at,
                )
            )
        return page

    def update_visibility(self, session_id: str, visibility: str) -> ShowPage:
        session_id = validate_session_id(session_id)
        if visibility not in VISIBILITIES:
            raise ShowPageError(f"Unsupported visibility: {visibility}", code="invalid_visibility")
        page = self.ensure(session_id)
        now = _utc_now_iso()
        values: dict[str, Any] = {
            "visibility": visibility,
            "updated_at": now,
            "offline_at": now if visibility == VISIBILITY_OFFLINE else None,
        }
        if visibility == VISIBILITY_PUBLIC and not page.share_id:
            values["share_id"] = self._unique_share_id()
        with self.engine.begin() as conn:
            conn.execute(update(show_pages).where(show_pages.c.session_id == session_id).values(**values))
        updated = self.get(session_id)
        assert updated is not None
        return updated

    def rotate_share(self, session_id: str) -> tuple[ShowPage, str | None]:
        session_id = validate_session_id(session_id)
        page = self.ensure(session_id)
        if page.visibility != VISIBILITY_PUBLIC:
            raise ShowPageError(
                "Share links can only be rotated while the Show Page is public.",
                code="not_public",
            )
        previous_share_id = page.share_id
        new_share_id = self._unique_share_id()
        now = _utc_now_iso()
        with self.engine.begin() as conn:
            conn.execute(
                update(show_pages)
                .where(show_pages.c.session_id == session_id)
                .values(share_id=new_share_id, updated_at=now)
            )
        updated = self.get(session_id)
        assert updated is not None
        return updated, previous_share_id

    def _unique_share_id(self) -> str:
        for _ in range(20):
            candidate = _new_share_id()
            if self.get_by_share_id(candidate) is None:
                return candidate
        raise ShowPageError("Could not allocate a unique share ID.", code="share_id_allocation_failed")


def _page_from_row(row: Any) -> ShowPage:
    return ShowPage(
        session_id=str(row["session_id"]),
        visibility=str(row["visibility"]),
        share_id=str(row["share_id"]) if row["share_id"] else None,
        offline_at=str(row["offline_at"]) if row["offline_at"] else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def show_page_payload(page: ShowPage, *, config: V2Config | None = None) -> dict[str, Any]:
    path = show_page_dir(page.session_id)
    private = private_url(page.session_id, config=config)
    public = public_url(page.share_id, config=config)
    active_url = None
    if page.visibility == VISIBILITY_PRIVATE:
        active_url = private
    elif page.visibility == VISIBILITY_PUBLIC:
        active_url = public
    return {
        "session_id": page.session_id,
        "visibility": page.visibility,
        "path": str(path),
        "active_url": active_url,
        "private_url": private,
        "public_url": public,
        "share_id": page.share_id,
        "offline": page.offline,
        "offline_at": page.offline_at,
        "created_at": page.created_at,
        "updated_at": page.updated_at,
    }


def _default_index_html(session_id: str) -> str:
    escaped = session_id.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Show Page</title>
    <style>
      :root {{
        color-scheme: light dark;
        font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #f7f8fb;
        color: #172033;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        padding: 32px 18px;
        box-sizing: border-box;
      }}
      main {{
        width: min(720px, 100%);
        border: 1px solid rgba(23, 32, 51, 0.12);
        border-radius: 14px;
        background: rgba(255, 255, 255, 0.86);
        padding: clamp(24px, 5vw, 48px);
        box-shadow: 0 24px 80px rgba(23, 32, 51, 0.10);
      }}
      p {{
        line-height: 1.65;
        margin: 10px 0 0;
      }}
      .eyebrow {{
        color: #526078;
        font-size: 13px;
        font-weight: 700;
        letter-spacing: 0.08em;
        text-transform: uppercase;
      }}
      h1 {{
        margin: 12px 0 0;
        font-size: clamp(32px, 8vw, 56px);
        line-height: 1;
        letter-spacing: 0;
      }}
      code {{
        background: rgba(82, 96, 120, 0.12);
        border-radius: 6px;
        padding: 2px 6px;
      }}
      @media (prefers-color-scheme: dark) {{
        :root {{
          background: #111827;
          color: #edf2ff;
        }}
        main {{
          background: rgba(17, 24, 39, 0.86);
          border-color: rgba(237, 242, 255, 0.14);
          box-shadow: 0 24px 80px rgba(0, 0, 0, 0.32);
        }}
        .eyebrow {{
          color: #a8b3cf;
        }}
        code {{
          background: rgba(237, 242, 255, 0.12);
        }}
      }}
    </style>
  </head>
  <body>
    <main>
      <div class="eyebrow">Vibe Remote Show Page</div>
      <h1>Ready to visualize</h1>
      <p>This session's Show Page workspace is ready. Replace this file with a focused visual explanation, report, diagram, dashboard, or prototype.</p>
      <p>Session: <code>{escaped}</code></p>
    </main>
  </body>
</html>
"""
