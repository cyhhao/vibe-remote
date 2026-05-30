import { Sparkles } from 'lucide-react';
import clsx from 'clsx';

// askill's AI score is 0–10. Mint ≥9, cyan ≥8.5, gold below — matches the
// design's score-coloured badge.
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
