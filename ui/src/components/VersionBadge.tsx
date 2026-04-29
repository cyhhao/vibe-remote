import React from 'react';
import { useTranslation } from 'react-i18next';
import { useApi, type VersionInfo, type UpgradeResult } from '../context/ApiContext';
import { Download, X, RefreshCw, Check, AlertCircle } from 'lucide-react';
import clsx from 'clsx';

export const VersionBadge: React.FC<{ openUpward?: boolean }> = ({ openUpward = false }) => {
  const { t } = useTranslation();
  const api = useApi();
  const [versionInfo, setVersionInfo] = React.useState<VersionInfo | null>(null);
  const [isPopupOpen, setIsPopupOpen] = React.useState(false);
  const [checking, setChecking] = React.useState(false);
  const [upgrading, setUpgrading] = React.useState(false);
  const [restarting, setRestarting] = React.useState(false);
  const [upgradeResult, setUpgradeResult] = React.useState<UpgradeResult | null>(null);
  const [autoUpdate, setAutoUpdate] = React.useState<boolean | null>(null);
  const [savingAutoUpdate, setSavingAutoUpdate] = React.useState(false);
  const popupRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    checkVersion();
    loadAutoUpdateSetting();
  }, []);

  React.useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (popupRef.current && !popupRef.current.contains(event.target as Node)) {
        setIsPopupOpen(false);
      }
    };
    if (isPopupOpen) {
      document.addEventListener('mousedown', handleClickOutside);
    }
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [isPopupOpen]);

  const loadAutoUpdateSetting = async () => {
    try {
      const config = await api.getConfig();
      setAutoUpdate(config.update?.auto_update ?? true);
    } catch (e) {
      console.error('Failed to load config:', e);
    }
  };

  const handleAutoUpdateToggle = async (enabled: boolean) => {
    setSavingAutoUpdate(true);
    try {
      await api.saveConfig({ update: { auto_update: enabled } });
      setAutoUpdate(enabled);
    } catch (e) {
      console.error('Failed to save auto-update setting:', e);
    } finally {
      setSavingAutoUpdate(false);
    }
  };

  const checkVersion = async () => {
    setChecking(true);
    try {
      const info = await api.getVersion();
      setVersionInfo(info);
    } catch (e) {
      console.error('Failed to check version:', e);
    } finally {
      setChecking(false);
    }
  };

  const handleUpgrade = async () => {
    setUpgrading(true);
    setUpgradeResult(null);
    try {
      const result = await api.doUpgrade();
      setUpgradeResult(result);
      if (result.ok) {
        if (result.restarting) {
          setRestarting(true);
          setTimeout(() => {
            window.location.reload();
          }, 4000);
        } else {
          setTimeout(() => checkVersion(), 1000);
        }
      }
    } catch (e) {
      setUpgradeResult({ ok: false, message: String(e), output: null, restarting: false });
    } finally {
      setUpgrading(false);
    }
  };

  const hasUpdate = versionInfo?.has_update === true;
  const currentVersion = versionInfo?.current || '...';

  return (
    <div className="relative" ref={popupRef}>
      {/* Version Badge trigger */}
      <button
        onClick={() => setIsPopupOpen(!isPopupOpen)}
        className={clsx(
          'relative cursor-pointer rounded-md border px-2 py-0.5 text-xs font-medium transition-colors',
          hasUpdate
            ? 'border-gold/30 bg-gold/15 text-gold hover:bg-gold/20'
            : 'border-border bg-surface-2/60 text-muted hover:bg-surface-2'
        )}
      >
        v{currentVersion}
        {hasUpdate && (
          <span className="absolute -top-1 -right-1 size-2.5 rounded-full border-2 border-background bg-gold animate-pulse" />
        )}
      </button>

      {/* Popup */}
      {isPopupOpen && (
        <div
          className={clsx(
            'z-50 rounded-lg border border-border bg-popover text-popover-foreground shadow-xl',
            // Mobile: full-width fixed below sticky header, with scroll
            'fixed inset-x-3 top-[4.5rem] max-h-[calc(100dvh-5.5rem)] overflow-auto',
            // Desktop: anchor to trigger, fixed width
            'md:absolute md:inset-x-auto md:max-h-none md:w-72 md:overflow-visible',
            openUpward
              ? 'md:bottom-full md:left-0 md:top-auto md:mb-2'
              : 'md:left-0 md:top-full md:mt-2'
          )}
        >
          {/* Header */}
          <div className="flex items-center justify-between border-b border-border px-4 py-3">
            <span className="text-sm font-medium text-foreground">{t('dashboard.versionAndUpdate')}</span>
            <div className="flex items-center gap-1">
              <button
                onClick={checkVersion}
                disabled={checking || restarting}
                className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-foreground disabled:opacity-50"
                aria-label={checking ? t('dashboard.checking') : t('dashboard.checkUpdate')}
                title={checking ? t('dashboard.checking') : t('dashboard.checkUpdate')}
              >
                <RefreshCw size={14} className={checking ? 'animate-spin' : ''} />
              </button>
              <button
                onClick={() => setIsPopupOpen(false)}
                className="rounded p-1.5 text-muted hover:bg-surface-2 hover:text-foreground"
              >
                <X size={14} />
              </button>
            </div>
          </div>

          {/* Content */}
          <div className="space-y-3 p-4">
            {/* Current Version */}
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted">{t('dashboard.currentVersion')}</span>
              <span className="font-mono font-medium text-foreground">{currentVersion}</span>
            </div>

            {/* Latest Version */}
            {versionInfo?.latest && (
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">{t('dashboard.latestVersion')}</span>
                <span className="font-mono font-medium text-foreground">{versionInfo.latest}</span>
              </div>
            )}

            {/* Update Status */}
            {hasUpdate ? (
              <div className="flex items-center gap-2 rounded-md border border-gold/30 bg-gold/10 px-3 py-2 text-sm text-gold">
                <AlertCircle size={16} className="shrink-0" />
                <span>
                  {t('dashboard.updateHint', {
                    from: currentVersion,
                    to: versionInfo?.latest,
                  })}
                </span>
              </div>
            ) : versionInfo && !versionInfo.error ? (
              <div className="flex items-center gap-2 rounded-md border border-mint/25 bg-mint/10 px-3 py-2 text-sm text-mint">
                <Check size={16} className="shrink-0" />
                <span>{t('dashboard.upToDate')}</span>
              </div>
            ) : versionInfo?.error ? (
              <div className="flex items-center gap-2 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                <AlertCircle size={16} className="shrink-0" />
                <span>{t('dashboard.checkFailed')}</span>
              </div>
            ) : null}

            {/* Upgrade Result */}
            {upgradeResult && (
              <div
                className={clsx(
                  'flex items-center gap-2 rounded-md border px-3 py-2 text-sm',
                  upgradeResult.ok
                    ? 'border-mint/25 bg-mint/10 text-mint'
                    : 'border-destructive/30 bg-destructive/10 text-destructive'
                )}
              >
                {upgradeResult.ok ? <Check size={16} className="shrink-0" /> : <AlertCircle size={16} className="shrink-0" />}
                <span>
                  {upgradeResult.ok ? t('dashboard.upgradeSuccess') : t('dashboard.upgradeFailed')}
                </span>
              </div>
            )}

            {/* Restarting Status */}
            {restarting && (
              <div className="flex items-center gap-2 rounded-md border border-cyan/30 bg-cyan/10 px-3 py-2 text-sm text-cyan">
                <RefreshCw size={16} className="shrink-0 animate-spin" />
                <span>{t('dashboard.restarting')}</span>
              </div>
            )}

            {/* Auto Update Toggle */}
            {autoUpdate !== null && (
              <div className="flex items-center justify-between gap-3 border-t border-border pt-3">
                <div className="min-w-0">
                  <div className="text-sm text-foreground">{t('dashboard.autoUpdate')}</div>
                  <div className="text-xs text-muted">{t('dashboard.autoUpdateHint')}</div>
                </div>
                <label className="relative inline-flex cursor-pointer items-center">
                  <input
                    type="checkbox"
                    checked={autoUpdate}
                    onChange={(e) => handleAutoUpdateToggle(e.target.checked)}
                    disabled={savingAutoUpdate}
                    className="peer sr-only"
                  />
                  <div className="peer h-5 w-9 rounded-full bg-surface-3 transition-colors after:absolute after:left-[2px] after:top-0.5 after:size-4 after:rounded-full after:bg-foreground after:transition-all after:content-[''] peer-checked:bg-mint peer-checked:after:translate-x-4 peer-checked:after:bg-primary-foreground peer-focus:ring-2 peer-focus:ring-ring" />
                </label>
              </div>
            )}
          </div>

          {/* Actions */}
          {hasUpdate && !restarting && (
            <div className="flex justify-end border-t border-border px-4 py-3">
              <button
                onClick={handleUpgrade}
                disabled={upgrading}
                className="inline-flex items-center gap-1.5 rounded-md bg-mint px-3 py-2 text-sm font-semibold text-primary-foreground transition-colors hover:bg-mint/90 disabled:opacity-50"
              >
                <Download size={14} className={upgrading ? 'animate-bounce' : ''} />
                {upgrading ? t('dashboard.upgrading') : t('dashboard.upgradeNow')}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
};
