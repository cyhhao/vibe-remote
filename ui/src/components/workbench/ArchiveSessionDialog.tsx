import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Loader2 } from 'lucide-react';

import { useApi } from '../../context/ApiContext';
import { Button } from '../ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';

interface ArchivePreview {
  tasks: number;
  watches: number;
  runs: number;
}

export interface ArchiveSessionDialogProps {
  /** The session to archive; ``null`` keeps the dialog inert (no preview fetch). */
  sessionId: string | null;
  sessionTitle?: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Perform the archive (+ any caller-side state update / navigation). The
   *  dialog closes on success; on throw it stays open so the user can retry. */
  onConfirm: () => Promise<void>;
}

// Irreversible-confirm dialog for permanently archiving a session. Archive is
// terminal (no restore) and reclaims the session's bound background work, so the
// dialog spells that out and previews the exact counts before the user commits.
export function ArchiveSessionDialog({
  sessionId,
  sessionTitle,
  open,
  onOpenChange,
  onConfirm,
}: ArchiveSessionDialogProps) {
  const { t } = useTranslation();
  const api = useApi();
  const [preview, setPreview] = useState<ArchivePreview | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [busy, setBusy] = useState(false);

  // Fetch the reclaim preview when the dialog opens for a session. A failed
  // preview never blocks archiving — we just fall back to the generic warning.
  useEffect(() => {
    if (!open || !sessionId) {
      setPreview(null);
      return;
    }
    let alive = true;
    setLoadingPreview(true);
    api
      .getArchivePreview(sessionId)
      .then((counts) => {
        if (alive) setPreview(counts);
      })
      .catch(() => {
        if (alive) setPreview(null);
      })
      .finally(() => {
        if (alive) setLoadingPreview(false);
      });
    return () => {
      alive = false;
    };
  }, [open, sessionId, api]);

  const confirm = async () => {
    if (busy) return;
    setBusy(true);
    try {
      await onConfirm();
      onOpenChange(false);
    } catch {
      // apiFetch already surfaced the error toast; keep the dialog open for retry.
    } finally {
      setBusy(false);
    }
  };

  const reclaimItems = preview
    ? ([
        preview.tasks > 0 ? t('workbench.archiveSession.reclaimTasks', { n: preview.tasks }) : null,
        preview.watches > 0 ? t('workbench.archiveSession.reclaimWatches', { n: preview.watches }) : null,
        preview.runs > 0 ? t('workbench.archiveSession.reclaimRuns', { n: preview.runs }) : null,
      ].filter(Boolean) as string[])
    : [];

  const label = sessionTitle?.trim() || t('workbench.untitledSession');

  return (
    <Dialog open={open} onOpenChange={(next) => (busy ? undefined : onOpenChange(next))}>
      <DialogContent className="max-w-[420px]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="grid size-7 shrink-0 place-items-center rounded-full bg-destructive/12 text-destructive">
              <AlertTriangle className="size-4" />
            </span>
            {t('workbench.archiveSession.title')}
          </DialogTitle>
          <DialogDescription>
            {t('workbench.archiveSession.body', { name: label })}
          </DialogDescription>
        </DialogHeader>

        <div className="rounded-lg border border-destructive/25 bg-destructive/[0.05] px-3.5 py-3 text-[13px] leading-relaxed text-foreground">
          {loadingPreview ? (
            <span className="flex items-center gap-2 text-muted">
              <Loader2 className="size-3.5 animate-spin" />
              {t('workbench.archiveSession.checking')}
            </span>
          ) : reclaimItems.length > 0 ? (
            <>
              <p className="mb-1.5 font-medium text-destructive">{t('workbench.archiveSession.reclaimIntro')}</p>
              <ul className="list-disc space-y-0.5 pl-4 text-muted">
                {reclaimItems.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          ) : (
            <span className="text-muted">{t('workbench.archiveSession.noReclaim')}</span>
          )}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
            {t('common.cancel')}
          </Button>
          <Button type="button" variant="destructive" onClick={confirm} disabled={busy}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : null}
            {t('workbench.archiveSession.confirm')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
