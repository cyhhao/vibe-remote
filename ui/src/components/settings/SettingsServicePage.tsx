import React, { useEffect, useState } from 'react';
import { FileText, Globe2, Play, RotateCw, Server, Square } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import { useStatus } from '@/context/StatusContext';
import { useApi } from '@/context/ApiContext';
import { apiFetch } from '@/lib/apiFetch';
import { RemoteAccess } from '@/components/RemoteAccess';
import { SettingsPageShell } from './SettingsPageShell';
import { CompactField, SettingsPanel, SettingsRow } from './SettingsPrimitives';

// Mirrors design.pen mHUcm (VR/CM/Service): two cards.
// svcSec1: header [16, 20] + value rows [12, 20] with bottom borders.
// svcSec2: single row card with title + value pill (read-only mono).
export const SettingsServicePage: React.FC = () => {
  const { t } = useTranslation();
  const { status, control } = useStatus();
  const api = useApi();
  const [config, setConfig] = useState<any>(null);
  const [loading, setLoading] = useState(false);
  const [uiSaving, setUiSaving] = useState(false);
  const [uiMessage, setUiMessage] = useState<string | null>(null);

  useEffect(() => {
    api.getConfig().then(setConfig).catch(() => {});
  }, [api]);

  const isRunning = status.state === 'running';

  const handleAction = async (action: string) => {
    setLoading(true);
    try {
      await control(action);
    } finally {
      setLoading(false);
    }
  };

  const handleUiSaveRestart = async () => {
    if (!config) return;
    setUiSaving(true);
    setUiMessage(null);
    try {
      const uiPayload = {
        setup_host: config.ui?.setup_host || '127.0.0.1',
        setup_port: config.ui?.setup_port || 5123,
      };
      await api.saveConfig({ ui: { ...(config.ui || {}), ...uiPayload } });
      await apiFetch('/ui/reload', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ host: uiPayload.setup_host, port: uiPayload.setup_port }),
      });
      setUiMessage(t('dashboard.uiRestartMessage'));
    } catch {
      setUiMessage(t('common.saveFailed'));
    } finally {
      setUiSaving(false);
    }
  };

  return (
    <SettingsPageShell
      activeTab="service"
      title={t('settings.serviceTitle')}
      subtitle={t('settings.serviceSubtitle')}
    >
      <SettingsPanel
        title={
          <span className="inline-flex items-center gap-2">
            <Server className="size-3.5 text-mint" />
            {t('settings.serviceRuntimeTitle')}
          </span>
        }
        description={t('settings.serviceRuntimeSubtitle')}
        actions={
          <span
            className={clsx(
              'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-[0.14em]',
              isRunning
                ? 'border-mint/30 bg-mint/[0.08] text-mint'
                : 'border-border bg-white/[0.04] text-muted'
            )}
          >
            <span
              className={clsx(
                'size-1.5 rounded-full',
                isRunning ? 'bg-mint shadow-[0_0_8px_rgba(91,255,160,0.9)]' : 'bg-muted'
              )}
            />
            {isRunning ? t('common.running') : t('common.stopped')}
          </span>
        }
      >
        <SettingsRow
          title={t('settings.statusNow')}
          description={`PID ${status.service_pid || status.pid || '-'}`}
          control={
            <div className="flex flex-wrap gap-2">
              {!isRunning && (
                <button
                  type="button"
                  onClick={() => void handleAction('start')}
                  disabled={loading}
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-mint px-3 text-[12px] font-bold text-[#080812] shadow-[0_0_18px_-4px_rgba(91,255,160,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Play className="size-3.5" strokeWidth={2.5} />
                  {t('common.start')}
                </button>
              )}
              {isRunning && (
                <button
                  type="button"
                  onClick={() => void handleAction('stop')}
                  disabled={loading}
                  className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-3 text-[12px] font-medium text-foreground transition hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Square className="size-3.5" strokeWidth={2.5} />
                  {t('common.stop')}
                </button>
              )}
              <button
                type="button"
                onClick={() => void handleAction('restart')}
                disabled={loading}
                className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-3 text-[12px] font-medium text-foreground transition hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-50"
              >
                <RotateCw className="size-3.5" strokeWidth={2.5} />
                {t('common.restart')}
              </button>
            </div>
          }
        />
        <SettingsRow
          title={
            <span className="inline-flex items-center gap-2">
              <Globe2 className="size-3.5 text-cyan" />
              {t('settings.consoleServerTitle')}
            </span>
          }
          description={uiMessage || t('settings.consoleServerHint')}
          control={
            <div className="grid grid-cols-[120px_96px_auto] items-center gap-2">
              <CompactField
                aria-label={t('dashboard.host')}
                value={config?.ui?.setup_host || '127.0.0.1'}
                onChange={(event) => {
                  const host = event.target.value || '127.0.0.1';
                  setUiMessage(null);
                  setConfig((prev: any) => ({
                    ...(prev || {}),
                    ui: { ...((prev && prev.ui) || {}), setup_host: host },
                  }));
                }}
              />
              <CompactField
                aria-label={t('dashboard.port')}
                type="number"
                min={1024}
                max={65535}
                value={config?.ui?.setup_port || 5123}
                onChange={(event) => {
                  const port = Number(event.target.value) || 5123;
                  setUiMessage(null);
                  setConfig((prev: any) => ({
                    ...(prev || {}),
                    ui: { ...((prev && prev.ui) || {}), setup_port: port },
                  }));
                }}
              />
              <button
                type="button"
                onClick={() => void handleUiSaveRestart()}
                disabled={uiSaving}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-3 text-[12px] font-medium text-foreground transition hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-50"
              >
                <RotateCw className={clsx('size-3.5', uiSaving && 'animate-spin')} strokeWidth={2.5} />
                {uiSaving ? t('common.saving') : t('common.saveAndRestart')}
              </button>
            </div>
          }
        />
      </SettingsPanel>

      {/* Mirrors design.pen CuVKM (svcSec2): single-row read-only value pill */}
      <SettingsPanel>
        <div className="flex flex-col gap-3 px-5 py-4 md:flex-row md:items-center md:justify-between">
          <div className="text-[13px] font-medium text-foreground">{t('settings.logFileLabel')}</div>
          <div className="inline-flex items-center gap-2 rounded-lg border border-border bg-white/[0.04] px-3 py-2">
            <FileText className="size-3.5 text-muted" />
            <span className="font-mono text-[11px] text-foreground">~/.vibe_remote/logs/vibe_remote.log</span>
          </div>
        </div>
      </SettingsPanel>

      <RemoteAccess embedded />
    </SettingsPageShell>
  );
};
