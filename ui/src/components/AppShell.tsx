import React, { useEffect, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { ArrowLeft, ArrowRight, FolderTree, Hash, Inbox, LayoutDashboard, LayoutGrid, Menu, MonitorPlay, Plus, Settings, SlidersHorizontal, Sparkles, Users } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import { useApi } from '../context/ApiContext';
import { useStatus } from '../context/StatusContext';
import { useWorkbenchInbox } from '../context/WorkbenchInboxContext';
import { AccountMenu } from './AccountMenu';
import { LanguageSwitcher } from './LanguageSwitcher';
import { ThemeToggle } from './ThemeToggle';
import { VersionBadge } from './VersionBadge';
import { WorkbenchSidebar } from './workbench/WorkbenchSidebar';
import logoImg from '../assets/logo.png';
import { getEnabledPlatforms, platformSupportsChannels } from '../lib/platforms';

type ShellNavItem = {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  match?: (pathname: string) => boolean;
  badge?: number;
};

// Mirrors design.pen kSWgv (VR/Sidebar): 240px width, fill --surface,
// right border, padding [20,16]. Mint-soft active state with mint glow.
const ShellNavLink: React.FC<{ item: ShellNavItem }> = ({ item }) => {
  const location = useLocation();
  const active = item.match ? item.match(location.pathname) : location.pathname === item.to;
  const Icon = item.icon;

  return (
    <NavLink
      to={item.to}
      className={clsx(
        'group flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-[13px] font-medium transition-colors',
        active
          ? 'border border-mint/30 bg-mint/[0.08] text-foreground shadow-[0_0_16px_-4px_rgba(91,255,160,0.5)]'
          : 'border border-transparent text-muted hover:bg-foreground/[0.04] hover:text-foreground'
      )}
    >
      <Icon className={clsx('size-4', active ? 'text-mint' : 'text-muted group-hover:text-foreground')} />
      <span>{item.label}</span>
    </NavLink>
  );
};

const MobileNavLink: React.FC<{ item: ShellNavItem }> = ({ item }) => {
  const location = useLocation();
  const active = item.match ? item.match(location.pathname) : location.pathname === item.to;
  const Icon = item.icon;

  return (
    <NavLink
      to={item.to}
      className={clsx(
        'flex min-w-0 flex-1 flex-col items-center justify-center gap-1 rounded-lg px-1 py-2 text-[10px] transition-colors',
        active ? 'bg-mint/[0.08] text-mint' : 'text-muted'
      )}
    >
      <span className="relative">
        <Icon className="size-4" />
        {item.badge ? (
          <span className="absolute -right-2 -top-1.5 min-w-[14px] rounded-full bg-mint px-1 text-center font-mono text-[9px] font-bold leading-[14px] text-background">
            {item.badge > 99 ? '99+' : item.badge}
          </span>
        ) : null}
      </span>
      <span className="max-w-full truncate">{item.label}</span>
    </NavLink>
  );
};

type CenterButton = { to: string; label: string; icon: React.ComponentType<{ className?: string }> };

// Mobile bottom tab bar shared by both shells. Section tabs flank a raised
// center FAB. Workbench: center = ＋ (new session). Control Panel: center =
// Workbench (jump back) — the symmetric counterpart Alex asked for, so each
// shell can reach the other from the tab bar.
const MobileTabBar: React.FC<{ items: ShellNavItem[]; center: CenterButton }> = ({ items, center }) => {
  const half = Math.ceil(items.length / 2);
  const left = items.slice(0, half);
  const right = items.slice(half);
  const CenterIcon = center.icon;
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-surface/96 px-2 pt-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))] backdrop-blur md:hidden">
      <div className="flex items-end justify-between gap-1">
        {left.map((item) => <MobileNavLink key={item.to} item={item} />)}
        <div className="flex flex-1 justify-center">
          <Link
            to={center.to}
            aria-label={center.label}
            className="grid size-12 -translate-y-1 place-items-center rounded-full bg-mint text-background shadow-[0_8px_20px_-4px_rgba(91,255,160,0.6)] transition active:scale-95"
          >
            <CenterIcon className="size-6" />
          </Link>
        </div>
        {right.map((item) => <MobileNavLink key={item.to} item={item} />)}
      </div>
    </nav>
  );
};

export const AppShell: React.FC = () => {
  const { t } = useTranslation();
  const { status } = useStatus();
  const { totalUnread } = useWorkbenchInbox();
  const api = useApi();
  const location = useLocation();
  const [enabledPlatforms, setEnabledPlatforms] = useState<string[]>([]);
  const [config, setConfig] = useState<any>(null);

  useEffect(() => {
    api.getConfig().then((c: any) => {
      setConfig(c);
      setEnabledPlatforms(getEnabledPlatforms(c));
    }).catch(() => {});
  }, [api]);

  const hasChannelPlatforms = enabledPlatforms.some((platform) => platformSupportsChannels(config, platform));
  const isRunning = status.state === 'running';

  if (location.pathname === '/setup') {
    return <Outlet />;
  }

  // Two shell modes share the same chrome (brand + bottom status):
  //   - admin: control-panel pages under /admin/* (legacy dashboard/groups/...
  //     paths are now Navigate redirects to /admin/*).
  //   - workbench: the new `/` entry. Commit 01 ships a placeholder with no
  //     sidebar nav; commit 02 layers in the capability modules + projects.
  const shellMode: 'workbench' | 'admin' =
    location.pathname.startsWith('/admin') ? 'admin' : 'workbench';

  const adminItems: ShellNavItem[] = [
    { to: '/admin/dashboard', label: t('nav.dashboard'), icon: LayoutDashboard },
    ...(hasChannelPlatforms ? [{ to: '/admin/groups', label: t('nav.channels'), icon: Hash }] : []),
    { to: '/admin/users', label: t('nav.users'), icon: Users },
    { to: '/admin/show-pages', label: t('nav.showPages'), icon: MonitorPlay },
    {
      to: '/admin/settings/service',
      label: t('nav.settings'),
      icon: Settings,
      match: (pathname) => pathname.startsWith('/admin/settings'),
    },
  ];

  const items: ShellNavItem[] = shellMode === 'admin' ? adminItems : [];

  // Workbench mobile tabs flatten the (desktop-only) WorkbenchSidebar into a
  // bottom tab bar: Inbox / Projects / Capabilities / More, around a center
  // ＋ that opens the workbench canvas (new session). Capabilities routes to
  // Agents and stays active across the four capability pages.
  const workbenchTabs: ShellNavItem[] = [
    { to: '/inbox', label: t('nav.inbox'), icon: Inbox, badge: totalUnread },
    { to: '/projects', label: t('nav.projects'), icon: FolderTree },
    {
      to: '/agents',
      label: t('nav.capabilities'),
      icon: LayoutGrid,
      match: (p) => ['/agents', '/skills', '/harness', '/vaults'].some((x) => p.startsWith(x)),
    },
    { to: '/more', label: t('nav.more'), icon: Menu, match: (p) => p.startsWith('/more') },
  ];

  // Chat is a full-screen detail (own composer); the wizard owns the whole
  // viewport. Hide the bottom tab bar on both.
  const showBottomNav = !location.pathname.startsWith('/chat/') && location.pathname !== '/setup';

  return (
    <div className="min-h-screen min-h-[100dvh] bg-background text-foreground">
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-[240px] flex-col border-r border-border bg-surface md:flex">
        <div className="flex h-full flex-col justify-between gap-6 px-4 py-5">
          {/* Top: Brand + Workspace label + Nav list */}
          <div className="flex flex-col gap-6">
            <div className="flex items-center gap-2.5 px-1 py-2">
              <img
                src={logoImg}
                alt="Vibe Remote Logo"
                className="size-9 rounded-lg border border-mint/35 bg-mint/[0.08] object-cover shadow-[0_0_16px_-4px_rgba(91,255,160,0.5)]"
              />
              <div className="min-w-0">
                <div className="truncate text-[13px] font-semibold text-foreground">{t('appShell.title')}</div>
                <div className="truncate text-[11px] text-muted">{t('appShell.subtitle')}</div>
              </div>
            </div>

            {shellMode === 'admin' && items.length > 0 && (
              <div className="flex flex-col gap-2">
                <div className="px-1 font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-muted">
                  {t('appShell.workspaceLabel')}
                </div>
                <nav className="flex flex-col gap-0.5">
                  {items.map((item) => <ShellNavLink key={item.to} item={item} />)}
                </nav>
              </div>
            )}
            {shellMode === 'workbench' && <WorkbenchSidebar />}
          </div>

          {/* Bottom: Status (with embedded version badge) + toggles + hostname */}
          <div className="flex flex-col gap-3">
            <div
              className={clsx(
                'flex items-center gap-2.5 rounded-lg border px-3 py-2.5',
                isRunning
                  ? 'border-mint/30 bg-mint/[0.08]'
                  : 'border-border bg-foreground/[0.02]'
              )}
            >
              <span
                className={clsx(
                  'size-2 shrink-0 rounded-full',
                  isRunning ? 'bg-mint shadow-[0_0_8px_rgba(91,255,160,0.9)]' : 'bg-muted'
                )}
              />
              <div className="min-w-0 flex-1">
                <div className="text-[12px] font-medium text-foreground">
                  {isRunning ? t('common.running') : t('common.stopped')}
                </div>
                <div className="text-[10px] text-muted">{t('appShell.statusLabel')}</div>
              </div>
              <VersionBadge openUpward />
            </div>

            {/* Language / theme / account quick-toggles only show in the
                Control Panel, which is the operational surface. The
                Workbench sidebar stays focused on the agent task itself;
                the same controls are reachable by switching modes. */}
            {shellMode === 'admin' && (
              <div className="flex items-center gap-2">
                <LanguageSwitcher openUpward />
                <ThemeToggle />
                <AccountMenu openUpward />
              </div>
            )}

            {config?.runtime?.hostname && (
              <div className="truncate font-mono text-[10px] text-muted">
                {config.runtime.hostname}
              </div>
            )}

            {/* Mode switch — flips between Workbench (`/`) and Control Panel
                (`/admin/*`). Distinct visual hierarchy from the toggle row
                above so users notice it as a destination, not a quick toggle. */}
            {shellMode === 'workbench' ? (
              <Link
                to="/admin/dashboard"
                className="flex items-center justify-center gap-2 rounded-lg border border-border-strong px-3 py-2.5 text-[12px] font-medium text-foreground transition hover:bg-foreground/[0.04]"
              >
                <SlidersHorizontal className="size-3.5" />
                <span>{t('appShell.openControlPanel')}</span>
                <ArrowRight className="size-3 text-muted" />
              </Link>
            ) : (
              <Link
                to="/"
                className="flex items-center justify-center gap-2 rounded-lg border border-mint/30 bg-mint/[0.06] px-3 py-2.5 text-[12px] font-semibold text-mint transition hover:bg-mint/[0.12]"
              >
                <ArrowLeft className="size-3.5" />
                <span>{t('appShell.backToWorkbench')}</span>
              </Link>
            )}
          </div>
        </div>
      </aside>

      <header className="sticky top-0 z-40 flex h-16 items-center justify-between gap-2 border-b border-border bg-background/92 px-4 backdrop-blur md:hidden">
        <div className="flex min-w-0 items-center gap-2">
          <img
            src={logoImg}
            alt="Vibe Remote Logo"
            className="size-6 shrink-0 rounded-md border border-mint/30 bg-mint/[0.08] object-cover"
          />
          <span className="truncate text-[13px] font-semibold">{t('appShell.title')}</span>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <VersionBadge />
          <LanguageSwitcher />
          <ThemeToggle />
          <AccountMenu />
        </div>
      </header>

      <main
        className={clsx(
          'min-h-screen md:ml-[240px] md:pb-0',
          showBottomNav ? 'pb-[calc(5.5rem+env(safe-area-inset-bottom))]' : 'pb-0',
          location.pathname.startsWith('/admin/settings') ? 'page-glow-settings' : 'page-glow-console'
        )}
      >
        <div className="mx-auto w-full px-4 py-5 md:px-10 md:py-8">
          <Outlet />
        </div>
      </main>

      {showBottomNav && (
        shellMode === 'admin' ? (
          <MobileTabBar
            items={adminItems}
            center={{ to: '/', label: t('appShell.backToWorkbench'), icon: Sparkles }}
          />
        ) : (
          <MobileTabBar
            items={workbenchTabs}
            center={{ to: '/', label: t('appShell.newSession'), icon: Plus }}
          />
        )
      )}
    </div>
  );
};
