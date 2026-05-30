import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Bot, ChevronDown, Loader2, MessageSquare, Pencil, Plus, Send, Square } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import type { VibeAgentBrief, WorkbenchMessage, WorkbenchSession } from '../../context/ApiContext';
import { apiFetch } from '../../lib/apiFetch';
import { formatLocalDateTime } from '../../lib/relativeTime';
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
  // Tracks which session's handed-off initial message we've already replayed
  // (see the initial-message effect below). Keyed by session id, not a global
  // boolean, so a second create-via-chat flow that reuses this ChatPage
  // instance (React Router swaps only the :sessionId) still fires.
  const initialHandledSessionRef = useRef<string | null>(null);

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

  // After a turn settles we only need the freshly-persisted messages — not a
  // full session/agents reload. Crucially this does NOT touch ``error``, so a
  // turn-level failure surfaced during streaming (a concurrent-turn refusal, a
  // backend error) stays on screen instead of being wiped by the post-send
  // reload the way ``refresh`` would.
  const reloadMessages = useCallback(async () => {
    if (!sessionId) return;
    try {
      const msgs = await api.listSessionMessages(sessionId, { limit: 50 });
      setMessages(msgs.messages);
    } catch {
      /* keep the current transcript + any streaming error visible */
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
        // The agent reply is now persisted (avibe mirror) into the same
        // session, so reload the transcript and THEN drop the optimistic
        // streaming chunks. Clearing only after the reload resolves means
        // the persisted row replaces the streaming card in a single render,
        // so the reply is never shown twice (streaming card + persisted row)
        // and the "Streaming" badge doesn't linger after the turn settles.
        // ``reloadMessages`` (not ``refresh``) so a turn error set during the
        // stream survives instead of being cleared.
        await reloadMessages();
        setStreamChunks([]);
      }
    },
    [sessionId, composing, reloadMessages],
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
      // An ``error``-kind chunk (a concurrent-turn refusal, or a turn that
      // failed) goes to the persistent error banner, not the transient
      // streaming card — so it survives the post-stream refresh and the user
      // sees why their message wasn't answered instead of it silently
      // vanishing.
      if (data?.kind === 'error') {
        setError(String(data?.text ?? data?.detail ?? 'stream error'));
        return;
      }
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
    if (!initialMessage || !sessionId) return;
    if (initialHandledSessionRef.current === sessionId) return;
    if (loading || !session) return;
    initialHandledSessionRef.current = sessionId;
    navigate(location.pathname, { replace: true, state: null });
    void sendMessage(initialMessage);
  }, [location.state, location.pathname, loading, session, sessionId, navigate, sendMessage]);

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
    // wraps every route in py-5/px-4 (mobile) and py-8/px-10 (desktop); we
    // cancel BOTH axes with negative margins so the header and compose bar
    // run edge-to-edge instead of leaving the page background showing
    // through on the left and right (regression feedback #4/#5).
    //
    // Height: on desktop the shell has no top bar (the mobile header is
    // ``md:hidden``) and ``-my-8`` already cancels the py-8, so the chat starts
    // at the viewport top — it must be a full ``100dvh`` tall. The previous
    // ``calc(100dvh-4rem)`` double-subtracted the (already-cancelled) padding
    // and left a 4rem dead gap below the compose bar. On mobile the sticky
    // ``h-16`` header occupies 4rem at the top, so subtract that instead.
    <div className="-mx-4 -my-5 flex h-[calc(100dvh-4rem)] flex-col md:-mx-10 md:-my-8 md:h-[100dvh]">
      <ChatHeaderBar session={session} agents={agents} onPatch={patch} onBack={() => navigate('/inbox')} />

      {error && (
        <div className="mx-auto mt-3 w-full max-w-[1080px] rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
          {error}
        </div>
      )}

      <Transcript messages={messages} session={session} streamChunks={streamChunks} composing={composing} />
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
  // fixed-height chat container; the transcript above scrolls instead. The
  // bar background fades from the page colour up to transparent (no opaque
  // "white bar" band, no hard top border) so the transcript scrolls cleanly
  // behind it and the input sits close to the very bottom edge (feedback #3).
  return (
    <div
      className="shrink-0 px-4 pb-4 pt-3 md:px-8"
      style={{ background: 'linear-gradient(to top, var(--background) 65%, transparent)' }}
    >
      {/* Input and send/stop button share one row (regression feedback #6):
          the textarea grows, the icon-only button sits flush right and swaps
          between Send (idle) and Stop (generating). No helper hint line. */}
      <div className="mx-auto flex w-full max-w-[1080px] items-end gap-2 rounded-2xl border border-border-strong bg-surface-2 py-2 pl-3.5 pr-2 shadow-[0_-4px_24px_-12px_rgba(0,0,0,0.5)]">
        <textarea
          ref={textareaRef}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends; Shift+Enter inserts a newline. ``isComposing``
            // guards against submitting mid-IME composition (Chinese /
            // Japanese / Korean), where Enter only commits the candidate.
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
          rows={1}
          placeholder={t('chat.compose.placeholder')}
          disabled={composing}
          className="max-h-40 flex-1 resize-none bg-transparent py-1.5 text-[13px] text-foreground outline-none placeholder:text-muted disabled:opacity-60"
        />
        {/* design.pen kxEkn compose bar: a 36px (size-9) icon button with a
            16px glyph. While generating it becomes a pink-soft Stop (the
            ``destructive-soft`` design-system variant), otherwise a flat mint
            Send — matching Icon Button/Default rather than the glowy brand CTA. */}
        {composing ? (
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
            disabled={!canSend}
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

interface ChatHeaderBarProps {
  session: WorkbenchSession;
  agents: VibeAgentBrief[];
  onPatch: (changes: Partial<WorkbenchSession>) => Promise<void>;
  onBack: () => void;
}

const ChatHeaderBar: React.FC<ChatHeaderBarProps> = ({ session, agents, onPatch, onBack }) => {
  const { t } = useTranslation();
  return (
    // A single compact row (design.pen IDQ5n): back button + click-to-edit
    // title on the left, the agent/model/effort picker on the right. The bar
    // runs edge-to-edge (the page root cancels the shell padding) with a
    // hairline bottom border separating it from the scrolling transcript.
    // No project-id pill and no override banner — both were noise the user
    // flagged (regression feedback #1/#3).
    <div className="shrink-0 border-b border-border bg-surface/70 px-4 py-2.5 backdrop-blur md:px-8">
      <div className="mx-auto flex w-full max-w-[1080px] items-center gap-3">
        <Button
          type="button"
          variant="outline"
          size="icon"
          onClick={onBack}
          aria-label={t('chat.backToInbox')}
          className="size-7 shrink-0"
        >
          <ArrowLeft className="size-3.5" />
        </Button>
        <TitleField key={session.id} title={session.title} onCommit={(title) => onPatch({ title })} />
        <AgentRoutePicker session={session} agents={agents} onPatch={onPatch} />
      </div>
    </div>
  );
};

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

interface AgentRoutePickerProps {
  session: WorkbenchSession;
  agents: VibeAgentBrief[];
  onPatch: (changes: Partial<WorkbenchSession>) => Promise<void>;
}

// design.pen Q5xIZa + its open-state mock: one cyan-ringed trigger showing
// ``[backend] agent · model · effort`` that opens a three-column cascading
// menu — Agent → Model → Effort (regression feedback #2, replacing the old
// popover + free-text model input + segmented-effort trio). Picking an agent
// seeds model/effort from its defaults; the model column is fetched lazily
// per backend (Claude / Codex / OpenCode each expose their own model list) so
// the user selects a real model instead of typing an override by hand.
const AgentRoutePicker: React.FC<AgentRoutePickerProps> = ({ session, agents, onPatch }) => {
  const { t } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [modelsByBackend, setModelsByBackend] = useState<Record<string, string[]>>({});
  const [loadingModels, setLoadingModels] = useState(false);

  const backend = session.agent_backend || '';
  const currentAgent = session.agent_name;
  const currentModel = session.model;
  const currentEffort = session.reasoning_effort;

  const grouped = useMemo(() => {
    const groups: Record<string, VibeAgentBrief[]> = {};
    for (const agent of agents) {
      (groups[agent.backend] ||= []).push(agent);
    }
    return groups;
  }, [agents]);

  // Fetch the active backend's model list the first time the menu opens for
  // it; cached per backend so toggling agents doesn't refetch.
  useEffect(() => {
    if (!open || !backend || modelsByBackend[backend]) return;
    let cancelled = false;
    setLoadingModels(true);
    (async () => {
      try {
        let models: string[] = [];
        if (backend === 'claude') models = (await api.claudeModels()).models ?? [];
        else if (backend === 'codex') models = (await api.codexModels()).models ?? [];
        else if (backend === 'opencode')
          // The providers endpoint returns RAW model ids per provider (never
          // provider-prefixed), and the OpenCode adapter resolves the override
          // by splitting the selected value on the FIRST "/" into
          // {providerID, modelID}. So ALWAYS prepend the provider id — even
          // when the raw id itself contains "/" (e.g. OpenRouter's
          // ``anthropic/claude-*`` must become ``openrouter/anthropic/claude-*``,
          // not be misread as provider ``anthropic``). The first-slash split
          // keeps the remainder (``anthropic/claude-*``) intact as the model.
          models = ((await api.getOpencodeProviders()).providers ?? []).flatMap((p) =>
            (p.models ?? []).map((m) => `${p.id}/${m}`),
          );
        if (!cancelled) setModelsByBackend((prev) => ({ ...prev, [backend]: models }));
      } catch {
        if (!cancelled) setModelsByBackend((prev) => ({ ...prev, [backend]: [] }));
      } finally {
        if (!cancelled) setLoadingModels(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, backend, api, modelsByBackend]);

  const models = modelsByBackend[backend] ?? [];

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="ml-auto inline-flex max-w-[62%] items-center gap-1.5 rounded-lg border border-cyan/40 bg-surface-2 px-2.5 py-1.5 text-[12px] transition hover:bg-cyan/[0.06]"
        >
          {backend && (
            <span className="inline-flex shrink-0 items-center gap-1 rounded border border-cyan/30 bg-cyan/[0.08] px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase text-cyan">
              <Bot className="size-3" />
              {backend}
            </span>
          )}
          <span className="truncate font-semibold text-foreground">{currentAgent || t('chat.pickAgent')}</span>
          {currentModel && (
            <>
              <span className="text-muted">·</span>
              <span className="truncate font-mono text-[10px] text-muted">{currentModel}</span>
            </>
          )}
          {currentEffort && (
            <>
              <span className="text-muted">·</span>
              <span className="shrink-0 text-[10px] capitalize text-muted">{currentEffort}</span>
            </>
          )}
          <ChevronDown className="size-3 shrink-0 text-muted" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[620px] max-w-[92vw] overflow-hidden p-0">
        <div className="grid grid-cols-3 divide-x divide-border">
          {/* Column 1 — Agent */}
          <RouteColumn title={t('chat.picker.agent')}>
            {agents.length === 0 && (
              <div className="px-2 py-3 text-center text-[11px] text-muted">{t('chat.noAgents')}</div>
            )}
            {Object.entries(grouped).map(([be, list]) => (
              <div key={be} className="flex flex-col gap-0.5 pb-1">
                <div className="px-2 pt-1.5 font-mono text-[9px] font-bold uppercase tracking-[0.12em] text-muted">
                  {be}
                </div>
                {list.map((agent) => (
                  <RouteItem
                    key={agent.id}
                    active={agent.name === currentAgent}
                    onClick={() =>
                      void onPatch({
                        agent_name: agent.name,
                        agent_id: agent.id,
                        agent_backend: agent.backend,
                        model: agent.model,
                        reasoning_effort: agent.reasoning_effort,
                      })
                    }
                  >
                    <span className="flex-1 truncate font-semibold">{agent.name}</span>
                    {agent.model && <span className="truncate font-mono text-[9px] text-muted">{agent.model}</span>}
                  </RouteItem>
                ))}
              </div>
            ))}
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                navigate('/agents');
              }}
              className="mt-1 flex items-center gap-1.5 rounded px-2 py-1.5 text-left text-[11px] font-medium text-cyan transition hover:bg-cyan/[0.08]"
            >
              <Plus className="size-3.5" />
              {t('chat.picker.newAgent')}
            </button>
          </RouteColumn>

          {/* Column 2 — Model (lazy-loaded for the active backend) */}
          <RouteColumn title={t('chat.picker.model')}>
            {loadingModels && models.length === 0 ? (
              <div className="flex items-center gap-1.5 px-2 py-3 text-[11px] text-muted">
                <Loader2 className="size-3 animate-spin" />
                {t('common.loading')}
              </div>
            ) : models.length === 0 ? (
              <div className="px-2 py-3 text-[11px] text-muted">{t('chat.picker.noModels')}</div>
            ) : (
              models.map((model) => (
                <RouteItem key={model} active={model === currentModel} onClick={() => void onPatch({ model })}>
                  <span className="flex-1 truncate font-mono text-[11px]">{model}</span>
                </RouteItem>
              ))
            )}
          </RouteColumn>

          {/* Column 3 — Effort */}
          <RouteColumn title={t('chat.picker.effort')}>
            {EFFORT_OPTIONS.map((opt) => (
              <RouteItem
                key={opt}
                active={opt === currentEffort}
                onClick={() => void onPatch({ reasoning_effort: opt })}
              >
                <span className="flex-1 capitalize">{t(`chat.picker.effortOptions.${opt}`)}</span>
              </RouteItem>
            ))}
          </RouteColumn>
        </div>
      </PopoverContent>
    </Popover>
  );
};

const RouteColumn: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div className="flex max-h-[320px] min-w-0 flex-col overflow-y-auto p-1.5">
    <div className="px-2 pb-1 pt-0.5 text-[10px] font-bold uppercase tracking-[0.1em] text-muted">{title}</div>
    {children}
  </div>
);

const RouteItem: React.FC<{ active: boolean; onClick: () => void; children: React.ReactNode }> = ({
  active,
  onClick,
  children,
}) => (
  <button
    type="button"
    onClick={onClick}
    className={clsx(
      'flex items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] transition',
      active ? 'bg-cyan/[0.10] text-cyan' : 'text-foreground hover:bg-foreground/[0.04]',
    )}
  >
    {children}
  </button>
);

interface TranscriptProps {
  messages: WorkbenchMessage[];
  session: WorkbenchSession;
  streamChunks: PendingChunk[];
  composing: boolean;
}

const Transcript: React.FC<TranscriptProps> = ({ messages, session, streamChunks, composing }) => {
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement | null>(null);
  // ``composing`` with no chunks yet means the turn is in flight but the agent
  // hasn't streamed anything — show the thinking bubble in that gap.
  const showThinking = composing && streamChunks.length === 0;
  // Auto-scroll to the bottom whenever a new message arrives, the current
  // stream emits another chunk, or the thinking bubble toggles — mirrors how
  // every other chat client behaves and saves the user from chasing the reply.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages.length, streamChunks.length, showThinking]);

  if (messages.length === 0 && streamChunks.length === 0 && !composing) {
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
        {showThinking && <ThinkingBubble session={session} />}
        <div ref={bottomRef} />
      </div>
    </div>
  );
};

// Shared markdown renderer for agent replies + streaming text. react-markdown
// + remark-gfm (tables, strikethrough, task lists, autolinks); the element
// styling lives in index.css under ``.vr-markdown`` because the project
// doesn't ship the Tailwind typography plugin.
const Markdown: React.FC<{ content: string }> = ({ content }) => (
  <div className="vr-markdown">
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
  </div>
);

// Shown while a turn is in flight but the agent hasn't streamed its first
// chunk yet — an agent-styled bubble with three dots that fade in sequence
// (``.vr-typing-dot`` keyframes in index.css), so the user gets immediate
// feedback that their message landed and a reply is coming (feedback #1).
const ThinkingBubble: React.FC<{ session: WorkbenchSession }> = ({ session }) => (
  <div className="flex flex-col gap-1.5 rounded-xl border border-mint/20 bg-mint/[0.04] px-4 py-3">
    <div className="flex items-center gap-2 text-[10px]">
      <span className="rounded border border-mint/40 bg-mint/[0.10] px-1.5 py-0 font-mono font-bold uppercase text-mint">
        agent
      </span>
      {session.agent_name && <span className="font-mono text-muted">{session.agent_name}</span>}
    </div>
    <div className="flex items-center gap-1 py-0.5">
      <span className="vr-typing-dot size-1.5 rounded-full bg-mint" />
      <span className="vr-typing-dot size-1.5 rounded-full bg-mint [animation-delay:0.2s]" />
      <span className="vr-typing-dot size-1.5 rounded-full bg-mint [animation-delay:0.4s]" />
    </div>
  </div>
);

// Renders the SSE chunks from the still-active turn. Once the turn
// settles, ``reloadMessages()`` reloads persisted rows and ``streamChunks`` is
// cleared so we never show the same agent reply twice (chunks + row). Chunks
// are concatenated and rendered as one markdown document (the agent streams
// markdown), matching how the settled persisted row will look.
const StreamingChunks: React.FC<{ chunks: PendingChunk[]; session: WorkbenchSession }> = ({
  chunks,
  session,
}) => {
  const { t } = useTranslation();
  const text = chunks.map((c) => c.text).join('');
  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-mint/30 bg-mint/[0.04] px-4 py-3">
      <div className="flex items-center gap-2 text-[10px]">
        <span className="rounded border border-mint/40 bg-mint/[0.10] px-1.5 py-0 font-mono font-bold uppercase text-mint">
          {t('chat.streaming')}
        </span>
        {session.agent_name && <span className="font-mono text-muted">{session.agent_name}</span>}
        <Loader2 className="ml-auto size-3 animate-spin text-muted" />
      </div>
      <Markdown content={text} />
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
        <span className="ml-auto font-mono text-muted">{formatLocalDateTime(message.created_at)}</span>
      </div>
      {/* Agent / system replies are markdown (render it); the user's own
          message is shown verbatim as typed. */}
      {message.text ? (
        isAgent || isSystem ? (
          <Markdown content={message.text} />
        ) : (
          <div className="whitespace-pre-wrap text-[13px] text-foreground">{message.text}</div>
        )
      ) : (
        <div className="text-[13px] text-muted">—</div>
      )}
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
