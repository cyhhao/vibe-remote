import React from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

interface PlatformSelectionProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

export const PlatformSelection: React.FC<PlatformSelectionProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const platform = data.platform || 'slack';

  return (
    <div className="flex flex-col h-full max-w-2xl mx-auto">
      <div className="mb-6">
        <h2 className="text-3xl font-display font-bold text-text">{t('platform.title')}</h2>
        <p className="text-muted mt-1">{t('platform.subtitle')}</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 flex-1">
        {['slack', 'discord'].map((option) => (
          <button
            key={option}
            onClick={() => onNext({ platform: option })}
            className={clsx(
              'text-left p-5 rounded-xl border transition-colors shadow-sm',
              platform === option
                ? 'border-accent bg-accent/5'
                : 'border-border bg-panel hover:border-accent/60'
            )}
          >
            <div className="text-lg font-semibold text-text">{t(`platform.${option}.title`)}</div>
            <div className="text-sm text-muted mt-2">{t(`platform.${option}.desc`)}</div>
          </button>
        ))}
      </div>

      <div className="mt-auto flex justify-between pt-6 border-t border-border">
        <button onClick={onBack} className="px-6 py-2 text-muted hover:text-text font-medium transition-colors">
          {t('common.back')}
        </button>
        <button
          onClick={() => onNext({ platform })}
          className="px-8 py-3 rounded-lg font-medium transition-colors shadow-sm bg-accent hover:bg-accent/90 text-white"
        >
          {t('common.continue')}
        </button>
      </div>
    </div>
  );
};
