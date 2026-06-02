import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Loader2, Mic, Paperclip, Send, Square, X } from 'lucide-react';
import clsx from 'clsx';

import { apiFetch } from '../../lib/apiFetch';
import { cn } from '../../lib/utils';
import { Button } from '../ui/button';

export type ComposerAttachment = {
  localId: string;
  token: string;
  name: string;
  mime: string;
  size: number;
  kind: 'image' | 'file';
  url: string;
  status: 'uploading' | 'ready' | 'error';
};

// Read a File/Blob as bare base64 (no ``data:...,`` prefix) for the JSON upload
// + transcribe endpoints — the auth/CSRF-guarded compat route parses JSON, not
// multipart, so binaries ride as base64.
function readFileAsBase64(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => {
      const result = String(reader.result || '');
      const comma = result.indexOf(',');
      resolve(comma >= 0 ? result.slice(comma + 1) : result);
    };
    reader.readAsDataURL(file);
  });
}

export interface ComposerProps {
  /** Fired with the trimmed text (+ ready attachments) when the user sends.
   *  Return (or resolve to) ``false`` to signal the send couldn't start, so the
   *  box keeps the text + attachments. */
  onSend: (
    text: string,
    attachments?: ComposerAttachment[],
  ) => boolean | void | Promise<boolean | void>;
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
  /** When set, enables file upload + voice input scoped to this session. The
   *  Workbench home leaves it unset → a plain text-only composer. */
  sessionId?: string;
}

// The chat-style input row: an auto-growing textarea + a Send/Stop icon button,
// plus (when ``sessionId`` is set) attachment upload and voice input on the left.
// Shared by the chat view (ChatPage) and the Workbench home so both use one input
// component instead of each hand-rolling its own. Owns its draft value; callers
// react via onSend / onDraftChange. design.pen kxEkn.
export const Composer: React.FC<ComposerProps> = ({
  onSend,
  busy = false,
  onStop,
  initialDraft = null,
  onDraftChange,
  placeholder,
  disabled = false,
  className,
  sessionId,
}) => {
  const { t } = useTranslation();
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const valueRef = useRef('');
  // Seed once from a saved draft, but only while the box is untouched so a
  // late-arriving draft can't clobber live typing.
  const draftAppliedRef = useRef(false);
  // Blocks a same-tick double-submit before the optimistic clear re-renders.
  const pendingRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
  const [asrAvailable, setAsrAvailable] = useState(false);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const streamRef = useRef<MediaStream | null>(null);
  const unmountedRef = useRef(false);

  // Upload + voice are scoped to a session (the upload endpoint needs one); the
  // home composer leaves them off.
  const mediaEnabled = Boolean(sessionId);

  useEffect(() => {
    if (draftAppliedRef.current || initialDraft == null) return;
    draftAppliedRef.current = true;
    if (initialDraft) setValue((cur) => (cur ? cur : initialDraft));
  }, [initialDraft]);

  // Keep a ref of the latest value so the async voice-fill can append without a
  // stale closure.
  useEffect(() => {
    valueRef.current = value;
  }, [value]);

  // Auto-grow the textarea with its content. ``min-h-9`` floors it at the 36px
  // send-button height so a single line sits vertically centered against the
  // button; ``max-h-40`` (160px) caps it, after which it scrolls.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, [value]);

  // The mic button only appears when transcription is wired up (Vibe Cloud
  // paired + enabled), so it never dead-ends on click.
  useEffect(() => {
    if (!mediaEnabled) return;
    let alive = true;
    apiFetch('/api/asr/status')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (alive) setAsrAvailable(Boolean(data?.available));
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [mediaEnabled]);

  // Release the mic + suppress post-unmount setState if the composer unmounts
  // mid-recording (it remounts on every session switch).
  useEffect(
    () => () => {
      unmountedRef.current = true;
      try {
        recorderRef.current?.stop();
      } catch {
        /* already stopped */
      }
      streamRef.current?.getTracks().forEach((track) => track.stop());
    },
    [],
  );

  const removeAttachment = (localId: string) => {
    setAttachments((cur) => cur.filter((a) => a.localId !== localId));
  };

  const uploadFiles = async (files: File[]) => {
    if (!sessionId) return;
    for (const file of files) {
      const localId = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const kind: 'image' | 'file' = file.type.startsWith('image/') ? 'image' : 'file';
      setAttachments((cur) => [
        ...cur,
        { localId, token: '', name: file.name, mime: file.type || 'application/octet-stream', size: file.size, kind, url: '', status: 'uploading' },
      ]);
      try {
        const data = await readFileAsBase64(file);
        const res = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/attachments`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: file.name, mime: file.type, data }),
        });
        const json = await res.json().catch(() => null);
        if (!res.ok || !json?.token) throw new Error('upload failed');
        setAttachments((cur) =>
          cur.map((a) =>
            a.localId === localId
              ? { ...a, token: json.token, url: json.url, mime: json.mime || a.mime, size: json.size ?? a.size, kind: json.kind || a.kind, status: 'ready' }
              : a,
          ),
        );
      } catch {
        setAttachments((cur) => cur.map((a) => (a.localId === localId ? { ...a, status: 'error' } : a)));
      }
    }
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (e) => {
        if (e.data.size) chunksRef.current.push(e.data);
      };
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        if (unmountedRef.current) return;
        setRecording(false);
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType || 'audio/webm' });
        if (!blob.size) return;
        setTranscribing(true);
        try {
          const data = await readFileAsBase64(blob);
          const res = await apiFetch('/api/asr/transcribe', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: 'voice.webm', mime: blob.type || 'audio/webm', data }),
          });
          const json = await res.json().catch(() => null);
          if (!unmountedRef.current && res.ok && json?.text) {
            // Append the transcript into the box (never auto-send) via the draft
            // path so it persists if the user switches away before sending.
            const next = valueRef.current ? `${valueRef.current} ${json.text}` : String(json.text);
            update(next);
          }
        } finally {
          if (!unmountedRef.current) setTranscribing(false);
        }
      };
      recorderRef.current = recorder;
      recorder.start();
      setRecording(true);
    } catch {
      // getUserMedia may have handed us a live stream before MediaRecorder
      // construction / start() threw — release it so the mic doesn't stay on.
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      setRecording(false);
    }
  };

  const toggleRecording = () => {
    if (recording) recorderRef.current?.stop();
    else void startRecording();
  };

  const trimmed = value.trim();
  const readyAttachments = attachments.filter((a) => a.status === 'ready');
  const uploading = attachments.some((a) => a.status === 'uploading');
  // Send on text OR a ready attachment (attachment-only runs a turn so the agent
  // reads the files); blocked while an upload is still in flight.
  const canSubmit = (trimmed.length > 0 || readyAttachments.length > 0) && !uploading && !disabled;

  const update = (next: string) => {
    setValue(next);
    onDraftChange?.(next);
  };

  const submit = async () => {
    if (!canSubmit || pendingRef.current) return;
    const submitted = trimmed;
    const sent = readyAttachments;
    pendingRef.current = true;
    // Clear optimistically so the box can't be re-submitted and a slow send can't
    // wipe text typed in the meantime.
    setValue('');
    onDraftChange?.('');
    setAttachments([]);
    try {
      // If the caller reports the send couldn't start (home no-project nudge),
      // restore the prompt + attachments for retry — unless the user typed anew.
      const started = await onSend(submitted, sent);
      if (started === false) {
        setValue((cur) => (cur ? cur : submitted));
        setAttachments((cur) => (cur.length ? cur : sent));
      }
    } finally {
      pendingRef.current = false;
    }
  };

  return (
    <div className={cn('mx-auto flex w-full max-w-[1080px] flex-col gap-2', className)}>
      {mediaEnabled && attachments.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {attachments.map((att) => (
            <div
              key={att.localId}
              className="flex items-center gap-2 rounded-lg border border-border bg-surface-2 py-1 pl-1.5 pr-1 text-[12px]"
            >
              {att.status === 'uploading' ? (
                <span className="grid size-7 place-items-center rounded text-muted">
                  <Loader2 className="size-4 animate-spin" />
                </span>
              ) : att.kind === 'image' && att.url ? (
                <img src={att.url} alt="" className="size-7 rounded object-cover" />
              ) : (
                <span className="grid size-7 place-items-center rounded bg-cyan/15 text-cyan">
                  <Paperclip className="size-3.5" />
                </span>
              )}
              <span className={clsx('max-w-[160px] truncate', att.status === 'error' ? 'text-pink' : 'text-foreground')}>
                {att.name}
              </span>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => removeAttachment(att.localId)}
                aria-label={t('chat.compose.removeAttachment')}
                className="size-5 shrink-0 text-muted hover:text-foreground"
              >
                <X className="size-3.5" />
              </Button>
            </div>
          ))}
        </div>
      )}
      <div
        className={cn(
          'flex w-full items-end gap-2 rounded-2xl border border-border-strong bg-surface-2 py-2 pr-2 shadow-[0_-4px_24px_-12px_rgba(0,0,0,0.5)]',
          mediaEnabled ? 'pl-2' : 'pl-3.5',
        )}
      >
        {mediaEnabled && (
          <>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={(e) => {
                if (e.target.files?.length) void uploadFiles(Array.from(e.target.files));
                e.target.value = '';
              }}
            />
            <Button
              type="button"
              variant="ghost"
              size="icon"
              onClick={() => fileInputRef.current?.click()}
              aria-label={t('chat.compose.attach')}
              className="size-9 shrink-0"
            >
              <Paperclip className="size-4" />
            </Button>
            {asrAvailable && (
              <Button
                type="button"
                variant={recording ? 'destructive-soft' : 'ghost'}
                size="icon"
                onClick={toggleRecording}
                disabled={transcribing}
                aria-label={t(recording ? 'chat.compose.stopRecording' : 'chat.compose.voice')}
                className={clsx('size-9 shrink-0', recording && 'animate-pulse')}
              >
                {transcribing ? <Loader2 className="size-4 animate-spin" /> : <Mic className="size-4" />}
              </Button>
            )}
          </>
        )}
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
          className="max-h-40 min-h-9 flex-1 resize-none bg-transparent py-2 text-[13px] leading-5 text-foreground outline-none placeholder:text-muted"
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
    </div>
  );
};
