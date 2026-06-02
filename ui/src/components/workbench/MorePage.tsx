import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowRight, Globe, Info, Link as LinkIcon, SlidersHorizontal } from 'lucide-react';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import { useStatus } from '../../context/StatusContext';
import { AccountMenu } from '../AccountMenu';
import { LanguageSwitcher } from '../LanguageSwitcher';
import { ThemeToggle } from '../ThemeToggle';
import { VersionBadge } from '../VersionBadge';

// Mobile-only "More" tab (workbench). The bridge to the Control Panel plus
// appearance / connection / account. Per product decision the service
// start/stop control lives ONLY in the Control Panel, so this screen shows a
// read-only status line. Design: design.pen `Nxnja`.
export const MorePage: React.FC = () => {
  const { t } = useTranslation();
  const { status } = useStatus();
  const api = useApi();
  const [config, setConfig] = useState<any>(null);

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, [api]);

  const isRunning = status.state === 'running';
  const hostname = config?.runtime?.hostname as string | undefined;

  return (
    <div className="mx-auto flex max-w-xl flex-col gap-4">
      <h1 className="text-xl font-bold">{t('more.title')}</h1>

      {/* Read-only service status — control lives in the Control Panel. */}
      <div
        className={clsx(
          'flex items-center gap-2.5 rounded-xl border px-4 py-3.5',
          isRunning ? 'border-mint/30 bg-mint/[0.08]' : 'border-border bg-surface'
        )}
      >
        <span
          className={clsx(
            'size-2.5 shrink-0 rounded-full',
            isRunning ? 'bg-mint shadow-[0_0_9px_rgba(91,255,160,0.9)]' : 'bg-muted'
          )}
        />
        <span className="flex-1 text-sm font-semibold">
          {isRunning ? t('common.running') : t('common.stopped')}
        </span>
        <VersionBadge />
      </div>

      {/* Bridge to the Control Panel (admin shell). */}
      <Link
        to="/admin/dashboard"
        className="flex items-center gap-3 rounded-xl border border-cyan/35 bg-surface px-4 py-3.5 transition hover:bg-foreground/[0.04]"
      >
        <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-cyan/[0.14]">
          <SlidersHorizontal className="size-[18px] text-cyan" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] font-semibold">{t('more.controlPanel')}</span>
          <span className="block truncate text-[11.5px] text-muted">{t('more.controlPanelDesc')}</span>
        </span>
        <ArrowRight className="size-[18px] shrink-0 text-cyan" />
      </Link>

      {/* Appearance — reuse the existing toggles as touch rows. */}
      <div className="rounded-xl border border-border bg-surface">
        <div className="flex items-center gap-3 px-4 py-3">
          <span className="flex-1 text-sm font-medium">{t('more.appearance')}</span>
          <ThemeToggle />
          <LanguageSwitcher />
        </div>
      </div>

      {/* Connection */}
      <div className="rounded-xl border border-border bg-surface">
        {hostname && (
          <div className="flex items-center gap-3 border-b border-border px-4 py-3">
            <LinkIcon className="size-4 text-muted" />
            <span className="flex-1 text-sm font-medium">{t('more.host')}</span>
            <span className="font-mono text-[12px] text-muted">{hostname}</span>
          </div>
        )}
        <div className="flex items-center gap-3 px-4 py-3">
          <Globe className="size-4 text-muted" />
          <span className="flex-1 text-sm font-medium">{t('more.connection')}</span>
          <AccountMenu openUpward />
        </div>
        <div className="flex items-center gap-3 border-t border-border px-4 py-3">
          <Info className="size-4 text-muted" />
          <span className="flex-1 text-sm font-medium">{t('more.version')}</span>
          <VersionBadge />
        </div>
      </div>
    </div>
  );
};
