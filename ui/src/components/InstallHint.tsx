import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Plus, Share, Sparkles, X } from 'lucide-react';

import { Popover, PopoverContent, PopoverTrigger } from './ui/popover';

const STORAGE_KEY = 'vibe-remote-a2hs';

// Only nudge on iOS Safari, on a phone, when not already installed to the Home
// Screen. Standalone mode is the real fix for the iOS keyboard/chrome issues, so
// we point users there; other iOS browsers can't "Add to Home Screen", and the
// desktop/iPad layout doesn't render the mobile header this mounts into.
function shouldShowHint(): boolean {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  if (!window.matchMedia('(max-width: 767px)').matches) return false;
  const ua = navigator.userAgent || '';
  const isIOS =
    /iP(hone|ad|od)/.test(ua) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  if (!isIOS) return false;
  if (/CriOS|FxiOS|EdgiOS|OPiOS/i.test(ua)) return false; // not Safari
  const standalone =
    (navigator as unknown as { standalone?: boolean }).standalone === true ||
    window.matchMedia('(display-mode: standalone)').matches;
  return !standalone;
}

// Top-right nudge to install the app to the iOS Home Screen (standalone PWA),
// which removes Safari's chrome and the keyboard whitespace/accessory issues.
// Shows a small bar; the ✕ collapses it to a persistent gold dot (we keep
// nudging rather than dismissing for good, per the "strongly recommend" intent).
// Both the bar and the dot open a popover with the Share → Add to Home Screen steps.
export const InstallHint: React.FC = () => {
  const { t } = useTranslation();
  const [visible, setVisible] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    setVisible(shouldShowHint());
    try {
      setCollapsed(window.localStorage.getItem(STORAGE_KEY) === 'dot');
    } catch {
      /* private mode / storage blocked — just keep it expanded */
    }
  }, []);

  if (!visible) return null;

  const collapse = () => {
    setOpen(false);
    setCollapsed(true);
    try {
      window.localStorage.setItem(STORAGE_KEY, 'dot');
    } catch {
      /* ignore */
    }
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      {collapsed ? (
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label={t('installHint.cta')}
            className="relative grid size-6 shrink-0 place-items-center"
          >
            <span className="size-2.5 rounded-full bg-gold shadow-[0_0_8px_rgba(245,200,92,0.9)]" />
            <span className="absolute inline-flex size-2.5 animate-ping rounded-full bg-gold/60" />
          </button>
        </PopoverTrigger>
      ) : (
        <div className="inline-flex shrink-0 items-center gap-1 rounded-full border border-gold/40 bg-gold/[0.12] py-0.5 pl-2 pr-1">
          <PopoverTrigger asChild>
            <button type="button" className="inline-flex items-center gap-1 text-[11px] font-semibold text-gold">
              <Sparkles className="size-3" />
              <span>{t('installHint.cta')}</span>
            </button>
          </PopoverTrigger>
          <button
            type="button"
            onClick={collapse}
            aria-label={t('installHint.dismiss')}
            className="grid size-4 place-items-center rounded-full text-gold/70 transition-colors hover:text-gold"
          >
            <X className="size-3" />
          </button>
        </div>
      )}

      <PopoverContent align="end" sideOffset={8} className="w-[18rem] border-gold/30">
        <div className="flex flex-col gap-2.5">
          <div className="flex items-center gap-2">
            <span className="grid size-7 shrink-0 place-items-center rounded-lg border border-gold/40 bg-gold/[0.12] text-gold">
              <Sparkles className="size-3.5" />
            </span>
            <div className="text-[13px] font-semibold text-foreground">{t('installHint.title')}</div>
          </div>
          <p className="text-[12px] leading-relaxed text-muted">{t('installHint.body')}</p>
          <ol className="flex flex-col gap-1.5 rounded-lg border border-border bg-foreground/[0.02] p-2.5 text-[12px] text-foreground">
            <li className="flex items-center gap-2">
              <Share className="size-4 shrink-0 text-cyan" />
              <span>{t('installHint.step1')}</span>
            </li>
            <li className="flex items-center gap-2">
              <Plus className="size-4 shrink-0 text-cyan" />
              <span>{t('installHint.step2')}</span>
            </li>
          </ol>
        </div>
      </PopoverContent>
    </Popover>
  );
};
