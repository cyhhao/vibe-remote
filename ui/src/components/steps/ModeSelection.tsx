import React from 'react';
import clsx from 'clsx';
import { Cloud, Server } from 'lucide-react';

interface ModeSelectionProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

const modes = [
  {
    id: 'saas',
    title: 'SaaS Mode (Recommended)',
    description: 'Fastest setup: OAuth install + cloud relay + local execution.',
    icon: Cloud,
  },
  {
    id: 'self_host',
    title: 'Self-host Mode',
    description: 'Use your own Slack app + Socket Mode tokens.',
    icon: Server,
  },
];

export const ModeSelection: React.FC<ModeSelectionProps> = ({ data, onNext, onBack }) => {
  const [selected, setSelected] = React.useState(data.mode || 'saas');

  return (
    <div className="flex flex-col h-full max-w-2xl mx-auto">
      <h2 className="text-3xl font-display font-bold mb-6 text-text">Choose Mode</h2>
      
      <div className="grid gap-4 mb-8">
        {modes.map((m) => {
          const isSelected = selected === m.id;
          const Icon = m.icon;
          return (
            <button
              key={m.id}
              onClick={() => setSelected(m.id)}
              className={clsx(
                'flex items-start gap-4 p-6 rounded-xl border-2 text-left transition-all',
                isSelected
                  ? 'border-accent bg-accent/5 shadow-md'
                  : 'border-border hover:border-accent/50 bg-panel'
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
            Back
         </button>
        <button
          onClick={() => onNext({ mode: selected })}
          className="px-8 py-3 bg-accent hover:bg-accent/90 text-white rounded-lg font-medium transition-colors shadow-sm"
        >
          Continue
        </button>
      </div>
    </div>
  );
};
