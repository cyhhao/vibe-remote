import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { NavLink, useNavigate } from 'react-router-dom';
import {
  Activity,
  ArrowRight,
  Bot,
  CheckCheck,
  ChevronRight,
  Folder,
  Inbox,
  KeyRound,
  Plus,
  WandSparkles,
} from 'lucide-react';
import clsx from 'clsx';
import type { LucideIcon } from 'lucide-react';

import { useWorkbenchInbox } from '../../context/WorkbenchInboxContext';
import type { WorkbenchMessage } from '../../context/ApiContext';
import { formatRelativeTime } from '../../lib/relativeTime';

interface CapabilityNavItem {
  to: string;
  i18nKey: string;
  icon: LucideIcon;
}

const CAPABILITY_NAV: CapabilityNavItem[] = [
  { to: '/agents', i18nKey: 'workbench.nav.agents', icon: Bot },
  { to: '/skills', i18nKey: 'workbench.nav.skills', icon: WandSparkles },
  { to: '/harness', i18nKey: 'workbench.nav.harness', icon: Activity },
  { to: '/vaults', i18nKey: 'workbench.nav.vaults', icon: KeyRound },
];

// 360px floating popover that opens when the user hovers the Inbox entry.
// Mirrors design.pen KmQ1L — header + a few rows + footer "open full inbox"
// link. Pure presentational; data comes from <WorkbenchInboxProvider>.
const InboxHoverPopover: React.FC<{
  visible: boolean;
  messages: WorkbenchMessage[];
  totalUnread: number;
  onItemClick: (message: WorkbenchMessage) => void;
  onMarkAllRead: () => void;
  onMouseEnter: () => void;
  onMouseLeave: () => void;
}> = ({ visible, messages, totalUnread, onItemClick, onMarkAllRead, onMouseEnter, onMouseLeave }) => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  if (!visible) return null;
  const shown = messages.slice(0, 5);
  const isUnread = (m: WorkbenchMessage) => m.author === 'agent' && !m.read_at;
  return (
    <div
      role="dialog"
      aria-label={t('workbench.inbox.title')}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      className="absolute left-full top-0 z-50 ml-3 flex w-[360px] flex-col gap-2.5 rounded-2xl border border-border-strong bg-surface-2 p-3.5 shadow-[0_24px_64px_-12px_rgba(0,0,0,0.6)]"
    >
      <div className="flex items-start gap-2">
        <div className="flex flex-1 flex-col">
          <div className="text-[13px] font-bold text-foreground">{t('workbench.inbox.title')}</div>
          <div className="text-[10px] text-muted">
            {t('workbench.inbox.headerCount', { unread: totalUnread, total: messages.length })}
          </div>
        </div>
        <button
          type="button"
          onClick={onMarkAllRead}
          disabled={totalUnread === 0}
          className={clsx(
            'rounded-md border px-2 py-1 text-[10px] font-medium transition',
            totalUnread === 0
              ? 'cursor-not-allowed border-border bg-foreground/[0.02] text-muted'
              : 'border-border-strong text-foreground hover:bg-foreground/[0.04]',
          )}
        >
          {t('workbench.inbox.markAllRead')}
        </button>
      </div>

      {shown.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-6 text-center text-[12px] text-muted">
          {t('workbench.inbox.empty')}
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          {shown.map((m) => {
            const unread = isUnread(m);
            const projectId = m.scope_id ? m.scope_id.split('::').pop() : null;
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => onItemClick(m)}
                className={clsx(
                  'flex flex-col gap-1.5 rounded-lg px-3 py-2.5 text-left transition',
                  unread
                    ? 'border-l-2 border-mint bg-mint/[0.06] hover:bg-mint/[0.10]'
                    : 'hover:bg-foreground/[0.04]',
                )}
              >
                <div className="flex items-center gap-1.5 text-[10px]">
                  <span className="truncate font-mono font-semibold text-cyan">{projectId || 'avibe'}</span>
                  <span className="text-muted">·</span>
                  <span className="flex-1 truncate font-semibold text-foreground">
                    {m.metadata?.session_title as string | undefined || m.session_id}
                  </span>
                  <span className="font-mono text-muted">
                    {formatRelativeTime(m.created_at, t)}
                  </span>
                </div>
                <div
                  className={clsx(
                    'line-clamp-2 text-[11.5px] leading-relaxed',
                    unread ? 'text-foreground' : 'text-muted',
                  )}
                >
                  {m.text || '—'}
                </div>
              </button>
            );
          })}
        </div>
      )}

      <button
        type="button"
        onClick={() => navigate('/inbox')}
        className="flex items-center justify-center gap-1.5 rounded-md pt-1 text-[11px] font-medium text-cyan hover:underline"
      >
        {t('workbench.inbox.viewAll')}
        <ArrowRight className="size-3" />
      </button>
    </div>
  );
};


export const WorkbenchSidebar: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { totalUnread, recentMessages, markRead } = useWorkbenchInbox();
  const [popoverOpen, setPopoverOpen] = useState(false);
  const closeTimer = useRef<number | null>(null);

  // Small open/close delays so the popover doesn't flicker as the cursor
  // brushes through the inbox row on its way somewhere else, and survives
  // the gap between the row and the popover body.
  const openPopover = () => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
    setPopoverOpen(true);
  };
  const queueClose = () => {
    if (closeTimer.current !== null) {
      window.clearTimeout(closeTimer.current);
    }
    closeTimer.current = window.setTimeout(() => {
      setPopoverOpen(false);
      closeTimer.current = null;
    }, 180);
  };
  useEffect(() => {
    return () => {
      if (closeTimer.current !== null) window.clearTimeout(closeTimer.current);
    };
  }, []);

  const onItemClick = (message: WorkbenchMessage) => {
    setPopoverOpen(false);
    if (message.session_id) {
      navigate(`/chat/${encodeURIComponent(message.session_id)}`);
      if (!message.read_at) markRead(message.session_id);
    } else {
      navigate('/inbox');
    }
  };

  const onMarkAllRead = async () => {
    // Mark every session with at least one unread agent message as read.
    // Cheaper than a dedicated endpoint for now since we already know the
    // candidate session ids from recentMessages.
    const sessionsToMark = new Set<string>();
    for (const m of recentMessages) {
      if (m.author === 'agent' && !m.read_at && m.session_id) {
        sessionsToMark.add(m.session_id);
      }
    }
    await Promise.all(Array.from(sessionsToMark).map((id) => markRead(id)));
  };

  const badge = useMemo(() => {
    if (totalUnread <= 0) return null;
    return totalUnread > 99 ? '99+' : String(totalUnread);
  }, [totalUnread]);

  return (
    <div className="flex flex-col gap-4">
      {/* Inbox entry — hover opens the floating popover. The wrapper element
          owns the group's hover state so moving from the row into the popover
          stays inside the open zone. */}
      <div
        className="relative"
        onMouseEnter={openPopover}
        onMouseLeave={queueClose}
      >
        <NavLink
          to="/inbox"
          className={({ isActive }) =>
            clsx(
              'group flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-[13px] font-semibold transition-colors',
              isActive
                ? 'border-mint/30 bg-mint/[0.08] text-foreground shadow-[0_0_16px_-4px_rgba(91,255,160,0.5)]'
                : 'border-border-strong text-foreground hover:bg-foreground/[0.04]',
            )
          }
        >
          {({ isActive }) => (
            <>
              <Inbox className={clsx('size-4', isActive ? 'text-mint' : 'text-foreground')} />
              <span className="flex-1">{t('workbench.nav.inbox')}</span>
              {badge && (
                <span className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-mint px-1.5 py-0.5 font-mono text-[9px] font-bold text-[#080812] shadow-[0_0_10px_-2px_rgba(91,255,160,0.7)]">
                  {badge}
                </span>
              )}
              <ChevronRight className="size-3.5 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
            </>
          )}
        </NavLink>
        <InboxHoverPopover
          visible={popoverOpen}
          messages={recentMessages}
          totalUnread={totalUnread}
          onItemClick={onItemClick}
          onMarkAllRead={onMarkAllRead}
          onMouseEnter={openPopover}
          onMouseLeave={queueClose}
        />
      </div>

      <div className="flex flex-col gap-2">
        <div className="px-1 font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-muted">
          {t('workbench.capabilitiesLabel')}
        </div>
        <nav className="flex flex-col gap-0.5">
          {CAPABILITY_NAV.map(({ to, i18nKey, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                clsx(
                  'group flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-[13px] font-medium transition-colors',
                  isActive
                    ? 'border border-mint/30 bg-mint/[0.08] text-foreground shadow-[0_0_16px_-4px_rgba(91,255,160,0.5)]'
                    : 'border border-transparent text-muted hover:bg-foreground/[0.04] hover:text-foreground',
                )
              }
            >
              {({ isActive }) => (
                <>
                  <Icon className={clsx('size-4', isActive ? 'text-mint' : 'text-muted group-hover:text-foreground')} />
                  <span>{t(i18nKey)}</span>
                </>
              )}
            </NavLink>
          ))}
        </nav>
      </div>

      <div className="flex flex-col gap-2">
        {/* Empty-state card sits directly under capability nav; the
            "Projects" label was redundant once the section's only
            content is the empty state + dashed border. */}
        <div className="relative flex flex-col items-center gap-1.5 rounded-lg border border-dashed border-border px-3 py-4 text-center">
          <button
            type="button"
            aria-label={t('workbench.addProject')}
            className="absolute right-2 top-2 flex size-5 items-center justify-center rounded-md border border-border-strong text-foreground transition hover:bg-foreground/[0.04]"
            disabled
          >
            <Plus className="size-3" />
          </button>
          <Folder className="size-4 text-muted" />
          <div className="text-[11px] text-muted">{t('workbench.projectsEmpty')}</div>
        </div>
      </div>
    </div>
  );
};

// Re-export for tests / future inbox-specific UIs.
export { InboxHoverPopover };
// Silence the import that exists only for the un-used CheckCheck icon —
// it'll be wired into the full Inbox view in commit 09 too.
void CheckCheck;
