import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, Folder, FolderOpen, FolderPlus, Loader2, RotateCw } from 'lucide-react';
import clsx from 'clsx';

import { useWorkbenchInbox } from '../../context/WorkbenchInboxContext';
import { useWorkbenchProjectsTree } from '../../context/WorkbenchProjectsContext';
import { formatRelativeTime } from '../../lib/relativeTime';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { NewProjectDialog } from './NewProjectDialog';

const DOT: Record<string, string> = {
  running: 'bg-mint shadow-[0_0_7px_rgba(91,255,160,0.9)]',
  failed: 'bg-destructive',
  idle: 'bg-muted',
};

// Mobile-only "Projects" tab (workbench): the desktop projects tree
// (WorkbenchSidebar) flattened into a full-page accordion — tap a project to
// expand its sessions, tap a session to open the chat. It shares the same data
// provider (useWorkbenchProjectsTree) as the sidebar, so loading / paging / SSE
// status+title / reconnect-reconcile / dedupe are one source of truth rather
// than a parallel reimplementation. Design: design.pen `FW7cI`.
export const ProjectsPage: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { markRead, unreadBySession } = useWorkbenchInbox();
  const {
    projects,
    projectsError,
    refreshProjects,
    sessionsOf,
    isExpanded,
    toggleExpanded,
    loadMore,
    reloadSessions,
    upsertProjectToTop,
  } = useWorkbenchProjectsTree();
  const [showNewProject, setShowNewProject] = useState(false);

  const openSession = (sessionId: string) => {
    // Opening a chat marks it read everywhere (matches the desktop tree + Inbox),
    // so unread badges/counts don't linger after a mobile drill-in.
    void markRead(sessionId);
    navigate(`/chat/${sessionId}`);
  };

  const list = projects ?? [];

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-3">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">{t('projects.title')}</h1>
        <Button
          type="button"
          variant="outline"
          size="icon"
          onClick={() => setShowNewProject(true)}
          aria-label={t('projects.newProject')}
          className="border-mint/35 bg-mint/[0.08] text-mint hover:bg-mint/[0.14]"
        >
          <FolderPlus className="size-4" />
        </Button>
      </div>

      {/* The provider often has projects cached already (it's mounted app-wide),
          so this loading flash only shows on a genuine cold start. */}
      {projects === null && !projectsError && (
        <div className="flex items-center justify-center gap-2 rounded-xl border border-border bg-surface px-4 py-10 text-sm text-muted">
          <Loader2 className="size-4 animate-spin" />
          {t('common.loading')}
        </div>
      )}
      {projectsError && list.length === 0 && (
        <button
          type="button"
          onClick={() => void refreshProjects()}
          className="flex items-center justify-center gap-2 rounded-xl border border-border bg-surface px-4 py-10 text-sm text-muted transition hover:text-foreground"
        >
          <RotateCw className="size-4" />
          {t('projects.loadFailed')}
        </button>
      )}
      {projects !== null && list.length === 0 && !projectsError && (
        <div className="rounded-xl border border-border bg-surface px-4 py-10 text-center text-sm text-muted">
          {t('projects.empty')}
        </div>
      )}

      {list.map((project) => {
        const open = isExpanded(project.id);
        const state = sessionsOf(project.id);
        const sessionRows = state.sessions ?? [];
        return (
          <div key={project.id} className="overflow-hidden rounded-xl border border-border bg-surface">
            <button
              type="button"
              onClick={() => toggleExpanded(project.id)}
              className="flex w-full items-center gap-2.5 px-4 py-3.5 text-left"
            >
              {open ? <FolderOpen className="size-4 shrink-0 text-cyan" /> : <Folder className="size-4 shrink-0 text-muted" />}
              <span className="min-w-0 flex-1 truncate text-sm font-semibold">{project.display_name}</span>
              {state.sessions !== null && !state.error && (
                <Badge variant="secondary" className="font-mono text-[10px]">
                  {state.sessions.length}
                  {state.cursor ? '+' : ''}
                </Badge>
              )}
              {open ? <ChevronDown className="size-4 shrink-0 text-muted" /> : <ChevronRight className="size-4 shrink-0 text-muted" />}
            </button>

            {open && (
              <div className="flex flex-col gap-0.5 border-t border-border px-2 py-2">
                {state.loading && sessionRows.length === 0 && (
                  <div className="flex items-center justify-center gap-2 px-3 py-3 text-[13px] text-muted">
                    <Loader2 className="size-3.5 animate-spin" />
                    {t('common.loading')}
                  </div>
                )}
                {state.error && sessionRows.length === 0 && (
                  <button
                    type="button"
                    onClick={() => reloadSessions(project.id)}
                    className="flex items-center justify-center gap-2 rounded-lg px-3 py-3 text-[13px] text-muted transition hover:text-foreground"
                  >
                    <RotateCw className="size-3.5" />
                    {t('projects.loadFailed')}
                  </button>
                )}
                {state.sessions !== null && sessionRows.length === 0 && !state.loading && !state.error && (
                  <div className="px-3 py-3 text-center text-[13px] text-muted">{t('projects.noSessions')}</div>
                )}
                {sessionRows.map((session) => {
                  const unread = unreadBySession[session.id] ?? 0;
                  return (
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
                      {unread > 0 ? (
                        <span className="shrink-0 rounded-full bg-mint px-1.5 py-0.5 font-mono text-[10px] font-bold text-background">
                          {unread > 99 ? '99+' : unread}
                        </span>
                      ) : (
                        <span className="shrink-0 text-[10.5px] text-muted">
                          {formatRelativeTime(session.last_active_at ?? session.updated_at, t)}
                        </span>
                      )}
                    </button>
                  );
                })}
                {state.cursor && (
                  <button
                    type="button"
                    onClick={() => loadMore(project.id)}
                    disabled={state.loadingMore}
                    className="flex items-center justify-center gap-2 rounded-lg px-3 py-2.5 text-[12px] font-medium text-cyan transition hover:bg-cyan/[0.06] disabled:opacity-50"
                  >
                    {state.loadingMore ? <Loader2 className="size-3.5 animate-spin" /> : <ChevronDown className="size-3.5" />}
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
          onCreated={(project) => {
            setShowNewProject(false);
            upsertProjectToTop(project);
          }}
        />
      )}
    </div>
  );
};
