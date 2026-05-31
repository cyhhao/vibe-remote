# Workbench: project archive-recovery by path + session pagination

Branch: `feat/workbench-project-reuse-session-paging`

## Background

Today (verified against code):

- A "project" is a `scopes` row (`platform='avibe'`, `scope_type='project'`) + a
  `scope_settings` row. The folder path lives in `scope_settings.workdir`
  (already stored resolved/absolute via `Path(...).expanduser().resolve()`).
- "Archived" == `scope_settings.enabled = 0`. `create_project` always mints a
  **new** random `proj_<hex>` scope, so the same folder can spawn duplicate
  projects, and there is **no unarchive path** anywhere (no service method, no
  route). `archive_project` only flips `enabled=0`.
- The sidebar loads sessions for a project with a hardcoded `limit: 50`,
  `status='active'`, and **ignores** the server cursor (`next_before_id`). The
  API client (`listSessions`) already supports `beforeId -> before_id` and the
  service already does cursor pagination (clamped 1..200). So pagination is a
  pure frontend wiring job.

## Goals (this change)

1. **Project archive recovery without a new endpoint.** On create/open
   (`POST /api/projects`, the only project-creation entry — `NewProjectDialog`),
   find-or-create by resolved folder path:
   - same-path project exists → **reuse** it (no duplicate);
   - if it was archived (`enabled=0`) → **un-archive** (`enabled=1`).
   Recovery UX = user clicks "open project", picks the same folder, it comes back
   with its sessions intact.
2. **Session pagination in the sidebar.** Load the most recent **10** active
   sessions per project; show a **"Load more"** button when more exist; each
   click appends the next 10 via the existing cursor.

## Non-goals

- **Session archiving stays untouched** (per request). `archive_session` +
  `DELETE /api/sessions/:id` + the unused `archiveSession` client method remain
  exactly as-is. We add no session-archive UI.
- No DB migration, no new uniqueness constraint, no merging of pre-existing
  duplicate-path projects.

## Design

### 1. Backend — `storage/projects_service.py::create_project` becomes find-or-create

```
folder = _resolve_folder(folder_path)            # existing; resolves ~ and symlinks
existing = _find_project_by_workdir(conn, str(folder))
if existing:
    if not existing.enabled:                     # archived -> revive
        scope_settings.enabled = 1
    scopes.last_seen_at = now; scopes.updated_at = now   # "opened" => recent
    # display_name: keep existing by default (see decision Q1)
    return _project_payload(conn, existing.scope_id)
# else: current insert path (new scope + scope_settings, enabled=1)
```

`_find_project_by_workdir(conn, workdir)`: join `scopes` (avibe/project) ⨝
`scope_settings` where `workdir = :workdir`, order by `enabled DESC,
last_seen_at DESC`, limit 1 (deterministic pick if legacy duplicates exist —
prefer an active one, else most recently active). `workdir` is indexed
(`ix_scope_settings_workdir`).

- Route `projects_create` already wraps the call in `engine.begin()` (single
  transaction) → the lookup+revive is atomic. Keep returning the project payload
  (status code: keep `201`, or `200` on reuse — decision Q5/low-stakes).
- This makes create idempotent-by-path and removes the need for any unarchive
  endpoint.

### 2. Frontend — pagination (`WorkbenchSidebar.tsx` + `SessionList`)

- Add `const SESSIONS_PAGE_SIZE = 10` (named constant; replaces the `50` magic
  number at line 446).
- Extend per-project state:
  - keep `sessionsByProject: Record<string, WorkbenchSession[]>` (now appended);
  - add `sessionCursorByProject: Record<string, string | null>` (server
    `next_before_id`; `null` = no more, absent = not loaded);
  - add a per-project "loading more" flag (reuse/extend `sessionsLoading`).
- `fetchSessions(projectId, { append })`:
  - first load: `listSessions({ projectId, status:'active', limit: 10 })`, replace;
  - load more: `listSessions({ ..., beforeId: cursor })`, **append**;
  - store `next_before_id` as the cursor both times.
- `SessionList`: render a "Load more" row/button when
  `cursor !== null && cursor !== undefined`; onClick → `fetchSessions(id, {append:true})`.
  Show a small spinner while loading more. (i18n key `workbench.sessionsLoadMore`.)
- New-session optimistic prepend (`createSessionForProject`) is unchanged and
  still correct with paging.

### 3. Frontend — open/restore UX (`NewProjectDialog` + sidebar `onCreated`)

- The dialog already posts `folder_path` (+ optional `display_name`). After the
  backend change, picking an archived path just restores it — **no dialog logic
  change required** for the core flow.
- Harden `onCreated`: dedupe by `id` (or refetch projects) so a reused/restored
  project shows up exactly once and is selected/expanded. (A restored project
  was absent from the list, so it must be added; an already-active reuse must not
  duplicate.)
- Copy: consider relabeling the entry + dialog title to "Open / Add project"
  (打开 / 添加项目) since it now opens-or-creates (decision Q4, low-stakes).

## Edge cases / open decisions (need product confirmation)

- **Q1 — display name on reuse.** Recommend: on reuse, **keep the existing
  project's name**; ignore the dialog's `display_name` unless the user explicitly
  typed a different one (rename stays a separate explicit action). Avoids the
  re-open auto-filling folder-basename and clobbering a custom name.
- **Q2 — reuse when path is already ACTIVE.** Recommend: **yes**, reuse (return
  existing) — makes create idempotent and fixes the current duplicate-project
  bug. (Matches "find same scope → reuse directly".)
- **Q3 — path equality.** Match on resolved absolute path (current storage
  shape). `~`, trailing slash, and symlinks converge. Known minor limit: macOS
  case-insensitive FS — different-case spellings won't dedupe. Accept for now.
- **Q4 — legacy duplicate paths.** Deterministic pick (active first, then most
  recent); no auto-merge.
- **Q5 — HTTP status on reuse.** `201` vs `200`. Low-stakes; default keep `201`.

## Changes (files)

- `storage/projects_service.py` — `_find_project_by_workdir` + find-or-create in
  `create_project` (+ revive/last_seen bump).
- `tests/test_projects_service.py` (new) — find-or-create returns same id;
  re-create of archived path re-enables it; different path makes a new project;
  duplicate-path deterministic pick.
- `ui/src/components/workbench/WorkbenchSidebar.tsx` — page size constant,
  cursor state, append, Load-more wiring; `onCreated` dedupe.
- `ui/src/i18n/en.json` + `zh.json` — `workbench.sessionsLoadMore` (+ any copy
  tweak for open/add project).
- Possibly `NewProjectDialog.tsx` — only if we change copy.

## Testing / evidence

- Unit: `tests/test_projects_service.py` (new) for find-or-create + revive.
  Run the storage test group, not just the new file.
- `ruff check` on changed Python before push.
- UI: `npm run build` in `ui/`.
- Manual / regression: archive a project, "open" same folder → it returns with
  sessions; project with >10 sessions shows Load-more and appends correctly.
- No scenario catalog applies to this surface (not an auth/setup flow).

## Out of scope / follow-ups

- Session archive UI (explicitly deferred).
- Real path-uniqueness constraint + duplicate-merge migration (future, if needed).
