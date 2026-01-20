import React from 'react';
import { useTranslation } from 'react-i18next';
import { useApi, type VersionInfo, type UpgradeResult } from '../context/ApiContext';
import { Download, X, RefreshCw, Check, AlertCircle } from 'lucide-react';
import clsx from 'clsx';

export const VersionBadge: React.FC = () => {
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

  // Check version and load config on mount
  React.useEffect(() => {
    checkVersion();
    loadAutoUpdateSetting();
  }, []);

  // Close popup when clicking outside
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
      const config = await api.getConfig();
      const updatedConfig = {
        ...config,
        update: {
          ...config.update,
          auto_update: enabled,
        },
      };
      await api.saveConfig(updatedConfig);
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
          // Show restarting state and reload page after delay
          setRestarting(true);
          setTimeout(() => {
            window.location.reload();
          }, 4000);
        } else {
          // Refresh version info after upgrade
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
      {/* Version Badge */}
      <button
        onClick={() => setIsPopupOpen(!isPopupOpen)}
        className={clsx(
          'relative px-2 py-0.5 text-xs font-medium rounded-md transition-colors cursor-pointer',
          hasUpdate
            ? 'bg-amber-100 text-amber-800 hover:bg-amber-200'
            : 'bg-neutral-100 text-neutral-600 hover:bg-neutral-200'
        )}
      >
        v{currentVersion}
        {/* Update indicator dot */}
        {hasUpdate && (
          <span className="absolute -top-1 -right-1 w-2.5 h-2.5 bg-amber-500 rounded-full border-2 border-white animate-pulse" />
        )}
      </button>

      {/* Popup */}
      {isPopupOpen && (
        <div className="absolute top-full left-0 mt-2 w-72 bg-white rounded-lg shadow-lg border border-border z-50">
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <span className="font-medium text-sm">{t('dashboard.versionAndUpdate')}</span>
            <button
              onClick={() => setIsPopupOpen(false)}
              className="text-muted hover:text-text p-1 rounded"
            >
              <X size={14} />
            </button>
          </div>

          {/* Content */}
          <div className="p-4 space-y-3">
            {/* Current Version */}
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted">{t('dashboard.currentVersion')}</span>
              <span className="font-mono font-medium">{currentVersion}</span>
            </div>

            {/* Latest Version */}
            {versionInfo?.latest && (
              <div className="flex items-center justify-between text-sm">
                <span className="text-muted">{t('dashboard.latestVersion')}</span>
                <span className="font-mono font-medium">{versionInfo.latest}</span>
              </div>
            )}

            {/* Update Status */}
            {hasUpdate ? (
              <div className="flex items-center gap-2 px-3 py-2 bg-amber-50 text-amber-800 rounded-md text-sm">
                <AlertCircle size={16} />
                <span>
                  {t('dashboard.updateHint', {
                    from: currentVersion,
                    to: versionInfo?.latest
                  })}
                </span>
              </div>
            ) : versionInfo && !versionInfo.error ? (
              <div className="flex items-center gap-2 px-3 py-2 bg-green-50 text-green-700 rounded-md text-sm">
                <Check size={16} />
                <span>{t('dashboard.upToDate')}</span>
              </div>
            ) : versionInfo?.error ? (
              <div className="flex items-center gap-2 px-3 py-2 bg-red-50 text-red-700 rounded-md text-sm">
                <AlertCircle size={16} />
                <span>{t('dashboard.checkFailed')}</span>
              </div>
            ) : null}

            {/* Upgrade Result */}
            {upgradeResult && (
              <div
                className={clsx(
                  'flex items-center gap-2 px-3 py-2 rounded-md text-sm',
                  upgradeResult.ok
                    ? 'bg-green-50 text-green-700'
                    : 'bg-red-50 text-red-700'
                )}
              >
                {upgradeResult.ok ? <Check size={16} /> : <AlertCircle size={16} />}
                <span>
                  {upgradeResult.ok
                    ? t('dashboard.upgradeSuccess')
                    : t('dashboard.upgradeFailed')}
                </span>
              </div>
            )}

            {/* Restarting Status */}
            {restarting && (
              <div className="flex items-center gap-2 px-3 py-2 bg-blue-50 text-blue-700 rounded-md text-sm">
                <RefreshCw size={16} className="animate-spin" />
                <span>{t('dashboard.restarting')}</span>
              </div>
            )}

            {/* Auto Update Toggle */}
            {autoUpdate !== null && (
              <div className="flex items-center justify-between pt-2 border-t border-border">
                <div>
                  <span className="text-sm text-text">{t('dashboard.autoUpdate')}</span>
                  <p className="text-xs text-muted">{t('dashboard.autoUpdateHint')}</p>
                </div>
                <label className="relative inline-flex items-center cursor-pointer">
                  <input
                    type="checkbox"
                    checked={autoUpdate}
                    onChange={(e) => handleAutoUpdateToggle(e.target.checked)}
                    disabled={savingAutoUpdate}
                    className="sr-only peer"
                  />
                  <div className="w-9 h-5 bg-neutral-200 rounded-full peer peer-checked:bg-green-500 peer-focus:ring-2 peer-focus:ring-green-200 after:content-[''] after:absolute after:top-0.5 after:left-[2px] after:bg-white after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:after:translate-x-4"></div>
                </label>
              </div>
            )}
          </div>

          {/* Actions */}
          <div className="px-4 py-3 border-t border-border flex gap-2">
            <button
              onClick={checkVersion}
              disabled={checking || restarting}
              className="flex-1 flex items-center justify-center gap-2 px-3 py-2 text-sm bg-neutral-100 hover:bg-neutral-200 rounded-md transition-colors disabled:opacity-50"
            >
              <RefreshCw size={14} className={checking ? 'animate-spin' : ''} />
              {checking ? t('dashboard.checking') : t('dashboard.checkUpdate')}
            </button>
            {hasUpdate && !restarting && (
              <button
                onClick={handleUpgrade}
                disabled={upgrading}
                className="flex-1 flex items-center justify-center gap-2 px-3 py-2 text-sm bg-accent text-white hover:bg-accent/90 rounded-md transition-colors disabled:opacity-50"
              >
                <Download size={14} className={upgrading ? 'animate-bounce' : ''} />
                {upgrading ? t('dashboard.upgrading') : t('dashboard.upgradeNow')}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
