import React, { useState } from 'react';
import { CheckCircle2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useApi } from '../../context/ApiContext';
import { useStatus } from '../../context/StatusContext';
import { useNavigate } from 'react-router-dom';

interface SummaryProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
  isFirst: boolean;
  isLast: boolean;
}

export const Summary: React.FC<SummaryProps> = ({ data, onBack }) => {
  const { t } = useTranslation();
  const api = useApi();
  const { control } = useStatus();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const saveAll = async () => {
    setSaving(true);
    setError(null);
    try {
      const configPayload = buildConfigPayload(data);
      await api.saveConfig(configPayload);
      await api.saveSettings(buildSettingsPayload(data));
      
      // Start service
      await control('start'); // Use start, fallback to restart if running? Or just start.
      // Wait a bit then redirect
      setTimeout(() => {
           navigate('/dashboard');
      }, 1000);

    } catch (exc: any) {
      const message = exc && exc.message ? exc.message : 'Failed to save configuration';
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col h-full max-w-2xl mx-auto">
      <div className="flex items-center gap-4 mb-8">
        <div className="w-12 h-12 bg-success/10 text-success rounded-full flex items-center justify-center border border-success/20">
          <CheckCircle2 size={32} />
        </div>
        <div>
          <h2 className="text-2xl font-display font-bold text-text">{t('summary.title')}</h2>
          <p className="text-muted">{t('summary.subtitle')}</p>
        </div>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto mb-6">
        <Section title={t('summary.mode')} value={data.mode} />
        <Section title={t('summary.slackBotToken')} value={mask(data.slack?.bot_token || '')} />
        <Section title={t('summary.slackAppToken')} value={mask(data.slack?.app_token || '')} />
        <Section title={t('summary.enabledAgents')} value={enabledAgents(data).join(', ')} />
        <Section title={t('summary.channelsConfigured')} value={Object.keys(data.channelConfigs || {}).filter(k => data.channelConfigs[k]?.enabled).length} />
      </div>

      {error && (
        <div className="p-4 bg-danger/10 text-danger border border-danger/20 rounded-lg mb-4 text-sm">
            {error}
        </div>
      )}

      <div className="mt-auto flex justify-between">
        <button
          onClick={onBack}
          className="px-6 py-2 text-muted hover:text-text font-medium transition-colors"
        >
          {t('common.back')}
        </button>
        <button
          onClick={saveAll}
          disabled={saving}
          className="px-8 py-3 bg-success hover:bg-success/90 text-white rounded-lg font-bold transition-colors shadow-sm"
        >
          {saving ? t('common.saving') : t('summary.finishAndStart')}
        </button>
      </div>
    </div>
  );
};

const Section = ({ title, value }: { title: string; value: any }) => (
  <div className="bg-panel border border-border rounded-lg p-4 shadow-sm flex justify-between items-center">
    <h3 className="text-sm font-medium text-muted uppercase tracking-wider">{title}</h3>
    <div className="text-text font-medium text-sm">{String(value)}</div>
  </div>
);

const mask = (value: string) => (value ? `${value.slice(0, 6)}...${value.slice(-4)}` : 'Not set');

const enabledAgents = (data: any) => {
  const agents = data.agents || {};
  return Object.keys(agents).filter((name) => agents[name]?.enabled);
};

const buildConfigPayload = (data: any) => {
  const agents = data.agents || {};
  return {
    mode: data.mode || 'self_host',
    version: 'v2',
    slack: {
      bot_token: data.slack?.bot_token || '',
      app_token: data.slack?.app_token || '',
      require_mention: data.slack?.require_mention || false,
    },
    runtime: {
      default_cwd: data.default_cwd || '/Users/cyh/PycharmProjects/vibe-remote/_tmp', // Use default if empty
      log_level: 'INFO',
    },
    agents: {
      default_backend: data.default_backend || 'opencode',
      opencode: {
        enabled: agents.opencode?.enabled ?? true,
        cli_path: agents.opencode?.cli_path || 'opencode',
        default_agent: data.opencode_default_agent || null,
        default_model: data.opencode_default_model || null,
        default_reasoning_effort: data.opencode_default_reasoning_effort || null,
      },
      claude: {
        enabled: agents.claude?.enabled ?? true,
        cli_path: agents.claude?.cli_path || 'claude',
        default_model: data.claude_default_model || null,
      },
      codex: {
        enabled: agents.codex?.enabled ?? false,
        cli_path: agents.codex?.cli_path || 'codex',
        default_model: data.codex_default_model || null,
      },
    },
    ui: {
      setup_host: data.ui?.setup_host || '127.0.0.1',
      setup_port: data.ui?.setup_port || 5123,
      open_browser: true,
    },
  };
};

const buildSettingsPayload = (data: any) => {
  const channels = data.channelConfigs || {};
  return {
    channels: Object.fromEntries(
      Object.entries(channels).map(([id, cfg]: any) => [
        id,
        {
          enabled: cfg.enabled,
          hidden_message_types: cfg.hidden_message_types || ['system', 'toolcall'],
          custom_cwd: cfg.custom_cwd || null,
          routing: {
            agent_backend: cfg.routing?.agent_backend || null,
            opencode_agent: cfg.routing?.opencode_agent || null,
            opencode_model: cfg.routing?.opencode_model || null,
            opencode_reasoning_effort: cfg.routing?.opencode_reasoning_effort || null,
          },
        },
      ])
    ),
  };
};
