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
  onOpen: () => void;
}

// The workbench center ＋ opens this instead of jumping to the home canvas.
// Pick a project (chips, most-recent first), describe the task, and it creates
// the session + routes to /chat with the message pre-seeded — the same flow as
// the desktop Workbench home, surfaced as a mobile bottom sheet (design.pen KSXXB).
export const NewSessionSheet: React.FC<NewSessionSheetProps> = ({ open, onClose, onOpen }) => {
  const { t } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const [projects, setProjects] = useState<WorkbenchProject[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  // Whether listProjects has succeeded since this open — distinguishes a real
  // "no projects" state from a transient load failure (so we don't push a user
  // who already has projects into the New Project flow on a flaky network).
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Stashed prompt: the no-project path closes the sheet (unmounting the
  // Composer) to create a project, so we hold the typed text and re-seed it
  // when the sheet reopens, instead of losing it.
  const [pendingDraft, setPendingDraft] = useState('');
  const [newProjectOpen, setNewProjectOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    // Permanently mounted by AppShell: reset per-open state so a prior submit /
    // error doesn't leak into the next open.
    setSending(false);
    setError(null);
    setLoaded(false);
    api
      .listProjects()
      .then((r) => {
        // A newer open (close → reopen, e.g. right after creating a project)
        // superseded this load; don't let the stale response overwrite it.
        if (cancelled) return;
        const sorted = r.projects
          .slice()
          .sort((a, b) => (b.last_active_at || b.created_at).localeCompare(a.last_active_at || a.created_at));
        setProjects(sorted);
        // Keep the current pick if it's still in the visible (first-6) set — e.g.
        // a project just created in this flow, which sorts to the top — otherwise
        // reset to the first visible (most-recent) one, so a stale id outside the
        // rendered chips can't leave no active chip while send() targets a hidden
        // project.
        setSelectedId((prev) => {
          const visible = sorted.slice(0, 6);
          return prev && visible.some((p) => p.id === prev) ? prev : sorted[0]?.id ?? null;
        });
        setLoaded(true);
      })
      .catch(() => {
        if (!cancelled) setError(t('newSession.loadError'));
      });
    return () => {
      cancelled = true;
    };
  }, [open, api, t]);

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
    // Never create from a stale cached list: require a successful project load.
    if (!loaded) return false;
    if (!target) {
      // Stash the prompt so it survives the sheet closing for project creation,
      // then route to the New Project flow.
      setPendingDraft(trimmed);
      openNewProject();
      return false;
    }
    setSending(true);
    setError(null);
    try {
      // Omit agent_backend so the server routes through agents.default_backend.
      const session = await api.createSession({ project_id: target.id });
      setSending(false);
      setPendingDraft('');
      onClose();
      navigate(`/chat/${encodeURIComponent(session.id)}`, { state: { initialMessage: trimmed } });
      return true;
    } catch (err: any) {
      setSending(false);
      setError(err?.message ?? t('newSession.createFailed'));
      return false;
    }
  };

  return (
    <>
      <Dialog open={open} onOpenChange={(o) => { if (!o && !sending) onClose(); }}>
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

          {error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
              {error}
            </div>
          )}

          {/* Disabled until projects load successfully, so a failed reload can't
              create a session under a stale/removed cached project. initialDraft
              re-seeds a prompt stashed when the no-project flow closed the sheet. */}
          <Composer
            onSend={send}
            placeholder={t('newSession.placeholder')}
            disabled={sending || !loaded}
            initialDraft={pendingDraft}
          />
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
            // Reopen the sheet so the user continues the new-session flow with the
            // freshly created project selected, instead of having to tap ＋ again.
            onOpen();
          }}
        />
      )}
    </>
  );
};
