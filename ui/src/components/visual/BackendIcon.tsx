import * as React from 'react';
import { Bot, Code2, Sparkles } from 'lucide-react';
import { cn } from '@/lib/utils';

export type BackendId = 'opencode' | 'claude' | 'codex' | string;

interface BackendIconProps extends React.HTMLAttributes<HTMLSpanElement> {
  backend: BackendId;
  size?: number;
  /**
   * `block` (default) renders a square tile with mono initials, tinted per-backend.
   * `glyph` renders just the lucide icon in the brand color (no tile, no border).
   */
  variant?: 'block' | 'glyph';
}

interface BackendMeta {
  label: string;
  blockCls: string;
  glyphCls: string;
  Icon: React.ComponentType<{ size?: number; className?: string }>;
}

const BACKEND_META: Record<string, BackendMeta> = {
  opencode: {
    label: 'OP',
    blockCls: 'border-mint/40 bg-mint/[0.10] text-mint',
    glyphCls: 'text-mint',
    Icon: Code2,
  },
  claude: {
    label: 'CL',
    blockCls: 'border-[rgba(217,119,87,0.4)] bg-[rgba(217,119,87,0.10)] text-[#e8a87c]',
    glyphCls: 'text-cyan',
    Icon: Sparkles,
  },
  codex: {
    label: 'CO',
    blockCls: 'border-violet/40 bg-violet/[0.10] text-violet',
    glyphCls: 'text-violet',
    Icon: Bot,
  },
};

const FALLBACK_META: BackendMeta = {
  label: '',
  blockCls: 'border-border bg-surface-2 text-foreground',
  glyphCls: 'text-muted',
  Icon: Bot,
};

export const BackendIcon: React.FC<BackendIconProps> = ({
  backend,
  size = 40,
  variant = 'block',
  className,
  ...props
}) => {
  const meta =
    BACKEND_META[backend] ||
    ({ ...FALLBACK_META, label: backend.slice(0, 2).toUpperCase() } as BackendMeta);

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
      {meta.label}
    </span>
  );
};
