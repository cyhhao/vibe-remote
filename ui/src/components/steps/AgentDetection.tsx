import React, { useEffect, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ChevronUp,
  Download,
  RefreshCw,
  Search,
  Settings,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';
import { BackendIcon, EyebrowBadge, WizardCard } from '../visual';
import type { BackendId } from '../visual';
import { BackendLifecycleChip } from '../settings/BackendLifecycleChip';
import { CompactField, CompactSelect, ToggleSwitch } from '../settings/SettingsPrimitives';
import { Button } from '../ui/button';

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
          <CompactSelect
            value={defaultBackend}
            onChange={(e) => setDefaultBackend(e.target.value)}
            className="mt-1 w-full md:max-w-[260px]"
          >
            <option value="opencode">OpenCode {t('agentDetection.recommended')}</option>
            <option value="claude">Claude Code</option>
            <option value="codex">Codex</option>
          </CompactSelect>
        </label>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={detectAll}
        >
          <Search className="size-3.5" /> {t('common.detectAll')}
        </Button>
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
                <BackendLifecycleChip
                  name={name}
                  enabled={agent.enabled}
                  cliStatus={agent.status || 'unknown'}
                  onChanged={() => detect(name, agent.cli_path)}
                />
                <ToggleSwitch
                  enabled={agent.enabled}
                  onClick={() => toggle(name, !agent.enabled)}
                />
              </div>
            </div>

            <div className="space-y-3 px-5 py-3.5">
              <div className="flex gap-2">
                <CompactField
                  type="text"
                  value={agent.cli_path}
                  onChange={(e) =>
                    setAgents((prev) => ({
                      ...prev,
                      [name]: { ...prev[name], cli_path: e.target.value },
                    }))
                  }
                  placeholder={t('agentDetection.cliPathPlaceholder', { name })}
                  className="flex-1 font-mono"
                />
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => detect(name, agent.cli_path)}
                  disabled={checking}
                >
                  {checking ? <RefreshCw className="size-3.5 animate-spin" /> : <Search className="size-3.5" />}
                  {t('common.detect')}
                </Button>
              </div>

              {isMissing(agent) && (
                <div className="space-y-2 rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2.5">
                  <p className="text-[11px] text-cyan">{t('agentDetection.installHint')}</p>
                  <div className="flex flex-wrap items-center gap-3">
                    <Button
                      variant="brand-cyan"
                      size="xs"
                      onClick={() => installAgent(name)}
                      disabled={isAnyInstalling}
                    >
                      {installingAgents[name] ? (
                        <RefreshCw className="size-3.5 animate-spin" />
                      ) : (
                        <Download className="size-3.5" />
                      )}
                      {installingAgents[name] ? t('agentDetection.installing') : t('agentDetection.installAgent')}
                    </Button>
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
                    <Button
                      variant="brand-gold"
                      size="xs"
                      onClick={setupPermission}
                      disabled={permissionState === 'loading'}
                    >
                      {permissionState === 'loading' ? (
                        <RefreshCw className="size-3.5 animate-spin" />
                      ) : (
                        <Settings className="size-3.5" />
                      )}
                      {t('agentDetection.setupPermission')}
                    </Button>
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
          <Button variant="brand" size="default" onClick={() => void handlePrimaryAction()} disabled={!canContinue}>
            {t('common.save')}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex w-full justify-center">
      <WizardCard className="gap-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-2">
            <EyebrowBadge tone="mint">{t('agentDetection.eyebrow')}</EyebrowBadge>
            <h2 className="text-[28px] font-bold leading-tight tracking-[-0.4px] text-foreground">
              {t('agentDetection.title')}
            </h2>
            <p className="max-w-[560px] text-[14px] leading-[1.55] text-muted">
              {t('agentDetection.subtitle')}
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-border bg-foreground/[0.04] px-3 py-1.5">
            <span className="font-mono text-[11px] font-bold uppercase tracking-[0.16em] text-mint">
              {enabledCount} active
            </span>
          </div>
        </div>

        {Inner}

        <div className="flex items-center justify-between border-t border-border pt-4">
          {onBack ? (
            <Button
              type="button"
              variant="secondary"
              size="default"
              onClick={onBack}
              className="font-semibold"
            >
              <ArrowLeft size={14} strokeWidth={2.25} />
              {t('common.back')}
            </Button>
          ) : (
            <span />
          )}
          <Button
            type="button"
            variant="brand"
            size="default"
            onClick={() => void handlePrimaryAction()}
            disabled={!canContinue}
          >
            {t('common.continue')}
            <ArrowRight size={14} strokeWidth={2.25} />
          </Button>
        </div>
      </WizardCard>
    </div>
  );
};

