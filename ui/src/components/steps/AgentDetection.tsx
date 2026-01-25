import React, { useEffect, useState } from 'react';
import { Check, X, RefreshCw, Search } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';

interface AgentDetectionProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

type AgentState = {
  enabled: boolean;
  cli_path: string;
  status?: 'unknown' | 'ok' | 'missing';
};

export const AgentDetection: React.FC<AgentDetectionProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const api = useApi();
  const [checking, setChecking] = useState(false);
  const [settingUpPermission, setSettingUpPermission] = useState(false);
  const [permissionSetupMessage, setPermissionSetupMessage] = useState<{type: 'success' | 'error', text: string} | null>(null);
  const [defaultBackend, setDefaultBackend] = useState<string>(data.default_backend || 'opencode');
  const [agents, setAgents] = useState<Record<string, AgentState>>(
    data.agents || {
      opencode: { enabled: true, cli_path: 'opencode', status: 'unknown' },
      claude: { enabled: true, cli_path: 'claude', status: 'unknown' },
      codex: { enabled: false, cli_path: 'codex', status: 'unknown' },
    }
  );

  const isMissing = (agent: AgentState) => agent.status === 'missing';

  useEffect(() => {
    if (!data.agents) {
      detectAll();
    }
  }, []);

  const detect = async (name: string) => {
    setChecking(true);
    try {
      let result;
      result = await api.detectCli(name);
      
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
    await Promise.all(Object.keys(agents).map((name) => detect(name)));
  };

  const toggle = (name: string, enabled: boolean) => {
    setAgents((prev) => ({
      ...prev,
      [name]: { ...prev[name], enabled },
    }));
  };

  const setupOpenCodePermission = async () => {
    setSettingUpPermission(true);
    setPermissionSetupMessage(null);
    try {
      const result = await api.opencodeSetupPermission();
      if (result.ok) {
        setPermissionSetupMessage({ type: 'success', text: t('agentDetection.permissionSetupSuccess') });
      } else {
        setPermissionSetupMessage({ type: 'error', text: result.message || t('agentDetection.permissionSetupFailed') });
      }
    } catch (error) {
      setPermissionSetupMessage({ type: 'error', text: t('agentDetection.permissionSetupFailed') });
    } finally {
      setSettingUpPermission(false);
    }
  };

  const canContinue = Object.values(agents).some((agent) => agent.enabled);

  return (
    <div className="flex flex-col h-full max-w-2xl mx-auto">
      <h2 className="text-3xl font-display font-bold mb-2 text-text">{t('agentDetection.title')}</h2>
      <p className="text-muted mb-6">
        {t('agentDetection.subtitle')}
      </p>

      <div className="mb-6 p-4 border border-border rounded-xl bg-panel shadow-sm">
        <label className="text-sm font-medium text-muted uppercase">{t('agentDetection.defaultBackend')}</label>
        <select
          value={defaultBackend}
          onChange={(e) => setDefaultBackend(e.target.value)}
          className="mt-2 w-full bg-bg border border-border rounded px-3 py-2 text-sm"
        >
          <option value="opencode">OpenCode {t('agentDetection.recommended')}</option>
          <option value="claude">Claude</option>
          <option value="codex">Codex</option>
        </select>
      </div>

      <div className="flex justify-end mb-4">
        <button
          onClick={detectAll}
          className="flex items-center gap-2 px-4 py-2 bg-neutral-100 hover:bg-neutral-200 text-text rounded-lg transition-colors font-medium text-sm"
        >
          <Search size={16} /> {t('common.detectAll')}
        </button>
      </div>

      <div className="space-y-4 mb-6">
        {Object.entries(agents).map(([name, agent]) => (
          <div key={name} className="p-5 bg-panel border border-border rounded-xl shadow-sm transition-shadow hover:shadow-md">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="font-semibold capitalize text-lg text-text font-display">{name}</h3>
                <p className="text-sm text-muted">{t('agentDetection.cliPathDetection')}</p>
              </div>
              <StatusBadge status={agent.status || 'unknown'} loading={checking} />
            </div>

            <div className="flex flex-col gap-4">
              <label className="flex items-center gap-2 text-sm text-text font-medium cursor-pointer w-fit">
                <input
                  type="checkbox"
                  checked={agent.enabled}
                  onChange={(e) => toggle(name, e.target.checked)}
                  className="w-4 h-4 text-accent rounded focus:ring-accent border-gray-300"
                />
                {t('common.enabled')}
              </label>

              <div className="flex-1">
                  <div className="flex items-center gap-2 mb-2">
                    <label className="text-xs font-medium text-muted uppercase">{t('agentDetection.cliPath')}</label>
                    {isMissing(agent) && (
                        <span className="text-[10px] text-danger bg-danger/10 px-1.5 py-0.5 rounded border border-danger/20">{t('common.notFound')}</span>
                    )}
                     {!isMissing(agent) && agent.status === 'ok' && (
                         <span className="text-[10px] text-success bg-success/10 px-1.5 py-0.5 rounded border border-success/20">{t('common.found')}</span>
                     )}
                  </div>
                  <div className="flex gap-2">
                    <input
                        type="text"
                        value={agent.cli_path}
                        onChange={(e) => setAgents(prev => ({
                            ...prev,
                            [name]: { ...prev[name], cli_path: e.target.value }
                        }))}
                        placeholder={t('agentDetection.cliPathPlaceholder', { name })}
                        className="flex-1 bg-bg border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-accent font-mono text-text"
                    />
                    <button
                        onClick={() => detect(name)}
                        disabled={checking}
                        className="px-3 py-2 bg-neutral-100 hover:bg-neutral-200 rounded text-sm text-muted hover:text-text font-medium transition-colors border border-border"
                    >
                        {checking ? <RefreshCw size={14} className="animate-spin" /> : t('common.detect')}
                    </button>
                  </div>
              </div>

              {name === 'opencode' && agent.status === 'ok' && (
                <div className="mt-2 p-3 bg-amber-50 dark:bg-amber-950/20 border border-amber-200 dark:border-amber-800 rounded-lg">
                  <p className="text-xs text-amber-800 dark:text-amber-200 mb-2 font-medium">
                    {t('agentDetection.permissionRequired')}
                  </p>
                  <button
                    onClick={setupOpenCodePermission}
                    disabled={settingUpPermission}
                    className="w-full px-4 py-2 bg-amber-600 hover:bg-amber-700 disabled:bg-amber-400 text-white rounded-lg font-medium text-sm transition-colors flex items-center justify-center gap-2"
                  >
                    {settingUpPermission ? (
                      <>
                        <RefreshCw size={14} className="animate-spin" />
                        {t('agentDetection.settingUpPermission')}
                      </>
                    ) : (
                      t('agentDetection.setupPermission')
                    )}
                  </button>
                  {permissionSetupMessage && (
                    <div className={clsx(
                      "mt-2 p-2 rounded text-xs font-medium",
                      permissionSetupMessage.type === 'success' 
                        ? "bg-green-50 dark:bg-green-950/20 text-green-800 dark:text-green-200 border border-green-200 dark:border-green-800"
                        : "bg-red-50 dark:bg-red-950/20 text-red-800 dark:text-red-200 border border-red-200 dark:border-red-800"
                    )}>
                      {permissionSetupMessage.text}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}

      </div>

      <div className="mt-auto flex justify-between pt-4">
        <button
          onClick={onBack}
          className="px-6 py-2 text-muted hover:text-text font-medium transition-colors"
        >
          {t('common.back')}
        </button>
        <button
          onClick={() => onNext({ agents, default_backend: defaultBackend })}
          disabled={!canContinue}
          className={clsx(
            'px-8 py-3 rounded-lg font-medium transition-colors shadow-sm',
            canContinue
              ? 'bg-accent hover:bg-accent/90 text-white'
              : 'bg-neutral-200 text-muted cursor-not-allowed'
          )}
        >
          {t('common.continue')}
        </button>
      </div>
    </div>
  );
};

const StatusBadge = ({ status, loading }: { status: 'unknown' | 'ok' | 'missing'; loading: boolean }) => {
  const { t } = useTranslation();
  
  if (loading) {
    return (
      <div className="animate-spin text-muted">
        <RefreshCw size={20} />
      </div>
    );
  }
  if (status === 'unknown') {
    return <span className="text-sm text-muted italic">{t('common.notChecked')}</span>;
  }
  return status === 'ok' ? (
    <div className="flex items-center gap-2 text-success bg-success/10 px-3 py-1 rounded-full text-sm font-medium border border-success/20">
      <Check size={14} /> {t('common.found')}
    </div>
  ) : (
    <div className="flex items-center gap-2 text-danger bg-danger/10 px-3 py-1 rounded-full text-sm font-medium border border-danger/20">
      <X size={14} /> {t('common.missing')}
    </div>
  );
};
