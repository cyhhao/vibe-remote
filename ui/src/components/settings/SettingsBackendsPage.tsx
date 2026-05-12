import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { Bot, ChevronRight, Settings2, Sparkles, Terminal } from 'lucide-react';
import clsx from 'clsx';

import { Button } from '../ui/button';
import { Badge } from '../ui/badge';
import { CompactSelect, ToggleSwitch } from './SettingsPrimitives';
import { BackendLifecycleChip } from './BackendLifecycleChip';
import { SettingsPageShell } from './SettingsPageShell';
import { useApi } from '@/context/ApiContext';
import { useToast } from '@/context/ToastContext';

// Mirrors design.pen qVHh4 (VR/CM/Backends): top bar with default-backend
// picker, then three horizontal cards (OpenCode/Claude/Codex). Each card
// surfaces icon + name/description + status chip + enable toggle + a
// "Configure" link that drills into the level-2 provider page. CLI path,
// detect, install, and permission profile live on the provider page now —
// keep the level-1 page about routing decisions, not setup mechanics.

type CliStatus = 'unknown' | 'ok' | 'missing';

type AgentState = {
  enabled: boolean;
  cli_path: string;
  status: CliStatus;
};

type BackendMeta = {
  id: string;
  label: string;
  Icon: React.ComponentType<{ size?: number; className?: string }>;
  // Tile background ($--violet-soft / $--cyan-soft / $--gold) and inner icon color.
  // Codex matches design.pen's solid gold tile so the brand pop survives in light mode.
  tileCls: string;
  iconCls: string;
  routeKey?: 'opencode' | 'claude' | 'codex';
};

const BACKENDS: BackendMeta[] = [
  {
    id: 'opencode',
    label: 'OpenCode',
    Icon: Terminal,
    tileCls: 'bg-violet-soft',
    iconCls: 'text-violet',
    routeKey: 'opencode',
  },
  {
    id: 'claude',
    label: 'Claude Code',
    Icon: Sparkles,
    tileCls: 'bg-cyan-soft',
    iconCls: 'text-cyan',
    routeKey: 'claude',
  },
  {
    id: 'codex',
    label: 'Codex',
    Icon: Bot,
    tileCls: 'bg-gold',
    iconCls: 'text-gold-foreground',
    routeKey: 'codex',
  },
];

const DEFAULT_AGENTS: Record<string, AgentState> = {
  opencode: { enabled: true, cli_path: 'opencode', status: 'unknown' },
  claude: { enabled: true, cli_path: 'claude', status: 'unknown' },
  codex: { enabled: false, cli_path: 'codex', status: 'unknown' },
};

const normalizeAgents = (source: any): Record<string, AgentState> => {
  const raw = source?.agents || {};
  return Object.fromEntries(
    Object.entries(DEFAULT_AGENTS).map(([name, fallback]) => {
      const next = raw?.[name] || {};
      return [
        name,
        {
          enabled: typeof next.enabled === 'boolean' ? next.enabled : fallback.enabled,
          cli_path: next.cli_path || fallback.cli_path,
          status: (next.status as CliStatus) || fallback.status,
        },
      ];
    })
  );
};

export const SettingsBackendsPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();

  const [loaded, setLoaded] = useState(false);
  const [agents, setAgents] = useState<Record<string, AgentState>>(DEFAULT_AGENTS);
  const [defaultBackend, setDefaultBackend] = useState<string>('opencode');

  useEffect(() => {
    let cancelled = false;
    api
      .getConfig()
      .then((config) => {
        if (cancelled) return;
        setAgents(normalizeAgents(config));
        setDefaultBackend(
          config?.default_backend || config?.agents?.default_backend || 'opencode'
        );
        setLoaded(true);
      })
      .catch(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  // Detect each CLI on mount so the status pill reflects reality without
  // making the user click Detect manually. Runs after the first config load.
  useEffect(() => {
    if (!loaded) return;
    let cancelled = false;
    (async () => {
      const results = await Promise.all(
        Object.entries(agents).map(async ([name, agent]) => {
          try {
            const result = await api.detectCli(agent.cli_path || name);
            return [name, result] as const;
          } catch {
            return [name, null] as const;
          }
        })
      );
      if (cancelled) return;
      setAgents((prev) => {
        const next = { ...prev };
        for (const [name, result] of results) {
          if (!result) continue;
          next[name] = {
            ...next[name],
            cli_path: result.path || next[name].cli_path,
            status: result.found ? 'ok' : 'missing',
          };
        }
        return next;
      });
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded]);

  const persist = async (nextAgents: Record<string, AgentState>, nextDefault: string) => {
    try {
      await api.saveConfig({
        agents: { ...nextAgents, default_backend: nextDefault },
      });
      showToast(t('common.saved'), 'success');
    } catch (e: any) {
      showToast(e?.message || t('common.saveFailed'), 'error');
    }
  };

  const handleToggle = async (name: string, enabled: boolean) => {
    const nextAgents = { ...agents, [name]: { ...agents[name], enabled } };
    setAgents(nextAgents);
    await persist(nextAgents, defaultBackend);
  };

  const handleDefaultChange = async (next: string) => {
    setDefaultBackend(next);
    // If the chosen default is disabled, flip it on so the routing target is
    // actually reachable — saves the user an extra round trip to enable it.
    let nextAgents = agents;
    if (!agents[next]?.enabled) {
      nextAgents = { ...agents, [next]: { ...agents[next], enabled: true } };
      setAgents(nextAgents);
    }
    await persist(nextAgents, next);
  };

  const refreshDetectionFor = async (name: string, cli_path: string) => {
    try {
      const result = await api.detectCli(cli_path || name);
      setAgents((prev) => ({
        ...prev,
        [name]: {
          ...prev[name],
          cli_path: result.path || prev[name].cli_path,
          status: result.found ? 'ok' : 'missing',
        },
      }));
    } catch {
      // ignore — chip falls back to muted "loading" pill
    }
  };

  return (
    <SettingsPageShell
      activeTab="backends"
      title={t('settings.backendsTitle')}
      subtitle={t('settings.backendsSubtitle')}
    >
      {!loaded ? (
        <div className="text-sm text-muted">{t('common.loading')}</div>
      ) : (
        <div className="flex flex-col gap-3.5">
          <div className="flex flex-col gap-3 rounded-xl border border-border bg-background px-5 py-4 md:flex-row md:items-center md:justify-between">
            <div className="flex flex-col gap-1">
              <span className="text-[14px] font-semibold text-foreground">
                {t('settings.backends.routingTitle')}
              </span>
              <span className="max-w-[520px] text-[12px] leading-snug text-muted">
                {t('settings.backends.routingHint')}
              </span>
            </div>
            <label className="flex items-center gap-2 self-start md:self-auto">
              <span className="text-[11px] font-medium text-muted">
                {t('settings.backends.defaultLabel')}
              </span>
              <CompactSelect
                value={defaultBackend}
                onChange={(e) => void handleDefaultChange(e.target.value)}
                className="min-w-[180px]"
                aria-label={t('settings.backends.defaultLabel') as string}
              >
                {BACKENDS.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.label}
                  </option>
                ))}
              </CompactSelect>
            </label>
          </div>

          {BACKENDS.map((meta) => {
            const agent = agents[meta.id];
            const Icon = meta.Icon;
            const isDefault = defaultBackend === meta.id;
            const route = meta.routeKey ? `/settings/backends/${meta.routeKey}` : null;

            return (
              <div
                key={meta.id}
                className="flex flex-col gap-4 rounded-xl border border-border bg-background px-5 py-4 transition-colors hover:border-border-strong md:flex-row md:items-center"
              >
                <div className="flex min-w-0 flex-1 items-center gap-4">
                  <div
                    className={clsx(
                      'flex size-11 shrink-0 items-center justify-center rounded-[10px]',
                      meta.tileCls
                    )}
                  >
                    <Icon size={22} className={meta.iconCls} />
                  </div>
                  <div className="flex min-w-0 flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[15px] font-semibold text-foreground">
                        {meta.label}
                      </span>
                      {isDefault && (
                        <Badge variant="success" className="font-mono uppercase tracking-[0.08em]">
                          {t('settings.backends.defaultBadge')}
                        </Badge>
                      )}
                    </div>
                    <p className="text-[12px] leading-snug text-muted">
                      {t(`settings.backends.${meta.id}Description`)}
                    </p>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-3 md:shrink-0 md:justify-end">
                  <BackendLifecycleChip
                    name={meta.id}
                    enabled={agent.enabled}
                    cliStatus={agent.status}
                    onChanged={async (info) => {
                      const installedPath = info?.installedPath || null;
                      if (installedPath) {
                        setAgents((prev) => ({
                          ...prev,
                          [meta.id]: { ...prev[meta.id], cli_path: installedPath },
                        }));
                      }
                      await refreshDetectionFor(meta.id, installedPath || agent.cli_path);
                    }}
                  />
                  <ToggleSwitch
                    enabled={agent.enabled}
                    onClick={() => void handleToggle(meta.id, !agent.enabled)}
                  />
                  {route && (
                    <Button asChild variant="secondary" size="xs">
                      <Link to={route} aria-label={t('settings.backends.configure', { name: meta.label }) as string}>
                        <Settings2 className="size-3.5" />
                        {t('settings.backends.configure')}
                        <ChevronRight className="size-3.5" />
                      </Link>
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </SettingsPageShell>
  );
};
