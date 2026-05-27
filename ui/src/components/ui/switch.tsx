import * as React from 'react';
import { cn } from '@/lib/utils';

export interface SwitchProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onChange'> {
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  /** ARIA label so screen readers can describe what the switch toggles. */
  label?: string;
}

// Mint-on / muted-off toggle. Mirrors design.pen's enable switches used
// across Agents / Harness rows — pill rail + drag-feel thumb that snaps
// to the active end. Keep this the single source of truth so the visual
// stays consistent the next time a toggle ships.
export const Switch = React.forwardRef<HTMLButtonElement, SwitchProps>(
  ({ className, checked, onCheckedChange, label, disabled, ...props }, ref) => {
    return (
      <button
        ref={ref}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label}
        disabled={disabled}
        onClick={() => onCheckedChange(!checked)}
        className={cn(
          'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full border transition disabled:cursor-not-allowed disabled:opacity-50',
          checked
            ? 'border-mint/50 bg-mint shadow-[0_0_12px_-4px_rgba(91,255,160,0.55)]'
            : 'border-border-strong bg-surface-2',
          className,
        )}
        {...props}
      >
        <span
          className={cn(
            'inline-block size-[18px] translate-x-0.5 rounded-full bg-background shadow-[0_1px_3px_rgba(0,0,0,0.4)] transition-transform',
            checked && 'translate-x-[22px] bg-[#080812]',
          )}
        />
      </button>
    );
  },
);
Switch.displayName = 'Switch';
