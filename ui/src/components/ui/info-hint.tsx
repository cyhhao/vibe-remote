import * as React from 'react';
import { Info } from 'lucide-react';

import { cn } from '../../lib/utils';
import { Popover, PopoverContent, PopoverTrigger } from './popover';

interface InfoHintProps {
  /** Help text revealed when the badge is hovered (desktop) or tapped (mobile). */
  content: React.ReactNode;
  /** Accessible label for the trigger button (e.g. "What is this?"). */
  label: string;
  className?: string;
  align?: 'start' | 'center' | 'end';
}

// A small "ⓘ" affordance that reveals a hint on hover (desktop) AND tap
// (mobile). Built on Popover on purpose: a pure CSS / Radix tooltip is
// hover/focus-only and never opens on touch, but Workbench pages are routinely
// opened from an IM app on a phone. Open is controlled — pointer enter/leave
// and focus drive desktop hover; clicking toggles for touch and keyboard.
export const InfoHint: React.FC<InfoHintProps> = ({ content, label, className, align = 'start' }) => {
  const [open, setOpen] = React.useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={label}
          onMouseEnter={() => setOpen(true)}
          onMouseLeave={() => setOpen(false)}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onClick={() => setOpen((prev) => !prev)}
          className={cn(
            'inline-flex size-4 shrink-0 items-center justify-center rounded-full text-muted outline-none transition hover:text-foreground focus-visible:text-foreground',
            className,
          )}
        >
          <Info className="size-3.5" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align={align}
        sideOffset={6}
        // Don't steal focus from the trigger on hover-open, so tabbing isn't trapped.
        onOpenAutoFocus={(event) => event.preventDefault()}
        className="w-64 p-3 text-[12px] font-normal leading-relaxed text-muted"
      >
        {content}
      </PopoverContent>
    </Popover>
  );
};
