# Refactor: shared workbench data hooks (kill the parallel reimplementations)

## Why
`ProjectsPage` + `NewSessionSheet` (mobile) re-derived logic that already exists in
`WorkbenchSidebar` (projects/sessions load, SSE status/title, paging, dedup, reconnect,
unread, mark-read) and `Workbench.tsx` (create+navigate+draft). ~15 of ~25 Codex P2s on
PRs #402/#406 were re-deriving behavior the desktop already had — a reuse-ladder/DRY miss.
Root fix: extract shared logic; desktop + mobile consume one source of truth.

## Hook A — `WorkbenchProjectsProvider` / `useWorkbenchProjectsTree` (CONTEXT PROVIDER)
Mounted once at app root next to `WorkbenchInboxProvider` (`App.tsx`). A provider (not a plain
hook) because `WorkbenchSidebar` is ALWAYS mounted (`md:flex` is CSS-only) and `ProjectsPage`
mounts on `/projects` — today that's **two** `connectWorkbenchEvents` subscriptions + duplicate
fetches on mobile. One provider = one EventSource, one cache, one reconcile (mirrors
`WorkbenchInboxContext`).

API: `projects`, `projectsError`, `refreshProjects`; `sessionsOf(id) → {sessions|null, loading,
loadingMore, cursor, error}`, `isExpanded`, `expanded`, `toggleExpanded`, `loadMore`;
`createSessionForProject(id) → WorkbenchSession|null` (optimistic prepend + expand; **returns**
the session, does NOT navigate), `creatingSession(id)`, `renameProject`, `archiveProject`,
`renameSession`, `upsertProjectToTop(project)`.

- **Navigation MUST stay in consumers** — the provider is mounted OUTSIDE `<BrowserRouter>`
  (App.tsx puts BrowserRouter inside WorkbenchInboxProvider), so it cannot call `useNavigate`.
- **Memoize the `value`** (`useMemo` + `useCallback`) — the iOS-Safari re-render lesson
  ([[feedback_react_context_value_memoize]]).
- Unify `PAGE_SIZE` (sidebar 10 vs mobile 50 — pick one), adopt the chunked `reconcileSessions`
  (mobile's, correct for >200-row windows clamp), standardize SSE patching on scan-by-`session_id`.
- Preserve the `createSessionForProject` "don't seed a 1-item cache when project unloaded" subtlety.
- Keep `error: boolean` per-project state (mobile retry needs it; desktop ignores it).
- Unread stays in `WorkbenchInboxContext` (both already read it directly).

Consumers: `WorkbenchSidebar` keeps ALL its JSX (popover, SessionRow/ProjectRow, context menus,
rename/archive UI, capabilities nav, brand) + inbox-popover hover machinery; its data layer moves
to the provider. `ProjectsPage` replaces its whole data layer with `useWorkbenchProjectsTree()`,
keeps its mobile JSX + `openSession` (markRead+navigate).

## Hook B — `useNewSession` (PLAIN HOOK)
Home (`/`) and the sheet (modal) are never usefully co-mounted + no live data → plain hook.
API: `projects` (sorted), `loaded`, `error`, `sending`, `selectedId`/`setSelected`, `target`,
`needsProject`, `send(text) → {sessionId, initialMessage}|null` (returns nav target, consumer
navigates), `upsertSelectProject(project)`, `reset()`. Option `active` (sheet passes `open`) to
re-run load+reset on the open rising-edge with a cancel guard.
- Draft stash + Radix close-before-open dialog stay in the SHEET (lifecycle, not create-flow).
- Home's "no project → NewProjectDialog" nudge stays in the consumer (check `needsProject`).

## Order / status
1. Hook B (`lib/useNewSession.ts`) + refactor `Workbench.tsx` + `NewSessionSheet.tsx`. ← safer, first
2. Hook A (`context/WorkbenchProjectsContext.tsx`) + mount in App.tsx + refactor
   `WorkbenchSidebar` + `ProjectsPage`. ← bigger, central; verify both surfaces.
3. `npm run build` green each step; push onto the #406 branch; Codex re-review (incl. sidebar);
   fix to approval; then merge #406.
