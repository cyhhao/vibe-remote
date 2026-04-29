import * as React from 'react';
import { cn } from '@/lib/utils';
import logoImg from '@/assets/logo.png';

interface BrandLogoProps extends React.HTMLAttributes<HTMLSpanElement> {
  size?: number;
  withGlow?: boolean;
}

export const BrandLogo: React.FC<BrandLogoProps> = ({
  size = 36,
  withGlow = true,
  className,
  ...props
}) => (
  <span
    className={cn(
      'inline-flex shrink-0 items-center justify-center overflow-hidden rounded-xl border border-mint/30 bg-mint/[0.08]',
      withGlow && 'shadow-[0_0_24px_-4px_rgba(91,255,160,0.45)]',
      className
    )}
    style={{ width: size, height: size }}
    {...props}
  >
    <img src={logoImg} alt="Vibe Remote" className="h-full w-full object-cover" />
  </span>
);
