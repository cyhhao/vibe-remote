import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Bot, ChevronDown, Clock, Loader2, MessageSquare, Pencil, Plus, X } from 'lucide-react';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import { useWorkbenchInbox } from '../../context/WorkbenchInboxContext';
import type { VibeAgentBrief, WorkbenchMessage, WorkbenchSession } from '../../context/ApiContext';
import { apiFetch } from '../../lib/apiFetch';
import { formatLocalDateTime } from '../../lib/relativeTime';
import { fetchBackendModels } from '../../lib/backendModels';
import { resolveEffortOptions } from '../../lib/effortOptions';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Markdown } from '../ui/markdown';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import { Composer } from './Composer';

// While a turn is in flight, reconcile the working/Stop state against the
// controller on this cadence (the backend ``GET /turn-state`` is authoritative).
// This recovers a DROPPED ``turn.end`` without ever killing a live turn on a
// timer: there is no turn-duration timeout, so a long agent (which can run for
// hours) keeps Stop + the indicator for as long as ``/turn-state`` reports
// ``in_flight:true``; only an idle reading (past the post-send grace) clears it.
const WORKING_RECONCILE_INTERVAL_MS = 60 * 1000;

// Grace window after we optimistically set ``working`` from a local send before
// an idle ``/turn-state`` reading is trusted to CLEAR it. A just-sent turn isn't
// registered in the controller's in-flight map until POST→dispatch_async lands,
// so an idle snapshot taken inside that gap is a false negative — wait this long
// (comfortably above dispatch latency) before letting a reconnect/visibility
// idle check clear Stop. A genuinely stale turn (missed ``turn.end``) was set
// working far longer ago than this, so it still clears (Codex P2).
const WORKING_SETTLE_GRACE_MS = 4000;

// The transcript-visible message types — mirrors the server filter on
// ``GET /api/sessions/{id}/messages`` so the live ``message.new`` feed appends
// the same rows the initial load shows (assistant / tool_call are process log).
const isTranscriptMessage = (msg: WorkbenchMessage): boolean =>
  msg.type === 'user' ||
  msg.type === 'result' ||
  msg.type === 'error' ||
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
  // Lifecycle guards for ``syncTurnState``'s clear-on-idle (Codex P2):
  //  - ``turnEpochRef`` bumps every time a turn STARTS (local send / send-now /
  //    observed ``turn.start``). syncTurnState captures it before its request and
  //    refuses to clear if it changed meanwhile — so an idle snapshot can't stomp
  //    a turn that started WHILE the request was in flight.
  //  - ``workingSetAtRef`` records when we last set working true, so syncTurnState
  //    can ignore an idle reading that lands inside the post-send registration gap.
  const turnEpochRef = useRef(0);
  const workingSetAtRef = useRef(0);
  // A single pending "re-check after the post-send grace expires" timer + a ref
  // to the latest syncTurnState, so an idle reading that arrives INSIDE the grace
  // (which we can't trust to clear yet) still gets re-evaluated once the grace
  // passes — otherwise a quick turn whose turn.end was missed leaves Stop stuck
  // until the next reconcile poll (Codex P2).
  const graceResyncRef = useRef<number | null>(null);
  const syncTurnStateRef = useRef<(() => void) | null>(null);
  // Mark a turn as live: bump the epoch + stamp the time, then show Stop. Used by
  // every "a turn is starting now" path so clear-on-idle stays race-safe.
  const markWorking = useCallback(() => {
    turnEpochRef.current += 1;
    workingSetAtRef.current = Date.now();
    setWorking(true);
  }, []);
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
      // tail: the RECENT window (not the oldest page), so a missed latest row in
      // a long chat is actually recovered (Codex P2).
      const res = await api.listSessionMessages(sessionId, { limit: 50, tail: true });
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

  // The fire-and-forget turn survives browser disconnects, so a freshly loaded /
  // reconnected page asks the controller whether a turn is still in flight and
  // restores the working/Stop state to match (Codex P2). Authoritative in BOTH
  // directions: sets Stop when a turn is live, and clears a stale Stop (a
  // ``turn.end`` we missed while the socket was down) when the controller reports
  // idle — guarded so it can't drop a turn that's genuinely starting.
  const syncTurnState = useCallback(async () => {
    if (!sessionId) return;
    const epochAtRequest = turnEpochRef.current;
    try {
      const res = await api.getTurnState(sessionId);
      if (sessionId !== sessionIdRef.current) return;
      if (res.in_flight) {
        // markWorking (not setWorking): bump the epoch + timestamp so an OLDER
        // overlapping sync whose idle response lands AFTER this one can't clear
        // the Stop we just confirmed live — its captured epoch is now stale (P2).
        markWorking();
        return;
      }
      // Idle snapshot — clear the stale indicator, but only when it's safe:
      //  (1) no turn STARTED while this request was in flight (epoch unchanged) —
      //      otherwise we'd stomp a turn.start that raced our idle reading;
      //  (2) we're past the post-send registration grace — a turn we just sent may
      //      not be in the controller's in-flight map yet, making this idle a
      //      false negative.
      if (turnEpochRef.current !== epochAtRequest) return;
      const sinceSet = Date.now() - workingSetAtRef.current;
      if (sinceSet > WORKING_SETTLE_GRACE_MS) {
        setWorking(false);
      } else if (graceResyncRef.current === null) {
        // Idle INSIDE the grace: either the registration gap (don't clear) or a
        // quick turn that already finished and whose turn.end we missed (a
        // backgrounded tab). Re-check once the grace expires so the latter clears
        // instead of waiting out the next reconcile poll. One pending retry at a time.
        graceResyncRef.current = window.setTimeout(() => {
          graceResyncRef.current = null;
          syncTurnStateRef.current?.();
        }, WORKING_SETTLE_GRACE_MS - sinceSet + 50);
      }
    } catch {
      /* controller unreachable — leave the indicator as-is */
    }
  }, [api, sessionId, markWorking]);

  // Keep a ref to the latest syncTurnState so the grace-resync timer can call the
  // current closure without baking it into a dependency cycle.
  useEffect(() => {
    syncTurnStateRef.current = syncTurnState;
  }, [syncTurnState]);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      const [fetched, agentList, msgs, queued, draft, turnState] = await Promise.all([
        api.getSession(sessionId),
        api.listVibeAgents({ includeDisabled: false }),
        // Recent window (tail), so opening a long chat shows the latest
        // conversation, not its oldest page (Codex P2).
        api.listSessionMessages(sessionId, { limit: 50, tail: true }),
        api.listSessionQueue(sessionId),
        api.getSessionDraft(sessionId),
        api.getTurnState(sessionId).catch(() => ({ in_flight: false })),
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
      // Restore Stop for a turn that is still running (e.g. opened in another tab
      // or reloaded mid-turn). markWorking on the live branch so a racing
      // syncTurnState idle response can't clear it; an idle load is authoritative
      // for the fresh page, so clear directly (Codex P2).
      if (turnState.in_flight) markWorking();
      else setWorking(false);
    } catch (err: any) {
      // Only surface the error if we're still on the session that failed — a
      // stale failure must not stamp an error onto the chat the user moved to.
      if (sessionId === sessionIdRef.current) setError(err?.message ?? String(err));
    } finally {
      // Same guard: a stale load finishing must not flip the new session out of
      // its own loading state into a premature not-found / error view (Codex P2).
      if (sessionId === sessionIdRef.current) setLoading(false);
    }
  }, [api, sessionId, markWorking]);

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
    // Drop any pending grace-resync so it can't fire against the new session.
    if (graceResyncRef.current !== null) {
      window.clearTimeout(graceResyncRef.current);
      graceResyncRef.current = null;
    }
  }, [sessionId]);

  // Persistent per-session subscription: append every transcript-visible
  // ``message.new`` for THIS session for as long as the page is open. An agent
  // ``result`` ends the working state (the turn produced its reply). Harness
  // turns (scheduled / watch) flow through here too — their prompt + reply both
  // appear without the user having sent anything.
  useEffect(() => {
    if (!sessionId) return;
    const disconnect = api.connectWorkbenchEvents({
      // NB: match against sessionIdRef.current (the CURRENT route), NOT the
      // captured ``sessionId`` — there is a window after a chat switch before
      // React runs this subscription's cleanup, during which an event for the
      // PREVIOUS chat would otherwise pass the stale check and append into the
      // new chat (Codex P2).
      onMessageNew: (msg) => {
        if (msg.session_id !== sessionIdRef.current) return;
        if (!isTranscriptMessage(msg)) return;
        appendMessage(msg);
        // Don't clear ``working`` from a result row here: with the queue, a
        // result can belong to an EARLIER turn while a newer queued turn is
        // already running, so clearing on it would hide Stop on the live turn
        // (Codex P2). ``turn.end`` is the authoritative end signal; a dropped
        // turn.end is recovered by syncTurnState (reconnect / visibility / the
        // while-working reconcile poll).
      },
      onTurnStart: (data) => {
        // markWorking (not setWorking): bump the epoch so a syncTurnState idle
        // reading already in flight can't clear this freshly-started turn.
        if (data.session_id === sessionIdRef.current) markWorking();
      },
      onTurnEnd: (data) => {
        // The controller confirms the turn settled (terminal result, agent error,
        // or user cancel) — the authoritative end of the working state. There is
        // no turn-duration timeout, so this only fires on a REAL terminal signal.
        if (data.session_id === sessionIdRef.current) setWorking(false);
      },
      onQueueUpdated: (data) => {
        // The send-while-busy queue changed (enqueue / flush / per-item delete).
        if (data.session_id === sessionIdRef.current) void refreshQueue();
      },
      onConnected: () => {
        // Every (re)connect recovers any state missed while the socket was down:
        // dropped message rows, the queue, and whether a turn is still running.
        void reconcile();
        void refreshQueue();
        void syncTurnState();
      },
      onError: () => {
        // Browser EventSource auto-reconnects; keep the page usable.
      },
    });
    return disconnect;
  }, [api, sessionId, appendMessage, reconcile, refreshQueue, syncTurnState, markWorking]);

  // Mobile tabs (the common case for IM users) get backgrounded mid-turn; the
  // SSE feed can be suspended without a clean reconnect, dropping the reply.
  // Reconcile when the page becomes visible again so the answer + working state
  // catch up to durable storage.
  useEffect(() => {
    if (!sessionId) return;
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return;
      // A suspended tab can drop the reply AND the turn.end, so recover all
      // three: missed rows, the queue, and the working/Stop state (Codex P2).
      void reconcile();
      void refreshQueue();
      void syncTurnState();
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => document.removeEventListener('visibilitychange', onVisible);
  }, [sessionId, reconcile, refreshQueue, syncTurnState]);

  // Reconcile (don't kill) while a turn is in flight: there is no turn-duration
  // timeout, so a long agent can run for hours and must keep Stop + the indicator
  // the whole time. Instead of a force-clear timer, poll the controller's
  // authoritative ``GET /turn-state`` on an interval while ``working`` is true AND
  // the page is visible. ``syncTurnState``'s grace-guarded logic clears ``working``
  // only when the backend reports ``in_flight:false`` — so a dropped ``turn.end``
  // is recovered, while a still-running turn keeps Stop. Cleared when ``working``
  // flips false / on unmount; skipped while hidden (visibilitychange already
  // reconciles on resume).
  useEffect(() => {
    if (!working) return;
    const interval = window.setInterval(() => {
      if (document.visibilityState !== 'visible') return;
      void syncTurnState();
    }, WORKING_RECONCILE_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, [working, syncTurnState]);

  const sendMessage = useCallback(
    async (text: string) => {
      // NB: no ``working`` guard — sending WHILE a turn runs is the queue
      // feature; the backend enqueues it (202) instead of refusing.
      if (!sessionId || !text.trim()) return;
      markWorking();
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
        // If the user switched chats while this POST was in flight, the response
        // belongs to the previous session — don't append it / mutate working /
        // error on the chat they moved to (Codex P2). The turn still ran for the
        // original session; its rows live there.
        if (sessionId !== sessionIdRef.current) return;
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
        if (sessionId === sessionIdRef.current) {
          setWorking(false);
          setError(err?.message ?? String(err));
        }
      }
    },
    [sessionId, appendMessage, refreshQueue, markWorking],
  );

  const stopMessage = useCallback(async () => {
    if (!sessionId || !working) return;
    try {
      const res = await api.cancelSession(sessionId);
      // Drop a stale response after a chat switch — it must not clear B's
      // working or stamp A's error on B (Codex P2).
      if (sessionId !== sessionIdRef.current) return;
      // On success the backend is interrupted and the authoritative ``turn.end``
      // clears the working state, so we don't clear it here.
      if (res && res.ok === false) {
        if (res.code === 'not_in_flight') {
          // The controller has no running turn — our working state was stale
          // (a missed turn.end). Clear it instead of leaving Stop stuck (Codex P2).
          setWorking(false);
          void syncTurnState();
        } else {
          // The stop didn't reach the backend (e.g. 503); the turn may still be
          // live, so keep Stop available + surface the failure.
          setError(res.detail ? String(res.detail) : t('chat.stopFailed'));
        }
      }
    } catch (err: any) {
      // The cancel request itself threw (network) — surface it; keep Stop.
      if (sessionId === sessionIdRef.current) setError(err?.message ?? String(err));
    }
  }, [api, sessionId, working, t, syncTurnState]);

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
    // A turn is about to run (the flushed queue) — reflect it immediately so
    // Stop stays available even if the controller's turn.start is missed/delayed
    // (especially for the idle-flush case that starts a fresh turn) (Codex P2).
    markWorking();
    try {
      const res = await api.sendQueuedNow(sessionId, queue[0].id);
      // Drop the response if the user switched chats mid-request (Codex P2).
      if (sessionId !== sessionIdRef.current) return;
      if (res && res.ok === false) {
        // stop_failed: the controller left the ORIGINAL turn running and the
        // queue intact — keep Stop visible so the user can still interrupt it
        // (Codex P2). Other failures mean no turn is running → clear working.
        if (res.code !== 'stop_failed') setWorking(false);
        setError(res.detail ? String(res.detail) : t('chat.stopFailed'));
      } else if (res && (res as { status?: string }).status === 'empty') {
        // Nothing was actually flushed (a stale queue item already gone) — no
        // turn is starting, so drop the optimistic working state + resync.
        setWorking(false);
        void refreshQueue();
      }
    } catch (err: any) {
      // Same session guard as the success path: a rejection after a chat switch
      // must not clear the new chat's working / stamp this error on it (Codex P2).
      if (sessionId === sessionIdRef.current) {
        setWorking(false);
        setError(err?.message ?? String(err));
      }
    }
  }, [api, sessionId, queue, t, refreshQueue, markWorking]);

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
      const patchedId = session.id;
      try {
        const updated = await api.updateSession(session.id, changes as any);
        // Drop a stale response after a chat switch: if the user navigated to a
        // different chat (this ChatPage instance is reused) before the PATCH
        // resolved, installing A's session into B would show A's title/picker on
        // B and make later edits patch the wrong session.id (Codex P2). Mirrors
        // the sessionIdRef guards on send/cancel.
        if (patchedId !== sessionIdRef.current) return;
        setSession(updated);
      } catch (err: any) {
        if (patchedId === sessionIdRef.current) setError(err?.message ?? String(err));
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

const Compose: React.FC<ComposeProps> = ({ onSend, onStop, busy, initialDraft, onDraftChange }) => (
  // shrink-0 pins the bar at the bottom of the fixed-height chat container; the
  // gradient fades the transcript out behind it (no opaque band / hard border)
  // so the input sits close to the bottom edge. The input row is the shared
  // <Composer>, also used by the Workbench home.
  <div
    className="shrink-0 px-4 pb-4 pt-3 md:px-8"
    style={{ background: 'linear-gradient(to top, var(--background) 65%, transparent)' }}
  >
    <Composer
      onSend={onSend}
      onStop={onStop}
      busy={busy}
      initialDraft={initialDraft}
      onDraftChange={onDraftChange}
    />
  </div>
);

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
  // Claude reasoning efforts are MODEL-specific (newer Opus/Sonnet add
  // xhigh/max), so the backend returns them keyed by model (plus a '' default).
  // Cached from /api/claude/models so the effort column can offer exactly the
  // efforts the selected model supports instead of a static low/medium/high
  // that hides xhigh/max (Codex P2).
  const [claudeReasoning, setClaudeReasoning] = useState<Record<string, { value: string; label: string }[]>>({});
  const [loadingModels, setLoadingModels] = useState(false);
  const [patching, setPatching] = useState(false);

  // Serialize picker patches: an agent pick carries the agent's default
  // model/effort, so if it resolves AFTER a subsequent model/effort pick the
  // later choice would be rolled back to the defaults. One patch at a time, with
  // the items disabled while it's in flight (Codex P2).
  const applyPatch = useCallback(
    async (changes: Partial<WorkbenchSession>) => {
      if (patching) return;
      setPatching(true);
      try {
        await onPatch(changes);
      } finally {
        setPatching(false);
      }
    },
    [patching, onPatch],
  );

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
        // Shared resolver (lib/backendModels) — OpenCode's provider-prefixing
        // and the per-backend fetch live there so every model picker stays
        // consistent (Agents detail panel, New Agent dialog, this menu).
        const { models, reasoningOptions } = await fetchBackendModels(api, backend);
        if (!cancelled) {
          // Claude returns per-model effort sets so Column 3 can offer
          // xhigh/max only for the models that actually support them.
          if (reasoningOptions) setClaudeReasoning(reasoningOptions);
          setModelsByBackend((prev) => ({ ...prev, [backend]: models }));
        }
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

  // Effort options for Column 3. Claude is model-aware: prefer the selected
  // model's reasoning set, fall back to the backend's '' default set, then the
  // static list (before /api/claude/models has resolved). The '__default__'
  // sentinel is dropped — effort is cleared by switching agents, not via a
  // pseudo-option. Other backends keep their static superset (Codex P2).
  const effortOptions = useMemo(
    () => resolveEffortOptions(backend, currentModel, claudeReasoning),
    [backend, currentModel, claudeReasoning],
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          className="ml-auto inline-flex h-auto max-w-[62%] items-center justify-start gap-1.5 rounded-lg border-cyan/40 bg-surface-2 px-2.5 py-1.5 text-[12px] font-normal hover:bg-cyan/[0.06]"
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
              {/* Localize the selected effort through the SAME key the column uses,
                  so the closed trigger doesn't show raw `low`/`max` in zh builds
                  (Codex P2). Unknown values fall back to the key (then raw). */}
              <span className="shrink-0 text-[10px] capitalize text-muted">
                {t(`chat.picker.effortOptions.${currentEffort}`, { defaultValue: currentEffort })}
              </span>
            </>
          )}
          <ChevronDown className="size-3 shrink-0 text-muted" />
        </Button>
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
                    disabled={patching}
                    onClick={() =>
                      void applyPatch({
                        agent_name: agent.name,
                        agent_id: agent.id,
                        agent_backend: agent.backend,
                        // Track agent_variant to the backend: the persisted
                        // native-session map is keyed by agent_variant while
                        // Claude/Codex resume by backend name, so leaving a stale
                        // variant (old agent name / 'default') means the session
                        // can't resume its native thread after a restart and starts
                        // fresh (Codex P2).
                        agent_variant: agent.backend,
                        // Explicit null (not undefined, which JSON.stringify
                        // drops) so switching to an agent with no default model /
                        // effort CLEARS the previous agent's override server-side.
                        model: agent.model ?? null,
                        reasoning_effort: agent.reasoning_effort ?? null,
                      })
                    }
                  >
                    <span className="flex-1 truncate font-semibold">{agent.name}</span>
                    {agent.model && <span className="truncate font-mono text-[9px] text-muted">{agent.model}</span>}
                  </RouteItem>
                ))}
              </div>
            ))}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setOpen(false);
                navigate('/agents');
              }}
              className="mt-1 h-auto w-full justify-start gap-1.5 rounded px-2 py-1.5 text-[11px] font-medium text-cyan hover:bg-cyan/[0.08] hover:text-cyan"
            >
              <Plus className="size-3.5" />
              {t('chat.picker.newAgent')}
            </Button>
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
                <RouteItem
                  key={model}
                  active={model === currentModel}
                  disabled={patching}
                  onClick={() => {
                    const patch: Partial<WorkbenchSession> = { model };
                    // Switching to a Claude model whose effort set no longer includes
                    // the current effort (e.g. xhigh/max → a model without them):
                    // clear it in the SAME patch. Otherwise the header keeps showing
                    // /storing an effort the new model can't run — the backend drops
                    // it via normalize_claude_reasoning_effort, so the displayed route
                    // wouldn't match what actually dispatches (Codex P2).
                    if (backend === 'claude' && currentEffort) {
                      const opts = claudeReasoning[model];
                      if (opts && !opts.some((o) => o.value === currentEffort)) {
                        patch.reasoning_effort = null;
                      }
                    }
                    void applyPatch(patch);
                  }}
                >
                  <span className="flex-1 truncate font-mono text-[11px]">{model}</span>
                </RouteItem>
              ))
            )}
          </RouteColumn>

          {/* Column 3 — Effort (Claude: model-specific; others: backend superset) */}
          <RouteColumn title={t('chat.picker.effort')}>
            {effortOptions.map((opt) => (
              <RouteItem
                key={opt}
                active={opt === currentEffort}
                disabled={patching}
                onClick={() => void applyPatch({ reasoning_effort: opt })}
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

// A picker row built on the shared Button primitive (variant + className
// overrides) rather than a raw <button>, so it inherits the design system's
// focus/disabled behavior instead of re-rolling token classes (per AGENTS.md).
const RouteItem: React.FC<{
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}> = ({ active, onClick, disabled, children }) => (
  <Button
    type="button"
    variant="ghost"
    size="sm"
    onClick={onClick}
    disabled={disabled}
    className={clsx(
      'h-auto w-full justify-start gap-2 rounded px-2 py-1.5 text-left text-[12px] font-normal',
      active ? 'bg-cyan/[0.10] text-cyan hover:bg-cyan/[0.10] hover:text-cyan' : 'text-foreground hover:bg-foreground/[0.04]',
    )}
  >
    {children}
  </Button>
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
  // reply. Hide it the moment the last row is a fresh agent terminal — a
  // successful ``result`` OR a failed ``error`` both end the turn.
  const lastIsAgentResult =
    messages.length > 0 &&
    messages[messages.length - 1].author === 'agent' &&
    (messages[messages.length - 1].type === 'result' || messages[messages.length - 1].type === 'error');
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
