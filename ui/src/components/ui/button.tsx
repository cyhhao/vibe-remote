import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';

import { cn } from '@/lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        // Mint primary — flat, no glow shadow (design.pen Button/Default).
        default: 'bg-primary text-primary-foreground hover:brightness-110',
        secondary: 'border border-border bg-secondary text-secondary-foreground hover:border-border-strong',
        // Outline — bg matches page surface so it sits cleanly on glow gradients.
        outline:
          'border border-border bg-background text-foreground shadow-[0_1px_2px_rgba(0,0,0,0.05)] hover:bg-surface-2',
        // Cyan outline — for "Read Vibe Remote" / docs style CTAs.
        'outline-cyan':
          'border border-cyan/40 bg-cyan/[0.06] text-cyan hover:bg-cyan/[0.10]',
        ghost: 'text-foreground hover:bg-surface-2',
        destructive: 'bg-destructive text-destructive-foreground hover:opacity-90',
        link: 'text-primary underline-offset-4 hover:underline',
        accent: 'border border-cyan/40 bg-cyan-soft text-cyan hover:bg-cyan/15',
      },
      size: {
        // Default = design's Large/Default: padding [8, 24].
        default: 'h-10 px-6 py-2',
        sm: 'h-8 rounded-md px-4 text-xs',
        lg: 'h-11 rounded-md px-6 text-sm',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  }
);

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean };

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />;
  }
);
Button.displayName = 'Button';

export { buttonVariants };
