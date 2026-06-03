import * as React from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronLeft, ChevronRight, Download, X } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { handleMediaDownloadClick } from '@/lib/downloadMedia';

// A session-scoped image lightbox. ChatPage computes the ordered list of media-
// proxy image URLs in the transcript and wraps the page in a provider; any chat
// image (markdown inline image or a user attachment) opens it via ``open(src)``,
// and the lightbox pages left/right through the whole session's images. Used
// through the optional context so the shared Markdown renderer keeps working
// (no-op) where there's no provider (e.g. the agent-config editor preview).

type ImageViewerContextValue = { open: (src: string) => void };

const ImageViewerContext = React.createContext<ImageViewerContextValue | null>(null);

export function useImageViewer(): ImageViewerContextValue | null {
  return React.useContext(ImageViewerContext);
}

// Overlay controls sit on a dark backdrop, so the shared Button's themed
// foreground/hover (tuned for app surfaces) would be invisible here — override
// to white-on-translucent while still inheriting Button's sizing/focus/disabled
// behavior instead of hand-rolling a <button>.
const OVERLAY_BTN = 'bg-white/10 text-white hover:bg-white/20 hover:text-white';

export const ImageViewerProvider: React.FC<{ images: string[]; children: React.ReactNode }> = ({
  images,
  children,
}) => {
  const { t } = useTranslation();
  // Track the *displayed URL*, not an index. ``images`` is recomputed on every
  // streamed message, so a stored index would drift; and keeping the context
  // value (``open``) free of any ``images`` dependency means it stays stable, so
  // chat images don't re-render on every streaming tick. A clicked src that
  // isn't in the list (shouldn't happen for our own clean proxy URLs, but be
  // safe) still shows exactly what was clicked — paging just turns off for it.
  const [src, setSrc] = React.useState<string | null>(null);

  const open = React.useCallback((next: string) => setSrc(next), []);
  const close = React.useCallback(() => setSrc(null), []);

  const index = src ? images.indexOf(src) : -1;
  const pageable = index >= 0 && images.length > 1;
  const step = React.useCallback(
    (delta: number) => {
      if (index < 0 || images.length === 0) return;
      setSrc(images[(index + delta + images.length) % images.length]);
    },
    [index, images],
  );

  React.useEffect(() => {
    if (src === null) return;
    // The lightbox is a modal: while open it OWNS Escape / arrows. Listen in the
    // capture phase and stop immediate propagation on the keys we handle so a
    // lower global handler (notably the Composer's "Escape aborts recording")
    // can't also fire — Escape here must only close the viewer, not discard an
    // in-progress voice recording. Capture runs before any bubble-phase window
    // listener regardless of registration order, so ownership is deterministic.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopImmediatePropagation();
        close();
      } else if (e.key === 'ArrowLeft') {
        e.stopImmediatePropagation();
        step(-1);
      } else if (e.key === 'ArrowRight') {
        e.stopImmediatePropagation();
        step(1);
      }
    };
    window.addEventListener('keydown', onKey, { capture: true });
    return () => window.removeEventListener('keydown', onKey, { capture: true });
  }, [src, close, step]);

  const ctx = React.useMemo(() => ({ open }), [open]);

  return (
    <ImageViewerContext.Provider value={ctx}>
      {children}
      {src && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-6 backdrop-blur-sm"
          onClick={close}
          role="dialog"
          aria-modal="true"
        >
          <div className="absolute right-4 top-4 flex items-center gap-2">
            <Button
              asChild
              variant="ghost"
              size="icon"
              className={OVERLAY_BTN}
            >
              <a
                href={`${src}?download=1`}
                download
                onClick={(e) => {
                  e.stopPropagation();
                  handleMediaDownloadClick(e, src);
                }}
                aria-label={t('chat.media.download')}
              >
                <Download className="size-4" />
              </a>
            </Button>
            <Button
              variant="ghost"
              size="icon"
              onClick={close}
              aria-label={t('chat.viewer.close')}
              className={OVERLAY_BTN}
            >
              <X className="size-4" />
            </Button>
          </div>
          {pageable && (
            <>
              <Button
                variant="ghost"
                size="icon"
                onClick={(e) => {
                  e.stopPropagation();
                  step(-1);
                }}
                aria-label={t('chat.viewer.previous')}
                className={`absolute left-4 top-1/2 -translate-y-1/2 rounded-full ${OVERLAY_BTN}`}
              >
                <ChevronLeft className="size-5" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                onClick={(e) => {
                  e.stopPropagation();
                  step(1);
                }}
                aria-label={t('chat.viewer.next')}
                className={`absolute right-4 top-1/2 -translate-y-1/2 rounded-full ${OVERLAY_BTN}`}
              >
                <ChevronRight className="size-5" />
              </Button>
            </>
          )}
          <img
            src={src}
            alt=""
            onClick={(e) => e.stopPropagation()}
            className="max-h-[90vh] max-w-[90vw] rounded-lg object-contain shadow-2xl"
          />
          {pageable && (
            <span className="absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full bg-white/10 px-3 py-1 font-mono text-[11px] text-white">
              {index + 1} / {images.length}
            </span>
          )}
        </div>
      )}
    </ImageViewerContext.Provider>
  );
};
