import React, { useState } from 'react';
import { CheckCircle2, MessageSquare, Zap, Terminal, Copy, Check, Key } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useApi } from '../../context/ApiContext';
import { useStatus } from '../../context/StatusContext';
import { useNavigate } from 'react-router-dom';
import { useToast } from '../../context/ToastContext';
import { copyTextToClipboard } from '../../lib/utils';
import { getEnabledPlatforms, getPrimaryPlatform } from '../../lib/platforms';

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
  const { showToast } = useToast();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bindCode, setBindCode] = useState<string | null>(null);
  const [codeCopied, setCodeCopied] = useState(false);
  const enabledPlatforms = getEnabledPlatforms(data);
  const primaryPlatform = getPrimaryPlatform(data);
  const discordGuildAllowlist = Array.isArray(data.discordGuildAllowlist)
    ? data.discordGuildAllowlist
    : Array.isArray(data.discord?.guild_allowlist)
      ? data.discord.guild_allowlist
      : [];
  const [requireMentionByPlatform, setRequireMentionByPlatform] = useState<Record<string, boolean>>(
    Object.fromEntries(
      enabledPlatforms.map((platform) => [
        platform,
        platform === 'discord'
          ? (data.discord?.require_mention || false)
          : platform === 'telegram'
            ? (data.telegram?.require_mention ?? true)
          : platform === 'lark'
            ? (data.lark?.require_mention || false)
            : platform === 'wechat'
              ? (data.wechat?.require_mention || false)
              : (data.slack?.require_mention || false),
      ])
    )
  );
  const [autoUpdate, setAutoUpdate] = useState(data.update?.auto_update ?? true);
  const navigate = useNavigate();

  const copyBindCode = async () => {
    if (!bindCode) return;
    const copied = await copyTextToClipboard(`bind ${bindCode}`);
    if (!copied) {
      showToast(t('common.copyFailed'), 'error');
      return;
    }

    setCodeCopied(true);
    setTimeout(() => setCodeCopied(false), 2000);
  };

  const saveAll = async () => {
    setSaving(true);
    setError(null);
    try {
      const updatedData = {
        ...data,
        platform: primaryPlatform,
        platforms: {
          enabled: enabledPlatforms,
          primary: primaryPlatform,
        },
        slack: {
          ...data.slack,
          require_mention: requireMentionByPlatform.slack ?? data.slack?.require_mention,
        },
        discord: {
          ...data.discord,
          require_mention: requireMentionByPlatform.discord ?? data.discord?.require_mention,
        },
        telegram: {
          ...data.telegram,
          require_mention: requireMentionByPlatform.telegram ?? data.telegram?.require_mention,
        },
        lark: {
          ...data.lark,
          require_mention: requireMentionByPlatform.lark ?? data.lark?.require_mention,
        },
        wechat: {
          ...data.wechat,
          require_mention: requireMentionByPlatform.wechat ?? data.wechat?.require_mention,
        },
        update: {
          ...data.update,
          auto_update: autoUpdate,
        },
      };
      const configPayload = buildConfigPayload(updatedData);
      await api.saveConfig(configPayload);
      const settingsByPlatform = buildSettingsPayload(updatedData);
      await Promise.all(
        Object.entries(settingsByPlatform).map(([platform, payload]) => api.saveSettings(payload, platform))
      );
      
      // Start service
      await control('start');

      // WeChat: skip bind code, auto-bind happens on QR login
      if (enabledPlatforms.every((platform) => platform === 'wechat')) {
        setSaving(false);
        showToast(t('wechat.setupComplete'));
        setTimeout(() => {
          navigate('/dashboard');
        }, 1000);
        return;
      }

      // Fetch first bind code (auto-generated on first access)
      try {
        const resp = await api.getFirstBindCode();
        if (resp?.code) {
          setBindCode(resp.code);
          setSaving(false);
          return; // Don't navigate yet — show bind code first
        }
      } catch {
        // Non-critical: skip bind code display
      }

      // No bind code — redirect immediately
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

  // If bind code is available, show the bind code screen
  if (bindCode) {
    return (
      <div className="flex flex-col h-full max-w-2xl mx-auto items-center justify-center">
        <div className="w-16 h-16 bg-success/10 text-success rounded-full flex items-center justify-center border border-success/20 mb-6">
          <CheckCircle2 size={40} />
        </div>
        <h2 className="text-2xl font-display font-bold text-text mb-2">{t('summary.title')}</h2>
        <p className="text-muted mb-8">{t('summary.serviceRunning')}</p>

        <div className="bg-panel border border-border rounded-lg p-6 shadow-sm w-full max-w-md">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-10 h-10 bg-warning/10 text-warning rounded-lg flex items-center justify-center flex-shrink-0">
              <Key size={20} />
            </div>
            <div>
              <h3 className="text-base font-semibold text-text">{t('summary.bindCodeTitle')}</h3>
              <p className="text-xs text-muted">{t('summary.bindCodeDesc')}</p>
            </div>
          </div>
          <div className="flex items-center gap-2 bg-bg border border-border rounded-md p-3">
            <code className="flex-1 text-sm font-mono text-text select-all">bind {bindCode}</code>
            <button
              onClick={copyBindCode}
              className="p-2 text-muted hover:text-text transition-colors rounded-md hover:bg-panel"
              title="Copy"
            >
              {codeCopied ? <Check size={16} className="text-success" /> : <Copy size={16} />}
            </button>
          </div>
          {codeCopied && (
            <p className="text-xs text-success mt-2">{t('summary.bindCodeCopied')}</p>
          )}
        </div>

        <button
          onClick={() => navigate('/dashboard')}
          className="mt-8 px-8 py-3 bg-accent hover:bg-accent/90 text-white rounded-lg font-bold transition-colors shadow-sm"
        >
          {t('summary.goToDashboard')}
        </button>
      </div>
    );
  }

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
        <Section title={t('summary.platform')} value={enabledPlatforms.join(', ')} />
        {enabledPlatforms.includes('slack') && (
          <>
            <Section title={t('summary.slackBotToken')} value={mask(data.slack?.bot_token || '')} />
            <Section title={t('summary.slackAppToken')} value={mask(data.slack?.app_token || '')} />
          </>
        )}
        {enabledPlatforms.includes('discord') && (
          <>
            <Section title={t('summary.discordBotToken')} value={mask(data.discord?.bot_token || '')} />
            <Section
              title={t('summary.discordGuild')}
              value={discordGuildAllowlist.join(', ') || t('summary.notSet')}
            />
          </>
        )}
        {enabledPlatforms.includes('telegram') && (
          <Section title={t('summary.telegramBotToken')} value={mask(data.telegram?.bot_token || '')} />
        )}
        {enabledPlatforms.includes('lark') && (
          <Section title={t('summary.larkAppId')} value={mask(data.lark?.app_id || '')} />
        )}
        {enabledPlatforms.includes('wechat') && (
          <Section title={t('summary.wechatBotToken')} value={mask(data.wechat?.bot_token || '')} />
        )}
        <Section title={t('summary.enabledAgents')} value={enabledAgents(data).join(', ')} />
        <Section title={t('summary.channelsConfigured')} value={countConfiguredChannels(data.channelConfigsByPlatform)} />
        
        {/* Require Mention Setting */}
        <div className="bg-panel border border-border rounded-lg p-4 shadow-sm space-y-4">
          <div>
            <h3 className="text-sm font-medium text-text">{t('summary.requireMention')}</h3>
            <p className="text-xs text-muted mt-1">{t('summary.requireMentionHint')}</p>
          </div>
          {enabledPlatforms.map((platform) => (
            <div key={platform} className="flex justify-between items-center border-t border-border pt-3 first:border-t-0 first:pt-0">
              <span className="text-sm text-text capitalize">{platform}</span>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={requireMentionByPlatform[platform] || false}
                  onChange={(e) =>
                    setRequireMentionByPlatform((current) => ({
                      ...current,
                      [platform]: e.target.checked,
                    }))
                  }
                  className="sr-only peer"
                />
                <div className="w-11 h-6 bg-border rounded-full peer peer-checked:bg-success peer-focus:ring-2 peer-focus:ring-success/20 after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:after:translate-x-full"></div>
              </label>
            </div>
          ))}
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
              <div className="w-8 h-8 bg-accent/10 text-accent rounded-lg flex items-center justify-center flex-shrink-0">
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

const countConfiguredChannels = (channelConfigsByPlatform: Record<string, Record<string, any>> = {}) =>
  Object.values(channelConfigsByPlatform).reduce(
    (count, channels) => count + Object.values(channels || {}).filter((config: any) => config?.enabled).length,
    0,
  );

const buildConfigPayload = (data: any) => {
  const agents = data.agents || {};
  const enabledPlatforms = getEnabledPlatforms(data);
  const primaryPlatform = getPrimaryPlatform(data);
  return {
    platform: primaryPlatform,
    platforms: {
      enabled: enabledPlatforms,
      primary: primaryPlatform,
    },
    mode: data.mode || 'self_host',
    version: 'v2',
    slack: {
      // Preserve all existing slack fields
      ...data.slack,
      // Override only the fields that setup modifies
      bot_token: data.slack?.bot_token || '',
      app_token: data.slack?.app_token || '',
      require_mention: data.slack?.require_mention || false,
    },
    discord: {
      ...data.discord,
      bot_token: data.discord?.bot_token || '',
      require_mention: data.discord?.require_mention || false,
    },
    telegram: {
      ...data.telegram,
      bot_token: data.telegram?.bot_token || '',
      require_mention: data.telegram?.require_mention ?? true,
      forum_auto_topic: data.telegram?.forum_auto_topic ?? true,
      use_webhook: data.telegram?.use_webhook ?? false,
    },
    lark: {
      ...data.lark,
      app_id: data.lark?.app_id || '',
      app_secret: data.lark?.app_secret || '',
      domain: data.lark?.domain || 'feishu',
      require_mention: data.lark?.require_mention || false,
    },
    wechat: {
      ...data.wechat,
      bot_token: data.wechat?.bot_token || '',
      base_url: data.wechat?.base_url || '',
      require_mention: data.wechat?.require_mention || false,
    },
    runtime: {
      // Preserve existing runtime config
      ...data.runtime,
      default_cwd: data.default_cwd || data.runtime?.default_cwd || '_tmp',
    },
    agents: {
      default_backend: data.default_backend || 'opencode',
      opencode: {
        // Preserve existing opencode config
        ...agents.opencode,
        enabled: agents.opencode?.enabled ?? true,
        cli_path: agents.opencode?.cli_path || 'opencode',
        default_agent: data.opencode_default_agent ?? agents.opencode?.default_agent ?? null,
        default_model: data.opencode_default_model ?? agents.opencode?.default_model ?? null,
        default_reasoning_effort: data.opencode_default_reasoning_effort ?? agents.opencode?.default_reasoning_effort ?? null,
      },
      claude: {
        // Preserve existing claude config
        ...agents.claude,
        enabled: agents.claude?.enabled ?? true,
        cli_path: agents.claude?.cli_path || 'claude',
        default_model: data.claude_default_model ?? agents.claude?.default_model ?? null,
      },
      codex: {
        // Preserve existing codex config
        ...agents.codex,
        enabled: agents.codex?.enabled ?? false,
        cli_path: agents.codex?.cli_path || 'codex',
        default_model: data.codex_default_model ?? agents.codex?.default_model ?? null,
      },
    },
    // Preserve gateway config entirely
    gateway: data.gateway,
    ui: {
      // Preserve existing ui config
      ...data.ui,
      setup_host: data.ui?.setup_host || '127.0.0.1',
      setup_port: data.ui?.setup_port || 5123,
    },
    // Preserve existing update config, only override auto_update from UI toggle
    update: data.update ? {
      ...data.update,
      auto_update: data.update.auto_update,
    } : undefined,
    // Preserve ack_mode
    ack_mode: data.ack_mode,
    show_duration: data.show_duration ?? false,
    // Preserve language
    language: data.language,
  };
};

const buildSettingsPayload = (data: any) => {
  const channelConfigsByPlatform = data.channelConfigsByPlatform || {};
  const discordGuildAllowlist = Array.isArray(data.discordGuildAllowlist)
    ? data.discordGuildAllowlist
    : Array.isArray(data.discord?.guild_allowlist)
      ? data.discord.guild_allowlist
      : [];
  return Object.fromEntries(
    Object.entries(channelConfigsByPlatform).map(([platform, channels]: any) => [
      platform,
      {
        channels: Object.fromEntries(
          Object.entries(channels || {}).map(([id, cfg]: any) => [
            id,
            {
              enabled: cfg.enabled,
              show_message_types: cfg.show_message_types || [],
              custom_cwd: cfg.custom_cwd || null,
              require_mention: cfg.require_mention ?? null,
              routing: {
                agent_backend: cfg.routing?.agent_backend || null,
                opencode_agent: cfg.routing?.opencode_agent || null,
                opencode_model: cfg.routing?.opencode_model || null,
                opencode_reasoning_effort: cfg.routing?.opencode_reasoning_effort || null,
                claude_agent: cfg.routing?.claude_agent || null,
                claude_model: cfg.routing?.claude_model || null,
                claude_reasoning_effort: cfg.routing?.claude_reasoning_effort || null,
                codex_agent: cfg.routing?.codex_agent || null,
                codex_model: cfg.routing?.codex_model || null,
                codex_reasoning_effort: cfg.routing?.codex_reasoning_effort || null,
              },
            },
          ])
        ),
        ...(platform === 'discord'
          ? {
              guilds: Object.fromEntries(
                discordGuildAllowlist.map((guildId: string) => [guildId, { enabled: true }])
              ),
            }
          : {}),
      },
    ])
  );
};
