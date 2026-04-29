import React, { useEffect, useMemo, useState } from 'react';
import { ArrowRight, MessageSquare } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import { useApi } from '@/context/ApiContext';
import { SettingsPageShell } from './SettingsPageShell';
import {
  platformHasCapability,
  getEnabledPlatforms,
  getPrimaryPlatform,
} from '@/lib/platforms';
import {
  CompactField,
  CompactSelect,
  SettingsPanel,
  SettingsRow,
  ToggleSwitch,
} from './SettingsPrimitives';

const SAVE_KEYS = [
  'platforms',
  'ack_mode',
  'show_duration',
  'include_user_info',
  'reply_enhancements',
  'slack',
  'discord',
  'telegram',
  'lark',
  'wechat',
  'agents',
] as const;

function buildMessagePatch(config: any) {
  const patch: Record<string, unknown> = {
    platform: getPrimaryPlatform(config),
  };

  for (const key of SAVE_KEYS) {
    patch[key] = config?.[key];
  }

  return patch;
}

function formatSavedAt(value: number | null, t: (key: string) => string) {
  if (!value) return t('settings.messagingStatusIdle');
  const deltaSec = Math.max(0, Math.round((Date.now() - value) / 1000));
  return deltaSec <= 1
    ? t('settings.messagingStatusJustNow')
    : t('settings.messagingStatusAgo').replace('{{seconds}}', String(deltaSec));
}

// Mirrors design.pen TDgw0 (VR/CM/Messaging):
// msgIntro 15px semibold + 12px muted, msgSec1 cornerRadius 12 fill --background
// stroke --border, value rows padding [14, 20] separated by bottom border.
export const SettingsMessagingPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const [config, setConfig] = useState<any>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, [api]);

  const enabledPlatforms = useMemo(() => getEnabledPlatforms(config), [config]);
  const slackSupportsLinkUnfurl = enabledPlatforms.includes('slack');
  const reactionSupported = enabledPlatforms.some((platform) =>
    platformHasCapability(config, platform, 'supports_reaction_indicator')
  );
  const typingSupported = enabledPlatforms.some((platform) =>
    platformHasCapability(config, platform, 'supports_typing_indicator')
  );

  const persist = async (nextConfig: any) => {
    setConfig(nextConfig);
    setSaveError(null);
    try {
      await api.saveConfig(buildMessagePatch(nextConfig));
      setSavedAt(Date.now());
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : t('common.saveFailed'));
    }
  };

  if (!config) {
    return (
      <SettingsPageShell
        activeTab="messaging"
        title={t('settings.messagingTitle')}
        subtitle={t('settings.messagingSubtitle')}
      >
        <div className="text-[13px] text-muted">{t('common.loading')}</div>
      </SettingsPageShell>
    );
  }

  const ackOptions = [
    { value: 'typing', label: t('dashboard.ackTyping'), disabled: !typingSupported },
    { value: 'reaction', label: t('dashboard.ackReaction'), disabled: !reactionSupported },
    { value: 'message', label: t('dashboard.ackMessage'), disabled: false },
  ];

  return (
    <SettingsPageShell
      activeTab="messaging"
      title={t('settings.messagingTitle')}
      subtitle={t('settings.messagingSubtitle')}
      actions={
        <div className="flex items-center gap-2">
          <span
            className={clsx(
              'inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.14em]',
              saveError
                ? 'border-danger/30 bg-danger/10 text-danger'
                : 'border-mint/30 bg-mint/[0.08] text-mint'
            )}
          >
            {saveError ? t('common.saveFailed') : t('settings.messagingAutosaved')}
          </span>
          <span className="font-mono text-[10px] text-muted">
            {saveError || formatSavedAt(savedAt, t)}
          </span>
        </div>
      }
    >
      <SettingsPanel
        title={
          <span className="inline-flex items-center gap-2">
            <MessageSquare className="size-3.5 text-cyan" />
            {t('dashboard.messageHandling')}
          </span>
        }
        description={t('settings.messagingCardDescription')}
      >
        <SettingsRow
          title={t('dashboard.ackMode')}
          description={t('dashboard.ackModeHint')}
          control={
            <CompactSelect
              value={config.ack_mode || 'typing'}
              onChange={(event) =>
                void persist({ ...config, ack_mode: event.target.value || 'typing' })
              }
              className="w-40"
            >
              {ackOptions.map((option) => (
                <option key={option.value} value={option.value} disabled={option.disabled}>
                  {option.label}
                </option>
              ))}
            </CompactSelect>
          }
        />

        <SettingsRow
          title={t('dashboard.errorRetryLimit')}
          description={t('dashboard.errorRetryLimitHint')}
          control={
            <CompactField
              type="number"
              min={0}
              max={10}
              value={config.agents?.opencode?.error_retry_limit ?? 1}
              onChange={(event) => {
                const limit = Math.max(0, Math.min(10, Number(event.target.value) || 0));
                void persist({
                  ...config,
                  agents: {
                    ...(config.agents || {}),
                    opencode: {
                      ...(config.agents?.opencode || {}),
                      error_retry_limit: limit,
                    },
                  },
                });
              }}
              className="w-24 text-center font-mono"
            />
          }
        />

        <SettingsRow
          title={t('dashboard.showDuration')}
          description={t('dashboard.showDurationHint')}
          control={
            <ToggleSwitch
              enabled={config.show_duration !== false}
              onClick={() => void persist({ ...config, show_duration: !config.show_duration })}
            />
          }
        />

        <SettingsRow
          title={t('dashboard.includeUserInfo')}
          description={t('dashboard.includeUserInfoHint')}
          control={
            <ToggleSwitch
              enabled={Boolean(config.include_user_info)}
              onClick={() =>
                void persist({ ...config, include_user_info: !config.include_user_info })
              }
            />
          }
        />

        <SettingsRow
          title={t('dashboard.replyEnhancements')}
          description={t('dashboard.replyEnhancementsHint')}
          control={
            <ToggleSwitch
              enabled={config.reply_enhancements !== false}
              onClick={() =>
                void persist({ ...config, reply_enhancements: !config.reply_enhancements })
              }
            />
          }
        />

        {slackSupportsLinkUnfurl && (
          <SettingsRow
            title={t('dashboard.slackLinkPreviews')}
            description={t('dashboard.slackLinkPreviewsHint')}
            control={
              <ToggleSwitch
                enabled={Boolean(config.slack?.disable_link_unfurl)}
                onClick={() =>
                  void persist({
                    ...config,
                    slack: {
                      ...(config.slack || {}),
                      disable_link_unfurl: !config.slack?.disable_link_unfurl,
                    },
                  })
                }
              />
            }
          />
        )}

        <SettingsRow
          title={t('dashboard.allowedChannels')}
          description={t('settings.messagingGroupsHint')}
          control={
            <Link
              to="/groups"
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-3 text-[12px] font-medium text-foreground transition hover:border-border-strong"
            >
              {t('common.manageChannels')}
              <ArrowRight className="size-3.5" strokeWidth={2.25} />
            </Link>
          }
        />
      </SettingsPanel>
    </SettingsPageShell>
  );
};
