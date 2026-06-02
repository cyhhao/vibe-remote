# Admin "Show Pages" management page

## Background
Show Pages (`core/show_pages.py`) are per-session managed React pages, each `private` / `public` / `offline`. The CLI (`vibe show list/status/update`) manages them, but there is no Web UI. Add a `/admin/show-pages` page + sidebar entry so users can manage every Show Page from the console.

Design is locked in `design.pen` frame **`m7wqzf`** ("vibe-remote — Show Pages (Dark)"); sidebar item added to shared component `kSWgv`.

## Goal (user-approved)
Admin can: list all Show Pages (private + public + offline), newest-first; per row see a friendly title + current status + live link; switch private↔public, take offline, restore from offline; rotate a public link; copy / open links.

## Design decisions
- **Status colors:** Public = gold, Private = cyan, Offline = grey/muted. Expanded-row selection glow stays mint (generic "selected" accent, not a status).
- **Row label = `session.title` if set, else `session_id`** (mono). No channel/scope middle fallback (user decision 2026-06-02). Full `session_id` always shown in the expanded Details.
- **3-way visibility segmented `[Private | Public | Offline]`** is the core switch (maps to `update --visibility`), covering restore-from-offline.
- **Public extras:** copy link, open, rotate link (revokes + reissues).
- **Cloud reachability:** when Avibe Cloud is not connected (`url_available == false`), show a gold warning by the live link; hidden when connected.
- Row sub-line: `platform · agent` context.

## Backend reuse (no new store logic)
- `ShowPageStore.list_page()` already filters + orders `updated_at desc` + paginates. `show_page_payload()` returns visibility / urls / `url_available` / `url_guidance` / `share_id` / timestamps. Reuse as-is.
- New `storage/sessions_service.read_session_titles(session_ids) -> {id: title}` (batch read of `agent_sessions.title`).
- New `vibe/api.py`: `list_show_pages()`, `set_show_page_visibility(session_id, visibility)`, `rotate_show_page_share(session_id)` — wrap the store + title enrichment, return `{ok, ...}` dicts (mirror `get_vibe_agents` style).
- New `ui_server.py` routes: `GET /api/show-pages`, `POST /api/show-pages/<sid>/visibility`, `POST /api/show-pages/<sid>/rotate-share`. `ShowPageError` -> mapped error response (mirror `_vibe_agent_result_response`).

## Frontend
- `AppShell.tsx`: add `adminItems` entry `{ to:'/admin/show-pages', label:t('nav.showPages'), icon: MonitorPlay }` between Users and Settings.
- `App.tsx`: route `/admin/show-pages -> <ShowPagesPage/>`.
- `components/ShowPagesPage.tsx`: header + search, visibility filter segmented, table (SESSION / VISIBILITY / LIVE LINK / UPDATED / actions), expandable row -> segmented + copy/open + rotate + Details. Reuse `Badge`/`Button`/ui primitives + `UserList` list/row layout classes. Client-side filter/search; fetch all via `getShowPages`.
- `context/ApiContext.tsx`: `getShowPages()`, `setShowPageVisibility(sid, vis)`, `rotateShowPageShare(sid)`.
- `i18n/en.json` + `zh.json`: `nav.showPages` + `showPages.*` keys.

## Build order
1. Backend: `sessions_service.read_session_titles` + `api` fns + routes + pytest.
2. Frontend: ApiContext + i18n + AppShell nav + App route + ShowPagesPage; `npm run build`.
3. Verify the built page side-by-side vs design frame `m7wqzf`.

## Evidence layers
- unit: pytest for `api.list_show_pages` (title join), visibility update, rotate-share, error mapping (canned sqlite state).
- build: `npm run build` in `ui/`; `ruff check` on changed Python.
- manual: render `/admin/show-pages` vs the design frame.
