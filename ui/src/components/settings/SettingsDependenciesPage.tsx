import React, { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { ArrowUpRight, Download, Hexagon, LayoutDashboard, Loader2, RefreshCw, ShieldCheck, Terminal, WandSparkles } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import clsx from 'clsx';

import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { SettingsPageShell } from './SettingsPageShell';
import { useApi } from '@/context/ApiContext';
import type { DependencyItem } from '@/context/ApiContext';
import { useToast } from '@/context/ToastContext';

// Mirrors design.pen "vibe-remote — Settings · Dependencies": one card per
// required local runtime (icon tile + name/REQUIRED + detail + status pill +
// action), reusing the Backends-page card shape. askill + the Show Page
// runtime auto-install during `vibe runtime prepare`; this page surfaces their
// status and offers manual re-check / install / repair. Backend CLIs are
// managed on the Backends tab — linked, not duplicated.

type DepMeta = { icon: LucideIcon; tileCls: string; iconCls: string };

const DEP_META: Record<string, DepMeta> = {
  askill: { icon: WandSparkles, tileCls: 'bg-mint-soft', iconCls: 'text-mint' },
  'show-runtime': { icon: LayoutDashboard, tileCls: 'bg-cyan-soft', iconCls: 'text-cyan' },
  node: { icon: Hexagon, tileCls: 'bg-violet-soft', iconCls: 'text-violet' },
};

export const SettingsDependenciesPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();

  const [deps, setDeps] = useState<DependencyItem[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await api.listDependencies();
      setDeps(res.deps ?? []);
    } catch {
      setDeps([]);
    }
  }, [api]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const install = async (dep: DependencyItem) => {
    setBusy(dep.id);
    try {
      const res = await api.installDependency(dep.id);
      showToast(
        res.ok ? t('settings.dependencies.installed', { name: dep.label }) : res.message || t('settings.dependencies.installFailed'),
        res.ok ? 'success' : 'error'
      );
      await refresh();
    } catch (e: any) {
      showToast(e?.message || t('settings.dependencies.installFailed'), 'error');
    } finally {
      setBusy(null);
    }
  };

  const statusText = (d: DependencyItem) => {
    if (!d.installed) return t('settings.dependencies.statusMissing');
    const word = d.kind === 'node' ? t('settings.dependencies.statusDetected') : t('settings.dependencies.statusReady');
    return d.version ? `${word} · v${String(d.version).replace(/^v/i, '')}` : word;
  };

  return (
    <SettingsPageShell
      activeTab="dependencies"
      title={t('settings.dependenciesTitle')}
      subtitle={t('settings.dependenciesSubtitle')}
      actions={
        <Button variant="secondary" size="sm" onClick={() => void refresh()}>
          <RefreshCw className="size-3.5" />
          {t('settings.dependencies.recheckAll')}
        </Button>
      }
    >
      {deps === null ? (
        <div className="text-sm text-muted">{t('common.loading')}</div>
      ) : (
        <div className="flex flex-col gap-3.5">
          <div className="flex items-center gap-3 rounded-xl border border-mint/30 bg-mint/[0.08] px-5 py-3.5">
            <ShieldCheck className="size-4 shrink-0 text-mint" />
            <span className="text-[13px] leading-snug text-foreground">{t('settings.dependencies.autoBanner')}</span>
          </div>

          {deps.map((d) => {
            const meta = DEP_META[d.id] ?? DEP_META.node;
            const Icon = meta.icon;
            const installing = busy === d.id;
            const showAction = d.id === 'askill' || d.id === 'show-runtime';
            return (
              <div
                key={d.id}
                className="flex flex-col gap-4 rounded-xl border border-border bg-background px-5 py-4 transition-colors hover:border-border-strong md:flex-row md:items-center"
              >
                <div className="flex min-w-0 flex-1 items-center gap-4">
                  <div className={clsx('flex size-11 shrink-0 items-center justify-center rounded-[10px]', meta.tileCls)}>
                    <Icon size={22} className={meta.iconCls} />
                  </div>
                  <div className="flex min-w-0 flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[15px] font-semibold text-foreground">{d.label}</span>
                      {d.required && (
                        <Badge variant="secondary" className="font-mono uppercase tracking-[0.08em]">
                          {t('settings.dependencies.required')}
                        </Badge>
                      )}
                    </div>
                    {d.detail && <p className="text-[12px] leading-snug text-muted">{d.detail}</p>}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-3 md:shrink-0 md:justify-end">
                  <Badge variant={d.installed ? 'success' : 'destructive'} className="font-mono">
                    {statusText(d)}
                  </Badge>
                  {showAction && (
                    <Button variant={d.installed ? 'secondary' : 'brand'} size="xs" disabled={installing} onClick={() => void install(d)}>
                      {installing ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : d.installed ? (
                        <RefreshCw className="size-3.5" />
                      ) : (
                        <Download className="size-3.5" />
                      )}
                      {installing
                        ? t('settings.dependencies.installing')
                        : d.installed
                          ? d.id === 'show-runtime'
                            ? t('settings.dependencies.repair')
                            : t('settings.dependencies.reinstall')
                          : t('settings.dependencies.install')}
                    </Button>
                  )}
                </div>
              </div>
            );
          })}

          <div className="flex flex-col gap-4 rounded-xl border border-border bg-background px-5 py-4 opacity-70 md:flex-row md:items-center">
            <div className="flex min-w-0 flex-1 items-center gap-4">
              <div className="flex size-11 shrink-0 items-center justify-center rounded-[10px] bg-surface-3">
                <Terminal size={22} className="text-muted" />
              </div>
              <div className="flex min-w-0 flex-col gap-1">
                <span className="text-[15px] font-semibold text-foreground">{t('settings.dependencies.backendsTitle')}</span>
                <p className="text-[12px] leading-snug text-muted">{t('settings.dependencies.backendsDetail')}</p>
              </div>
            </div>
            <div className="md:shrink-0">
              <Button asChild variant="secondary" size="xs">
                <Link to="/admin/settings/backends">
                  {t('settings.dependencies.manageBackends')}
                  <ArrowUpRight className="size-3.5" />
                </Link>
              </Button>
            </div>
          </div>
        </div>
      )}
    </SettingsPageShell>
  );
};
