import * as React from 'react';
import { useTranslation } from 'react-i18next';
import { Download } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { useImageViewer } from '@/components/ui/image-viewer';
import { handleMediaDownloadClick } from '@/lib/downloadMedia';
import { cn } from '@/lib/utils';

// When a markdown image is wrapped in a link (``[![](media)](href)``),
// react-markdown nests the image inside the outer <a>. The Markdown ``a`` handler
// wraps such children in this provider so the ChatImage inside renders WITHOUT
// its own download <a> (nested anchors are invalid HTML) and without the
// lightbox click (the surrounding link is the intended action) — just the
// capped <img>.
const LinkedImageContext = React.createContext(false);
export const LinkedImageProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <LinkedImageContext.Provider value={true}>{children}</LinkedImageContext.Provider>
);

// The width cap is an inline style so it beats the ``.vr-markdown img {
// max-width: 100% }`` descendant rule.
const IMG_BOX: React.CSSProperties = { maxWidth: 'min(22rem, 100%)', maxHeight: '15rem' };
const IMG_CLASS = 'block h-auto w-auto rounded-lg border border-border object-contain';

// One inline chat image — used by both the Markdown renderer (agent replies) and
// the user-attachment renderer (ChatPage). Capped to a preview width and shown
// at its natural aspect (never stretched / full-width); click to open the
// lightbox; hover reveals a download button.
export const ChatImage: React.FC<{ src: string; alt?: string; className?: string }> = ({ src, alt, className }) => {
  const { t } = useTranslation();
  const viewer = useImageViewer();
  const linked = React.useContext(LinkedImageContext);

  // Inside a markdown link: a bare capped image — no nested download anchor and
  // no lightbox click stealing the link.
  if (linked) {
    return (
      <img src={src} alt={alt || ''} loading="lazy" style={IMG_BOX} className={cn('my-1 align-bottom', IMG_CLASS, className)} />
    );
  }

  return (
    <span className={cn('group relative my-1 inline-block max-w-full align-bottom', className)}>
      <img
        src={src}
        alt={alt || ''}
        loading="lazy"
        onClick={() => viewer?.open(src)}
        style={IMG_BOX}
        className={cn(IMG_CLASS, viewer ? 'cursor-zoom-in' : '')}
      />
      <Button
        asChild
        variant="ghost"
        size="icon"
        // Overlay badge on the image corner: smaller than a standard icon button,
        // dark translucent, revealed on hover. Inline color/decoration override
        // because this <a> can render inside ``.vr-markdown`` whose ``a`` rule
        // would otherwise tint it cyan + underline.
        className="absolute right-2 top-2 size-7 rounded-md bg-black/55 text-white opacity-0 backdrop-blur-sm transition hover:bg-black/70 hover:text-white group-hover:opacity-100"
      >
        <a
          href={`${src}?download=1`}
          download
          onClick={(e) => {
            e.stopPropagation();
            handleMediaDownloadClick(e, src);
          }}
          aria-label={t('chat.media.download')}
          style={{ color: '#fff', textDecoration: 'none' }}
        >
          <Download className="size-4" />
        </a>
      </Button>
    </span>
  );
};
