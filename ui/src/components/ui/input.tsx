import * as React from 'react';

import { cn } from '@/lib/utils';
import { fieldBaseClass } from './field';

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

export const Input = React.forwardRef<HTMLInputElement, InputProps>(({ className, type, ...props }, ref) => (
  <input
    ref={ref}
    type={type}
    className={cn(fieldBaseClass, 'flex h-9 px-3 py-1', className)}
    {...props}
  />
));
Input.displayName = 'Input';
