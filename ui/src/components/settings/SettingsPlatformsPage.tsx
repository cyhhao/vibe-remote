import React, { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { Check, ChevronUp, Loader2, Pencil, RefreshCw, RotateCw } from 'lucide-react';

import { useApi } from '@/context/ApiContext';
import { useStatus } from '@/context/StatusContext';
import { useToast } from '@/context/ToastContext';
import { getEnabledPlatforms, getPlatformCatalog, getPrimaryPlatform, platformHasCredentials } from '@/lib/platforms';
import { PlatformIcon } from '@/components/visual';
import { SlackConfig } from '@/components/steps/SlackConfig';
import { DiscordConfig } from '@/components/steps/DiscordConfig';
import { TelegramConfig } from '@/components/steps/TelegramConfig';
import { LarkConfig } from '@/components/steps/LarkConfig';
import { WeChatConfig } from '@/components/steps/WeChatConfig';
import { SettingsPageShell } from './SettingsPageShell';
import { Button } from '@/components/ui/button';

const PLATFORM_TILE_STYLES: Record<string, { bg: string; border: string }> = {
  slack: { bg: 'bg-[#4A154B26]', border: 'border-[#4A154B66]' },
  discord: { bg: 'bg-[#5865F226]', border: 'border-[#5865F255]' },
  telegram: { bg: 'bg-[#0088CC26]', border: 'border-[#0088CC55]' },
  lark: { bg: 'bg-[#06A0FB1F]', border: 'border-[#06A0FB55]' },
  feishu: { bg: 'bg-[#06A0FB1F]', border: 'border-[#06A0FB55]' },
  wechat: { bg: 'bg-[#07C16026]', border: 'border-[#07C16055]' },
};

type ExpandedKey = string | null; // 'enabled' | platform id | null

export const SettingsPlatformsPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { control } = useStatus();
  const { showToast } = useToast();
  const [config, setConfig] = useState<any>(null);
  const [expanded, setExpanded] = useState<ExpandedKey>(null);
  const [draftEnabled, setDraftEnabled] = useState<string[]>([]);
  const [draftPrimary, setDraftPrimary] = useState<string>('');
  const [savingEnabled, setSavingEnabled] = useState(false);
  const [restartPhase, setRestartPhase] = useState<'idle' | 'saving' | 'restarting'>('idle');

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, [api]);

  const platformCatalog = useMemo(() => (config ? getPlatformCatalog(config) : []), [config]);
  const enabledPlatforms = useMemo(() => (config ? getEnabledPlatforms(config) : []), [config]);
  const primary = useMemo(() => (config ? getPrimaryPlatform(config) : ''), [config]);

  const toggle = (key: string) => {
    setExpanded((prev) => (prev === key ? null : key));
    if (key === 'enabled') {
      setDraftEnabled(enabledPlatforms);
      setDraftPrimary(primary);
    }
  };

  const closeAll = () => setExpanded(null);

  const saveAndRestart = async (nextData: any) => {
    setRestartPhase('saving');
    try {
      try {
        await api.saveConfig(nextData);
      } catch {
        // Surface save failures to the user instead of letting the rejection
        // propagate as an unhandled async error from the click handler.
        showToast(t('common.saveFailed'), 'error');
        return;
      }
      setConfig((prev: any) => ({ ...(prev || {}), ...nextData }));
      closeAll();
      setRestartPhase('restarting');
      try {
        await control('restart');
        showToast(t('platform.restartedSuccess'), 'success');
      } catch {
        showToast(t('platform.restartFailed'), 'error');
      }
    } finally {
      setRestartPhase('idle');
    }
  };

  const handleApplyPlatform = async (nextData: any) => {
    await saveAndRestart(nextData);
  };

  const toggleDraftPlatform = (id: string) => {
    setDraftEnabled((prev) => {
      if (prev.includes(id)) {
        const next = prev.filter((p) => p !== id);
        if (next.length && draftPrimary === id) setDraftPrimary(next[0]);
        return next.length ? next : prev;
      }
      const next = [...prev, id];
      if (!prev.length) setDraftPrimary(id);
      return next;
    });
  };

  const applyEnabled = async () => {
    if (!draftEnabled.length) return;
    const resolvedPrimary = draftEnabled.includes(draftPrimary) ? draftPrimary : draftEnabled[0];
    const nextData = {
      ...config,
      platform: resolvedPrimary,
      platforms: { enabled: draftEnabled, primary: resolvedPrimary },
    };
    setSavingEnabled(true);
    try {
      await saveAndRestart(nextData);
    } finally {
      setSavingEnabled(false);
    }
  };

  if (!config) {
    return (
      <SettingsPageShell
        activeTab="platforms"
        title={t('settings.platformsTitle')}
        subtitle={t('settings.platformsSubtitle')}
      >
        <div className="text-sm text-muted">{t('common.loading')}</div>
      </SettingsPageShell>
    );
  }

  return (
    <SettingsPageShell
      activeTab="platforms"
      title={t('settings.platformsTitle')}
      subtitle={t('settings.platformsSubtitle')}
    >
      <div className="mx-auto flex w-full max-w-[920px] flex-col gap-3">
        {restartPhase !== 'idle' && (
          <div
            role="status"
            aria-live="polite"
            className="sticky top-2 z-10 flex items-center gap-3 rounded-xl border border-cyan/35 bg-cyan/[0.08] px-4 py-3 shadow-[0_8px_24px_-8px_rgba(0,212,255,0.35)]"
          >
            {restartPhase === 'saving' ? (
              <Loader2 size={16} className="shrink-0 animate-spin text-cyan" />
            ) : (
              <RotateCw size={16} className="shrink-0 animate-spin text-cyan" strokeWidth={2.25} />
            )}
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-semibold text-foreground">
                {restartPhase === 'saving' ? t('platform.applyingConfig') : t('platform.restartingService')}
              </div>
              <div className="mt-0.5 text-[11px] text-muted">
                {restartPhase === 'saving' ? t('common.saving') : t('dashboard.restarting')}
              </div>
            </div>
          </div>
        )}
        {/* Enabled platforms card */}
        <CollapseCard
          expanded={expanded === 'enabled'}
          onToggle={() => toggle('enabled')}
          header={
            <div className="flex min-w-0 flex-1 items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-[14px] font-semibold text-foreground">{t('platform.enabledPlatforms')}</div>
                <div className="mt-1 flex flex-wrap items-center gap-1.5">
                  {enabledPlatforms.map((id) => {
                    const descriptor = platformCatalog.find((p) => p.id === id);
                    const tile = PLATFORM_TILE_STYLES[id] || { bg: 'bg-foreground/[0.04]', border: 'border-foreground/[0.10]' };
                    return (
                      <span
                        key={id}
                        className={clsx(
                          'inline-flex items-center gap-1.5 rounded-md border px-2 py-0.5 text-[11px] font-medium text-foreground',
                          tile.bg,
                          tile.border
                        )}
                      >
                        <PlatformIcon platform={id as any} size={12} />
                        {t(descriptor?.title_key || `platform.${id}.title`)}
                        {id === primary && (
                          <span className="rounded bg-mint/20 px-1 text-[9px] font-bold uppercase tracking-wide text-mint">
                            {t('platform.primary')}
                          </span>
                        )}
                      </span>
                    );
                  })}
                </div>
              </div>
            </div>
          }
        >
          <div className="space-y-4 px-5 py-4">
            <p className="text-[12px] leading-relaxed text-muted">{t('platform.subtitle')}</p>

            <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 lg:grid-cols-5">
              {platformCatalog.map((platform) => {
                const id = platform.id;
                const active = draftEnabled.includes(id);
                const tile = PLATFORM_TILE_STYLES[id] || { bg: 'bg-foreground/[0.04]', border: 'border-foreground/[0.10]' };
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => toggleDraftPlatform(id)}
                    className={clsx(
                      'flex flex-col items-center gap-2 rounded-xl px-3 py-3.5 transition-colors',
                      active
                        ? 'border-2 border-mint bg-mint/[0.16]'
                        : 'border border-foreground/[0.08] bg-background hover:border-foreground/[0.16] hover:bg-foreground/[0.02]'
                    )}
                  >
                    <span
                      className={clsx(
                        'inline-flex size-9 items-center justify-center rounded-[10px] border',
                        tile.bg,
                        tile.border
                      )}
                    >
                      <PlatformIcon platform={id as any} size={18} />
                    </span>
                    <span
                      className={clsx(
                        'text-[12px] leading-tight transition-colors',
                        active ? 'font-bold text-foreground' : 'font-medium text-muted'
                      )}
                    >
                      {t(platform.title_key || `platform.${id}.title`)}
                    </span>
                  </button>
                );
              })}
            </div>

            <div className="flex items-center justify-end gap-2 border-t border-border pt-3">
              <Button
                type="button"
                variant="secondary"
                size="xs"
                onClick={closeAll}
                disabled={savingEnabled}
              >
                {t('common.cancel')}
              </Button>
              <Button
                type="button"
                variant="brand"
                size="xs"
                onClick={() => void applyEnabled()}
                disabled={!draftEnabled.length || savingEnabled}
              >
                {savingEnabled ? <RefreshCw size={12} className="animate-spin" /> : <Check size={12} />}
                {t('platform.apply')}
              </Button>
            </div>
          </div>
        </CollapseCard>

        {/* One card per enabled platform */}
        {enabledPlatforms.map((id) => {
          const descriptor = platformCatalog.find((p) => p.id === id);
          const label = t(descriptor?.title_key || `platform.${id}.title`);
          const description = t(descriptor?.description_key || `platform.${id}.desc`);
          const tile = PLATFORM_TILE_STYLES[id] || { bg: 'bg-foreground/[0.04]', border: 'border-foreground/[0.10]' };
          const configured = platformHasCredentials(config, id);
          return (
            <CollapseCard
              key={id}
              expanded={expanded === id}
              onToggle={() => toggle(id)}
              header={
                <div className="flex min-w-0 flex-1 items-center gap-3">
                  <span
                    className={clsx(
                      'inline-flex size-9 shrink-0 items-center justify-center rounded-[10px] border',
                      tile.bg,
                      tile.border
                    )}
                  >
                    <PlatformIcon platform={id as any} size={18} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-[14px] font-semibold text-foreground">{label}</span>
                      {configured ? (
                        <span className="inline-flex items-center gap-1 rounded border border-mint/30 bg-mint/[0.08] px-1.5 py-0.5 text-[10px] font-medium text-mint">
                          <Check size={10} />
                          {t('platform.validationSuccess')}
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded border border-gold/30 bg-gold/10 px-1.5 py-0.5 text-[10px] font-medium text-gold">
                          {t('platform.stepAddBotToken')}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 truncate text-[11px] text-muted">{description}</p>
                  </div>
                </div>
              }
            >
              <div className="px-5 py-4">
                <PlatformConfigEmbed
                  platform={id}
                  config={config}
                  onApply={handleApplyPlatform}
                  onCancel={closeAll}
                />
              </div>
            </CollapseCard>
          );
        })}
      </div>
    </SettingsPageShell>
  );
};

const CollapseCard: React.FC<{
  expanded: boolean;
  onToggle: () => void;
  header: React.ReactNode;
  children: React.ReactNode;
}> = ({ expanded, onToggle, header, children }) => {
  const { t } = useTranslation();
  return (
    <section
      className={clsx(
        'overflow-hidden rounded-xl border bg-surface-2 transition-colors',
        expanded ? 'border-mint/35 shadow-[0_8px_32px_-8px_rgba(91,255,160,0.078)]' : 'border-border'
      )}
    >
      <div className="flex items-stretch gap-3 px-5 py-4">
        {header}
        <button
          type="button"
          onClick={onToggle}
          className={clsx(
            'inline-flex shrink-0 items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12px] font-medium transition',
            expanded
              ? 'border-mint/35 bg-mint/[0.08] text-mint'
              : 'border-border bg-foreground/[0.04] text-foreground hover:border-border-strong'
          )}
        >
          {expanded ? (
            <>
              <ChevronUp size={14} />
              {t('common.close')}
            </>
          ) : (
            <>
              <Pencil size={12} />
              {t('common.edit')}
            </>
          )}
        </button>
      </div>
      {expanded && <div className="border-t border-border bg-background/40">{children}</div>}
    </section>
  );
};

const PlatformConfigEmbed: React.FC<{
  platform: string;
  config: any;
  onApply: (data: any) => Promise<void>;
  onCancel: () => void;
}> = ({ platform, config, onApply, onCancel }) => {
  const noopNext = () => {};
  if (platform === 'slack') {
    return <SlackConfig data={config} onNext={noopNext} embedded onApply={onApply} onCancel={onCancel} />;
  }
  if (platform === 'discord') {
    return <DiscordConfig data={config} onNext={noopNext} embedded onApply={onApply} onCancel={onCancel} />;
  }
  if (platform === 'telegram') {
    return <TelegramConfig data={config} onNext={noopNext} embedded onApply={onApply} onCancel={onCancel} />;
  }
  if (platform === 'lark') {
    return <LarkConfig data={config} onNext={noopNext} embedded onApply={onApply} onCancel={onCancel} />;
  }
  if (platform === 'wechat') {
    return <WeChatConfig data={config} onNext={noopNext} embedded onApply={onApply} onCancel={onCancel} />;
  }
  return null;
};

