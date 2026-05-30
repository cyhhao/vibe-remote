import { WandSparkles, Globe, Ellipsis } from 'lucide-react';
import clsx from 'clsx';
import type { SkillBrief } from '../../../context/ApiContext';
import { backendsFromAgents } from '../../../lib/backendAccent';
import { BackendChip } from './BackendChip';

export interface SkillRowProps {
  skill: SkillBrief;
  selected?: boolean;
  /** Dim + tag rows that are inherited from global into a project view. */
  inherited?: boolean;
  onSelect?: () => void;
}

/** One installed-skill row: lead icon · name + desc + version · backend chips. */
export function SkillRow({ skill, selected, inherited, onSelect }: SkillRowProps) {
  const backends = backendsFromAgents(skill.agents);
  return (
    <button
      type="button"
      onClick={onSelect}
      className={clsx(
        'group flex w-full items-center gap-3 rounded-lg border px-[14px] py-3 text-left transition',
        selected
          ? 'border-mint/40 bg-mint-soft shadow-[0_0_18px_-10px_rgba(91,255,160,0.6)]'
          : 'border-border bg-surface hover:border-border-strong hover:bg-surface-2',
        inherited && 'opacity-[0.66]',
      )}
    >
      <span className="flex size-9 shrink-0 items-center justify-center rounded-[9px] border border-border bg-surface-3">
        <WandSparkles className={clsx('size-4', selected ? 'text-mint' : 'text-muted')} />
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex items-center gap-2">
          <span className="truncate text-[14px] font-semibold text-foreground">{skill.name}</span>
          {inherited ? (
            <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border-strong px-1.5 font-mono text-[9px] font-bold text-muted">
              <Globe className="size-2.5" />
              GLOBAL
            </span>
          ) : null}
        </span>
        {skill.description ? <span className="truncate text-[11.5px] text-muted">{skill.description}</span> : null}
        {skill.version ? (
          <span className="font-mono text-[10px] text-muted">v{skill.version}</span>
        ) : null}
      </span>
      <span className="flex shrink-0 items-center gap-1.5">
        {backends.map((backend) => (
          <BackendChip key={backend} backend={backend} />
        ))}
      </span>
      <span className="flex size-7 shrink-0 items-center justify-center rounded-md text-muted opacity-0 transition group-hover:opacity-100">
        <Ellipsis className="size-3.5" />
      </span>
    </button>
  );
}
