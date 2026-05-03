import * as React from 'react';
import { cn } from '@/lib/utils';

interface WizardCardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Outer max-width. Design.pen uses 840 (Platforms) or 920 (rest). */
  width?: number;
  /** Use the larger Welcome / Summary padding (64) instead of default (40, 48). */
  size?: 'default' | 'hero';
  /** Extra mint accent border for the Summary "all done" card. */
  accent?: boolean;
}

// Mirrors design.pen wCard / welCard / sumCard:
//  cornerRadius 20, fill --surface-2, stroke --border 1px, padding [40, 48]
//  shadow blur 64 / color #5BFFA014 / offset y32 / spread -12
export const WizardCard: React.FC<WizardCardProps> = ({
  width = 920,
  size = 'default',
  accent = false,
  className,
  children,
  ...props
}) => (
  <div
    className={cn(
      'mx-auto flex w-full flex-col rounded-2xl border bg-surface-2',
      'shadow-[0_32px_64px_-12px_rgba(91,255,160,0.078)]',
      accent ? 'border-mint/35' : 'border-border',
      size === 'hero' ? 'p-12 md:p-16' : 'px-6 py-8 md:px-12 md:py-10',
      className
    )}
    style={{ maxWidth: width }}
    {...props}
  >
    {children}
  </div>
);
