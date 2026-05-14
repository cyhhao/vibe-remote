import clsx from 'clsx';

/**
 * Generic segmented-radio control used by the Settings → Backends auth-mode
 * tabs (Claude / Codex / OpenCode) and elsewhere. Previously cloned inline in
 * every page that needed it; lifted here once a third caller appeared.
 *
 * Visual contract mirrors ``shared/RoutingConfigPanel.tsx``:
 * - The whole row is one `role="radiogroup"` with the active option styled
 *   mint-on-mint-soft and inactive options muted.
 * - When ``disabled`` is true, the row dims and individual buttons reject
 *   clicks. Callers that need a stronger lock (e.g. defending against an iOS
 *   Safari quirk where ``disabled`` buttons still register a tap) should
 *   ALSO guard the state mutation site itself — see ``useOAuthFlowLock``.
 *
 * The generic ``T`` is a string-literal union of the radio's possible
 * values, so ``onChange`` is typed strictly to the option ids.
 */
export type SegmentedRadioOption<T extends string> = {
  id: T;
  label: string;
};

export interface SegmentedRadioProps<T extends string> {
  value: T;
  onChange: (next: T) => void;
  options: ReadonlyArray<SegmentedRadioOption<T>>;
  ariaLabel: string;
  disabled?: boolean;
}

export function SegmentedRadio<T extends string>({
  value,
  onChange,
  options,
  ariaLabel,
  disabled,
}: SegmentedRadioProps<T>) {
  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      aria-disabled={disabled || undefined}
      className={clsx(
        'flex h-9 items-stretch gap-0.5 rounded-md border border-border bg-foreground/[0.03] p-0.5',
        disabled && 'opacity-60',
      )}
    >
      {options.map((opt) => {
        const active = value === opt.id;
        return (
          <button
            key={opt.id}
            type="button"
            role="radio"
            aria-checked={active}
            disabled={disabled}
            onClick={() => onChange(opt.id)}
            className={clsx(
              'flex-1 rounded-[4px] px-3 text-[12px] transition-colors',
              active
                ? 'border border-mint/30 bg-mint-soft font-bold text-mint'
                : 'font-medium text-muted hover:text-foreground',
              disabled && 'cursor-not-allowed',
            )}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}
