import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Bot, ChevronDown, Loader2, MessageSquare, Pencil, Send, StopCircle } from 'lucide-react';
import clsx from 'clsx';

import { Info } from 'lucide-react';

import { useApi } from '../../context/ApiContext';
import type { VibeAgentBrief, WorkbenchMessage, WorkbenchSession } from '../../context/ApiContext';
import { apiFetch } from '../../lib/apiFetch';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';

interface PendingChunk {
  id: string;
  kind: string;
  text: string;
  message_id: string | null;
}

const EFFORT_OPTIONS = ['low', 'medium', 'high', 'max'];

// Mirrors design.pen kxEkn — the inline header replaces the old "Session
// settings" dialog. Title is click-to-edit; the cyan-bordered pill on the
// right opens a single popover that drives agent / model / effort all at
// once so the user doesn't have to navigate three different menus.
export const ChatPage: React.FC = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const api = useApi();
  const [session, setSession] = useState<WorkbenchSession | null>(null);
  const [agents, setAgents] = useState<VibeAgentBrief[]>([]);
  const [messages, setMessages] = useState<WorkbenchMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [streamChunks, setStreamChunks] = useState<PendingChunk[]>([]);
  const [composing, setComposing] = useState(false);
  // Guards the one-shot replay of a message handed off from the Workbench
  // canvas (see the initial-message effect below).
  const initialMessageSentRef = useRef(false);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      const [fetched, agentList, msgs] = await Promise.all([
        api.getSession(sessionId),
        api.listVibeAgents({ includeDisabled: false }),
        api.listSessionMessages(sessionId, { limit: 50 }),
      ]);
      setSession(fetched);
      setAgents(agentList.agents);
      setMessages(msgs.messages);
    } catch (err: any) {
      setError(err?.message ?? String(err));
    } finally {
      setLoading(false);
    }
  }, [api, sessionId]);

  const sendMessage = useCallback(
    async (text: string) => {
      if (!sessionId || !text.trim() || composing) return;
      setComposing(true);
      setStreamChunks([]);
      setError(null);
      try {
        // ``apiFetch`` attaches the CSRF token cookie + header that
        // ``protect_mutating_ui_requests`` requires under remote-access
        // mode. Raw ``fetch`` would 403 here.
        const response = await apiFetch(
          `/api/sessions/${encodeURIComponent(sessionId)}/messages?stream=1`,
          {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
          },
        );
        if (!response.ok || !response.body) {
          throw new Error(`HTTP ${response.status}`);
        }
        const reader = response.body
          .pipeThrough(new TextDecoderStream())
          .getReader();
        let buf = '';
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += value;
          // SSE frame boundary is a blank line.
          let idx = buf.indexOf('\n\n');
          while (idx !== -1) {
            const frame = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            handleSSEFrame(frame);
            idx = buf.indexOf('\n\n');
          }
        }
      } catch (err: any) {
        setError(err?.message ?? String(err));
      } finally {
        setComposing(false);
        // The mirror writes the agent reply into the messages table, so a
        // refresh after the stream settles drops the optimistic chunks
        // in favour of the persisted row(s).
        refresh();
      }
    },
    [sessionId, composing, refresh],
  );

  const stopMessage = useCallback(async () => {
    if (!sessionId || !composing) return;
    try {
      await api.cancelSession(sessionId);
    } catch (err: any) {
      // The fetch already swallows non-2xx; an exception here means the
      // request itself failed. Surface it so the user knows the stop
      // didn't reach the controller and they may have to wait the turn
      // out instead.
      setError(err?.message ?? String(err));
    }
  }, [api, sessionId, composing]);

  const handleSSEFrame = useCallback((frame: string) => {
    let event = 'message';
    let data: any = null;
    for (const rawLine of frame.split('\n')) {
      const line = rawLine.trimEnd();
      if (line.startsWith('event:')) {
        event = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        try {
          data = JSON.parse(line.slice(5).trimStart());
        } catch {
          /* ignore malformed line */
        }
      }
    }
    if (data === null) return;
    if (event === 'stream.start') {
      // Append the persisted user message immediately so the transcript
      // shows it before the agent replies.
      const userMessage = data?.user_message;
      if (userMessage) {
        setMessages((prev) =>
          prev.some((m) => m.id === userMessage.id) ? prev : [...prev, userMessage],
        );
      }
    } else if (event === 'turn.chunk') {
      setStreamChunks((prev) => [
        ...prev,
        {
          id: `pending-${prev.length}`,
          kind: String(data?.kind ?? 'chunk'),
          text: String(data?.text ?? ''),
          message_id: data?.message_id ?? null,
        },
      ]);
    } else if (event === 'stream.error') {
      setError(data?.detail ?? data?.reason ?? 'stream error');
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // The Workbench canvas creates the session and hands its first message
  // over as router state. Replay it once through the streaming compose path
  // so the agent turn actually starts. Clear the state afterwards so a manual
  // page refresh (which preserves history state) doesn't resend it.
  useEffect(() => {
    const initialMessage = (location.state as { initialMessage?: string } | null)?.initialMessage;
    if (!initialMessage || initialMessageSentRef.current) return;
    if (loading || !session) return;
    initialMessageSentRef.current = true;
    navigate(location.pathname, { replace: true, state: null });
    void sendMessage(initialMessage);
  }, [location.state, location.pathname, loading, session, navigate, sendMessage]);

  const patch = useCallback(
    async (changes: Partial<WorkbenchSession>) => {
      if (!session) return;
      try {
        const updated = await api.updateSession(session.id, changes as any);
        setSession(updated);
      } catch (err: any) {
        setError(err?.message ?? String(err));
      }
    },
    [api, session],
  );

  if (!sessionId) {
    return <ChatMissing onBack={() => navigate('/inbox')} />;
  }

  if (loading && !session) {
    return (
      <div className="flex h-[60vh] flex-col items-center justify-center gap-2 text-muted">
        <Loader2 className="size-5 animate-spin" />
        <span className="text-[12px]">{t('common.loading')}</span>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 py-8">
        <button
          type="button"
          onClick={() => navigate('/inbox')}
          className="inline-flex items-center gap-1.5 text-[12px] text-cyan hover:underline"
        >
          <ArrowLeft className="size-3.5" />
          {t('chat.backToInbox')}
        </button>
        <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
          {error ?? t('chat.notFound')}
        </div>
      </div>
    );
  }

  return (
    // Fill the viewport so the transcript is the only scrolling region and
    // the compose bar genuinely anchors to the bottom. The outer AppShell
    // wraps every route in py-5 (mobile) / py-8 (desktop), so we subtract
    // that here to avoid double-padding.
    <div className="-my-5 flex h-[calc(100dvh-2.5rem)] flex-col md:-my-8 md:h-[calc(100dvh-4rem)]">
      <ChatHeaderBar session={session} agents={agents} onPatch={patch} onBack={() => navigate(-1)} />

      {error && (
        <div className="mx-auto mt-3 w-full max-w-[1080px] rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
          {error}
        </div>
      )}

      <Transcript messages={messages} session={session} streamChunks={streamChunks} />
      <Compose onSend={sendMessage} onStop={stopMessage} composing={composing} />
    </div>
  );
};

interface ComposeProps {
  onSend: (text: string) => void;
  onStop: () => void;
  composing: boolean;
}

const Compose: React.FC<ComposeProps> = ({ onSend, onStop, composing }) => {
  const { t } = useTranslation();
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const trimmed = value.trim();
  const canSend = trimmed.length > 0 && !composing;

  const submit = () => {
    if (!canSend) return;
    onSend(trimmed);
    setValue('');
  };

  // shrink-0 keeps the compose bar pinned at the bottom of the
  // fixed-height chat container; the transcript above scrolls instead.
  return (
    <div className="shrink-0 border-t border-border bg-surface/95 px-4 py-3 backdrop-blur md:px-8">
      <div className="mx-auto flex w-full max-w-[1080px] flex-col gap-2 rounded-2xl border border-border-strong bg-surface-2 p-3 shadow-[0_-4px_24px_-12px_rgba(0,0,0,0.5)]">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            // Cmd/Ctrl+Enter sends; bare Enter still inserts a newline so
            // multi-line drafting works without a separate "expand" toggle.
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
          rows={3}
          placeholder={t('chat.compose.placeholder')}
          disabled={composing}
          className="resize-none rounded-md border border-border bg-surface-3 px-3 py-2 text-[13px] text-foreground outline-none focus:border-cyan disabled:opacity-60"
        />
        <div className="flex items-center justify-between text-[11px] text-muted">
          <span>{t('chat.compose.hint')}</span>
          <div className="flex items-center gap-2">
            {composing && (
              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={onStop}
                className="border-pink/40 bg-pink/[0.08] text-pink hover:bg-pink/[0.14]"
              >
                <StopCircle />
                {t('chat.compose.stop')}
              </Button>
            )}
            <Button
              type="button"
              variant="brand"
              size="xs"
              onClick={submit}
              disabled={!canSend}
            >
              {composing ? <Loader2 className="animate-spin" /> : <Send />}
              {composing ? t('chat.compose.sending') : t('chat.compose.send')}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};

interface ChatHeaderBarProps {
  session: WorkbenchSession;
  agents: VibeAgentBrief[];
  onPatch: (changes: Partial<WorkbenchSession>) => Promise<void>;
  onBack: () => void;
}

const ChatHeaderBar: React.FC<ChatHeaderBarProps> = ({ session, agents, onPatch, onBack }) => {
  const { t } = useTranslation();
  return (
    // shrink-0 keeps the header full-height; bottom border separates it
    // from the scrolling transcript without a floating-card look that
    // would compete with messages for attention.
    <div className="shrink-0 border-b border-border bg-surface/95 px-5 py-3.5 backdrop-blur md:px-8">
      <div className="mx-auto flex w-full max-w-[1080px] flex-col gap-3">
        {/* Single header row — design.pen IDQ5n: project+title on the
            left, cyan-bordered agent/model/effort cluster on the right.
            Wraps gracefully on narrow viewports. */}
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={onBack}
            aria-label={t('chat.backToInbox')}
            className="size-7"
          >
            <ArrowLeft className="size-3.5" />
          </Button>
          <ProjectPill projectId={session.project_id} />
          <TitleField key={session.id} title={session.title} onCommit={(title) => onPatch({ title })} />
          {/* Agent / model / effort cluster — design.pen Q5xIZa wraps these
              three controls in a single cyan-ringed surface so it reads as
              one runtime-settings unit. */}
          <div className="ml-auto flex flex-wrap items-center gap-2 rounded-lg border border-cyan/40 bg-surface-2 px-3 py-1.5">
            <AgentPicker session={session} agents={agents} onPatch={onPatch} />
            <span className="text-muted">·</span>
            <ModelField key={`model-${session.id}`} model={session.model} onCommit={(model) => onPatch({ model })} />
            <span className="text-muted">·</span>
            <EffortPicker effort={session.reasoning_effort} onPick={(value) => onPatch({ reasoning_effort: value })} />
          </div>
        </div>
        {/* Gold info banner — design.pen gSqYM. Explains the session-vs-
            project override semantics. Replaces the old terse
            "changes apply next reply" mute hint that used to live on the
            right edge of the cluster row. */}
        <div className="flex items-center gap-2 rounded-lg border border-gold/40 bg-gold/[0.08] px-3.5 py-2">
          <Info className="size-3 shrink-0 text-gold" />
          <span className="text-[11px] font-medium text-gold">{t('chat.sessionOverrideHint')}</span>
        </div>
      </div>
    </div>
  );
};

const ProjectPill: React.FC<{ projectId: string | null }> = ({ projectId }) => (
  <Badge variant="info" className="font-mono text-[10px]">
    <span className="size-1.5 rounded-full bg-cyan" />
    {projectId || 'workbench'}
  </Badge>
);

interface TitleFieldProps {
  title: string | null;
  onCommit: (next: string | null) => void;
}

const TitleField: React.FC<TitleFieldProps> = ({ title, onCommit }) => {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(title ?? '');
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    setValue(title ?? '');
  }, [title]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="group inline-flex flex-1 items-center gap-2 truncate text-left text-[16px] font-bold text-foreground hover:text-foreground"
      >
        <span className="truncate">{title || t('chat.untitled')}</span>
        <Pencil className="size-3.5 shrink-0 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
      </button>
    );
  }

  const commit = (next: string) => {
    const trimmed = next.trim();
    if (trimmed === (title ?? '')) {
      setEditing(false);
      return;
    }
    onCommit(trimmed || null);
    setEditing(false);
  };

  return (
    <input
      ref={inputRef}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => commit(value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') commit(value);
        if (e.key === 'Escape') {
          setValue(title ?? '');
          setEditing(false);
        }
      }}
      placeholder={t('chat.titlePlaceholder')}
      className="flex-1 rounded-md border border-cyan/40 bg-surface-2 px-2 py-1 text-[15px] font-bold text-foreground outline-none focus:border-cyan"
    />
  );
};

interface AgentPickerProps {
  session: WorkbenchSession;
  agents: VibeAgentBrief[];
  onPatch: (changes: Partial<WorkbenchSession>) => Promise<void>;
}

const AgentPicker: React.FC<AgentPickerProps> = ({ session, agents, onPatch }) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const current = session.agent_name;

  const grouped = useMemo(() => {
    const groups: Record<string, VibeAgentBrief[]> = {};
    for (const agent of agents) {
      groups[agent.backend] = groups[agent.backend] || [];
      groups[agent.backend].push(agent);
    }
    return groups;
  }, [agents]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-md border border-cyan/30 bg-cyan/[0.06] px-2 py-1 text-[12px] font-semibold text-foreground hover:bg-cyan/[0.10]"
        >
          <Bot className="size-3.5 text-cyan" />
          <span>{current || t('chat.pickAgent')}</span>
          <ChevronDown className="size-3 text-muted" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[260px] p-2">
        {Object.keys(grouped).length === 0 && (
          <div className="px-3 py-4 text-center text-[12px] text-muted">{t('chat.noAgents')}</div>
        )}
        {Object.entries(grouped).map(([backend, list]) => (
          <div key={backend} className="flex flex-col gap-1 py-1">
            <div className="px-2 font-mono text-[9px] font-bold uppercase tracking-[0.12em] text-muted">{backend}</div>
            <div className="flex flex-col">
              {list.map((agent) => {
                const active = agent.name === current;
                return (
                  <button
                    key={agent.id}
                    type="button"
                    onClick={async () => {
                      setOpen(false);
                      if (active) return;
                      await onPatch({
                        agent_name: agent.name,
                        agent_id: agent.id,
                        agent_backend: agent.backend,
                        model: agent.model,
                        reasoning_effort: agent.reasoning_effort,
                      });
                    }}
                    className={clsx(
                      'flex items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] transition',
                      active ? 'bg-cyan/[0.10] text-cyan' : 'text-foreground hover:bg-foreground/[0.04]',
                    )}
                  >
                    <span className="flex-1 truncate font-semibold">{agent.name}</span>
                    {agent.model && <span className="font-mono text-[10px] text-muted">{agent.model}</span>}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </PopoverContent>
    </Popover>
  );
};

interface ModelFieldProps {
  model: string | null;
  onCommit: (next: string | null) => void;
}

const ModelField: React.FC<ModelFieldProps> = ({ model, onCommit }) => {
  const { t } = useTranslation();
  const [value, setValue] = useState(model ?? '');

  useEffect(() => {
    setValue(model ?? '');
  }, [model]);

  return (
    <input
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => {
        if (value !== (model ?? '')) onCommit(value.trim() || null);
      }}
      placeholder={t('chat.modelPlaceholder')}
      className="w-[200px] rounded-md border border-border-strong bg-surface-2 px-2 py-1 font-mono text-[11px] text-foreground outline-none focus:border-cyan"
    />
  );
};

const EffortPicker: React.FC<{ effort: string | null; onPick: (value: string) => void }> = ({ effort, onPick }) => (
  <div className="flex rounded-md border border-border-strong bg-surface-2 p-0.5">
    {EFFORT_OPTIONS.map((opt) => (
      <button
        key={opt}
        type="button"
        onClick={() => onPick(opt)}
        className={clsx(
          'rounded px-2 py-0.5 text-[11px] font-semibold capitalize transition',
          effort === opt ? 'bg-mint/[0.10] text-mint' : 'text-muted hover:text-foreground',
        )}
      >
        {opt}
      </button>
    ))}
  </div>
);

interface TranscriptProps {
  messages: WorkbenchMessage[];
  session: WorkbenchSession;
  streamChunks: PendingChunk[];
}

const Transcript: React.FC<TranscriptProps> = ({ messages, session, streamChunks }) => {
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement | null>(null);
  // Auto-scroll to the bottom whenever a new message arrives or the
  // current stream emits another chunk — mirrors how every other chat
  // client behaves and saves the user from chasing the latest reply.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages.length, streamChunks.length]);

  if (messages.length === 0 && streamChunks.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center text-muted">
        <MessageSquare className="size-8 opacity-60" />
        <div className="text-[13px]">{t('chat.transcriptEmpty')}</div>
      </div>
    );
  }
  return (
    <div className="flex-1 overflow-y-auto px-4 py-5 md:px-8">
      <div className="mx-auto flex w-full max-w-[1080px] flex-col gap-3">
        {messages.map((message) => (
          <MessageRow key={message.id} message={message} session={session} />
        ))}
        {streamChunks.length > 0 && <StreamingChunks chunks={streamChunks} session={session} />}
        <div ref={bottomRef} />
      </div>
    </div>
  );
};

// Renders the SSE chunks from the still-active turn. Once the turn
// settles, ``refresh()`` reloads persisted rows and ``streamChunks`` is
// cleared so we never show the same agent reply twice (chunks + row).
const StreamingChunks: React.FC<{ chunks: PendingChunk[]; session: WorkbenchSession }> = ({
  chunks,
  session,
}) => {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-1 rounded-xl border border-mint/30 bg-mint/[0.04] px-4 py-3">
      <div className="flex items-center gap-2 text-[10px]">
        <span className="rounded border border-mint/40 bg-mint/[0.10] px-1.5 py-0 font-mono font-bold uppercase text-mint">
          {t('chat.streaming')}
        </span>
        {session.agent_name && <span className="font-mono text-muted">{session.agent_name}</span>}
        <Loader2 className="ml-auto size-3 animate-spin text-muted" />
      </div>
      {chunks.map((chunk) => (
        <div key={chunk.id} className="whitespace-pre-wrap text-[13px] text-foreground">
          {chunk.text}
        </div>
      ))}
    </div>
  );
};

const MessageRow: React.FC<{ message: WorkbenchMessage; session: WorkbenchSession }> = ({ message, session }) => {
  const isAgent = message.author === 'agent';
  const isSystem = message.author === 'system';
  return (
    <div
      className={clsx(
        'flex flex-col gap-1 rounded-xl border px-4 py-3',
        isAgent
          ? 'border-mint/20 bg-mint/[0.04]'
          : isSystem
          ? 'border-border bg-foreground/[0.02]'
          : 'border-border bg-surface',
      )}
    >
      <div className="flex items-center gap-2 text-[10px]">
        <span
          className={clsx(
            'rounded border px-1.5 py-0 font-mono font-bold uppercase',
            isAgent ? 'border-mint/40 bg-mint/[0.10] text-mint' : 'border-border-strong bg-foreground/[0.04] text-muted',
          )}
        >
          {message.author}
        </span>
        {message.author_name && <span className="font-semibold text-foreground">{message.author_name}</span>}
        {isAgent && session.agent_name && <span className="font-mono text-muted">{session.agent_name}</span>}
        <span className="ml-auto font-mono text-muted">{message.created_at}</span>
      </div>
      <div className="whitespace-pre-wrap text-[13px] text-foreground">{message.text || '—'}</div>
    </div>
  );
};

const ChatMissing: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const { t } = useTranslation();
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 py-8">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-[12px] text-cyan hover:underline"
      >
        <ArrowLeft className="size-3.5" />
        {t('chat.backToInbox')}
      </button>
      <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
        {t('chat.missingSessionId')}
      </div>
    </div>
  );
};
