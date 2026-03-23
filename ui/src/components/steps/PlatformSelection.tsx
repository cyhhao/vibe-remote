import React, { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { ALL_PLATFORMS, getEnabledPlatforms, getPrimaryPlatform } from '../../lib/platforms';

interface PlatformSelectionProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

export const PlatformSelection: React.FC<PlatformSelectionProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const initialPlatforms = useMemo(() => getEnabledPlatforms(data), [data]);
  const [selected, setSelected] = useState<string[]>(initialPlatforms);
  const [primary, setPrimary] = useState<string>(getPrimaryPlatform(data));

  const togglePlatform = (platform: string) => {
    setSelected((current) => {
      if (current.includes(platform)) {
        const next = current.filter((item) => item !== platform);
        if (!next.length) {
          return current;
        }
        if (primary === platform) {
          setPrimary(next[0]);
        }
        return next;
      }
      const next = [...current, platform];
      if (!current.length) {
        setPrimary(platform);
      }
      return next;
    });
  };

  const handleContinue = () => {
    const normalized = selected.length ? selected : ['slack'];
    const resolvedPrimary = normalized.includes(primary) ? primary : normalized[0];
    onNext({
      platform: resolvedPrimary,
      platforms: {
        enabled: normalized,
        primary: resolvedPrimary,
      },
    });
  };

  return (
    <div className="flex flex-col h-full max-w-3xl mx-auto">
      <div className="mb-6">
        <h2 className="text-3xl font-display font-bold text-text">{t('platform.title')}</h2>
        <p className="text-muted mt-1">{t('platform.subtitle')}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 flex-1">
        {ALL_PLATFORMS.map((option) => {
          const active = selected.includes(option);
          return (
            <button
              type="button"
              key={option}
              onClick={() => togglePlatform(option)}
              className={clsx(
                'text-left p-5 rounded-xl border transition-colors shadow-sm relative',
                active ? 'border-accent bg-accent/5' : 'border-border bg-panel hover:border-accent/60'
              )}
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-lg font-semibold text-text">{t(`platform.${option}.title`)}</div>
                  <div className="text-sm text-muted mt-2">{t(`platform.${option}.desc`)}</div>
                </div>
                <div
                  className={clsx(
                    'mt-1 h-5 w-5 rounded-full border flex items-center justify-center text-xs font-bold',
                    active ? 'border-accent bg-accent text-white' : 'border-border bg-bg'
                  )}
                >
                  {active ? selected.indexOf(option) + 1 : ''}
                </div>
              </div>
              {active && primary === option && (
                <div className="mt-4 inline-flex items-center rounded-full bg-success/10 text-success px-2 py-1 text-xs font-medium">
                  {t('platform.primary')}
                </div>
              )}
            </button>
          );
        })}
      </div>

      <div className="mt-6 bg-panel border border-border rounded-xl p-4">
        <div className="text-sm font-medium text-text">{t('platform.primaryTitle')}</div>
        <p className="text-xs text-muted mt-1">{t('platform.primaryDesc')}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          {selected.map((platform) => (
            <button
              key={platform}
              type="button"
              onClick={() => setPrimary(platform)}
              className={clsx(
                'px-3 py-1.5 rounded-full text-sm border transition-colors',
                primary === platform ? 'bg-accent text-white border-accent' : 'bg-bg text-text border-border hover:border-accent/60'
              )}
            >
              {t(`platform.${platform}.title`)}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-auto flex justify-between pt-6 border-t border-border">
        <button onClick={onBack} className="px-6 py-2 text-muted hover:text-text font-medium transition-colors">
          {t('common.back')}
        </button>
        <button
          onClick={handleContinue}
          disabled={!selected.length}
          className="px-8 py-3 rounded-lg font-medium transition-colors shadow-sm bg-accent hover:bg-accent/90 text-white disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {t('common.continue')}
        </button>
      </div>
    </div>
  );
};
