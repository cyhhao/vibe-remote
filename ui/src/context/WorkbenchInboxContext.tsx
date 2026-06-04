import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { useApi } from './ApiContext';
import type { InboxSession } from './ApiContext';

const PAGE_SIZE = 30;

interface InboxState {
  /** Per-session ("Slack-like") feed: one card per conversation, newest
   *  activity first. Driven by realtime ``inbox.session.updated`` upserts. */
  inboxSessions: InboxSession[];
  /** Pagination-independent per-session unread counts — the sidebar badges
   *  each session row from this (a session with unread may sit past the first
   *  inbox page, so the feed array alone isn't a complete source). */
  unreadBySession: Record<string, number>;
  /** Sum of ``unreadBySession`` — the Inbox nav badge. */
  totalUnread: number;
  /** Number of sessions with ≥1 unread reply — the header "N unread" count. */
  unreadSessions: number;
  /** Keyset cursor for "load more"; null when the feed is fully loaded. */
  nextCursor: string | null;
  loading: boolean;
  loadingMore: boolean;
  refresh: () => Promise<void>;
  loadMore: () => Promise<void>;
  markRead: (sessionId: string, untilMessageId?: string) => Promise<void>;
}

const WorkbenchInboxContext = createContext<InboxState | undefined>(undefined);

// Sort matches the backend keyset order: last activity (any author) desc, then
// session_id desc as the stable tie-break, so client upserts stay consistent
// with server-paginated pages.
const byActivityDesc = (a: InboxSession, b: InboxSession): number => {
  if (a.last_activity_at !== b.last_activity_at) {
    return a.last_activity_at < b.last_activity_at ? 1 : -1;
  }
  if (a.session_id === b.session_id) return 0;
  return a.session_id < b.session_id ? 1 : -1;
};

const upsertSession = (list: InboxSession[], row: InboxSession): InboxSession[] => {
  const next = list.filter((s) => s.session_id !== row.session_id);
  next.push(row);
  next.sort(byActivityDesc);
  return next;
};

const appendPage = (prev: InboxSession[], page: InboxSession[]): InboxSession[] => {
  const seen = new Set(prev.map((s) => s.session_id));
  const merged = prev.concat(page.filter((s) => !seen.has(s.session_id)));
  merged.sort(byActivityDesc);
  return merged;
};

/** Provider that owns the Inbox state shared across WorkbenchSidebar + InboxPage.
 *
 *  Connects to ``/api/events`` (reopening the stream on resume — see the resync
 *  effect) and updates the per-session feed in place: ``inbox.session.updated``
 *  upserts + re-sorts a card (the realtime "bump to top"), ``inbox.unread.changed``
 *  refreshes the unread map after a mark-read elsewhere. Each (re)connect also
 *  does a full ``refresh()`` so events missed while the socket was down (the
 *  broker has no replay) are recovered. The provider value is memoized per
 *  [[feedback_react_context_value_memoize]] so consumer ``useEffect`` hooks that
 *  depend on context functions don't re-fire on every parent render. */
export const WorkbenchInboxProvider = ({ children }: { children: ReactNode }) => {
  const api = useApi();
  const [inboxSessions, setInboxSessions] = useState<InboxSession[]>([]);
  const [unreadBySession, setUnreadBySession] = useState<Record<string, number>>({});
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  // Bumped on resume (tab visible again / network back) to force the SSE
  // connection effect to tear down the (possibly dead) stream and reopen it.
  // See the resync effect below for why a frozen mobile PWA needs this.
  const [connectionEpoch, setConnectionEpoch] = useState(0);
  // Mirror the cursor into a ref so ``loadMore`` can read the latest value
  // without re-creating its identity (and the context value) on every page.
  const cursorRef = useRef<string | null>(null);
  cursorRef.current = nextCursor;
  // Mirror the loaded feed so ``reconcile`` can size its re-read to the current
  // window without depending on (and re-identifying with) ``inboxSessions``.
  const inboxSessionsRef = useRef<InboxSession[]>([]);
  inboxSessionsRef.current = inboxSessions;
  // Only the very first mount does the destructive first-page refresh; every
  // later effect rerun — an ``api`` identity change (e.g. a locale switch, which
  // ApiProvider documents as rebuilding the value) or a resume-driven
  // connectionEpoch bump — reconciles the loaded window instead, so a non-resume
  // rerun never collapses a multi-page feed back to page one.
  const initialFetched = useRef(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.listInbox({ platform: 'avibe', limit: PAGE_SIZE });
      setInboxSessions(result.sessions);
      setNextCursor(result.next_cursor);
      setUnreadBySession(result.unread_by_session ?? {});
    } catch (err) {
      console.error('[inbox] refresh failed', err);
    } finally {
      setLoading(false);
    }
  }, [api]);

  const loadMore = useCallback(async () => {
    const cursor = cursorRef.current;
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const result = await api.listInbox({ platform: 'avibe', limit: PAGE_SIZE, before: cursor });
      setInboxSessions((prev) => appendPage(prev, result.sessions));
      setNextCursor(result.next_cursor);
    } catch (err) {
      console.error('[inbox] load more failed', err);
    } finally {
      setLoadingMore(false);
    }
  }, [api]);

  const markRead = useCallback(
    async (sessionId: string, untilMessageId?: string) => {
      const result = await api.markSessionRead(sessionId, untilMessageId);
      // The unread map is authoritative for badges; the card's unread styling
      // derives from it, so clearing here clears the dot without touching the
      // feed order (a read doesn't change last activity).
      setUnreadBySession(result.unread_by_session ?? {});
    },
    [api],
  );

  // Resume reconcile: re-read the feed WITHOUT collapsing pagination. A
  // visibility/online resume can fire after the user has loaded several pages;
  // a plain first-page refresh() would drop every row past page 1 and reset the
  // cursor. Re-read enough rows to cover what's loaded (capped at the API's
  // 100-row max) and merge in place so existing rows update and any sessions
  // that arrived during the gap surface at top. No loading flag — the user
  // already has content; this is a silent catch-up.
  const reconcile = useCallback(async () => {
    const limit = Math.min(Math.max(inboxSessionsRef.current.length, PAGE_SIZE), 100);
    try {
      const result = await api.listInbox({ platform: 'avibe', limit });
      setInboxSessions((prev) => {
        const incoming = new Map(result.sessions.map((s) => [s.session_id, s]));
        const merged = prev.map((s) => incoming.get(s.session_id) ?? s);
        const have = new Set(prev.map((s) => s.session_id));
        for (const s of result.sessions) if (!have.has(s.session_id)) merged.push(s);
        merged.sort(byActivityDesc);
        return merged;
      });
      // Whole-account unread map (not paginated) — always authoritative.
      setUnreadBySession(result.unread_by_session ?? {});
      // Cursor: keep the existing one when it's non-null (we only re-read the
      // already-loaded window, we didn't advance pagination). But if the feed
      // was previously exhausted (null cursor) and the gap pushed it past the
      // reconciled window, adopt the new cursor so "Load more" reappears for the
      // overflow rows instead of staying hidden.
      setNextCursor((prev) => prev ?? result.next_cursor);
    } catch (err) {
      console.error('[inbox] reconcile failed', err);
    }
  }, [api]);

  useEffect(() => {
    // First mount loads page one; every later rerun reconciles the loaded window
    // instead — whether the rerun is a resume-driven connectionEpoch bump or just
    // an ``api`` identity change (e.g. a locale switch rebuilding the value) — so
    // a non-resume rerun never collapses a multi-page feed back to page one. The
    // broker fans events out live with no replay (sse_broker.py ``/api/events``),
    // so anything missed while the socket was down must be re-read; plain HTTP,
    // independent of whether the SSE stream itself comes back up.
    if (!initialFetched.current) {
      initialFetched.current = true;
      void refresh();
    } else {
      void reconcile();
    }
    const disconnect = api.connectWorkbenchEvents({
      onInboxSessionUpdated: (row) => {
        setInboxSessions((prev) => upsertSession(prev, row));
        setUnreadBySession((prev) => {
          if ((prev[row.session_id] ?? 0) === row.unread_count) return prev;
          const next = { ...prev };
          if (row.unread_count > 0) next[row.session_id] = row.unread_count;
          else delete next[row.session_id];
          return next;
        });
      },
      onInboxUnreadChanged: (data) => {
        if (data?.unread_by_session) {
          setUnreadBySession(data.unread_by_session);
        }
      },
      onError: (err) => {
        // Browser EventSource auto-reconnects on transient drops; the
        // visibility/online resync below covers what it can't — a frozen mobile
        // tab whose socket died without ever firing a clean error. Keep this a
        // log, not a crash, so the workbench stays usable.
        console.debug('[inbox] sse error', err);
      },
    });
    return disconnect;
  }, [api, refresh, reconcile, connectionEpoch]);

  // Recover after the OS suspended us. A backgrounded mobile PWA has its page
  // frozen and its SSE socket dropped, and the broker never replays the gap; on
  // iOS the stream frequently does NOT auto-reconnect (it can sit in a zombie
  // OPEN state that never fires onerror), so neither the live handlers nor the
  // refresh above re-fire on their own. Bump the connection epoch when the tab
  // becomes visible again or the network returns: the effect above tears the
  // dead stream down, reopens it, and re-reads the feed + unread map — so the
  // inbox cards and sidebar dots catch up to messages that arrived while away.
  // StatusContext (runtime status) and ChatPage (transcript) already resync on
  // visibility; the inbox feed was the surface missing it.
  useEffect(() => {
    const resync = () => {
      if (document.visibilityState === 'visible') setConnectionEpoch((e) => e + 1);
    };
    document.addEventListener('visibilitychange', resync);
    window.addEventListener('online', resync);
    return () => {
      document.removeEventListener('visibilitychange', resync);
      window.removeEventListener('online', resync);
    };
  }, []);

  const totalUnread = useMemo(
    () => Object.values(unreadBySession).reduce((sum, n) => sum + (n || 0), 0),
    [unreadBySession],
  );
  const unreadSessions = useMemo(
    () => Object.values(unreadBySession).filter((n) => (n || 0) > 0).length,
    [unreadBySession],
  );

  const value = useMemo<InboxState>(
    () => ({
      inboxSessions,
      unreadBySession,
      totalUnread,
      unreadSessions,
      nextCursor,
      loading,
      loadingMore,
      refresh,
      loadMore,
      markRead,
    }),
    [
      inboxSessions,
      unreadBySession,
      totalUnread,
      unreadSessions,
      nextCursor,
      loading,
      loadingMore,
      refresh,
      loadMore,
      markRead,
    ],
  );

  return <WorkbenchInboxContext.Provider value={value}>{children}</WorkbenchInboxContext.Provider>;
};

export const useWorkbenchInbox = (): InboxState => {
  const ctx = useContext(WorkbenchInboxContext);
  if (ctx === undefined) {
    throw new Error('useWorkbenchInbox must be used inside <WorkbenchInboxProvider>');
  }
  return ctx;
};
