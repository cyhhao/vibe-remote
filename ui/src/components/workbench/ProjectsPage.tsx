import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, Folder, FolderOpen, FolderPlus, Loader2, RotateCw } from 'lucide-react';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import type { WorkbenchProject, WorkbenchSession } from '../../context/ApiContext';
import { useWorkbenchInbox } from '../../context/WorkbenchInboxContext';
import { formatRelativeTime } from '../../lib/relativeTime';
import { NewProjectDialog } from './NewProjectDialog';

const DOT: Record<string, string> = {
  running: 'bg-mint shadow-[0_0_7px_rgba(91,255,160,0.9)]',
  failed: 'bg-destructive',
  idle: 'bg-muted',
};

const PAGE_SIZE = 50;

// Per-project session list: tracks paging cursor + load status so a transient
// failure can be retried (not cached as an empty list) and projects with >50
// active sessions can page in the rest — mirroring the desktop sidebar.
type SessionState = {
  status: 'loading' | 'loaded' | 'error';
  sessions: WorkbenchSession[];
  nextBeforeId: string | null;
};

// Mobile-only "Projects" tab (workbench). The desktop projects tree
// (WorkbenchSidebar) flattened into a full-page accordion: tap a project to
// expand its sessions, tap a session to open the chat. Design: design.pen `FW7cI`.
export const ProjectsPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const { markRead } = useWorkbenchInbox();
  const [projects, setProjects] = useState<WorkbenchProject[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [sessions, setSessions] = useState<Record<string, SessionState>>({});
  const [showNewProject, setShowNewProject] = useState(false);
  const expandedRef = useRef<Set<string>>(new Set());
  const sessionsRef = useRef<Record<string, SessionState>>({});
  const [projectsError, setProjectsError] = useState(false);

  const fetchProjects = useCallback(async () => {
    setProjectsError(false);
    try {
      const res = await api.listProjects();
      setProjects(res.projects);
    } catch {
      // Don't strand the user on an empty-state for a transient failure —
      // surface a retry instead of a false "No projects yet".
      setProjectsError(true);
    }
  }, [api]);

  useEffect(() => {
    void fetchProjects();
  }, [fetchProjects]);

  // Load (or page) a project's sessions. `beforeId` appends the next page;
  // omitting it loads the first page. On failure we store status:'error' WITHOUT
  // an empty list so the user can retry (re-expand or the Retry button) instead
  // of being stuck on a permanent "No sessions".
  const loadSessions = useCallback(
    async (projectId: string, beforeId?: string) => {
      setSessions((prev) => {
        const cur = prev[projectId];
        return {
          ...prev,
          [projectId]: {
            status: 'loading',
            sessions: cur?.sessions ?? [],
            nextBeforeId: cur?.nextBeforeId ?? null,
          },
        };
      });
      try {
        const res = await api.listSessions({ projectId, status: 'active', limit: PAGE_SIZE, beforeId });
        setSessions((prev) => {
          const existing = beforeId ? prev[projectId]?.sessions ?? [] : [];
          // Dedupe against already-loaded ids: a page can overlap if a session's
          // last_active_at shifts between requests or Load more is double-tapped.
          const seen = new Set(existing.map((s) => s.id));
          const merged = [...existing, ...res.sessions.filter((s) => !seen.has(s.id))];
          return {
            ...prev,
            [projectId]: { status: 'loaded', sessions: merged, nextBeforeId: res.next_before_id },
          };
        });
      } catch {
        setSessions((prev) => ({
          ...prev,
          [projectId]: {
            status: 'error',
            sessions: prev[projectId]?.sessions ?? [],
            nextBeforeId: prev[projectId]?.nextBeforeId ?? null,
          },
        }));
      }
    },
    [api],
  );

  const toggle = useCallback(
    (projectId: string) => {
      setExpanded((prev) => {
        const next = new Set(prev);
        next.has(projectId) ? next.delete(projectId) : next.add(projectId);
        return next;
      });
      // Fetch on first expand or after a prior failure; a successful load is cached.
      const cur = sessions[projectId];
      if (!cur || cur.status === 'error') void loadSessions(projectId);
    },
    [sessions, loadSessions],
  );

  const openSession = useCallback(
    (sessionId: string) => {
      // Opening a chat marks it read everywhere (matches the desktop tree + Inbox),
      // so unread badges/counts don't linger after a mobile drill-in.
      void markRead(sessionId);
      navigate(`/chat/${sessionId}`);
    },
    [markRead, navigate],
  );

  useEffect(() => {
    expandedRef.current = expanded;
  }, [expanded]);

  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);

  // Refetch a project's already-loaded window (not just page 1) so a reconnect
  // preserves paged-in rows instead of collapsing back to the first page.
  const reconcileSessions = useCallback(
    async (projectId: string, limit: number) => {
      try {
        const res = await api.listSessions({ projectId, status: 'active', limit });
        setSessions((prev) => ({
          ...prev,
          [projectId]: { status: 'loaded', sessions: res.sessions, nextBeforeId: res.next_before_id },
        }));
      } catch {
        /* keep the current window on a failed reconcile */
      }
    },
    [api],
  );

  // Keep /projects live: patch a row's status dot from session.status events and
  // re-pull expanded projects on SSE reconnect, so dots don't go stale like a
  // one-shot listSessions would (mirrors the desktop WorkbenchSidebar).
  useEffect(() => {
    const disconnect = api.connectWorkbenchEvents({
      onSessionStatus: ({ session_id, agent_status }) => {
        setSessions((prev) => {
          let changed = false;
          const next: Record<string, SessionState> = {};
          for (const [pid, st] of Object.entries(prev)) {
            let rowChanged = false;
            const updated = st.sessions.map((s) => {
              if (s.id === session_id && s.agent_status !== agent_status) {
                rowChanged = true;
                return { ...s, agent_status };
              }
              return s;
            });
            next[pid] = rowChanged ? { ...st, sessions: updated } : st;
            if (rowChanged) changed = true;
          }
          return changed ? next : prev;
        });
      },
      onConnected: () => {
        // Refetch the already-loaded window per expanded project (not page 1),
        // so paged-in rows survive a reconnect.
        for (const pid of expandedRef.current) {
          const loaded = sessionsRef.current[pid]?.sessions.length ?? 0;
          void reconcileSessions(pid, Math.max(PAGE_SIZE, loaded));
        }
      },
    });
    return disconnect;
  }, [api, reconcileSessions]);

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-3">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">{t('projects.title')}</h1>
        <button
          type="button"
          onClick={() => setShowNewProject(true)}
          className="grid size-9 place-items-center rounded-lg border border-mint/35 bg-mint/[0.08] text-mint transition hover:bg-mint/[0.14]"
          aria-label={t('projects.newProject')}
        >
          <FolderPlus className="size-4" />
        </button>
      </div>

      {projects.length === 0 && projectsError && (
        <button
          type="button"
          onClick={() => void fetchProjects()}
          className="flex items-center justify-center gap-2 rounded-xl border border-border bg-surface px-4 py-10 text-sm text-muted transition hover:text-foreground"
        >
          <RotateCw className="size-4" />
          {t('projects.loadFailed')}
        </button>
      )}
      {projects.length === 0 && !projectsError && (
        <div className="rounded-xl border border-border bg-surface px-4 py-10 text-center text-sm text-muted">
          {t('projects.empty')}
        </div>
      )}

      {projects.map((project) => {
        const open = expanded.has(project.id);
        const state = sessions[project.id];
        return (
          <div key={project.id} className="overflow-hidden rounded-xl border border-border bg-surface">
            <button
              type="button"
              onClick={() => toggle(project.id)}
              className="flex w-full items-center gap-2.5 px-4 py-3.5 text-left"
            >
              {open ? <FolderOpen className="size-4 shrink-0 text-cyan" /> : <Folder className="size-4 shrink-0 text-muted" />}
              <span className="min-w-0 flex-1 truncate text-sm font-semibold">{project.display_name}</span>
              {state?.status === 'loaded' && (
                <span className="rounded-full bg-foreground/[0.06] px-2 py-0.5 font-mono text-[10px] text-muted">
                  {state.sessions.length}
                  {state.nextBeforeId ? '+' : ''}
                </span>
              )}
              {open ? <ChevronDown className="size-4 shrink-0 text-muted" /> : <ChevronRight className="size-4 shrink-0 text-muted" />}
            </button>

            {open && (
              <div className="flex flex-col gap-0.5 border-t border-border px-2 py-2">
                {state?.status === 'loading' && state.sessions.length === 0 && (
                  <div className="flex items-center justify-center gap-2 px-3 py-3 text-[13px] text-muted">
                    <Loader2 className="size-3.5 animate-spin" />
                    {t('common.loading')}
                  </div>
                )}
                {state?.status === 'error' && state.sessions.length === 0 && (
                  <button
                    type="button"
                    onClick={() => void loadSessions(project.id)}
                    className="flex items-center justify-center gap-2 rounded-lg px-3 py-3 text-[13px] text-muted transition hover:text-foreground"
                  >
                    <RotateCw className="size-3.5" />
                    {t('projects.loadFailed')}
                  </button>
                )}
                {state?.status === 'loaded' && state.sessions.length === 0 && (
                  <div className="px-3 py-3 text-center text-[13px] text-muted">{t('projects.noSessions')}</div>
                )}
                {state?.sessions.map((session) => (
                  <button
                    key={session.id}
                    type="button"
                    onClick={() => openSession(session.id)}
                    className="flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-left transition hover:bg-foreground/[0.04]"
                  >
                    <span className={clsx('size-1.5 shrink-0 rounded-full', DOT[session.agent_status] ?? DOT.idle)} />
                    <span className="min-w-0 flex-1 truncate text-[13px] font-medium">
                      {session.title || `#${session.id.slice(-6)}`}
                    </span>
                    <span className="shrink-0 text-[10.5px] text-muted">
                      {formatRelativeTime(session.last_active_at ?? session.updated_at, t)}
                    </span>
                  </button>
                ))}
                {state?.nextBeforeId && (
                  <button
                    type="button"
                    onClick={() => void loadSessions(project.id, state.nextBeforeId!)}
                    disabled={state.status === 'loading'}
                    className="flex items-center justify-center gap-2 rounded-lg px-3 py-2.5 text-[12px] font-medium text-cyan transition hover:bg-cyan/[0.06] disabled:opacity-50"
                  >
                    {state.status === 'loading' ? <Loader2 className="size-3.5 animate-spin" /> : <ChevronDown className="size-3.5" />}
                    {t('projects.loadMore')}
                  </button>
                )}
              </div>
            )}
          </div>
        );
      })}

      {showNewProject && (
        <NewProjectDialog
          onClose={() => setShowNewProject(false)}
          onCreated={() => {
            setShowNewProject(false);
            void fetchProjects();
          }}
        />
      )}
    </div>
  );
};
