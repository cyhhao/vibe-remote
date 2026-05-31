import * as React from 'react';
import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { Dialog, DialogContent, DialogDescription, DialogTitle } from './dialog';
import { SegmentedRadio } from './segmented';
import { Button } from './button';
import { Textarea } from './textarea';
import { Markdown } from './markdown';

type EditorMode = 'edit' | 'preview';

export interface EditorDialogProps {
  open: boolean;
  onClose: () => void;
  /** Heading shown at the top-left of the modal. */
  title: string;
  /** Optional one-line subtitle under the title. */
  description?: string;
  /** Current text; the modal edits a private draft seeded from this on open. */
  value: string;
  /** Commit the edited text. Called on Save, just before the modal closes. */
  onSave: (value: string) => void;
  placeholder?: string;
  /** Show the Edit/Preview toggle + Markdown preview (default true). */
  markdownPreview?: boolean;
  /** Optional left-aligned footer node derived from the live draft (e.g. a
   *  token/char count). */
  footerHint?: (draft: string) => React.ReactNode;
}

// Generic full-size text editor in a modal: a large monospace textarea with an
// optional Edit/Preview Markdown toggle, reusing the shared <Markdown> renderer
// and the .vr-markdown styling. Built for agent system prompts first, but kept
// field-agnostic (title / value / onSave) so any long-text field can adopt it
// and grow here later toward a real editor — syntax highlighting, code, docs.
export const EditorDialog: React.FC<EditorDialogProps> = ({
  open,
  onClose,
  title,
  description,
  value,
  onSave,
  placeholder,
  markdownPreview = true,
  footerHint,
}) => {
  const { t } = useTranslation();
  const [draft, setDraft] = useState(value);
  const [mode, setMode] = useState<EditorMode>('edit');

  // Reseed the draft (and reset to edit mode) each time the modal opens, so a
  // cancelled edit never leaks into the next open and an external change to
  // ``value`` between opens is picked up.
  useEffect(() => {
    if (open) {
      setDraft(value);
      setMode('edit');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const handleSave = () => {
    onSave(draft);
    onClose();
  };

  const showPreview = markdownPreview && mode === 'preview';

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <DialogContent className="flex h-[82vh] w-[92vw] max-w-[920px] flex-col gap-0 overflow-hidden p-0">
        {/* Header — title + optional subtitle. pr-12 leaves room for the
            Dialog's built-in close X (absolute, top-right). */}
        <div className="flex flex-col gap-1 border-b border-border px-5 py-4 pr-12">
          <DialogTitle className="text-[15px] font-bold text-foreground">{title}</DialogTitle>
          {description && (
            <DialogDescription className="text-[12px] leading-relaxed text-muted">
              {description}
            </DialogDescription>
          )}
        </div>

        {/* Toolbar — Edit/Preview toggle + Markdown hint (preview-capable only). */}
        {markdownPreview && (
          <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-2.5">
            <div className="w-[200px]">
              <SegmentedRadio<EditorMode>
                value={mode}
                onChange={setMode}
                ariaLabel={t('editor.modeLabel')}
                options={[
                  { id: 'edit', label: t('editor.edit') },
                  { id: 'preview', label: t('editor.preview') },
                ]}
              />
            </div>
            <span className="font-mono text-[10px] text-muted">{t('editor.markdownHint')}</span>
          </div>
        )}

        {/* Body — editor or preview, fills the remaining height. */}
        <div className="min-h-0 flex-1 overflow-hidden p-5">
          {showPreview ? (
            <div className="h-full overflow-auto rounded-lg border border-border bg-surface-2 px-4 py-3">
              {draft.trim() ? (
                <Markdown content={draft} />
              ) : (
                <span className="text-[12px] text-muted">{t('editor.previewEmpty')}</span>
              )}
            </div>
          ) : (
            <Textarea
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                // Cmd/Ctrl+Enter saves from anywhere in the textarea.
                if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                  e.preventDefault();
                  handleSave();
                }
              }}
              placeholder={placeholder}
              className="h-full resize-none font-mono text-[13px] leading-relaxed"
            />
          )}
        </div>

        {/* Footer — optional draft-derived hint + actions. */}
        <div className="flex items-center justify-between gap-3 border-t border-border px-5 py-3">
          <span className="text-[11px] text-muted">{footerHint?.(draft)}</span>
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" size="sm" onClick={onClose}>
              {t('common.cancel')}
            </Button>
            <Button type="button" variant="brand" size="sm" onClick={handleSave}>
              {t('common.save')}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
};
