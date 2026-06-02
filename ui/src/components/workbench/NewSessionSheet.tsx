import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Folder, FolderOpen, FolderPlus } from 'lucide-react';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import type { WorkbenchProject } from '../../context/ApiContext';
import { Dialog, DialogContent, DialogTitle } from '../ui/dialog';
import { Composer } from './Composer';
import { NewProjectDialog } from './NewProjectDialog';

interface NewSessionSheetProps {
  open: boolean;
  onClose: () => void;
}

// The workbench center ＋ opens this instead of jumping to the home canvas.
// Pick a project (chips, most-recent first), describe the task, and it creates
// the session + routes to /chat with the message pre-seeded — the same flow as
// the desktop Workbench home, surfaced as a mobile bottom sheet (design.pen KSXXB).
export const NewSessionSheet: React.FC<NewSessionSheetProps> = ({ open, onClose }) => {
  const { t } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<WorkbenchProject[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [newProjectOpen, setNewProjectOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    // This component is permanently mounted by AppShell, so reset the per-submit
    // flag each time the sheet opens — otherwise a prior successful create leaves
    // `sending` true and disables the Composer on the next open.
    setSending(false);
    api
      .listProjects()
      .then((r) => {
        const sorted = r.projects
          .slice()
          .sort((a, b) => (b.last_active_at || b.created_at).localeCompare(a.last_active_at || a.created_at));
        setProjects(sorted);
        setSelectedId((prev) => prev ?? sorted[0]?.id ?? null);
      })
      .catch(() => {});
  }, [open, api]);

  const target = projects.find((p) => p.id === selectedId) ?? projects[0] ?? null;

  // Close the sheet first, THEN open the project dialog: the parent Radix Dialog
  // traps focus/pointer to its own content, so a NewProjectDialog rendered while
  // the sheet is open would be unreachable. Sheet closed → no trap → accessible.
  const openNewProject = () => {
    onClose();
    setNewProjectOpen(true);
  };

  const send = async (text: string): Promise<boolean> => {
    const trimmed = text.trim();
    if (!trimmed || sending) return false;
    if (!target) {
      openNewProject();
      return false;
    }
    setSending(true);
    try {
      // Omit agent_backend so the server routes through agents.default_backend.
      const session = await api.createSession({ project_id: target.id });
      setSending(false);
      onClose();
      navigate(`/chat/${encodeURIComponent(session.id)}`, { state: { initialMessage: trimmed } });
      return true;
    } catch {
      setSending(false);
      return false;
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
        <DialogContent className="gap-5" onOpenAutoFocus={(e) => e.preventDefault()}>
          <DialogTitle className="text-lg font-bold">{t('newSession.title')}</DialogTitle>

          <div className="flex flex-col gap-2">
            <div className="font-mono text-[11px] font-bold uppercase tracking-[0.08em] text-muted">
              {t('newSession.project')}
            </div>
            <div className="flex flex-wrap gap-2">
              {projects.slice(0, 6).map((project) => {
                const active = project.id === target?.id;
                return (
                  <button
                    key={project.id}
                    type="button"
                    onClick={() => setSelectedId(project.id)}
                    className={clsx(
                      'flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[12.5px] font-medium transition',
                      active ? 'border-mint/40 bg-mint-soft text-mint' : 'border-border-strong text-foreground hover:bg-foreground/[0.04]',
                    )}
                  >
                    {active ? <FolderOpen className="size-3.5" /> : <Folder className="size-3.5" />}
                    <span className="max-w-[140px] truncate">{project.display_name}</span>
                  </button>
                );
              })}
              <button
                type="button"
                onClick={openNewProject}
                className="flex items-center gap-1.5 rounded-full border border-border-strong px-3 py-1.5 text-[12.5px] font-medium text-muted transition hover:text-foreground"
              >
                <FolderPlus className="size-3.5" />
                {t('newSession.newProject')}
              </button>
            </div>
          </div>

          <Composer onSend={send} placeholder={t('newSession.placeholder')} disabled={sending} />
        </DialogContent>
      </Dialog>

      {/* Sibling of the sheet's Dialog (and opened only after the sheet closes)
          so the parent modal's focus trap can't make the folder picker / confirm
          step unreachable on mobile. */}
      {newProjectOpen && (
        <NewProjectDialog
          onClose={() => setNewProjectOpen(false)}
          onCreated={(project) => {
            setNewProjectOpen(false);
            setProjects((prev) => [project, ...prev.filter((p) => p.id !== project.id)]);
            setSelectedId(project.id);
          }}
        />
      )}
    </>
  );
};
