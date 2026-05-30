import { Sparkles } from 'lucide-react';
import clsx from 'clsx';
import type { SkillAiBreakdown } from '../../../context/ApiContext';

// askill's AI score is 0–10. Mint ≥9, cyan ≥8.5, gold below — matches the
// design's score-coloured bars + badge.
function scoreText(score: number): string {
  if (score >= 9) return 'text-mint';
  if (score >= 8.5) return 'text-cyan';
  return 'text-gold';
}

function scoreFill(score: number): string {
  if (score >= 9) return 'bg-mint';
  if (score >= 8.5) return 'bg-cyan';
  return 'bg-gold';
}

function scoreChip(score: number): string {
  if (score >= 9) return 'bg-mint-soft border-mint/40 text-mint';
  if (score >= 8.5) return 'bg-cyan-soft border-cyan/40 text-cyan';
  return 'bg-gold/[0.12] border-gold/40 text-gold';
}

/** Compact "AI 9.5" pill used on registry result cards. */
export function AiScoreBadge({ score, className }: { score: number; className?: string }) {
  return (
    <span
      className={clsx(
        'inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 font-mono text-[10px] font-bold',
        scoreChip(score),
        className,
      )}
    >
      <Sparkles className="size-2.5" />
      AI {score.toFixed(1)}
    </span>
  );
}

/** Detail-panel quality block: eyebrow + big score + per-dimension bars. */
export function QualityBars({
  label,
  score,
  breakdown,
}: {
  label: string;
  score: number;
  breakdown: SkillAiBreakdown[];
}) {
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted">{label}</span>
        <div className="flex-1" />
        <span className={clsx('text-[20px] font-bold leading-none', scoreText(score))}>{score.toFixed(1)}</span>
        <span className="text-[11px] text-muted">/10</span>
      </div>
      {breakdown.map((dim) => (
        <div key={dim.key} className="flex items-center gap-2.5">
          <span className="w-24 shrink-0 font-mono text-[10px] text-muted">{dim.label}</span>
          <div className="h-[5px] flex-1 overflow-hidden rounded-full bg-surface-3">
            <div
              className={clsx('h-full rounded-full', scoreFill(dim.score))}
              style={{ width: `${Math.max(0, Math.min(100, (dim.score / 10) * 100))}%` }}
            />
          </div>
          <span className="w-7 shrink-0 text-right font-mono text-[10px] font-medium text-foreground">
            {dim.score.toFixed(1)}
          </span>
        </div>
      ))}
    </div>
  );
}
