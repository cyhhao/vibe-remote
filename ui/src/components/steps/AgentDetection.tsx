import React, { useEffect, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronDown,
  ChevronUp,
  Download,
  RefreshCw,
  Search,
  Settings,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';
import { BackendIcon, EyebrowBadge, WizardCard } from '../visual';
import type { BackendId } from '../visual';

interface AgentDetectionProps {
  data: any;
  onNext: (data: any) => void;
  onBack?: () => void;
  isPage?: boolean;
  onSave?: (data: { agents: Record<string, AgentState>; default_backend: string }) => Promise<void> | void;
}

type AgentState = {
  enabled: boolean;
  cli_path: string;
  status?: 'unknown' | 'ok' | 'missing';
};

type PermissionState = 'idle' | 'loading' | 'success' | 'error';

const DEFAULT_AGENTS: Record<string, AgentState> = {
  opencode: { enabled: true, cli_path: 'opencode', status: 'unknown' },
  claude: { enabled: true, cli_path: 'claude', status: 'unknown' },
  codex: { enabled: false, cli_path: 'codex', status: 'unknown' },
};

const AGENT_LABEL: Record<string, string> = {
  opencode: 'OpenCode',
  claude: 'Claude Code',
  codex: 'Codex',
};

const normalizeAgents = (source: any): Record<string, AgentState> => {
  const raw = source?.agents || {};
  return Object.fromEntries(
    Object.entries(DEFAULT_AGENTS).map(([name, fallback]) => {
      const next = raw?.[name] || {};
      return [
        name,
        {
          ...fallback,
          ...next,
          status: next.status || fallback.status,
        },
      ];
    })
  );
};

// Mirrors design.pen JHgjz (Backends wizard step) and qVHh4 (Settings → Backends).
// 920-wide WizardCard, mint eyebrow, compact backend cards with header (icon, label,
// status pill, switch) and body (CLI path + detect, optional permission/install row).
export const AgentDetection: React.FC<AgentDetectionProps> = ({ data, onNext, onBack, isPage = false, onSave }) => {
  const { t } = useTranslation();
  const api = useApi();
  const [checking, setChecking] = useState(false);
  const [defaultBackend, setDefaultBackend] = useState<string>(
    data.default_backend || data.agents?.default_backend || 'opencode'
  );
  const [agents, setAgents] = useState<Record<string, AgentState>>(normalizeAgents(data));
  const [permissionState, setPermissionState] = useState<PermissionState>('idle');
  const [permissionMessage, setPermissionMessage] = useState<string>('');
  const [installingAgents, setInstallingAgents] = useState<Record<string, boolean>>({});
  const [installResults, setInstallResults] = useState<
    Record<string, { ok: boolean; message: string; output?: string | null }>
  >({});
  const [expandedOutputs, setExpandedOutputs] = useState<Record<string, boolean>>({});
  const isMissing = (agent: AgentState) => agent.status === 'missing';

  const isAnyInstalling = Object.values(installingAgents).some(Boolean);

  useEffect(() => {
    detectAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const detect = async (name: string, binary?: string) => {
    setChecking(true);
    try {
      const result = await api.detectCli(binary || name);
      setAgents((prev) => ({
        ...prev,
        [name]: {
          ...prev[name],
          cli_path: result.path || prev[name].cli_path,
          status: result.found ? 'ok' : 'missing',
        },
      }));
    } finally {
      setChecking(false);
    }
  };

  const detectAll = async () => {
    await Promise.all(Object.entries(agents).map(([name, agent]) => detect(name, agent.cli_path)));
  };

  const toggle = (name: string, enabled: boolean) => {
    setAgents((prev) => ({
      ...prev,
      [name]: { ...prev[name], enabled },
    }));
  };

  const setupPermission = async () => {
    setPermissionState('loading');
    try {
      const result = await api.opencodeSetupPermission();
      if (result.ok) {
        setPermissionState('success');
        setPermissionMessage(result.message);
      } else {
        setPermissionState('error');
        setPermissionMessage(result.message);
      }
    } catch (e) {
      setPermissionState('error');
      setPermissionMessage(String(e));
    }
  };

  const installAgent = async (name: string) => {
    if (isAnyInstalling) return;

    setInstallingAgents((prev) => ({ ...prev, [name]: true }));
    setInstallResults((prev) => ({ ...prev, [name]: { ok: false, message: '', output: null } }));
    setExpandedOutputs((prev) => ({ ...prev, [name]: false }));

    try {
      const result = await api.installAgent(name);
      const installedPath = typeof result.path === 'string' && result.path ? result.path : null;
      setInstallResults((prev) => ({
        ...prev,
        [name]: { ok: result.ok, message: result.message, output: result.output },
      }));
      if (result.ok) {
        if (installedPath) {
          setAgents((prev) => ({
            ...prev,
            [name]: { ...prev[name], cli_path: installedPath },
          }));
        }
        await detect(name, installedPath || agents[name]?.cli_path || name);
      }
    } catch (e) {
      setInstallResults((prev) => ({
        ...prev,
        [name]: { ok: false, message: String(e), output: null },
      }));
    } finally {
      setInstallingAgents((prev) => ({ ...prev, [name]: false }));
    }
  };

  const toggleOutput = (name: string) => {
    setExpandedOutputs((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  const canContinue = Object.values(agents).some((agent) => agent.enabled);

  const handlePrimaryAction = async () => {
    const nextData = { agents, default_backend: defaultBackend };
    if (isPage && onSave) {
      await onSave(nextData);
      return;
    }
    onNext(nextData);
  };

  const enabledCount = Object.values(agents).filter((a) => a.enabled && a.status === 'ok').length;

  // Page mode keeps the existing settings shell — render the inner content only
  const Inner = (
    <>
      <div className="flex flex-col gap-3 rounded-xl border border-border bg-background px-4 py-3 md:flex-row md:items-end md:justify-between">
        <label className="min-w-0 flex-1">
          <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">
            {t('agentDetection.defaultBackend')}
          </span>
          <select
            value={defaultBackend}
            onChange={(e) => setDefaultBackend(e.target.value)}
            className="mt-1 h-9 w-full rounded-lg border border-border bg-surface-2 px-3 text-[12px] text-foreground outline-none transition focus:border-cyan focus:ring-1 focus:ring-cyan/40 md:max-w-[260px]"
          >
            <option value="opencode">OpenCode {t('agentDetection.recommended')}</option>
            <option value="claude">Claude Code</option>
            <option value="codex">Codex</option>
          </select>
        </label>
        <button
          onClick={detectAll}
          className="inline-flex h-9 items-center gap-2 rounded-lg border border-border bg-white/[0.04] px-3 text-[12px] font-medium text-foreground transition hover:border-border-strong"
        >
          <Search className="size-3.5" /> {t('common.detectAll')}
        </button>
      </div>

      <div className="flex flex-col gap-3">
        {Object.entries(agents).map(([name, agent]) => (
          <div
            key={name}
            className="overflow-hidden rounded-xl border border-border bg-background transition-colors hover:border-border-strong"
          >
            <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3.5">
              <div className="flex min-w-0 items-center gap-3">
                <div className="flex size-9 items-center justify-center rounded-lg border border-border bg-surface-2">
                  <BackendIcon backend={name as BackendId} size={18} />
                </div>
                <div className="min-w-0">
                  <h3 className="text-[13px] font-semibold text-foreground">{AGENT_LABEL[name] || name}</h3>
                  <div className="mt-0.5 text-[11px] text-muted">{t('agentDetection.cliPath')}</div>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <StatusBadge status={agent.status || 'unknown'} loading={checking} />
                <button
                  role="switch"
                  aria-checked={agent.enabled}
                  onClick={() => toggle(name, !agent.enabled)}
                  className={clsx(
                    'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors focus:outline-none focus:ring-2 focus:ring-mint/40',
                    agent.enabled
                      ? 'border-mint/50 bg-mint shadow-[0_0_12px_-2px_rgba(91,255,160,0.6)]'
                      : 'border-border bg-surface-2'
                  )}
                >
                  <span
                    className={clsx(
                      'inline-block size-3.5 rounded-full bg-background shadow transition-transform',
                      agent.enabled ? 'translate-x-[18px]' : 'translate-x-1'
                    )}
                  />
                </button>
              </div>
            </div>

            <div className="space-y-3 px-5 py-3.5">
              <div className="flex gap-2">
                <input
                  type="text"
                  value={agent.cli_path}
                  onChange={(e) =>
                    setAgents((prev) => ({
                      ...prev,
                      [name]: { ...prev[name], cli_path: e.target.value },
                    }))
                  }
                  placeholder={t('agentDetection.cliPathPlaceholder', { name })}
                  className="h-9 flex-1 rounded-lg border border-border bg-surface-2 px-3 font-mono text-[12px] text-foreground outline-none transition focus:border-cyan focus:ring-1 focus:ring-cyan/40"
                />
                <button
                  onClick={() => detect(name, agent.cli_path)}
                  disabled={checking}
                  className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-3 text-[12px] font-medium text-foreground transition hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-50"
                >
                  {checking ? <RefreshCw className="size-3.5 animate-spin" /> : <Search className="size-3.5" />}
                  {t('common.detect')}
                </button>
              </div>

              {isMissing(agent) && (
                <div className="space-y-2 rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2.5">
                  <p className="text-[11px] text-cyan">{t('agentDetection.installHint')}</p>
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      onClick={() => installAgent(name)}
                      disabled={isAnyInstalling}
                      className="inline-flex h-8 items-center gap-2 rounded-lg bg-cyan px-3 text-[12px] font-bold text-[#080812] shadow-[0_0_18px_-4px_rgba(63,224,229,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {installingAgents[name] ? (
                        <RefreshCw className="size-3.5 animate-spin" />
                      ) : (
                        <Download className="size-3.5" />
                      )}
                      {installingAgents[name] ? t('agentDetection.installing') : t('agentDetection.installAgent')}
                    </button>
                    {installResults[name]?.message && (
                      <span
                        className={clsx(
                          'text-[11px]',
                          installResults[name].ok ? 'text-mint' : 'text-danger'
                        )}
                      >
                        {installResults[name].message}
                      </span>
                    )}
                  </div>
                  {installResults[name]?.output && (
                    <div>
                      <button
                        onClick={() => toggleOutput(name)}
                        className="inline-flex items-center gap-1 text-[11px] text-cyan transition hover:text-cyan/80"
                      >
                        {expandedOutputs[name] ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                        {t('agentDetection.showOutput')}
                      </button>
                      {expandedOutputs[name] && (
                        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded border border-border bg-background px-3 py-2 font-mono text-[11px] text-muted">
                          {installResults[name].output}
                        </pre>
                      )}
                    </div>
                  )}
                </div>
              )}

              {name === 'opencode' && agent.status === 'ok' && (
                <div className="rounded-lg border border-gold/30 bg-gold/10 px-3 py-2.5">
                  <p className="mb-2 text-[11px] text-gold">{t('agentDetection.permissionHint')}</p>
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      onClick={setupPermission}
                      disabled={permissionState === 'loading'}
                      className="inline-flex h-8 items-center gap-2 rounded-lg bg-gold px-3 text-[12px] font-bold text-[#080812] shadow-[0_0_18px_-4px_rgba(255,200,87,0.55)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {permissionState === 'loading' ? (
                        <RefreshCw className="size-3.5 animate-spin" />
                      ) : (
                        <Settings className="size-3.5" />
                      )}
                      {t('agentDetection.setupPermission')}
                    </button>
                    {permissionState === 'success' && (
                      <span className="text-[11px] text-mint">{permissionMessage}</span>
                    )}
                    {permissionState === 'error' && (
                      <span className="text-[11px] text-danger">{permissionMessage}</span>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </>
  );

  if (isPage) {
    return (
      <div className="flex flex-col gap-4">
        {Inner}
        <div className="flex justify-end">
          <button
            onClick={() => void handlePrimaryAction()}
            disabled={!canContinue}
            className="inline-flex items-center gap-2 rounded-lg bg-mint px-5 py-2.5 text-[13px] font-bold text-[#080812] shadow-[0_0_32px_-6px_rgba(91,255,160,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
          >
            {t('common.save')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex w-full justify-center">
      <WizardCard className="gap-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-2">
            <EyebrowBadge tone="mint">Backends</EyebrowBadge>
            <h2 className="text-[28px] font-bold leading-tight tracking-[-0.4px] text-foreground">
              {t('agentDetection.title')}
            </h2>
            <p className="max-w-[560px] text-[14px] leading-[1.55] text-muted">
              {t('agentDetection.subtitle')}
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-border bg-white/[0.04] px-3 py-1.5">
            <span className="font-mono text-[11px] font-bold uppercase tracking-[0.16em] text-mint">
              {enabledCount} active
            </span>
          </div>
        </div>

        {Inner}

        <div className="flex items-center justify-between border-t border-border pt-4">
          {onBack ? (
            <button
              type="button"
              onClick={onBack}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-4 py-2 text-[13px] font-semibold text-foreground transition hover:border-border-strong"
            >
              <ArrowLeft size={14} strokeWidth={2.25} />
              {t('common.back')}
            </button>
          ) : (
            <span />
          )}
          <button
            type="button"
            onClick={() => void handlePrimaryAction()}
            disabled={!canContinue}
            className="inline-flex items-center gap-2 rounded-lg bg-mint px-5 py-2.5 text-[13px] font-bold text-[#080812] shadow-[0_0_32px_-6px_rgba(91,255,160,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
          >
            {t('common.continue')}
            <ArrowRight size={14} strokeWidth={2.25} />
          </button>
        </div>
      </WizardCard>
    </div>
  );
};

const StatusBadge: React.FC<{ status: 'unknown' | 'ok' | 'missing'; loading: boolean }> = ({ status, loading }) => {
  const { t } = useTranslation();

  if (loading) {
    return (
      <div className="text-muted">
        <RefreshCw className="size-3.5 animate-spin" />
      </div>
    );
  }
  if (status === 'unknown') {
    return <span className="text-[11px] text-muted">{t('common.notChecked')}</span>;
  }
  return status === 'ok' ? (
    <div className="inline-flex items-center gap-1.5 rounded-full border border-mint/30 bg-mint/[0.08] px-2 py-0.5 text-[11px] font-medium text-mint">
      <Check className="size-3" /> {t('common.found')}
    </div>
  ) : (
    <div className="inline-flex items-center gap-1.5 rounded-full border border-danger/30 bg-danger/10 px-2 py-0.5 text-[11px] font-medium text-danger">
      <X className="size-3" /> {t('common.missing')}
    </div>
  );
};
