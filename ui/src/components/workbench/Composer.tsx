import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Send, Square } from 'lucide-react';

import { cn } from '../../lib/utils';
import { Button } from '../ui/button';

export interface ComposerProps {
  /** Fired with the trimmed text when the user sends. Return (or resolve to)
   *  ``false`` to signal the send couldn't start, so the box keeps the text. */
  onSend: (text: string) => boolean | void | Promise<boolean | void>;
  /** A turn is running — the send button becomes a Stop button. */
  busy?: boolean;
  /** Pressed while busy. */
  onStop?: () => void;
  /** Seed the box once from a saved draft (chat sessions). */
  initialDraft?: string | null;
  /** Persist draft changes (chat sessions). */
  onDraftChange?: (text: string) => void;
  /** Idle placeholder override; while busy the chat "working" placeholder wins. */
  placeholder?: string;
  /** Disable sending (e.g. while the caller creates a session + navigates). */
  disabled?: boolean;
  /** Override the row container — e.g. a narrower max-width on the home canvas. */
  className?: string;
}

// The chat-style input row: an auto-growing textarea + a Send/Stop icon button.
// Shared by the chat view (ChatPage) and the Workbench home so both use one
// input component instead of each hand-rolling its own. Owns its draft value;
// callers react via onSend / onDraftChange. design.pen kxEkn.
export const Composer: React.FC<ComposerProps> = ({
  onSend,
  busy = false,
  onStop,
  initialDraft = null,
  onDraftChange,
  placeholder,
  disabled = false,
  className,
}) => {
  const { t } = useTranslation();
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  // Seed once from a saved draft, but only while the box is untouched so a
  // late-arriving draft can't clobber live typing.
  const draftAppliedRef = useRef(false);
  useEffect(() => {
    if (draftAppliedRef.current || initialDraft == null) return;
    draftAppliedRef.current = true;
    if (initialDraft) setValue((cur) => (cur ? cur : initialDraft));
  }, [initialDraft]);

  const trimmed = value.trim();
  const canSubmit = trimmed.length > 0 && !disabled;

  const update = (next: string) => {
    setValue(next);
    onDraftChange?.(next);
  };

  const submit = async () => {
    if (!canSubmit) return;
    // Clear only once the caller confirms the send started. The home composer
    // resolves false when it can't (no project yet, or a create-session error),
    // so the typed prompt is preserved for retry instead of vanishing.
    const started = await onSend(trimmed);
    if (started === false) return;
    setValue('');
    onDraftChange?.('');
  };

  return (
    <div
      className={cn(
        'mx-auto flex w-full max-w-[1080px] items-end gap-2 rounded-2xl border border-border-strong bg-surface-2 py-2 pl-3.5 pr-2 shadow-[0_-4px_24px_-12px_rgba(0,0,0,0.5)]',
        className,
      )}
    >
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => update(e.target.value)}
        onKeyDown={(e) => {
          // Enter sends; Shift+Enter inserts a newline. ``isComposing`` guards
          // against submitting mid-IME composition (CJK), where Enter commits
          // the candidate rather than the message.
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        rows={1}
        placeholder={busy ? t('chat.compose.placeholderBusy') : placeholder ?? t('chat.compose.placeholder')}
        className="max-h-40 flex-1 resize-none bg-transparent py-1.5 text-[13px] text-foreground outline-none placeholder:text-muted"
      />
      {/* 36px (size-9) icon button: pink-soft Stop while a turn runs, else a
          flat mint Send — design-system variants, not a glowy brand CTA. */}
      {busy ? (
        <Button
          type="button"
          variant="destructive-soft"
          size="icon"
          onClick={onStop}
          aria-label={t('chat.compose.stop')}
          className="size-9 shrink-0"
        >
          <Square className="size-4" />
        </Button>
      ) : (
        <Button
          type="button"
          variant="default"
          size="icon"
          onClick={submit}
          disabled={!canSubmit}
          aria-label={t('chat.compose.send')}
          className="size-9 shrink-0"
        >
          <Send className="size-4" />
        </Button>
      )}
    </div>
  );
};
