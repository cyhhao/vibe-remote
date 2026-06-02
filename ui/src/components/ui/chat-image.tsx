import * as React from 'react';
import { Download } from 'lucide-react';

import { useImageViewer } from '@/components/ui/image-viewer';
import { cn } from '@/lib/utils';

// One inline chat image — used by both the Markdown renderer (agent replies) and
// the user-attachment renderer (ChatPage). Capped to a preview width and shown
// at its natural aspect (never stretched / full-width); click to open the
// lightbox; hover reveals a download button. The width cap is an inline style so
// it beats the ``.vr-markdown img { max-width: 100% }`` descendant rule.
export const ChatImage: React.FC<{ src: string; alt?: string; className?: string }> = ({ src, alt, className }) => {
  const viewer = useImageViewer();
  return (
    <span className={cn('group relative my-1 inline-block max-w-full align-bottom', className)}>
      <img
        src={src}
        alt={alt || ''}
        loading="lazy"
        onClick={() => viewer?.open(src)}
        style={{ maxWidth: 'min(22rem, 100%)', maxHeight: '15rem' }}
        className={cn(
          'block h-auto w-auto rounded-lg border border-border object-contain',
          viewer ? 'cursor-zoom-in' : '',
        )}
      />
      <a
        href={`${src}?download=1`}
        download
        onClick={(e) => e.stopPropagation()}
        aria-label="Download image"
        // Inline color/decoration override: this <a> can render inside
        // ``.vr-markdown`` whose ``a`` rule would otherwise tint it cyan + underline.
        style={{ color: '#fff', textDecoration: 'none' }}
        className="absolute right-2 top-2 grid size-7 place-items-center rounded-md bg-black/55 opacity-0 backdrop-blur-sm transition group-hover:opacity-100"
      >
        <Download className="size-4" />
      </a>
    </span>
  );
};
