import React from 'react';
import { Link } from 'react-router-dom';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import {
  MessageSquare,
  PlugZap,
  Server,
  Sparkles,
  Stethoscope,
} from 'lucide-react';

type SettingsTab = 'service' | 'platforms' | 'backends' | 'messaging' | 'diagnostics';

const TABS: Array<{
  key: SettingsTab;
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  { key: 'service', href: '/settings/service', label: 'settings.tabs.service', icon: Server },
  { key: 'platforms', href: '/settings/platforms', label: 'settings.tabs.platforms', icon: PlugZap },
  { key: 'backends', href: '/settings/backends', label: 'settings.tabs.backends', icon: Sparkles },
  { key: 'messaging', href: '/settings/messaging', label: 'settings.tabs.messaging', icon: MessageSquare },
  { key: 'diagnostics', href: '/settings/diagnostics', label: 'settings.tabs.diagnostics', icon: Stethoscope },
];

export type SettingsPageShellProps = {
  title: string;
  subtitle: string;
  activeTab: SettingsTab;
  breadcrumb?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
};

// Mirrors design.pen l6PdZd → wH3uC (sTabs):
// underline tabs over a 1px --border baseline. Each tab is padding [10, 16],
// 13px Inter, gap 8 between icon + label. Inactive: muted text/icon, font 500.
// Active: mint 2px bottom border, foreground text, mint icon, font 600.
export const SettingsPageShell: React.FC<SettingsPageShellProps> = ({
  title,
  subtitle,
  activeTab,
  breadcrumb,
  actions,
  children,
}) => {
  const { t } = useTranslation();

  return (
    <div className="flex flex-col gap-6">
      {breadcrumb && <div className="font-mono text-[11px] text-muted">{breadcrumb}</div>}

      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <h1 className="text-[28px] font-bold leading-tight tracking-[-0.4px] text-foreground">{title}</h1>
          <p className="max-w-3xl text-[14px] leading-[1.55] text-muted">{subtitle}</p>
        </div>
        {actions && <div className="shrink-0">{actions}</div>}
      </div>

      <div className="border-b border-border">
        <nav className="-mb-px flex flex-wrap gap-1" aria-label="Settings sections">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const active = tab.key === activeTab;
            return (
              <Link
                key={tab.key}
                to={tab.href}
                className={clsx(
                  'inline-flex items-center gap-2 border-b-2 px-4 py-2.5 text-[13px] transition-colors',
                  active
                    ? 'border-mint font-semibold text-foreground'
                    : 'border-transparent font-medium text-muted hover:border-border-strong hover:text-foreground'
                )}
              >
                <Icon className={clsx('size-3.5', active ? 'text-mint' : 'text-muted')} />
                {t(tab.label)}
              </Link>
            );
          })}
        </nav>
      </div>

      <div className="flex flex-col gap-4">{children}</div>
    </div>
  );
};
