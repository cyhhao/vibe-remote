import React from 'react';
import { Link } from 'react-router-dom';
import clsx from 'clsx';
import { useTranslation } from 'react-i18next';
import {
  AlertCircle,
  Bot,
  MessageSquare,
  Settings as SettingsIcon,
  Stethoscope,
} from 'lucide-react';

type SettingsTab = 'service' | 'platforms' | 'backends' | 'messaging' | 'diagnostics';

const TABS: Array<{
  key: SettingsTab;
  href: string;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}> = [
  { key: 'service', href: '/settings/service', label: 'settings.tabs.service', icon: SettingsIcon },
  { key: 'platforms', href: '/settings/platforms', label: 'settings.tabs.platforms', icon: AlertCircle },
  { key: 'backends', href: '/settings/backends', label: 'settings.tabs.backends', icon: Bot },
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

// Mirrors design.pen niFAd (platSubTabs) but applied at the settings root:
// rounded-full pills, mint-soft active state with #5BFFA055 stroke,
// 13px lucide icon + 12px label, padding [8, 14].
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

      <nav className="flex flex-wrap gap-1.5" aria-label="Settings sections">
        {TABS.map((tab) => {
          const Icon = tab.icon;
          const active = tab.key === activeTab;
          return (
            <Link
              key={tab.key}
              to={tab.href}
              className={clsx(
                'inline-flex items-center gap-2 rounded-full border px-3.5 py-2 text-[12px] font-semibold transition-colors',
                active
                  ? 'border-mint/35 bg-mint/[0.08] text-foreground shadow-[0_0_18px_-6px_rgba(91,255,160,0.5)]'
                  : 'border-border bg-white/[0.04] text-muted hover:border-border-strong hover:text-foreground'
              )}
            >
              <Icon className={clsx('size-3.5', active ? 'text-mint' : 'text-muted')} />
              {t(tab.label)}
            </Link>
          );
        })}
      </nav>

      <div className="flex flex-col gap-4">{children}</div>
    </div>
  );
};
