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

// A small "ⓘ" affordance that reveals a hint on click / tap — uniform across
// desktop and mobile (Workbench pages are routinely opened from an IM app on a
// phone, where hover doesn't exist). Built on a MODAL Popover on purpose: these
// hints live inside modal Dialogs, and a non-modal popover portals its content
// as a sibling of the dialog, where Radix marks it aria-hidden/inert — the same
// reason AgentRoutePicker takes a `modal` prop. Click toggles; Escape / outside
// click dismiss via Radix.
export const InfoHint: React.FC<InfoHintProps> = ({ content, label, className, align = 'start' }) => {
  const [open, setOpen] = React.useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen} modal>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={label}
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
