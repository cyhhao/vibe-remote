import * as React from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-semibold tracking-wide transition-colors focus:outline-none',
  {
    variants: {
      variant: {
        default: 'border border-border-strong bg-surface text-foreground',
        secondary: 'border border-border bg-surface text-muted',
        outline: 'border border-border-strong bg-transparent text-foreground',
        success: 'border border-mint/40 bg-mint-soft text-mint',
        warning: 'border border-gold/40 bg-gold/10 text-gold',
        info: 'border border-cyan/40 bg-cyan-soft text-cyan',
        destructive: 'border border-destructive/40 bg-destructive/10 text-destructive',
        // Eyebrow — JetBrains Mono cyan w/ glow (design.pen Badge/Eyebrow mtcmf).
        eyebrow:
          'rounded-full border border-cyan/50 bg-cyan/[0.16] px-3 py-1.5 font-mono text-[11px] font-bold uppercase tracking-[0.14em] text-cyan shadow-[0_0_24px_-4px_rgba(63,224,229,0.33)]',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
);

export type BadgeProps = React.HTMLAttributes<HTMLSpanElement> & VariantProps<typeof badgeVariants>;

export const Badge = ({ className, variant, ...props }: BadgeProps) => (
  <span className={cn(badgeVariants({ variant }), className)} {...props} />
);
