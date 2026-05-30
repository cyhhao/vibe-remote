import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Bot, ChevronDown, Clock, Loader2, MessageSquare, Pencil, Plus, Send, Square, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import { useWorkbenchInbox } from '../../context/WorkbenchInboxContext';
import type { VibeAgentBrief, WorkbenchMessage, WorkbenchSession } from '../../context/ApiContext';
import { apiFetch } from '../../lib/apiFetch';
import { formatLocalDateTime } from '../../lib/relativeTime';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';

// Reasoning-effort options are backend-specific (mirrors the backend's own
// lists in modules/agents/opencode/utils.py): Codex is minimal..xhigh, Claude is
// low/medium/high. Offering a global low/medium/high/max let a Codex session
// pick an invalid 'max' and hid valid 'minimal'/'xhigh' (Codex P2). OpenCode's
// is model-dependent; use the broad superset as a reasonable default.
const EFFORT_BY_BACKEND: Record<string, string[]> = {
  claude: ['low', 'medium', 'high'],
  codex: ['minimal', 'low', 'medium', 'high', 'xhigh'],
  opencode: ['minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
};
const DEFAULT_EFFORTS = ['low', 'medium', 'high'];
const effortOptionsFor = (backend: string): string[] => EFFORT_BY_BACKEND[backend] ?? DEFAULT_EFFORTS;

// Last-resort failsafe for a LOST ``turn.end`` event. The controller is the
// authority on turn end (``turn.start`` / ``turn.end`` over the bus) and its own
// dispatch safety timeout is 600s, so a real turn always ends by then. This is
// deliberately longer (11 min) so it only fires if the ``turn.end`` event itself
// was dropped in transit — never while the backend could still be running, which
// would hide Stop on a live turn (Codex P2).
const WORKING_FALLBACK_MS = 11 * 60 * 1000;

// The transcript-visible message types — mirrors the server filter on
// ``GET /api/sessions/{id}/messages`` so the live ``message.new`` feed appends
// the same rows the initial load shows (assistant / tool_call are process log).
const isTranscriptMessage = (msg: WorkbenchMessage): boolean =>
  msg.type === 'user' ||
  msg.type === 'result' ||
  msg.type === 'notify' ||
  (msg.metadata as { source?: string } | null)?.source === 'show_page';

// Durable transcript order: ``created_at`` is second-resolution, so the
// message id (a microsecond-clock prefix, see messages_service._new_message_id)
// is the tie-break — matching the server's ``(created_at, id)`` ordering.
const byCreatedThenId = (a: WorkbenchMessage, b: WorkbenchMessage): number => {
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? -1 : 1;
  if (a.id === b.id) return 0;
  return a.id < b.id ? -1 : 1;
};

// Union two row sets, deduped by id and re-sorted into durable order. Used for
// the initial snapshot + live merge AND every live append, so a fast agent
// result that arrives over /api/events *before* its prompt row still lands in
// the correct position instead of ahead of the prompt (Codex P2). Also closes
// the load/subscribe race where a blind setMessages(snapshot) would clobber a
// message that arrived over the stream before the REST load returned.
const mergeById = (existing: WorkbenchMessage[], incoming: WorkbenchMessage[]): WorkbenchMessage[] => {
  const seen = new Set(existing.map((m) => m.id));
  const merged = [...existing, ...incoming.filter((m) => !seen.has(m.id))];
  merged.sort(byCreatedThenId);
  return merged;
};

// Mirrors design.pen kxEkn — the inline header replaces the old "Session
// settings" dialog. Title is click-to-edit; the cyan-bordered pill on the
// right opens a single popover that drives agent / model / effort all at
// once so the user doesn't have to navigate three different menus.
//
// Transcript model (session/page-scoped, NOT per-turn): on mount we load the
// persisted history once, then subscribe to this session's ``message.new`` for
// as long as the page is open — so EVERY message lands live, including agent
// replies the user didn't trigger (scheduled task / watch / proactive). Sending
// is a plain fire-and-forget POST; the reply arrives over the same stream.
export const ChatPage: React.FC = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const api = useApi();
  const { unreadBySession, markRead: markInboxRead } = useWorkbenchInbox();
  const [session, setSession] = useState<WorkbenchSession | null>(null);
  const [agents, setAgents] = useState<VibeAgentBrief[]>([]);
  const [messages, setMessages] = useState<WorkbenchMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ``working`` = a turn is in flight for this session (from our send, or any
  // other origin we observe). Drives the thinking bubble + the Send→Stop swap.
  const [working, setWorking] = useState(false);
  // Send-while-busy queue (messages sent while a turn runs, shown above the
  // composer) + the loaded draft to seed the composer with.
  const [queue, setQueue] = useState<WorkbenchMessage[]>([]);
  const [initialDraft, setInitialDraft] = useState<string | null>(null);
  const draftTimerRef = useRef<number | null>(null);
  // The debounced draft save still owed to the server, tagged with the session
  // it belongs to — so a fast session switch flushes it instead of dropping it.
  const draftPendingRef = useRef<{ sessionId: string; text: string } | null>(null);
  // Tracks which session's handed-off initial message we've already replayed
  // (see the initial-message effect below). Keyed by session id, not a global
  // boolean, so a second create-via-chat flow that reuses this ChatPage
  // instance (React Router swaps only the :sessionId) still fires.
  const initialHandledSessionRef = useRef<string | null>(null);
  // The session the component is currently on. Async loads capture their
  // request's sessionId and compare against this before committing state, so a
  // load that resolves after the user switched chats can't leak the previous
  // session's rows into the current one (Codex P2).
  const sessionIdRef = useRef(sessionId);
  sessionIdRef.current = sessionId;

  const appendMessage = useCallback((msg: WorkbenchMessage) => {
    // Dedupe by id (a sent user row is appended optimistically AND echoed over
    // the stream) and keep durable (created_at, id) order so an out-of-order
    // live event can't render a reply ahead of its prompt.
    setMessages((prev) => (prev.some((m) => m.id === msg.id) ? prev : mergeById(prev, [msg])));
  }, []);

  // Reconcile against durable storage after a window where ``message.new`` could
  // have been missed — the SSE broker is an in-memory fan-out with no replay, so
  // a reconnect or a backgrounded mobile tab can drop events while the reply is
  // safely in SQLite. Re-fetches the RECENT WINDOW (not just rows after a cursor)
  // and merges (deduped), so a missed EARLIER row — a flushed queued prompt, or a
  // prompt sent from another tab — is recovered even if a later row already
  // arrived; a cursor-after query would skip past the gap forever (Codex P2).
  // Does NOT touch ``working``: ``turn.end`` is the authoritative end signal, and
  // clearing on a fetched (possibly older) result could hide Stop on a newer
  // queued turn that is still in flight (Codex P2). Cheap + idempotent.
  const reconcile = useCallback(async () => {
    if (!sessionId) return;
    try {
      const res = await api.listSessionMessages(sessionId, { limit: 50 });
      if (sessionId !== sessionIdRef.current) return; // switched chats mid-fetch
      const fresh = res.messages.filter(isTranscriptMessage);
      if (fresh.length) {
        setMessages((prev) => mergeById(prev, fresh));
      }
    } catch {
      /* keep the current transcript; the next reconnect retries */
    }
  }, [api, sessionId]);

  // The send-while-busy queue (pending messages shown above the composer).
  // Re-fetched on mount + on every ``queue.updated`` (enqueue / flush / remove).
  const refreshQueue = useCallback(async () => {
    if (!sessionId) return;
    try {
      const res = await api.listSessionQueue(sessionId);
      if (sessionId !== sessionIdRef.current) return; // switched chats mid-fetch
      setQueue(res.queued ?? []);
    } catch {
      /* leave the last-known queue; the next queue.updated refetches */
    }
  }, [api, sessionId]);

  // Persist the composer's unsent text server-side (debounced) so it survives a
  // reload / device switch. The send path clears it server-side; this only
  // saves while typing.
  const onDraftChange = useCallback(
    (text: string) => {
      if (!sessionId) return;
      // Tag the pending save with THIS session so the timer (and the
      // session-change flush) save to the right session even if the user has
      // since navigated away.
      draftPendingRef.current = { sessionId, text };
      if (draftTimerRef.current) window.clearTimeout(draftTimerRef.current);
      draftTimerRef.current = window.setTimeout(() => {
        const pending = draftPendingRef.current;
        draftPendingRef.current = null;
        draftTimerRef.current = null;
        if (pending) void api.setSessionDraft(pending.sessionId, pending.text);
      }, 600);
    },
    [api, sessionId],
  );

  // Flush a still-pending draft for the session we're leaving, so switching
  // chats within the debounce window doesn't drop it (Codex P2). Runs on
  // sessionId change + unmount.
  useEffect(() => {
    return () => {
      if (draftTimerRef.current) {
        window.clearTimeout(draftTimerRef.current);
        draftTimerRef.current = null;
      }
      const pending = draftPendingRef.current;
      draftPendingRef.current = null;
      if (pending) void api.setSessionDraft(pending.sessionId, pending.text);
    };
  }, [sessionId, api]);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      const [fetched, agentList, msgs, queued, draft] = await Promise.all([
        api.getSession(sessionId),
        api.listVibeAgents({ includeDisabled: false }),
        api.listSessionMessages(sessionId, { limit: 50 }),
        api.listSessionQueue(sessionId),
        api.getSessionDraft(sessionId),
      ]);
      // Dropped if the user switched chats while this load was in flight.
      if (sessionId !== sessionIdRef.current) return;
      setSession(fetched);
      setAgents(agentList.agents);
      // Merge (not replace) so a row that arrived over the stream during the
      // load isn't clobbered; the session-change reset keeps prior sessions out.
      setMessages((prev) => mergeById(msgs.messages, prev));
      setQueue(queued.queued ?? []);
      setInitialDraft(draft.text ?? '');
    } catch (err: any) {
      setError(err?.message ?? String(err));
    } finally {
      setLoading(false);
    }
  }, [api, sessionId]);

  // Clear per-session state the instant the session changes (React Router swaps
  // only :sessionId, reusing this instance), before the new session's
  // load/subscribe — so the previous conversation / queue / draft never leak in
  // and the merge in ``refresh`` only ever unions same-session rows.
  useEffect(() => {
    // Clear ``session`` too (not just messages/queue/draft): otherwise the header
    // keeps rendering the previous chat's title + agent picker until the new load
    // finishes, and a rename / agent change would patch() the STALE session.id
    // while the URL is already on the new chat (Codex P2). Nulling it shows the
    // loading state until refresh() resolves the new session.
    setSession(null);
    setMessages([]);
    setWorking(false);
    setQueue([]);
    setInitialDraft(null);
  }, [sessionId]);

  // Persistent per-session subscription: append every transcript-visible
  // ``message.new`` for THIS session for as long as the page is open. An agent
  // ``result`` ends the working state (the turn produced its reply). Harness
  // turns (scheduled / watch) flow through here too — their prompt + reply both
  // appear without the user having sent anything.
  useEffect(() => {
    if (!sessionId) return;
    const disconnect = api.connectWorkbenchEvents({
      onMessageNew: (msg) => {
        if (msg.session_id !== sessionId) return;
        if (!isTranscriptMessage(msg)) return;
        appendMessage(msg);
        // ``turn.end`` is the authoritative end signal, but a ``result`` row is
        // itself a terminal agent output — clear working on it too, so a live
        // reply whose later ``turn.end`` was dropped in transit doesn't leave
        // Stop stuck until the fallback (Codex P2). NB: only ``result``, never
        // ``notify`` — a Codex system/thread.started row persists as notify
        // mid-turn, and clearing on that would hide Stop while the backend runs.
        if (msg.author === 'agent' && msg.type === 'result') {
          setWorking(false);
        }
      },
      onTurnStart: (data) => {
        if (data.session_id === sessionId) setWorking(true);
      },
      onTurnEnd: (data) => {
        // The controller confirms the turn settled (result, agent error, cancel,
        // or its own timeout) — the authoritative end of the working state.
        if (data.session_id === sessionId) setWorking(false);
      },
      onQueueUpdated: (data) => {
        // The send-while-busy queue changed (enqueue / flush / per-item delete).
        if (data.session_id === sessionId) void refreshQueue();
      },
      onConnected: () => {
        // Every (re)connect reconciles durable storage, recovering any
        // ``message.new`` dropped while the socket was down.
        void reconcile();
        void refreshQueue();
      },
      onError: () => {
        // Browser EventSource auto-reconnects; keep the page usable.
      },
    });
    return disconnect;
  }, [api, sessionId, appendMessage, reconcile, refreshQueue]);

  // Mobile tabs (the common case for IM users) get backgrounded mid-turn; the
  // SSE feed can be suspended without a clean reconnect, dropping the reply.
  // Reconcile when the page becomes visible again so the answer + working state
  // catch up to durable storage.
  useEffect(() => {
    if (!sessionId) return;
    const onVisible = () => {
      if (document.visibilityState === 'visible') void reconcile();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [sessionId, reconcile]);

  // Fallback timer so a turn that never emits a result doesn't pin the
  // indicator. Armed when ``working`` flips true, cleared when it flips false.
  useEffect(() => {
    if (!working) return;
    const timer = window.setTimeout(() => setWorking(false), WORKING_FALLBACK_MS);
    return () => window.clearTimeout(timer);
  }, [working]);

  const sendMessage = useCallback(
    async (text: string) => {
      // NB: no ``working`` guard — sending WHILE a turn runs is the queue
      // feature; the backend enqueues it (202) instead of refusing.
      if (!sessionId || !text.trim()) return;
      setWorking(true);
      setError(null);
      try {
        // Plain (non-streaming) POST: the turn runs fire-and-forget on the
        // controller and its reply arrives over the persistent ``message.new``
        // stream — we don't hold the response open. ``apiFetch`` attaches the
        // CSRF token that ``protect_mutating_ui_requests`` requires under
        // remote-access mode (raw ``fetch`` would 403).
        const response = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text }),
        });
        const body = await response.json().catch(() => null);
        if (!response.ok) {
          setWorking(false);
          throw new Error(body?.detail ? String(body.detail) : `HTTP ${response.status}`);
        }
        if (body?.queued) {
          // Sent while a turn was running → enqueued (shows above the composer
          // via queue.updated). A turn IS in flight, so keep working/Stop; don't
          // add a transcript row. Refresh immediately in case the event races.
          void refreshQueue();
          return;
        }
        // A turn started — optimistically show the user row (echo dedupes by id).
        if (body && body.id) appendMessage(body as WorkbenchMessage);
      } catch (err: any) {
        setWorking(false);
        setError(err?.message ?? String(err));
      }
    },
    [sessionId, appendMessage, refreshQueue],
  );

  const stopMessage = useCallback(async () => {
    if (!sessionId || !working) return;
    try {
      const res = await api.cancelSession(sessionId);
      // Don't clear the working state here. cancelSession returns a non-throwing
      // payload for controller-side failures (503 socket-unavailable, 404
      // not_in_flight); if the stop didn't reach the backend the turn may still
      // be live, so keep Stop available and surface the failure (Codex P2). On
      // success the backend is interrupted and the authoritative ``turn.end``
      // clears the working state.
      if (res && res.ok === false) {
        setError(res.detail ? String(res.detail) : t('chat.stopFailed'));
      }
    } catch (err: any) {
      // The cancel request itself threw (network) — surface it; keep Stop.
      setError(err?.message ?? String(err));
    }
  }, [api, sessionId, working, t]);

  const removeQueued = useCallback(
    async (messageId: string) => {
      if (!sessionId) return;
      setQueue((prev) => prev.filter((m) => m.id !== messageId)); // optimistic
      try {
        await api.removeQueuedMessage(sessionId, messageId);
      } catch {
        void refreshQueue(); // restore on failure
      }
    },
    [api, sessionId, refreshQueue],
  );

  const sendQueueNow = useCallback(async () => {
    // "立即发送": interrupt the running turn + flush the queue now. The queue
    // flushes as one merged turn, so this runs the whole queue.
    if (!sessionId || queue.length === 0) return;
    try {
      const res = await api.sendQueuedNow(sessionId, queue[0].id);
      if (res && res.ok === false) {
        setError(res.detail ? String(res.detail) : t('chat.stopFailed'));
      }
    } catch (err: any) {
      setError(err?.message ?? String(err));
    }
  }, [api, sessionId, queue, t]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // The user is actively viewing this session, so an agent reply here is seen,
  // not "new". Clear unread whenever it appears — on open, or when a realtime
  // inbox.session.updated lands after a reply — so the Inbox/sidebar never badge
  // the chat you're looking at. Reactive to the unread map, so it's race-free
  // against the cross-process event ordering.
  useEffect(() => {
    if (sessionId && (unreadBySession[sessionId] ?? 0) > 0) {
      void markInboxRead(sessionId);
    }
  }, [sessionId, unreadBySession, markInboxRead]);

  // The Workbench canvas creates the session and hands its first message over
  // as router state. Replay it once through the compose path so the agent turn
  // starts. Clear the state afterwards so a manual page refresh (which preserves
  // history state) doesn't resend it.
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

      <Transcript messages={messages} session={session} working={working} />
      <QueueStrip queue={queue} onRemove={removeQueued} onSendNow={sendQueueNow} />
      {/* key by session so the composer remounts per session — its draft-seeding
          + local value reset, instead of carrying across sessions (Codex P2). */}
      <Compose
        key={sessionId}
        onSend={sendMessage}
        onStop={stopMessage}
        busy={working}
        initialDraft={initialDraft}
        onDraftChange={onDraftChange}
      />
    </div>
  );
};

// Pending send-while-busy messages, shown between the transcript and the
// composer (Codex-GUI style). Each can be dropped; "立即发送" interrupts the
// running turn and flushes the whole queue now (the queue flushes merged).
const QueueStrip: React.FC<{
  queue: WorkbenchMessage[];
  onRemove: (id: string) => void;
  onSendNow: () => void;
}> = ({ queue, onRemove, onSendNow }) => {
  const { t } = useTranslation();
  if (queue.length === 0) return null;
  return (
    <div className="shrink-0 px-4 md:px-8">
      <div className="mx-auto w-full max-w-[1080px] rounded-xl border border-cyan/25 bg-cyan/[0.04] p-2">
        <div className="flex items-center justify-between px-1 pb-1.5">
          <span className="inline-flex items-center gap-1.5 font-mono text-[10px] font-bold uppercase tracking-[0.1em] text-cyan">
            <Clock className="size-3" />
            {t('chat.queue.title', { count: queue.length })}
          </span>
          <Button type="button" variant="ghost" size="sm" onClick={onSendNow} className="h-6 px-2 text-[11px] text-cyan">
            {t('chat.queue.sendNow')}
          </Button>
        </div>
        <div className="flex max-h-32 flex-col gap-1 overflow-y-auto">
          {queue.map((item) => (
            <div key={item.id} className="flex items-center gap-2 rounded-lg bg-surface-2 px-2.5 py-1.5">
              <span className="flex-1 truncate text-[12px] text-foreground">{item.text}</span>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                onClick={() => onRemove(item.id)}
                aria-label={t('chat.queue.remove')}
                className="size-6 shrink-0 text-muted hover:text-destructive"
              >
                <X className="size-3.5" />
              </Button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

interface ComposeProps {
  onSend: (text: string) => void;
  onStop: () => void;
  busy: boolean;
  initialDraft: string | null;
  onDraftChange: (text: string) => void;
}

const Compose: React.FC<ComposeProps> = ({ onSend, onStop, busy, initialDraft, onDraftChange }) => {
  const { t } = useTranslation();
  const [value, setValue] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  // Seed the composer with the saved draft once it loads — but only if the box
  // is still untouched, so a late-arriving draft can't clobber live typing.
  const draftAppliedRef = useRef(false);
  useEffect(() => {
    if (draftAppliedRef.current || initialDraft == null) return;
    draftAppliedRef.current = true;
    if (initialDraft) setValue((cur) => (cur ? cur : initialDraft));
  }, [initialDraft]);

  const trimmed = value.trim();
  // Enter always sends when there's text — sending WHILE a turn runs queues it
  // (the queue feature), so the composer stays usable during a turn.
  const canSubmit = trimmed.length > 0;

  const update = (next: string) => {
    setValue(next);
    onDraftChange(next);
  };

  const submit = () => {
    if (!canSubmit) return;
    onSend(trimmed);
    setValue('');
    onDraftChange('');
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
          onChange={(e) => update(e.target.value)}
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
          placeholder={busy ? t('chat.compose.placeholderBusy') : t('chat.compose.placeholder')}
          className="max-h-40 flex-1 resize-none bg-transparent py-1.5 text-[13px] text-foreground outline-none placeholder:text-muted"
        />
        {/* design.pen kxEkn compose bar: a 36px (size-9) icon button with a
            16px glyph. While generating it becomes a pink-soft Stop (the
            ``destructive-soft`` design-system variant), otherwise a flat mint
            Send — matching Icon Button/Default rather than the glowy brand CTA. */}
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
    <Input
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
      className="h-8 flex-1 px-2 text-[15px] font-bold"
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

          {/* Column 3 — Effort (options match the selected backend) */}
          <RouteColumn title={t('chat.picker.effort')}>
            {effortOptionsFor(backend).map((opt) => (
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
  working: boolean;
}

const Transcript: React.FC<TranscriptProps> = ({ messages, session, working }) => {
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement | null>(null);
  // The reply arrives atomically as a persisted ``result`` row (no streaming
  // card), so the thinking bubble shows for the whole gap between send and
  // reply. Hide it the moment the last row is already a fresh agent result.
  const lastIsAgentResult =
    messages.length > 0 &&
    messages[messages.length - 1].author === 'agent' &&
    messages[messages.length - 1].type === 'result';
  const showThinking = working && !lastIsAgentResult;
  // Auto-scroll to the bottom whenever a new message arrives or the thinking
  // bubble toggles — mirrors how every other chat client behaves and saves the
  // user from chasing the reply.
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [messages.length, showThinking]);

  if (messages.length === 0 && !working) {
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
        {showThinking && <ThinkingBubble session={session} />}
        <div ref={bottomRef} />
      </div>
    </div>
  );
};

// Shared markdown renderer for agent replies. react-markdown + remark-gfm
// (tables, strikethrough, task lists, autolinks); the element styling lives in
// index.css under ``.vr-markdown`` because the project doesn't ship the
// Tailwind typography plugin.
const Markdown: React.FC<{ content: string }> = ({ content }) => (
  <div className="vr-markdown">
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
  </div>
);

// Shown while a turn is in flight but the reply hasn't landed yet — an
// agent-styled bubble with three dots that fade in sequence
// (``.vr-typing-dot`` keyframes in index.css), so the user gets immediate
// feedback that a reply is coming (feedback #1).
const ThinkingBubble: React.FC<{ session: WorkbenchSession }> = ({ session }) => {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-1.5 rounded-xl border border-mint/20 bg-mint/[0.04] px-4 py-3">
      <div className="flex items-center gap-2 text-[10px]">
        <span className="rounded border border-mint/40 bg-mint/[0.10] px-1.5 py-0 font-mono font-bold uppercase text-mint">
          {t('chat.thinking')}
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
};

// Maps a harness trigger kind (the ``author_name`` on a source='harness' row)
// to a friendly provenance label. Distinguishes Task vs Watch per the spec; a
// finer kind (webhook) gets its own label, anything else falls back.
const harnessLabel = (kind: string | null | undefined, t: (k: string) => string): string => {
  switch (kind) {
    case 'watch':
      return t('chat.source.watch');
    case 'webhook':
      return t('chat.source.webhook');
    case 'scheduled':
    case 'task_run':
      return t('chat.source.scheduled');
    default:
      return t('chat.source.harness');
  }
};

const MessageRow: React.FC<{ message: WorkbenchMessage; session: WorkbenchSession }> = ({ message, session }) => {
  const { t } = useTranslation();
  // A notify row is a turn-terminal marker (e.g. an agent run that failed and
  // stopped without a result). Render it distinctly from an agent reply — gold
  // box, "Notify" identifier — so the user reads it as a status, not an answer.
  const isNotify = message.type === 'notify';
  const isAgent = !isNotify && message.author === 'agent';
  const isSystem = !isNotify && message.author === 'system';
  // A harness-origin row is a user-role prompt the human didn't type (scheduled
  // task / watch / webhook). Tag it so the user understands why the agent
  // replied without them sending anything (cyan "Scheduled task" / "Watch").
  const isHarness = !isNotify && !isAgent && !isSystem && message.source === 'harness';
  return (
    <div
      className={clsx(
        'flex flex-col gap-1 rounded-xl border px-4 py-3',
        isNotify
          ? 'border-gold/30 bg-gold/[0.06]'
          : isAgent
          ? 'border-mint/20 bg-mint/[0.04]'
          : isSystem
          ? 'border-border bg-foreground/[0.02]'
          : isHarness
          ? 'border-cyan/25 bg-cyan/[0.04]'
          : 'border-border bg-surface',
      )}
    >
      <div className="flex items-center gap-2 text-[10px]">
        {isHarness ? (
          <span className="inline-flex items-center gap-1 rounded border border-cyan/40 bg-cyan/[0.10] px-1.5 py-0 font-mono font-bold uppercase text-cyan">
            <Clock className="size-2.5" />
            {harnessLabel(message.author_name, t)}
          </span>
        ) : (
          <span
            className={clsx(
              'rounded border px-1.5 py-0 font-mono font-bold uppercase',
              isNotify
                ? 'border-gold/40 bg-gold/10 text-gold'
                : isAgent
                ? 'border-mint/40 bg-mint/[0.10] text-mint'
                : 'border-border-strong bg-foreground/[0.04] text-muted',
            )}
          >
            {isNotify ? t('chat.notifyLabel') : message.author}
          </span>
        )}
        {!isNotify && !isHarness && message.author_name && (
          <span className="font-semibold text-foreground">{message.author_name}</span>
        )}
        {isAgent && session.agent_name && <span className="font-mono text-muted">{session.agent_name}</span>}
        <span className="ml-auto font-mono text-muted">{formatLocalDateTime(message.created_at)}</span>
      </div>
      {/* Agent / system replies are markdown (render it); the user's own and
          harness-triggered prompts are shown verbatim as typed/sent. */}
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
