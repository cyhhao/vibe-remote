import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ChevronDown, ChevronRight, Folder, FolderOpen, FolderPlus } from 'lucide-react';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import type { WorkbenchProject, WorkbenchSession } from '../../context/ApiContext';
import { formatRelativeTime } from '../../lib/relativeTime';
import { NewProjectDialog } from './NewProjectDialog';

const DOT: Record<string, string> = {
  running: 'bg-mint shadow-[0_0_7px_rgba(91,255,160,0.9)]',
  failed: 'bg-destructive',
  idle: 'bg-muted',
};

// Mobile-only "Projects" tab (workbench). The desktop projects tree
// (WorkbenchSidebar) flattened into a full-page accordion: tap a project to
// expand its sessions, tap a session to open the chat. Design: design.pen `FW7cI`.
export const ProjectsPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<WorkbenchProject[]>([]);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [sessions, setSessions] = useState<Record<string, WorkbenchSession[]>>({});
  const [showNewProject, setShowNewProject] = useState(false);

  const fetchProjects = useCallback(async () => {
    try {
      const res = await api.listProjects();
      setProjects(res.projects);
    } catch {
      /* surfaced elsewhere */
    }
  }, [api]);

  useEffect(() => {
    void fetchProjects();
  }, [fetchProjects]);

  const toggle = useCallback(
    async (projectId: string) => {
      setExpanded((prev) => {
        const next = new Set(prev);
        next.has(projectId) ? next.delete(projectId) : next.add(projectId);
        return next;
      });
      if (!sessions[projectId]) {
        try {
          const res = await api.listSessions({ projectId, status: 'active' });
          setSessions((prev) => ({ ...prev, [projectId]: res.sessions }));
        } catch {
          setSessions((prev) => ({ ...prev, [projectId]: [] }));
        }
      }
    },
    [api, sessions]
  );

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

      {projects.length === 0 && (
        <div className="rounded-xl border border-border bg-surface px-4 py-10 text-center text-sm text-muted">
          {t('projects.empty')}
        </div>
      )}

      {projects.map((project) => {
        const open = expanded.has(project.id);
        const projectSessions = sessions[project.id];
        return (
          <div key={project.id} className="overflow-hidden rounded-xl border border-border bg-surface">
            <button
              type="button"
              onClick={() => void toggle(project.id)}
              className="flex w-full items-center gap-2.5 px-4 py-3.5 text-left"
            >
              {open ? <FolderOpen className="size-4 shrink-0 text-cyan" /> : <Folder className="size-4 shrink-0 text-muted" />}
              <span className="min-w-0 flex-1 truncate text-sm font-semibold">{project.display_name}</span>
              {projectSessions && (
                <span className="rounded-full bg-foreground/[0.06] px-2 py-0.5 font-mono text-[10px] text-muted">
                  {projectSessions.length}
                </span>
              )}
              {open ? <ChevronDown className="size-4 shrink-0 text-muted" /> : <ChevronRight className="size-4 shrink-0 text-muted" />}
            </button>

            {open && (
              <div className="flex flex-col gap-0.5 border-t border-border px-2 py-2">
                {projectSessions && projectSessions.length === 0 && (
                  <div className="px-3 py-3 text-center text-[13px] text-muted">{t('projects.noSessions')}</div>
                )}
                {projectSessions?.map((session) => (
                  <button
                    key={session.id}
                    type="button"
                    onClick={() => navigate(`/chat/${session.id}`)}
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
