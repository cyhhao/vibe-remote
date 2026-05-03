import React, { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { Hash, LayoutDashboard, Settings, Users } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import { useApi } from '../context/ApiContext';
import { useStatus } from '../context/StatusContext';
import { LanguageSwitcher } from './LanguageSwitcher';
import { ThemeToggle } from './ThemeToggle';
import { VersionBadge } from './VersionBadge';
import logoImg from '../assets/logo.png';
import { getEnabledPlatforms, platformSupportsChannels } from '../lib/platforms';

type ShellNavItem = {
  to: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
  match?: (pathname: string) => boolean;
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
          : 'border border-transparent text-muted hover:bg-white/[0.04] hover:text-foreground'
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
      <Icon className="size-4" />
      <span className="max-w-full truncate">{item.label}</span>
    </NavLink>
  );
};

export const AppShell: React.FC = () => {
  const { t } = useTranslation();
  const { status } = useStatus();
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

  const items: ShellNavItem[] = [
    { to: '/dashboard', label: t('nav.dashboard'), icon: LayoutDashboard },
    ...(hasChannelPlatforms ? [{ to: '/groups', label: t('nav.channels'), icon: Hash }] : []),
    { to: '/users', label: t('nav.users'), icon: Users },
    {
      to: '/settings/service',
      label: t('nav.settings'),
      icon: Settings,
      match: (pathname) => pathname.startsWith('/settings'),
    },
  ];

  // Mobile nav uses the same routes as desktop; diagnostics lives under
  // the Settings tab so we don't promote it to its own bottom-nav slot.
  const mobileItems = items;

  return (
    <div className="min-h-screen min-h-[100dvh] bg-background text-foreground">
      <aside className="fixed inset-y-0 left-0 hidden w-[240px] flex-col border-r border-border bg-surface md:flex">
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

            <div className="flex flex-col gap-2">
              <div className="px-1 font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-muted">
                {t('appShell.workspaceLabel')}
              </div>
              <nav className="flex flex-col gap-0.5">
                {items.map((item) => <ShellNavLink key={item.to} item={item} />)}
              </nav>
            </div>
          </div>

          {/* Bottom: Status (with embedded version badge) + toggles + hostname */}
          <div className="flex flex-col gap-3">
            <div
              className={clsx(
                'flex items-center gap-2.5 rounded-lg border px-3 py-2.5',
                isRunning
                  ? 'border-mint/30 bg-mint/[0.08]'
                  : 'border-border bg-white/[0.02]'
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

            <div className="flex items-center gap-2">
              <LanguageSwitcher openUpward />
              <ThemeToggle />
            </div>

            {config?.runtime?.hostname && (
              <div className="truncate font-mono text-[10px] text-muted">
                {config.runtime.hostname}
              </div>
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
        </div>
      </header>

      <main
        className={clsx(
          'min-h-screen pb-[calc(5.5rem+env(safe-area-inset-bottom))] md:ml-[240px] md:pb-0',
          location.pathname.startsWith('/settings') ? 'page-glow-settings' : 'page-glow-console'
        )}
      >
        <div className="mx-auto w-full px-4 py-5 md:px-10 md:py-8">
          <Outlet />
        </div>
      </main>

      <nav className="fixed bottom-0 left-0 right-0 z-50 border-t border-border bg-surface/96 px-2 py-2 pb-[calc(0.5rem+env(safe-area-inset-bottom))] backdrop-blur md:hidden">
        <div className="flex gap-1">
          {mobileItems.map((item) => <MobileNavLink key={item.to} item={item} />)}
        </div>
      </nav>
    </div>
  );
};
