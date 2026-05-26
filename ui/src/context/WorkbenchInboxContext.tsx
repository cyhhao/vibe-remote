import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import { useApi } from './ApiContext';
import type { WorkbenchMessage } from './ApiContext';

interface InboxState {
  /** Per-scope unread counts (only agent-authored messages count). */
  unreadByScope: Record<string, number>;
  /** Sum of ``unreadByScope`` — cached so consumers don't re-derive. */
  totalUnread: number;
  /** Recent agent messages across all sessions (reverse-chronological). */
  recentMessages: WorkbenchMessage[];
  loading: boolean;
  refresh: () => Promise<void>;
  markRead: (sessionId: string, untilMessageId?: string) => Promise<void>;
}

const WorkbenchInboxContext = createContext<InboxState | undefined>(undefined);

/** Provider that owns the Inbox state shared across WorkbenchSidebar + InboxPage.
 *
 *  Connects to ``/api/events`` once on mount and updates state in place when
 *  new messages or unread-count changes arrive. The provider value is memoized
 *  per [[feedback_react_context_value_memoize]] so consumer ``useEffect``
 *  hooks that depend on context functions don't re-fire on every parent
 *  render. */
export const WorkbenchInboxProvider = ({ children }: { children: ReactNode }) => {
  const api = useApi();
  const [unreadByScope, setUnreadByScope] = useState<Record<string, number>>({});
  const [recentMessages, setRecentMessages] = useState<WorkbenchMessage[]>([]);
  const [loading, setLoading] = useState(false);
  // Avoid duplicate refresh-on-first-mount during StrictMode double-invoke.
  const initialFetched = useRef(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.listInbox({ platform: 'avibe', limit: 30 });
      setRecentMessages(result.messages);
      setUnreadByScope(result.unread_counts);
    } catch (err) {
      console.error('[inbox] refresh failed', err);
    } finally {
      setLoading(false);
    }
  }, [api]);

  const markRead = useCallback(
    async (sessionId: string, untilMessageId?: string) => {
      const result = await api.markSessionRead(sessionId, untilMessageId);
      setUnreadByScope(result.unread_counts);
    },
    [api],
  );

  useEffect(() => {
    if (!initialFetched.current) {
      initialFetched.current = true;
      refresh();
    }
    const disconnect = api.connectWorkbenchEvents({
      onMessageNew: (message) => {
        // Only agent turns belong on the inbox feed — the user's own
        // messages don't need to come back as "new" notifications.
        if (message.author !== 'agent' || message.platform !== 'avibe') return;
        setRecentMessages((prev) => {
          // Skip duplicates if the REST POST optimistic update already
          // added the row (rare for agent-authored events, but defensive).
          if (prev.some((m) => m.id === message.id)) return prev;
          return [message, ...prev].slice(0, 30);
        });
        if (!message.read_at && message.scope_id) {
          setUnreadByScope((prev) => ({
            ...prev,
            [message.scope_id as string]: (prev[message.scope_id as string] ?? 0) + 1,
          }));
        }
      },
      onInboxUnreadChanged: (data) => {
        if (data?.unread_counts) {
          setUnreadByScope(data.unread_counts);
        }
      },
      onError: (err) => {
        // Browser EventSource will auto-reconnect; keep this as a log
        // entry rather than a crash so the workbench stays usable when
        // the dev proxy is restarting / etc.
        console.debug('[inbox] sse error', err);
      },
    });
    return disconnect;
  }, [api, refresh]);

  const totalUnread = useMemo(
    () => Object.values(unreadByScope).reduce((sum, n) => sum + (n || 0), 0),
    [unreadByScope],
  );

  const value = useMemo<InboxState>(
    () => ({
      unreadByScope,
      totalUnread,
      recentMessages,
      loading,
      refresh,
      markRead,
    }),
    [unreadByScope, totalUnread, recentMessages, loading, refresh, markRead],
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
