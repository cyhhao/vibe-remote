import { useTranslation } from 'react-i18next';
import { Bot, Sparkles } from 'lucide-react';
import clsx from 'clsx';

import type { VibeAgentBrief } from '../../context/ApiContext';
import { Button } from '../ui/button';

interface AgentPickerProps {
  agents: VibeAgentBrief[];
  defaultAgentName: string | null;
  /** Selected agent, or null for the server default (agents.default_backend). */
  value: VibeAgentBrief | null;
  onChange: (id: string | null) => void;
  disabled?: boolean;
}

// Shared agent (backend) picker for the create surfaces. A horizontal scroll
// row: a Default chip (null → the server's default backend, labelled with the
// resolved agent when known) followed by every enabled Vibe Agent, each tagged
// with its backend. Hidden entirely when no agents are loaded (the create then
// just uses the default).
export const AgentPicker: React.FC<AgentPickerProps> = ({ agents, defaultAgentName, value, onChange, disabled }) => {
  const { t } = useTranslation();
  if (agents.length === 0) return null;
  const chipClass = (active: boolean) =>
    clsx(
      'h-auto shrink-0 gap-1.5 rounded-full px-3 py-1.5 text-[12.5px] font-medium',
      active ? 'border-cyan/40 bg-cyan/[0.08] text-cyan hover:bg-cyan/[0.08] hover:text-cyan' : 'text-foreground',
    );
  return (
    <div className="flex flex-col gap-2">
      <div className="font-mono text-[11px] font-bold uppercase tracking-[0.08em] text-muted">
        {t('newSession.agent')}
      </div>
      <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => onChange(null)}
          disabled={disabled}
          className={chipClass(value === null)}
        >
          <Sparkles className="size-3.5" />
          <span className="max-w-[160px] truncate">
            {defaultAgentName ? t('newSession.defaultAgentNamed', { name: defaultAgentName }) : t('newSession.defaultAgent')}
          </span>
        </Button>
        {agents.map((agent) => (
          <Button
            key={agent.id}
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onChange(agent.id)}
            disabled={disabled}
            className={chipClass(value?.id === agent.id)}
          >
            <Bot className="size-3.5" />
            <span className="max-w-[140px] truncate">{agent.name}</span>
            <span className="rounded bg-foreground/[0.06] px-1 font-mono text-[9px] uppercase text-muted">{agent.backend}</span>
          </Button>
        ))}
      </div>
    </div>
  );
};
