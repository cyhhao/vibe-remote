import React, { useState } from 'react';
import { CheckCircle2, MessageSquare, Zap, Terminal } from 'lucide-react';
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
  const [requireMention, setRequireMention] = useState(data.slack?.require_mention || false);
  const [autoUpdate, setAutoUpdate] = useState(data.update?.auto_update ?? true);
  const navigate = useNavigate();

  const saveAll = async () => {
    setSaving(true);
    setError(null);
    try {
      const updatedData = {
        ...data,
        slack: {
          ...data.slack,
          require_mention: requireMention,
        },
        update: {
          ...data.update,
          auto_update: autoUpdate,
        },
      };
      const configPayload = buildConfigPayload(updatedData);
      await api.saveConfig(configPayload);
      await api.saveSettings(buildSettingsPayload(updatedData));
      
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
        
        {/* Require Mention Setting */}
        <div className="bg-panel border border-border rounded-lg p-4 shadow-sm">
          <div className="flex justify-between items-center">
            <div>
              <h3 className="text-sm font-medium text-text">{t('summary.requireMention')}</h3>
              <p className="text-xs text-muted mt-1">{t('summary.requireMentionHint')}</p>
            </div>
            <label className="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={requireMention}
                onChange={(e) => setRequireMention(e.target.checked)}
                className="sr-only peer"
              />
              <div className="w-11 h-6 bg-border rounded-full peer peer-checked:bg-success peer-focus:ring-2 peer-focus:ring-success/20 after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:after:translate-x-full"></div>
            </label>
          </div>
        </div>

        {/* Auto Update Setting */}
        <div className="bg-panel border border-border rounded-lg p-4 shadow-sm">
          <div className="flex justify-between items-center">
            <div>
              <h3 className="text-sm font-medium text-text">{t('summary.autoUpdate')}</h3>
              <p className="text-xs text-muted mt-1">{t('summary.autoUpdateHint')}</p>
            </div>
            <label className="relative inline-flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={autoUpdate}
                onChange={(e) => setAutoUpdate(e.target.checked)}
                className="sr-only peer"
              />
              <div className="w-11 h-6 bg-border rounded-full peer peer-checked:bg-success peer-focus:ring-2 peer-focus:ring-success/20 after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:after:translate-x-full"></div>
            </label>
          </div>
        </div>

        {/* Usage Tips */}
        <div className="bg-panel border border-border rounded-lg p-4 shadow-sm">
          <h3 className="text-sm font-medium text-text mb-3">{t('summary.usageTips')}</h3>
          <div className="space-y-3">
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 bg-primary/10 text-primary rounded-lg flex items-center justify-center flex-shrink-0">
                <Terminal size={16} />
              </div>
              <div>
                <p className="text-sm font-medium text-text">{t('summary.tipStartCommand')}</p>
                <p className="text-xs text-muted">{t('summary.tipStartCommandDesc')}</p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 bg-warning/10 text-warning rounded-lg flex items-center justify-center flex-shrink-0">
                <Zap size={16} />
              </div>
              <div>
                <p className="text-sm font-medium text-text">{t('summary.tipAgentSwitch')}</p>
                <p className="text-xs text-muted">{t('summary.tipAgentSwitchDesc')}</p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <div className="w-8 h-8 bg-success/10 text-success rounded-lg flex items-center justify-center flex-shrink-0">
                <MessageSquare size={16} />
              </div>
              <div>
                <p className="text-sm font-medium text-text">{t('summary.tipThread')}</p>
                <p className="text-xs text-muted">{t('summary.tipThreadDesc')}</p>
              </div>
            </div>
          </div>
        </div>
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
      default_cwd: data.default_cwd || '_tmp',
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
    update: {
      auto_update: data.update?.auto_update ?? true,
      check_interval_minutes: data.update?.check_interval_minutes ?? 10,
      idle_minutes: data.update?.idle_minutes ?? 30,
      notify_slack: data.update?.notify_slack ?? true,
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
          show_message_types: cfg.show_message_types || [],
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
