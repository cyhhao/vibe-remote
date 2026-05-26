import { useTranslation } from 'react-i18next';
import { NavLink } from 'react-router-dom';
import { Activity, Bot, ChevronRight, Folder, Inbox, KeyRound, Plus, WandSparkles } from 'lucide-react';
import clsx from 'clsx';
import type { LucideIcon } from 'lucide-react';

interface CapabilityNavItem {
  to: string;
  i18nKey: string;
  icon: LucideIcon;
}

// Order mirrors design.pen DnkGJ — capability modules below the global
// inbox entry, projects beneath that.
const CAPABILITY_NAV: CapabilityNavItem[] = [
  { to: '/agents', i18nKey: 'workbench.nav.agents', icon: Bot },
  { to: '/skills', i18nKey: 'workbench.nav.skills', icon: WandSparkles },
  { to: '/harness', i18nKey: 'workbench.nav.harness', icon: Activity },
  { to: '/vaults', i18nKey: 'workbench.nav.vaults', icon: KeyRound },
];

// Inbox + 4 capabilities + Projects header for the workbench-mode sidebar
// middle. Brand and bottom controls live in AppShell so the chrome stays
// consistent across modes. Project data and the Inbox hover popover are
// staged in by later commits — commit 02 only ships the entries themselves.
export const WorkbenchSidebar: React.FC = () => {
  const { t } = useTranslation();

  return (
    <div className="flex flex-col gap-4">
      <NavLink
        to="/inbox"
        className={({ isActive }) =>
          clsx(
            'group flex items-center gap-2.5 rounded-lg border px-3 py-2.5 text-[13px] font-semibold transition-colors',
            isActive
              ? 'border-mint/30 bg-mint/[0.08] text-foreground shadow-[0_0_16px_-4px_rgba(91,255,160,0.5)]'
              : 'border-border-strong text-foreground hover:bg-foreground/[0.04]'
          )
        }
      >
        {({ isActive }) => (
          <>
            <Inbox className={clsx('size-4', isActive ? 'text-mint' : 'text-foreground')} />
            <span className="flex-1">{t('workbench.nav.inbox')}</span>
            <ChevronRight className="size-3.5 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
          </>
        )}
      </NavLink>

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
                    : 'border border-transparent text-muted hover:bg-foreground/[0.04] hover:text-foreground'
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
        <div className="flex items-center justify-between px-1">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.18em] text-muted">
            {t('workbench.projectsLabel')}
          </div>
          <button
            type="button"
            aria-label={t('workbench.addProject')}
            className="flex size-5 items-center justify-center rounded-md border border-border-strong text-foreground transition hover:bg-foreground/[0.04]"
            // Wired up in commit 05 (projects REST + folder picker).
            disabled
          >
            <Plus className="size-3" />
          </button>
        </div>
        <div className="flex flex-col items-center gap-1.5 rounded-lg border border-dashed border-border px-3 py-4 text-center">
          <Folder className="size-4 text-muted" />
          <div className="text-[11px] text-muted">{t('workbench.projectsEmpty')}</div>
        </div>
      </div>
    </div>
  );
};
