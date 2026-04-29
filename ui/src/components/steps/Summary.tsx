import React, { useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  Check,
  Copy,
  Key,
  MessageSquare,
  Sparkles,
  Terminal,
  Zap,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';
import { useStatus } from '../../context/StatusContext';
import { useToast } from '../../context/ToastContext';
import { copyTextToClipboard } from '../../lib/utils';
import { getEnabledPlatforms, getPrimaryPlatform } from '../../lib/platforms';
import { EyebrowBadge, WizardCard } from '../visual';

interface SummaryProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
  isFirst: boolean;
  isLast: boolean;
}

// Mirrors design.pen X9wTM (Summary): mint-accented WizardCard with 72×72 check
// halo, 38px title, recap rows, then secondary toggles and quick-start tips.
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

      await control('start');

      if (enabledPlatforms.every((platform) => platform === 'wechat')) {
        setSaving(false);
        showToast(t('wechat.setupComplete'));
        setTimeout(() => {
          navigate('/dashboard');
        }, 1000);
        return;
      }

      try {
        const resp = await api.getFirstBindCode();
        if (resp?.code) {
          setBindCode(resp.code);
          setSaving(false);
          return;
        }
      } catch {
        /* non-critical */
      }

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

  const recapRows: Array<{ label: string; value: string }> = [
    { label: t('summary.platform'), value: enabledPlatforms.map((p) => titleCase(p)).join(', ') || '—' },
    {
      label: t('summary.enabledAgents'),
      value: enabledAgents(data).map(titleCase).join(', ') || '—',
    },
    {
      label: t('summary.channelsConfigured'),
      value: String(countConfiguredChannels(data.channelConfigsByPlatform)),
    },
  ];

  if (enabledPlatforms.includes('slack')) {
    recapRows.push({ label: t('summary.slackBotToken'), value: mask(data.slack?.bot_token || '') });
  }
  if (enabledPlatforms.includes('discord')) {
    recapRows.push({ label: t('summary.discordBotToken'), value: mask(data.discord?.bot_token || '') });
    recapRows.push({
      label: t('summary.discordGuild'),
      value: discordGuildAllowlist.join(', ') || t('summary.notSet'),
    });
  }
  if (enabledPlatforms.includes('telegram')) {
    recapRows.push({ label: t('summary.telegramBotToken'), value: mask(data.telegram?.bot_token || '') });
  }
  if (enabledPlatforms.includes('lark')) {
    recapRows.push({ label: t('summary.larkAppId'), value: mask(data.lark?.app_id || '') });
  }
  if (enabledPlatforms.includes('wechat')) {
    recapRows.push({ label: t('summary.wechatBotToken'), value: mask(data.wechat?.bot_token || '') });
  }

  if (bindCode) {
    return (
      <div className="flex w-full justify-center">
        <WizardCard accent size="hero" className="items-center gap-6 text-center">
          <div className="flex size-[72px] items-center justify-center rounded-full border-2 border-mint/40 bg-mint/[0.08] text-mint shadow-[0_0_48px_-6px_rgba(91,255,160,0.7)]">
            <Check size={36} strokeWidth={2.4} />
          </div>
          <div className="space-y-2">
            <h2 className="text-[38px] font-bold leading-tight tracking-[-0.7px] text-foreground">
              {t('summary.title')}
            </h2>
            <p className="mx-auto max-w-[600px] text-[15px] leading-[1.55] text-muted">
              {t('summary.serviceRunning')}
            </p>
          </div>
          <div className="w-full max-w-md rounded-xl border border-gold/30 bg-gold/[0.06] px-5 py-4 text-left">
            <div className="mb-3 flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-lg border border-gold/30 bg-gold/15 text-gold">
                <Key size={18} />
              </div>
              <div>
                <h3 className="text-[14px] font-semibold text-foreground">{t('summary.bindCodeTitle')}</h3>
                <p className="text-[11px] text-muted">{t('summary.bindCodeDesc')}</p>
              </div>
            </div>
            <div className="flex items-center gap-2 rounded-lg border border-border bg-background px-3 py-2.5">
              <code className="flex-1 select-all font-mono text-[13px] text-foreground">bind {bindCode}</code>
              <button
                onClick={copyBindCode}
                className="rounded-md p-1.5 text-muted transition hover:bg-white/[0.04] hover:text-foreground"
                title="Copy"
                aria-label={t('common.copy') as string}
              >
                {codeCopied ? <Check size={16} className="text-mint" /> : <Copy size={16} />}
              </button>
            </div>
            {codeCopied && (
              <p className="mt-2 text-[11px] text-mint">{t('summary.bindCodeCopied')}</p>
            )}
          </div>
          <button
            onClick={() => navigate('/dashboard')}
            className="inline-flex items-center gap-2 rounded-lg bg-mint px-7 py-3 text-[14px] font-bold text-[#080812] shadow-[0_0_48px_-8px_rgba(91,255,160,0.7)] transition hover:brightness-105"
          >
            {t('summary.goToDashboard')}
            <ArrowRight size={16} strokeWidth={2.25} />
          </button>
        </WizardCard>
      </div>
    );
  }

  return (
    <div className="flex w-full justify-center">
      <WizardCard accent size="hero" className="gap-6">
        <div className="flex flex-col items-center gap-5 text-center">
          <div className="flex size-[72px] items-center justify-center rounded-full border-2 border-mint/40 bg-mint/[0.08] text-mint shadow-[0_0_48px_-6px_rgba(91,255,160,0.7)]">
            <Check size={36} strokeWidth={2.4} />
          </div>
          <div className="space-y-2">
            <EyebrowBadge tone="mint">Summary</EyebrowBadge>
            <h2 className="text-[38px] font-bold leading-tight tracking-[-0.7px] text-foreground">
              {t('summary.title')}
            </h2>
            <p className="mx-auto max-w-[600px] text-[15px] leading-[1.55] text-muted">
              {t('summary.subtitle')}
            </p>
          </div>
        </div>

        {/* Recap card */}
        <div className="overflow-hidden rounded-xl border border-border bg-background">
          {recapRows.map((row, idx) => (
            <div
              key={row.label}
              className={clsx(
                'flex items-center justify-between gap-4 px-5 py-3.5',
                idx < recapRows.length - 1 && 'border-b border-border'
              )}
            >
              <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-muted">{row.label}</span>
              <span className="truncate font-mono text-[12px] text-foreground" title={row.value}>
                {row.value}
              </span>
            </div>
          ))}
        </div>

        {/* Require Mention */}
        {enabledPlatforms.length > 0 && (
          <div className="rounded-xl border border-border bg-background px-5 py-4">
            <div className="mb-3">
              <h3 className="text-[13px] font-semibold text-foreground">{t('summary.requireMention')}</h3>
              <p className="mt-0.5 text-[11px] text-muted">{t('summary.requireMentionHint')}</p>
            </div>
            <div className="flex flex-col gap-2.5">
              {enabledPlatforms.map((platform) => (
                <div
                  key={platform}
                  className="flex items-center justify-between rounded-lg border border-border bg-surface-2 px-3 py-2"
                >
                  <span className="text-[12px] font-medium text-foreground">{titleCase(platform)}</span>
                  <Switch
                    checked={!!requireMentionByPlatform[platform]}
                    onChange={(value) =>
                      setRequireMentionByPlatform((current) => ({ ...current, [platform]: value }))
                    }
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Auto Update */}
        <div className="flex items-center justify-between rounded-xl border border-border bg-background px-5 py-4">
          <div>
            <h3 className="text-[13px] font-semibold text-foreground">{t('summary.autoUpdate')}</h3>
            <p className="mt-0.5 text-[11px] text-muted">{t('summary.autoUpdateHint')}</p>
          </div>
          <Switch checked={autoUpdate} onChange={setAutoUpdate} />
        </div>

        {/* Quick start tips */}
        <div className="rounded-xl border border-cyan/30 bg-cyan/[0.05] px-5 py-4">
          <div className="mb-3 flex items-center gap-2">
            <Sparkles size={14} className="text-cyan" />
            <h3 className="text-[13px] font-semibold text-foreground">{t('summary.usageTips')}</h3>
          </div>
          <div className="space-y-3">
            <Tip
              icon={<Terminal size={14} />}
              tone="cyan"
              title={t('summary.tipStartCommand')}
              description={t('summary.tipStartCommandDesc')}
            />
            <Tip
              icon={<Zap size={14} />}
              tone="gold"
              title={t('summary.tipAgentSwitch')}
              description={t('summary.tipAgentSwitchDesc')}
            />
            <Tip
              icon={<MessageSquare size={14} />}
              tone="mint"
              title={t('summary.tipThread')}
              description={t('summary.tipThreadDesc')}
            />
          </div>
        </div>

        {error && (
          <div className="rounded-lg border border-danger/30 bg-danger/10 px-4 py-3 text-[12px] text-danger">
            {error}
          </div>
        )}

        <div className="flex items-center justify-between border-t border-border pt-4">
          <button
            type="button"
            onClick={onBack}
            disabled={saving}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-4 py-2 text-[13px] font-semibold text-foreground transition hover:border-border-strong disabled:opacity-50"
          >
            <ArrowLeft size={14} strokeWidth={2.25} />
            {t('common.back')}
          </button>
          <button
            type="button"
            onClick={saveAll}
            disabled={saving}
            className="inline-flex items-center gap-2 rounded-lg bg-mint px-7 py-3 text-[14px] font-bold text-[#080812] shadow-[0_0_48px_-8px_rgba(91,255,160,0.7)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50 disabled:shadow-none"
          >
            {saving ? t('common.saving') : t('summary.finishAndStart')}
            {!saving && <ArrowRight size={16} strokeWidth={2.25} />}
          </button>
        </div>
      </WizardCard>
    </div>
  );
};

const Switch: React.FC<{ checked: boolean; onChange: (next: boolean) => void }> = ({ checked, onChange }) => (
  <button
    type="button"
    role="switch"
    aria-checked={checked}
    onClick={() => onChange(!checked)}
    className={clsx(
      'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors focus:outline-none focus:ring-2 focus:ring-mint/40',
      checked
        ? 'border-mint/50 bg-mint shadow-[0_0_12px_-2px_rgba(91,255,160,0.6)]'
        : 'border-border bg-surface-2'
    )}
  >
    <span
      className={clsx(
        'inline-block size-3.5 rounded-full bg-background shadow transition-transform',
        checked ? 'translate-x-[18px]' : 'translate-x-1'
      )}
    />
  </button>
);

const Tip: React.FC<{
  icon: React.ReactNode;
  tone: 'cyan' | 'gold' | 'mint';
  title: string;
  description: string;
}> = ({ icon, tone, title, description }) => {
  const toneClasses = {
    cyan: 'border-cyan/30 bg-cyan/[0.08] text-cyan',
    gold: 'border-gold/30 bg-gold/10 text-gold',
    mint: 'border-mint/30 bg-mint/[0.08] text-mint',
  }[tone];
  return (
    <div className="flex items-start gap-3">
      <div className={clsx('flex size-8 shrink-0 items-center justify-center rounded-lg border', toneClasses)}>
        {icon}
      </div>
      <div className="min-w-0">
        <p className="text-[12px] font-semibold text-foreground">{title}</p>
        <p className="mt-0.5 text-[11px] leading-[1.5] text-muted">{description}</p>
      </div>
    </div>
  );
};

const titleCase = (value: string) => value.charAt(0).toUpperCase() + value.slice(1);

const mask = (value: string) => (value ? `${value.slice(0, 6)}...${value.slice(-4)}` : 'Not set');

const enabledAgents = (data: any) => {
  const agents = data.agents || {};
  return Object.keys(agents).filter((name) => agents[name]?.enabled);
};

const countConfiguredChannels = (channelConfigsByPlatform: Record<string, Record<string, any>> = {}) =>
  Object.values(channelConfigsByPlatform).reduce(
    (count, channels) => count + Object.values(channels || {}).filter((config: any) => config?.enabled).length,
    0
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
      ...data.slack,
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
      ...data.runtime,
      default_cwd: data.default_cwd || data.runtime?.default_cwd || '_tmp',
    },
    agents: {
      default_backend: data.default_backend || 'opencode',
      opencode: {
        ...agents.opencode,
        enabled: agents.opencode?.enabled ?? true,
        cli_path: agents.opencode?.cli_path || 'opencode',
        default_agent: data.opencode_default_agent ?? agents.opencode?.default_agent ?? null,
        default_model: data.opencode_default_model ?? agents.opencode?.default_model ?? null,
        default_reasoning_effort:
          data.opencode_default_reasoning_effort ?? agents.opencode?.default_reasoning_effort ?? null,
      },
      claude: {
        ...agents.claude,
        enabled: agents.claude?.enabled ?? true,
        cli_path: agents.claude?.cli_path || 'claude',
        default_model: data.claude_default_model ?? agents.claude?.default_model ?? null,
      },
      codex: {
        ...agents.codex,
        enabled: agents.codex?.enabled ?? false,
        cli_path: agents.codex?.cli_path || 'codex',
        default_model: data.codex_default_model ?? agents.codex?.default_model ?? null,
      },
    },
    gateway: data.gateway,
    ui: {
      ...data.ui,
      setup_host: data.ui?.setup_host || '127.0.0.1',
      setup_port: data.ui?.setup_port || 5123,
    },
    update: data.update
      ? {
          ...data.update,
          auto_update: data.update.auto_update,
        }
      : undefined,
    ack_mode: data.ack_mode,
    show_duration: data.show_duration ?? false,
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
  const shouldPersistDiscordGuilds =
    discordGuildAllowlist.length > 0 || data.discordGuildAllowlistTouched === true;
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
        ...(platform === 'discord' && shouldPersistDiscordGuilds
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
