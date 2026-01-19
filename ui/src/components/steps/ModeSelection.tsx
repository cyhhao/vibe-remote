import React from 'react';
import clsx from 'clsx';
import { Cloud, Server } from 'lucide-react';
import { useTranslation } from 'react-i18next';

interface ModeSelectionProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

export const ModeSelection: React.FC<ModeSelectionProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const [selected, setSelected] = React.useState(data.mode || 'self_host');

  const modes = [
    {
      id: 'saas',
      title: t('modeSelection.saasMode'),
      description: t('modeSelection.saasDescription'),
      icon: Cloud,
      disabled: true,
    },
    {
      id: 'self_host',
      title: t('modeSelection.selfHostMode'),
      description: t('modeSelection.selfHostDescription'),
      icon: Server,
    },
  ];

  return (
    <div className="flex flex-col h-full max-w-2xl mx-auto">
      <h2 className="text-3xl font-display font-bold mb-6 text-text">{t('modeSelection.title')}</h2>
      
      <div className="grid gap-4 mb-8">
        {modes.map((m) => {
          const isSelected = selected === m.id;
          const Icon = m.icon;
          const isDisabled = m.disabled;
          return (
            <button
              key={m.id}
              onClick={() => {
                if (!isDisabled) {
                  setSelected(m.id);
                }
              }}
              disabled={isDisabled}
              className={clsx(
                'flex items-start gap-4 p-6 rounded-xl border-2 text-left transition-all',
                isSelected
                  ? 'border-accent bg-accent/5 shadow-md'
                  : 'border-border bg-panel',
                isDisabled ? 'opacity-50 cursor-not-allowed' : 'hover:border-accent/50'
              )}
            >
              <div className={clsx("p-3 rounded-lg", isSelected ? "bg-accent/10 text-accent" : "bg-neutral-100 text-muted")}>
                  <Icon size={24} />
              </div>
              <div>
                <h3 className={clsx("font-semibold text-lg font-display", isSelected ? "text-accent" : "text-text")}>{m.title}</h3>
                <p className="text-muted mt-1">{m.description}</p>
              </div>
            </button>
          );
        })}
      </div>

      <div className="mt-auto flex justify-between">
         <button onClick={onBack} className="px-6 py-2 text-muted hover:text-text font-medium">
            {t('common.back')}
         </button>
        <button
          onClick={() => onNext({ mode: selected })}
          className="px-8 py-3 bg-accent hover:bg-accent/90 text-white rounded-lg font-medium transition-colors shadow-sm"
        >
          {t('common.continue')}
        </button>
      </div>
    </div>
  );
};
