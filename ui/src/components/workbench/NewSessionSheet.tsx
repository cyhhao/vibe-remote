import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Folder, FolderOpen, FolderPlus } from 'lucide-react';
import clsx from 'clsx';

import { useNewSession } from '../../lib/useNewSession';
import { Dialog, DialogContent, DialogTitle } from '../ui/dialog';
import { Button } from '../ui/button';
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
// The create flow itself lives in the shared useNewSession hook (one source of
// truth with the home); the sheet only owns its open/close + draft lifecycle.
export const NewSessionSheet: React.FC<NewSessionSheetProps> = ({ open, onClose, onOpen }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  // active: open → the hook reloads + resets per-open (the sheet is permanently
  // mounted by AppShell, so stale submit/error state must not leak across opens).
  const ns = useNewSession({
    active: open,
    loadErrorText: t('newSession.loadError'),
    createFailedText: t('newSession.createFailed'),
  });
  // Stashed prompt: the no-project path closes the sheet (unmounting the
  // Composer) to create a project, so we hold the typed text and re-seed it
  // when the sheet reopens, instead of losing it.
  const [pendingDraft, setPendingDraft] = useState('');
  const [newProjectOpen, setNewProjectOpen] = useState(false);

  // Close the sheet first, THEN open the project dialog: the parent Radix Dialog
  // traps focus/pointer to its own content, so a NewProjectDialog rendered while
  // the sheet is open would be unreachable. Sheet closed → no trap → accessible.
  const openNewProject = () => {
    // Don't tear down the sheet for project creation while a session create is
    // in flight — the pending success would still navigate, stranding the
    // project modal over the new chat.
    if (ns.sending) return;
    onClose();
    setNewProjectOpen(true);
  };

  const send = async (text: string): Promise<boolean> => {
    const result = await ns.send(text);
    if (result) {
      setPendingDraft('');
      onClose();
      navigate(`/chat/${encodeURIComponent(result.sessionId)}`, { state: { initialMessage: result.initialMessage } });
      return true;
    }
    // No project to target → stash the prompt and route to the New Project flow.
    const trimmed = text.trim();
    if (trimmed && ns.needsProject) {
      setPendingDraft(trimmed);
      openNewProject();
    }
    return false;
  };

  return (
    <>
      <Dialog open={open} onOpenChange={(o) => { if (!o && !ns.sending) onClose(); }}>
        <DialogContent className="gap-5" onOpenAutoFocus={(e) => e.preventDefault()}>
          <DialogTitle className="text-lg font-bold">{t('newSession.title')}</DialogTitle>

          <div className="flex flex-col gap-2">
            <div className="font-mono text-[11px] font-bold uppercase tracking-[0.08em] text-muted">
              {t('newSession.project')}
            </div>
            <div className="flex flex-wrap gap-2">
              {ns.projects.slice(0, 6).map((project) => {
                const active = project.id === ns.target?.id;
                return (
                  <Button
                    key={project.id}
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => ns.setSelected(project.id)}
                    disabled={ns.sending}
                    className={clsx(
                      'h-auto gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-medium',
                      active ? 'border-mint/40 bg-mint-soft text-mint hover:bg-mint-soft hover:text-mint' : 'text-foreground',
                    )}
                  >
                    {active ? <FolderOpen className="size-3.5" /> : <Folder className="size-3.5" />}
                    <span className="max-w-[140px] truncate">{project.display_name}</span>
                  </Button>
                );
              })}
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={openNewProject}
                disabled={ns.sending}
                className="h-auto gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-medium text-muted"
              >
                <FolderPlus className="size-3.5" />
                {t('newSession.newProject')}
              </Button>
            </div>
          </div>

          {ns.error && (
            <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
              {ns.error}
            </div>
          )}

          {/* Disabled until projects load successfully, so a failed reload can't
              create a session under a stale/removed cached project. initialDraft
              re-seeds a prompt stashed when the no-project flow closed the sheet. */}
          <Composer
            onSend={send}
            placeholder={t('newSession.placeholder')}
            disabled={ns.sending || !ns.loaded}
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
            ns.upsertSelectProject(project);
            // Reopen the sheet so the user continues the new-session flow with the
            // freshly created project selected, instead of having to tap ＋ again.
            onOpen();
          }}
        />
      )}
    </>
  );
};
