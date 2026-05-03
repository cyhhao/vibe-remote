import React from 'react';
import clsx from 'clsx';

type SettingsPanelProps = {
  title?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
};

// Mirrors design.pen O8BNR/B6qFRA (svcSec1 / msgSec1):
// cornerRadius 12, fill --background, stroke --border 1px,
// header padding [16, 20] with bottom border, rows padding [12, 20].
export const SettingsPanel: React.FC<SettingsPanelProps> = ({
  title,
  description,
  actions,
  children,
  className,
}) => (
  <section
    className={clsx(
      'flex flex-col overflow-hidden rounded-xl border border-border bg-background',
      className
    )}
  >
    {(title || description || actions) && (
      <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
        <div className="flex min-w-0 flex-col gap-1">
          {title && <h2 className="text-[14px] font-semibold text-foreground">{title}</h2>}
          {description && (
            <p className="max-w-2xl text-[12px] leading-relaxed text-muted">{description}</p>
          )}
        </div>
        {actions && <div className="shrink-0">{actions}</div>}
      </div>
    )}
    {children}
  </section>
);

type SettingsRowProps = {
  title: React.ReactNode;
  description?: React.ReactNode;
  meta?: React.ReactNode;
  control?: React.ReactNode;
  tone?: 'default' | 'success' | 'warning' | 'info';
};

export const SettingsRow: React.FC<SettingsRowProps> = ({
  title,
  description,
  meta,
  control,
  tone = 'default',
}) => (
  <div
    className={clsx(
      'flex flex-col gap-3 border-b border-border px-5 py-3 last:border-b-0 md:flex-row md:items-center md:justify-between',
      tone === 'success' && 'bg-mint/[0.05]',
      tone === 'warning' && 'bg-gold/[0.06]',
      tone === 'info' && 'bg-cyan/[0.06]'
    )}
  >
    <div className="flex min-w-0 flex-col gap-0.5">
      <div className="text-[13px] font-medium text-foreground">{title}</div>
      {description && (
        <div className="max-w-2xl text-[11px] leading-relaxed text-muted">{description}</div>
      )}
      {meta && <div className="font-mono text-[10px] text-muted">{meta}</div>}
    </div>
    {control && <div className="shrink-0">{control}</div>}
  </div>
);

export const CompactField: React.FC<React.InputHTMLAttributes<HTMLInputElement>> = ({
  className,
  ...props
}) => (
  <input
    className={clsx(
      'h-9 rounded-lg border border-border bg-white/[0.04] px-3 text-[12px] text-foreground outline-none transition focus:border-cyan focus:ring-1 focus:ring-cyan/40',
      className
    )}
    {...props}
  />
);

export const CompactSelect: React.FC<React.SelectHTMLAttributes<HTMLSelectElement>> = ({
  className,
  ...props
}) => (
  <select
    className={clsx(
      'h-9 rounded-lg border border-border bg-white/[0.04] px-3 text-[12px] text-foreground outline-none transition focus:border-cyan focus:ring-1 focus:ring-cyan/40',
      className
    )}
    {...props}
  />
);

// Mirrors design.pen FALFE / ylboi (mint switch on): cornerRadius 9999,
// fill --mint, blur 8 #5BFFA055 glow, 38×22 with knob inset 3.
export const ToggleSwitch: React.FC<{ enabled: boolean; onClick: () => void; disabled?: boolean }> = ({
  enabled,
  onClick,
  disabled,
}) => (
  <button
    type="button"
    role="switch"
    aria-checked={enabled}
    disabled={disabled}
    onClick={onClick}
    className={clsx(
      'relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors focus:outline-none focus:ring-2 focus:ring-mint/40 disabled:opacity-50',
      enabled
        ? 'border-mint/50 bg-mint shadow-[0_0_12px_-2px_rgba(91,255,160,0.6)]'
        : 'border-border bg-surface-2'
    )}
  >
    <span
      className={clsx(
        'inline-block size-3.5 rounded-full bg-background shadow transition-transform',
        enabled ? 'translate-x-[18px]' : 'translate-x-1'
      )}
    />
  </button>
);
