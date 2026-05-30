import { Check } from 'lucide-react';
import clsx from 'clsx';

export interface CheckboxProps {
  checked: boolean;
  onCheckedChange: (next: boolean) => void;
  disabled?: boolean;
  label?: string;
  className?: string;
}

/**
 * Lightweight controlled checkbox (no Radix dependency — the repo only pulls
 * in `@radix-ui/react-dialog`, and a checkbox this simple doesn't warrant a
 * new one). Mint fill + dark check when checked; bordered surface when not.
 * Visual contract mirrors design.pen's multi-select rows.
 */
export function Checkbox({ checked, onCheckedChange, disabled, label, className }: CheckboxProps) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={clsx(
        'flex size-[18px] shrink-0 items-center justify-center rounded-[5px] border transition-colors',
        checked
          ? 'border-transparent bg-mint text-[#06060e]'
          : 'border-border-strong bg-surface-3 text-transparent',
        disabled && 'cursor-not-allowed opacity-50',
        className,
      )}
    >
      <Check className="size-3" strokeWidth={3} />
    </button>
  );
}
