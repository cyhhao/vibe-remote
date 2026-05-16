import * as React from 'react';
import { cn } from '@/lib/utils';
import { getBackendUiMeta, type BackendId } from '@/lib/agentBackends';

export type { BackendId } from '@/lib/agentBackends';

interface BackendIconProps extends React.HTMLAttributes<HTMLSpanElement> {
  backend: BackendId;
  size?: number;
  /**
   * `block` (default) renders a square tile with mono initials, tinted per-backend.
   * `glyph` renders just the lucide icon in the brand color (no tile, no border).
   */
  variant?: 'block' | 'glyph';
}

export const BackendIcon: React.FC<BackendIconProps> = ({
  backend,
  size = 40,
  variant = 'block',
  className,
  ...props
}) => {
  const meta = getBackendUiMeta(backend);

  if (variant === 'glyph') {
    const Icon = meta.Icon;
    return (
      <span
        className={cn('inline-flex shrink-0 items-center justify-center', meta.glyphCls, className)}
        style={{ width: size, height: size }}
        {...props}
      >
        <Icon size={size} />
      </span>
    );
  }

  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-xl border font-mono text-[13px] font-bold tracking-wider',
        meta.blockCls,
        className
      )}
      style={{ width: size, height: size }}
      {...props}
    >
      {meta.initials}
    </span>
  );
};
