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
 *  Connects to ``/api/events`` once on mount and updates the per-session feed in
 *  place: ``inbox.session.updated`` upserts + re-sorts a card (the realtime
 *  "bump to top"), ``inbox.unread.changed`` refreshes the unread map after a
 *  mark-read elsewhere. The provider value is memoized per
 *  [[feedback_react_context_value_memoize]] so consumer ``useEffect`` hooks that
 *  depend on context functions don't re-fire on every parent render. */
export const WorkbenchInboxProvider = ({ children }: { children: ReactNode }) => {
  const api = useApi();
  const [inboxSessions, setInboxSessions] = useState<InboxSession[]>([]);
  const [unreadBySession, setUnreadBySession] = useState<Record<string, number>>({});
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  // Avoid duplicate refresh-on-first-mount during StrictMode double-invoke.
  const initialFetched = useRef(false);
  // Mirror the cursor into a ref so ``loadMore`` can read the latest value
  // without re-creating its identity (and the context value) on every page.
  const cursorRef = useRef<string | null>(null);
  cursorRef.current = nextCursor;

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

  useEffect(() => {
    if (!initialFetched.current) {
      initialFetched.current = true;
      refresh();
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
        // Browser EventSource auto-reconnects; keep this a log, not a crash,
        // so the workbench stays usable while the dev proxy restarts / etc.
        console.debug('[inbox] sse error', err);
      },
    });
    return disconnect;
  }, [api, refresh]);

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
