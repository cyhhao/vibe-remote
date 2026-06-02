import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { NavLink, useLocation, useNavigate } from 'react-router-dom';
import {
  Activity,
  Archive,
  ArrowRight,
  Bot,
  ChevronDown,
  ChevronRight,
  Ellipsis,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  Inbox,
  KeyRound,
  Loader2,
  Pencil,
  Plus,
  WandSparkles,
} from 'lucide-react';
import clsx from 'clsx';
import type { LucideIcon } from 'lucide-react';

import { useApi } from '../../context/ApiContext';
import { useWorkbenchInbox } from '../../context/WorkbenchInboxContext';
import type { InboxSession, WorkbenchProject, WorkbenchSession } from '../../context/ApiContext';
import { formatRelativeTime } from '../../lib/relativeTime';
import { Popover, PopoverAnchor, PopoverContent, PopoverTrigger } from '../ui/popover';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Markdown } from '../ui/markdown';
import { NewProjectDialog } from './NewProjectDialog';
import { ProjectAgentsMdDialog } from './ProjectAgentsMdDialog';

interface CapabilityNavItem {
  to: string;
  i18nKey: string;
  icon: LucideIcon;
}

const CAPABILITY_NAV: CapabilityNavItem[] = [
  { to: '/agents', i18nKey: 'workbench.nav.agents', icon: Bot },
  { to: '/skills', i18nKey: 'workbench.nav.skills', icon: WandSparkles },
  { to: '/harness', i18nKey: 'workbench.nav.harness', icon: Activity },
  { to: '/vaults', i18nKey: 'workbench.nav.vaults', icon: KeyRound },
];

// How many sessions to load per page under a project. The server caps the page
// size and returns a cursor (next_before_id); the sidebar appends the next page
// via the "Load more" control rather than loading every session up front.
const SESSIONS_PAGE_SIZE = 10;

// 360px floating popover that opens when the user hovers the Inbox entry.
// Mirrors design.pen KmQ1L — header + a few session cards + footer "open full
// inbox" link. Pure presentational; data comes from <WorkbenchInboxProvider>.
const InboxHoverPopover: React.FC<{
  visible: boolean;
  sessions: InboxSession[];
  unreadBySession: Record<string, number>;
  unreadSessions: number;
  totalUnread: number;
  onItemClick: (session: InboxSession) => void;
  onMarkAllRead: () => void;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}> = ({
  visible,
  sessions,
  unreadBySession,
  unreadSessions,
  totalUnread,
  onItemClick,
  onMarkAllRead,
  onMouseEnter,
  onMouseLeave,
}) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  if (!visible) return null;
  const shown = sessions.slice(0, 5);
  // The unread map is authoritative; a session absent from it has 0 unread
  // (don't fall back to the card's stale unread_count — see InboxPage).
  const unreadOf = (s: InboxSession) => unreadBySession[s.session_id] ?? 0;
  return (
    <div
      role="dialog"
      aria-label={t('workbench.inbox.title')}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      className="absolute left-full top-0 z-50 ml-3 flex w-[360px] flex-col gap-2.5 rounded-2xl border border-border-strong bg-surface-2 p-3.5 shadow-[0_24px_64px_-12px_rgba(0,0,0,0.6)]"
    >
      <div className="flex items-start gap-2">
        <div className="flex flex-1 flex-col">
          <div className="text-[13px] font-bold text-foreground">{t('workbench.inbox.title')}</div>
          <div className="text-[10px] text-muted">
            {t('workbench.inbox.headerCount', { unread: unreadSessions, total: sessions.length })}
          </div>
        </div>
        <button
          type="button"
          onClick={onMarkAllRead}
          disabled={totalUnread === 0}
          className={clsx(
            'rounded-md border px-2 py-1 text-[10px] font-medium transition',
            totalUnread === 0
              ? 'cursor-not-allowed border-border bg-foreground/[0.02] text-muted'
              : 'border-border-strong text-foreground hover:bg-foreground/[0.04]',
          )}
        >
          {t('workbench.inbox.markAllRead')}
        </button>
      </div>

      {shown.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-[12px] text-muted">
          {t('workbench.inbox.empty')}
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          {shown.map((s) => {
            const unread = unreadOf(s);
            const projectLabel = s.project_name || s.project_id || 'avibe';
            return (
              <button
                key={s.session_id}
                type="button"
                onClick={() => onItemClick(s)}
                className={clsx(
                  'flex flex-col gap-1.5 rounded-lg px-3 py-2.5 text-left transition',
                  unread > 0
                    ? 'border-l-2 border-mint bg-mint/[0.06] hover:bg-mint/[0.10]'
                    : 'hover:bg-foreground/[0.04]',
                )}
              >
                <div className="flex items-center gap-1.5 text-[10px]">
                  <span className="truncate font-semibold text-cyan">{projectLabel}</span>
                  <span className="text-muted">·</span>
                  <span className="flex-1 truncate font-semibold text-foreground">
                    {s.title?.trim() || s.session_id}
                  </span>
                  {s.replied && (
                    <span className="shrink-0 font-semibold text-cyan" title={t('workbench.inbox.replied')}>
                      ↩
                    </span>
                  )}
                  <span className="font-mono text-muted">{formatRelativeTime(s.last_activity_at, t)}</span>
                </div>
                {s.preview_text ? (
                  <div
                    className={clsx(
                      'line-clamp-2 text-[11.5px] leading-relaxed',
                      unread > 0 ? 'text-foreground' : 'text-muted',
                    )}
                  >
                    <Markdown content={s.preview_text} interactive={false} className="vr-markdown--preview" />
                  </div>
                ) : (
                  <div className="text-[11.5px] leading-relaxed text-muted">—</div>
                )}
              </button>
            );
          })}
        </div>
      )}

      <button
        type="button"
        onClick={() => navigate('/inbox')}
        className="flex items-center justify-center gap-1.5 rounded-md pt-1 text-[11px] font-medium text-cyan hover:underline"
      >
        {t('workbench.inbox.viewAll')}
        <ArrowRight className="size-3" />
      </button>
    </div>
  );
};

// Session status dot colours. Maps the agent-runtime status to the user's
// gray / green / red: idle → muted (gray), running → mint (green) + glow,
// failed → destructive (red) + glow. Tokens resolve from src/index.css.
const STATUS_DOT_CLASS: Record<string, string> = {
  running: 'bg-mint shadow-[0_0_6px_0_rgba(91,255,160,0.65)]',
  failed: 'bg-destructive shadow-[0_0_6px_0_rgba(255,107,107,0.6)]',
  idle: 'bg-muted',
};

// One session row under a project. Left-click opens the chat; right-click opens
// a small menu whose action is Rename — an inline edit equivalent to the chat
// header's title field. Rename calls api.updateSession({ title }); the live
// session.activity 'updated' event then patches the title in this list (see the
// onSessionActivity handler in WorkbenchSidebar), so no manual local patch here.
const SessionRow: React.FC<{
  session: WorkbenchSession;
  unread: number;
  onSessionMarkRead: (sessionId: string) => void;
  onRenameSession: (sessionId: string, title: string) => Promise<void>;
}> = ({ session, unread, onSessionMarkRead, onRenameSession }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const active = location.pathname === `/chat/${session.id}`;
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(session.title ?? '');
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (renaming) inputRef.current?.focus();
  }, [renaming]);

  const commitRename = async () => {
    const trimmed = draft.trim();
    setRenaming(false);
    // No-op when unchanged; an empty name clears to "untitled" like the header.
    if (trimmed === (session.title ?? '').trim()) return;
    try {
      await onRenameSession(session.id, trimmed);
    } catch {
      // The shared apiFetch layer already surfaced the error toast.
    }
  };

  if (renaming) {
    return (
      <div className="flex items-center gap-2 py-1.5 pl-[30px] pr-2.5">
        <span
          className={clsx(
            'size-[5px] shrink-0 rounded-full',
            STATUS_DOT_CLASS[session.agent_status] ?? STATUS_DOT_CLASS.idle,
          )}
        />
        <Input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === 'Enter') commitRename();
            if (e.key === 'Escape') {
              setDraft(session.title ?? '');
              setRenaming(false);
            }
          }}
          placeholder={t('workbench.sessionRenamePlaceholder')}
          className="h-7 flex-1 px-1.5 text-[12px]"
        />
      </div>
    );
  }

  const displayName = session.title?.trim() || t('workbench.untitledSession');
  return (
    <Popover open={menuOpen} onOpenChange={setMenuOpen}>
      <PopoverAnchor asChild>
        <button
          type="button"
          onClick={() => {
            navigate(`/chat/${encodeURIComponent(session.id)}`);
            if (unread > 0) onSessionMarkRead(session.id);
          }}
          onContextMenu={(e) => {
            e.preventDefault();
            setMenuOpen(true);
          }}
          className={clsx(
            'group/sess flex items-center gap-2 rounded-md py-1.5 pl-[30px] pr-2.5 text-left transition',
            active
              ? 'border-l-2 border-mint bg-mint-soft pl-[28px] font-semibold text-foreground'
              : 'hover:bg-foreground/[0.04]',
          )}
        >
          <span
            title={t(`workbench.sessionStatus.${session.agent_status}`)}
            className={clsx(
              'size-[5px] shrink-0 rounded-full',
              STATUS_DOT_CLASS[session.agent_status] ?? STATUS_DOT_CLASS.idle,
            )}
          />
          <span
            className={clsx(
              'flex-1 truncate text-[12px]',
              active ? 'font-semibold text-foreground' : 'font-medium text-foreground',
            )}
          >
            {displayName}
          </span>
          {unread > 0 && (
            <span className="inline-flex min-w-[1.1rem] items-center justify-center rounded-full bg-mint px-1.5 font-mono text-[9px] font-bold text-[#080812]">
              {unread > 99 ? '99+' : unread}
            </span>
          )}
        </button>
      </PopoverAnchor>
      <PopoverContent align="start" className="w-[160px] p-1">
        <button
          type="button"
          onClick={() => {
            setMenuOpen(false);
            setDraft(session.title ?? '');
            setRenaming(true);
          }}
          className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-foreground transition hover:bg-foreground/[0.04]"
        >
          <Pencil className="size-3 text-muted" />
          {t('workbench.sessionRename')}
        </button>
      </PopoverContent>
    </Popover>
  );
};

// One project row + (when expanded) the session list under it. Mirrors
// design.pen N96dsm/C68Ul (project row) and C7clY/R2C8U (session row).
const ProjectRow: React.FC<{
  project: WorkbenchProject;
  expanded: boolean;
  sessions: WorkbenchSession[] | null;
  loading: boolean;
  loadingMore: boolean;
  hasMore: boolean;
  onLoadMore: () => void;
  onToggle: () => void;
  onCreateSession: () => void;
  creatingSession: boolean;
  unreadBySession: Record<string, number>;
  onSessionMarkRead: (sessionId: string) => void;
  onRename: (next: string) => Promise<void>;
  onArchive: () => Promise<void>;
  onRenameSession: (sessionId: string, title: string) => Promise<void>;
}> = ({
  project,
  expanded,
  sessions,
  loading,
  loadingMore,
  hasMore,
  onLoadMore,
  onToggle,
  onCreateSession,
  creatingSession,
  unreadBySession,
  onSessionMarkRead,
  onRename,
  onArchive,
  onRenameSession,
}) => {
  const { t } = useTranslation();
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState(project.display_name);
  const [menuOpen, setMenuOpen] = useState(false);
  const [agentsMdOpen, setAgentsMdOpen] = useState(false);
  const renameInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (renaming) renameInputRef.current?.focus();
  }, [renaming]);

  const commitRename = async () => {
    const trimmed = renameDraft.trim();
    if (!trimmed || trimmed === project.display_name) {
      setRenaming(false);
      setRenameDraft(project.display_name);
      return;
    }
    await onRename(trimmed);
    setRenaming(false);
  };

  return (
    <div className="flex flex-col gap-0.5">
      <div
        className="group flex items-center gap-1.5 rounded-md px-2 py-1.5 transition hover:bg-foreground/[0.04]"
        title={project.folder_path}
        onContextMenu={(e) => {
          // Right-click opens the same menu as the ⋯ button (anchored to it).
          if (renaming) return;
          e.preventDefault();
          setMenuOpen(true);
        }}
      >
        {renaming ? (
          <div className="flex flex-1 items-center gap-1.5">
            {expanded ? (
              <FolderOpen className="size-3.5 shrink-0 text-muted" />
            ) : (
              <Folder className="size-3.5 shrink-0 text-muted" />
            )}
            <Input
              ref={renameInputRef}
              value={renameDraft}
              onChange={(e) => setRenameDraft(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => {
                if (e.key === 'Enter') commitRename();
                if (e.key === 'Escape') {
                  setRenameDraft(project.display_name);
                  setRenaming(false);
                }
              }}
              placeholder={t('workbench.projectRenamePlaceholder')}
              className="h-7 flex-1 px-1.5 text-[12px] font-medium"
            />
          </div>
        ) : (
          <button
            type="button"
            onClick={onToggle}
            className="flex flex-1 items-center gap-1.5 text-left"
          >
            <Chevron className="size-3 shrink-0 text-muted" />
            {expanded ? (
              <FolderOpen className="size-3.5 shrink-0 text-muted" />
            ) : (
              <Folder className="size-3.5 shrink-0 text-muted" />
            )}
            <span className="flex-1 truncate text-[12px] font-medium text-foreground">
              {project.display_name}
            </span>
          </button>
        )}
        {!renaming && (
          <>
            <Popover open={menuOpen} onOpenChange={setMenuOpen}>
              <PopoverTrigger asChild>
                <button
                  type="button"
                  aria-label={t('workbench.projectActions')}
                  className={clsx(
                    'flex size-5 shrink-0 items-center justify-center rounded-md text-muted transition',
                    'opacity-0 group-hover:opacity-100 hover:text-foreground hover:bg-foreground/[0.06]',
                    menuOpen && 'opacity-100',
                  )}
                >
                  <Ellipsis className="size-3" />
                </button>
              </PopoverTrigger>
              <PopoverContent align="end" className="w-[160px] p-1">
                <button
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    setRenaming(true);
                    setRenameDraft(project.display_name);
                  }}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-foreground transition hover:bg-foreground/[0.04]"
                >
                  <Pencil className="size-3 text-muted" />
                  {t('workbench.projectRename')}
                </button>
                {project.folder_path && (
                  <button
                    type="button"
                    onClick={() => {
                      setMenuOpen(false);
                      setAgentsMdOpen(true);
                    }}
                    className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-foreground transition hover:bg-foreground/[0.04]"
                  >
                    <FileText className="size-3 text-muted" />
                    {t('workbench.projectEditAgents')}
                  </button>
                )}
                <button
                  type="button"
                  onClick={async () => {
                    setMenuOpen(false);
                    const ok = window.confirm(
                      t('workbench.projectArchiveConfirm', { name: project.display_name }),
                    );
                    if (ok) await onArchive();
                  }}
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-pink transition hover:bg-pink/[0.08]"
                >
                  <Archive className="size-3" />
                  {t('workbench.projectArchive')}
                </button>
              </PopoverContent>
            </Popover>
            <button
              type="button"
              aria-label={t('workbench.addSession')}
              onClick={onCreateSession}
              disabled={creatingSession}
              className={clsx(
                'flex size-5 shrink-0 items-center justify-center rounded-md text-muted transition',
                'opacity-0 group-hover:opacity-100 hover:text-foreground hover:bg-foreground/[0.06]',
                creatingSession && 'opacity-100',
              )}
            >
              {creatingSession ? <Loader2 className="size-3 animate-spin" /> : <Plus className="size-3" />}
            </button>
          </>
        )}
      </div>

      {expanded && (
        <div className="flex flex-col gap-0.5 pb-0.5">
          {loading && sessions === null && (
            <div className="px-3 py-2 pl-[30px] text-[11px] italic text-muted">{t('workbench.sessionsLoading')}</div>
          )}
          {sessions !== null && sessions.length === 0 && !loading && (
            <div className="px-3 py-2 pl-[30px] text-[11px] italic text-muted">{t('workbench.sessionsEmpty')}</div>
          )}
          {sessions !== null &&
            sessions.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                unread={unreadBySession[session.id] || 0}
                onSessionMarkRead={onSessionMarkRead}
                onRenameSession={onRenameSession}
              />
            ))}
          {hasMore && (
            <button
              type="button"
              onClick={onLoadMore}
              disabled={loadingMore}
              className="flex items-center gap-1.5 rounded-md py-1.5 pl-[30px] pr-2.5 text-left text-[11px] font-medium text-muted transition hover:bg-foreground/[0.04] hover:text-foreground disabled:cursor-default disabled:opacity-60"
            >
              {loadingMore ? <Loader2 className="size-3 animate-spin" /> : <ChevronDown className="size-3" />}
              {t('workbench.sessionsLoadMore')}
            </button>
          )}
        </div>
      )}

      <ProjectAgentsMdDialog
        project={project}
        open={agentsMdOpen}
        onClose={() => setAgentsMdOpen(false)}
      />
    </div>
  );
};

export const WorkbenchSidebar: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const { totalUnread, unreadSessions, inboxSessions, markRead, unreadBySession } = useWorkbenchInbox();
  const [popoverOpen, setPopoverOpen] = useState(false);
  const closeTimer = useRef<number | null>(null);

  // Projects state
  const [projects, setProjects] = useState<WorkbenchProject[] | null>(null);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [sessionsByProject, setSessionsByProject] = useState<Record<string, WorkbenchSession[]>>({});
  const [sessionsLoading, setSessionsLoading] = useState<Record<string, boolean>>({});
  const [sessionsLoadingMore, setSessionsLoadingMore] = useState<Record<string, boolean>>({});
  // Cursor (server next_before_id) per project: a string means "more pages
  // exist", null means "fully loaded", absent means "not loaded yet". The ref
  // mirror lets fetchSessions read the latest cursor without going stale inside
  // its memoised closure.
  const [sessionCursor, setSessionCursor] = useState<Record<string, string | null>>({});
  const sessionCursorRef = useRef<Record<string, string | null>>({});
  // Projects with an in-flight session fetch — serialises first-page and
  // load-more calls per project so a refetch can't race or truncate an append.
  const sessionsInFlightRef = useRef<Set<string>>(new Set());
  const [creatingSession, setCreatingSession] = useState<Set<string>>(new Set());
  const [showNewProject, setShowNewProject] = useState(false);
  // Mirror the set of projects whose sessions are currently loaded so the
  // (re)connect handler can refetch exactly those without re-subscribing the
  // event stream on every expand (stale-closure-safe, like cursorRef in the
  // inbox context).
  const loadedProjectsRef = useRef<string[]>([]);
  loadedProjectsRef.current = Object.keys(sessionsByProject);
  // Mirror the loaded session rows so the (re)connect reconcile can refetch the
  // SAME already-paged-in window (not just the first page) without a stale closure.
  const sessionsByProjectRef = useRef<Record<string, WorkbenchSession[]>>({});
  sessionsByProjectRef.current = sessionsByProject;

  const fetchProjects = useCallback(async () => {
    try {
      const result = await api.listProjects();
      setProjects(result.projects);
      setProjectsError(null);
    } catch (err: any) {
      setProjectsError(err?.message ?? String(err));
    }
  }, [api]);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  const fetchSessions = useCallback(
    async (projectId: string, opts?: { append?: boolean; reconcile?: boolean }) => {
      const append = opts?.append ?? false;
      const reconcile = opts?.reconcile ?? false;
      if (append && !sessionCursorRef.current[projectId]) return; // nothing more to load
      if (sessionsInFlightRef.current.has(projectId)) return; // serialise per project
      sessionsInFlightRef.current.add(projectId);
      if (append) {
        setSessionsLoadingMore((prev) => ({ ...prev, [projectId]: true }));
      } else {
        setSessionsLoading((prev) => ({ ...prev, [projectId]: true }));
      }
      try {
        // A (re)connect reconcile refetches the SAME already-loaded window (every
        // paged-in row), not just the first page — otherwise a transient SSE
        // reconnect / controller restart truncates an expanded project back to the
        // first SESSIONS_PAGE_SIZE rows until the user pages again (Codex P2).
        const loadedCount = sessionsByProjectRef.current[projectId]?.length ?? 0;
        const limit = reconcile ? Math.max(loadedCount, SESSIONS_PAGE_SIZE) : SESSIONS_PAGE_SIZE;
        const result = await api.listSessions({
          projectId,
          status: 'active',
          limit,
          beforeId: append ? sessionCursorRef.current[projectId] ?? undefined : undefined,
        });
        // Mutate the ref in place so a concurrent load for another project
        // can't drop this cursor via a stale read-modify-write snapshot.
        sessionCursorRef.current[projectId] = result.next_before_id;
        setSessionCursor((prev) => ({ ...prev, [projectId]: result.next_before_id }));
        setSessionsByProject((prev) => {
          if (!append) return { ...prev, [projectId]: result.sessions };
          // Cursor pages can overlap if a row's last_active_at shifts between
          // fetches (the cursor is just a row id resolved against current
          // activity); drop ids we already hold so rows never duplicate.
          const existing = prev[projectId] ?? [];
          const seen = new Set(existing.map((s) => s.id));
          return { ...prev, [projectId]: [...existing, ...result.sessions.filter((s) => !seen.has(s.id))] };
        });
      } catch (err) {
        // First-page failure: surface as empty so the user can collapse +
        // re-expand to retry. On a "load more" failure, keep the existing list
        // and cursor so the button stays available for another attempt.
        if (!append) {
          setSessionsByProject((prev) => ({ ...prev, [projectId]: prev[projectId] ?? [] }));
        }
      } finally {
        sessionsInFlightRef.current.delete(projectId);
        if (append) {
          setSessionsLoadingMore((prev) => ({ ...prev, [projectId]: false }));
        } else {
          setSessionsLoading((prev) => ({ ...prev, [projectId]: false }));
        }
      }
    },
    [api],
  );

  // Keep cached session rows in sync with edits made elsewhere (e.g. renaming
  // a session from the chat header). The server broadcasts session.activity
  // with event "updated"; patch the matching row's title in place so the
  // sidebar label tracks the chat header without a manual refresh.
  useEffect(() => {
    const disconnect = api.connectWorkbenchEvents({
      // (Re)connect reconciliation: after a controller restart the crash-recovery
      // reset (running → idle) ran server-side with NO event subscriber to
      // broadcast to, and any status events during the drop were missed. The
      // sidebar dots' authoritative source is listSessions, so refetch projects +
      // every already-expanded project's sessions whenever the stream (re)opens.
      onConnected: () => {
        fetchProjects();
        for (const projectId of loadedProjectsRef.current) {
          fetchSessions(projectId, { reconcile: true });
        }
      },
      onSessionActivity: (data) => {
        if (data.event !== 'updated' || !data.scope_id) return;
        const projectId = data.scope_id.split('::').pop();
        if (!projectId) return;
        const nextTitle = data.title ?? null;
        setSessionsByProject((prev) => {
          const list = prev[projectId];
          if (!list) return prev;
          let changed = false;
          const next = list.map((s) => {
            if (s.id === data.session_id && s.title !== nextTitle) {
              changed = true;
              return { ...s, title: nextTitle };
            }
            return s;
          });
          return changed ? { ...prev, [projectId]: next } : prev;
        });
      },
      // Recolor the session dot when its agent-runtime status changes. The
      // event carries only session_id, so find the project list holding it and
      // patch that row in place (mirrors the title patch above).
      onSessionStatus: (data) => {
        setSessionsByProject((prev) => {
          let changed = false;
          const next: Record<string, WorkbenchSession[]> = {};
          for (const [projectId, list] of Object.entries(prev)) {
            let listChanged = false;
            const patched = list.map((s) => {
              if (s.id === data.session_id && s.agent_status !== data.agent_status) {
                listChanged = true;
                return { ...s, agent_status: data.agent_status };
              }
              return s;
            });
            next[projectId] = listChanged ? patched : list;
            if (listChanged) changed = true;
          }
          return changed ? next : prev;
        });
      },
    });
    return disconnect;
  }, [api, fetchProjects, fetchSessions]);

  const toggleExpanded = useCallback(
    (projectId: string) => {
      setExpanded((prev) => {
        const next = new Set(prev);
        if (next.has(projectId)) {
          next.delete(projectId);
        } else {
          next.add(projectId);
          if (!sessionsByProject[projectId]) {
            fetchSessions(projectId);
          }
        }
        return next;
      });
    },
    [fetchSessions, sessionsByProject],
  );

  const createSessionForProject = useCallback(
    async (projectId: string) => {
      setCreatingSession((prev) => {
        const next = new Set(prev);
        next.add(projectId);
        return next;
      });
      // Whether this project's session list is already cached. If not, we must
      // NOT seed a partial cache below: toggleExpanded treats any cached entry
      // as "already loaded" and would never fetch the project's existing
      // sessions, making them vanish until a full refresh.
      const alreadyLoaded = sessionsByProject[projectId] !== undefined;
      try {
        // Omit agent_backend so the server defers to the configured default
        // Vibe Agent rather than pinning a hard-coded backend.
        const session = await api.createSession({ project_id: projectId });
        if (alreadyLoaded) {
          // Optimistically prepend so the user sees it before any refetch.
          setSessionsByProject((prev) => ({
            ...prev,
            [projectId]: [session, ...(prev[projectId] ?? [])],
          }));
        }
        setExpanded((prev) => {
          if (prev.has(projectId)) return prev;
          const next = new Set(prev);
          next.add(projectId);
          return next;
        });
        if (!alreadyLoaded) {
          // Load the full list (which now includes the new session) instead of
          // seeding a one-item cache that hides the pre-existing sessions.
          fetchSessions(projectId);
        }
        navigate(`/chat/${encodeURIComponent(session.id)}`);
      } catch (err: any) {
        // No toast service available here without prop drilling — fall back
        // to opening the project so the user notices nothing happened.
        console.error('[sidebar] create session failed', err);
      } finally {
        setCreatingSession((prev) => {
          const next = new Set(prev);
          next.delete(projectId);
          return next;
        });
      }
    },
    [api, navigate, fetchSessions, sessionsByProject],
  );

  const onSessionMarkRead = useCallback(
    (sessionId: string) => {
      markRead(sessionId);
    },
    [markRead],
  );

  const renameProject = useCallback(
    async (projectId: string, newName: string) => {
      try {
        const updated = await api.updateProject(projectId, { display_name: newName });
        setProjects((prev) =>
          prev ? prev.map((p) => (p.id === projectId ? updated : p)) : prev,
        );
      } catch (err) {
        console.error('[sidebar] rename project failed', err);
      }
    },
    [api],
  );

  const archiveProject = useCallback(
    async (projectId: string) => {
      try {
        await api.archiveProject(projectId);
        // Drop from the visible list. Sessions stay in the DB; user can
        // still reach them by URL or by un-archiving via the CLI for now.
        setProjects((prev) => (prev ? prev.filter((p) => p.id !== projectId) : prev));
        setExpanded((prev) => {
          if (!prev.has(projectId)) return prev;
          const next = new Set(prev);
          next.delete(projectId);
          return next;
        });
      } catch (err) {
        console.error('[sidebar] archive project failed', err);
      }
    },
    [api],
  );

  // Small open/close delays so the popover doesn't flicker as the cursor
  // brushes through the inbox row on its way somewhere else, and survives
  // the gap between the row and the popover body.
  const openPopover = () => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
    setPopoverOpen(true);
  };
  const queueClose = () => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
    }
    closeTimer.current = window.setTimeout(() => {
      setPopoverOpen(false);
      closeTimer.current = null;
    }, 180);
  };
  useEffect(() => {
    return () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    };
  }, []);

  const onItemClick = (session: InboxSession) => {
    setPopoverOpen(false);
    navigate(`/chat/${encodeURIComponent(session.session_id)}`);
    if ((unreadBySession[session.session_id] ?? 0) > 0) markRead(session.session_id);
  };

  const onMarkAllRead = async () => {
    // Mark every session that still has unread agent replies. The unread map is
    // pagination-independent, so this clears sessions beyond the first page too.
    const ids = Object.entries(unreadBySession)
      .filter(([, n]) => (n || 0) > 0)
      .map(([id]) => id);
    await Promise.all(ids.map((id) => markRead(id)));
  };

  const badge = useMemo(() => {
    if (totalUnread <= 0) return null;
    return totalUnread > 99 ? '99+' : String(totalUnread);
  }, [totalUnread]);

  return (
    <div className="flex flex-col gap-4">
      {/* Inbox entry — hover opens the floating popover. */}
      <div
        className="relative"
        onMouseEnter={openPopover}
        onMouseLeave={queueClose}
      >
        <NavLink
          to="/inbox"
          className={({ isActive }) =>
            clsx(
              'group flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-[13px] font-semibold transition-colors',
              // Cyan active state per design.pen ze15A — mint is reserved
              // for sessions / projects so the two reads stay distinct.
              isActive
                ? 'border-cyan/40 bg-cyan-soft text-foreground shadow-[0_0_16px_-4px_rgba(63,224,229,0.5)]'
                : 'border-border-strong text-foreground hover:bg-foreground/[0.04]',
            )
          }
        >
          {({ isActive }) => (
            <>
              <Inbox className={clsx('size-4', isActive ? 'text-cyan' : 'text-foreground')} />
              <span className="flex-1">{t('workbench.nav.inbox')}</span>
              {badge && (
                <span className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-cyan px-1.5 py-0.5 font-mono text-[9px] font-bold text-[#080812] shadow-[0_0_10px_-2px_rgba(63,224,229,0.7)]">
                  {badge}
                </span>
              )}
              <ChevronRight className="size-3.5 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
            </>
          )}
        </NavLink>
        <InboxHoverPopover
          visible={popoverOpen}
          sessions={inboxSessions}
          unreadBySession={unreadBySession}
          unreadSessions={unreadSessions}
          totalUnread={totalUnread}
          onItemClick={onItemClick}
          onMarkAllRead={onMarkAllRead}
          onMouseEnter={openPopover}
          onMouseLeave={queueClose}
        />
      </div>

      <div className="flex flex-col gap-2">
        <div className="px-1 font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-muted">
          {t('workbench.capabilitiesLabel')}
        </div>
        <nav className="flex flex-col gap-0.5">
          {CAPABILITY_NAV.map(({ to, i18nKey, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                clsx(
                  'group flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-[13px] font-medium transition-colors',
                  isActive
                    ? 'border border-mint/30 bg-mint/[0.08] text-foreground shadow-[0_0_16px_-4px_rgba(91,255,160,0.5)]'
                    : 'border border-transparent text-muted hover:bg-foreground/[0.04] hover:text-foreground',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className={clsx('size-4', isActive ? 'text-mint' : 'text-muted group-hover:text-foreground')} />
                  <span>{t(i18nKey)}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>
      </div>

      {/* Projects section — design.pen b8wX2. Header row carries the
          "Projects" label on the left (matching the Capabilities label
          style) and the 22x22 add button on the right. */}
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between px-1">
          <span className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-muted">
            {t('workbench.projectsLabel')}
          </span>
          {/* Borderless ghost icon button (design-system Button) — bumped from
              a 22px bordered box to a roomier 28px tap target. */}
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="size-7 shrink-0 text-muted hover:text-foreground"
            aria-label={t('workbench.addProject')}
            onClick={() => setShowNewProject(true)}
          >
            <FolderPlus className="size-4" />
          </Button>
        </div>

        <div className="flex flex-col gap-0.5">
          {projects === null && !projectsError && (
            <div className="flex items-center gap-2 rounded-md border border-dashed border-border px-3 py-3 text-[11px] text-muted">
              <Loader2 className="size-3 animate-spin" />
              {t('workbench.projectsLoading')}
            </div>
          )}
          {projectsError && (
            <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[11px] text-destructive">
              {t('workbench.projectsLoadError')}
            </div>
          )}
          {projects !== null && projects.length === 0 && (
            <div className="flex flex-col items-center gap-1.5 rounded-md border border-dashed border-border px-3 py-4 text-center">
              <Folder className="size-4 text-muted" />
              <div className="text-[11px] text-muted">{t('workbench.projectsEmpty')}</div>
            </div>
          )}
          {projects !== null &&
            projects.map((project) => (
              <ProjectRow
                key={project.id}
                project={project}
                expanded={expanded.has(project.id)}
                sessions={sessionsByProject[project.id] ?? null}
                loading={!!sessionsLoading[project.id]}
                loadingMore={!!sessionsLoadingMore[project.id]}
                hasMore={!!sessionCursor[project.id]}
                onLoadMore={() => fetchSessions(project.id, { append: true })}
                onToggle={() => toggleExpanded(project.id)}
                onCreateSession={() => createSessionForProject(project.id)}
                creatingSession={creatingSession.has(project.id)}
                unreadBySession={unreadBySession}
                onSessionMarkRead={onSessionMarkRead}
                onRename={(next) => renameProject(project.id, next)}
                onArchive={() => archiveProject(project.id)}
                onRenameSession={async (sessionId, title) => {
                  const nextTitle = title || null;
                  // Reflect immediately (optimistic) so the row updates the
                  // instant the user commits; the session.activity 'updated'
                  // event then confirms the same value from the server.
                  setSessionsByProject((prev) => {
                    const list = prev[project.id];
                    if (!list) return prev;
                    return {
                      ...prev,
                      [project.id]: list.map((s) =>
                        s.id === sessionId ? { ...s, title: nextTitle } : s,
                      ),
                    };
                  });
                  await api.updateSession(sessionId, { title: nextTitle });
                }}
              />
            ))}
        </div>
      </div>

      {showNewProject && (
        <NewProjectDialog
          onClose={() => setShowNewProject(false)}
          onCreated={(project) => {
            setShowNewProject(false);
            // Opening a folder we already track returns the existing project
            // (idempotent by path), refreshed (revived / last_active_at bumped).
            // Drop any stale copy and hoist the fresh one to the top instead of
            // adding a duplicate row.
            setProjects((prev) => {
              if (!prev) return [project];
              return [project, ...prev.filter((p) => p.id !== project.id)];
            });
            setExpanded((prev) => {
              const next = new Set(prev);
              next.add(project.id);
              return next;
            });
            // Load sessions only if we don't already have them cached. A new or
            // restored project has no cache yet, so this fetches its real list;
            // an already-open project keeps the pages the user already paged in
            // instead of being truncated back to the first page.
            if (sessionsByProject[project.id] === undefined) {
              fetchSessions(project.id);
            }
          }}
        />
      )}
    </div>
  );
};

// Re-export for tests / future inbox-specific UIs.
export { InboxHoverPopover };
