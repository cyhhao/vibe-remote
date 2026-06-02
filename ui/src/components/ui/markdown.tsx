import * as React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { FileCard } from '@/components/ui/file-card';
import { isProxyMediaUrl } from '@/lib/mediaProxy';
import { cn } from '@/lib/utils';

// Shared markdown renderer. react-markdown + remark-gfm (tables, strikethrough,
// task lists, autolinks); the element styling lives in index.css under
// ``.vr-markdown`` because the project doesn't ship the Tailwind typography
// plugin. Promoted out of ChatPage once a second caller (the agent-config
// editor preview) needed the same renderer — one home for "render markdown the
// Vibe Remote way", so the security-conscious <img> handling is shared too.
// ``interactive`` (default true) keeps the normal chat/editor rendering where
// links and image-links are clickable. Pass ``interactive={false}`` for snippets
// that live inside a clickable row/button (e.g. inbox previews): links render as
// plain text so a nested <a> can't become invalid interactive content or steal
// the row's click.
export const Markdown: React.FC<{ content: string; className?: string; interactive?: boolean }> = ({
  content,
  className,
  interactive = true,
}) => (
  <div className={cn('vr-markdown', className)}>
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        // Markdown here is untrusted (agent replies, user-authored prompts) and
        // can embed images. The default <img> renderer would auto-fetch any URL
        // the moment the view opens (``![](http://attacker/x)``), leaking the
        // viewer's IP / network metadata to an attacker-chosen host. So we only
        // render a real inline <img> for our OWN same-origin media proxy; every
        // other URL stays a click-through link (or plain text when
        // non-interactive) so nothing is fetched without an explicit action.
        img: ({ src, alt }) => {
          if (!src) return null;
          const url = String(src);
          if (interactive && isProxyMediaUrl(url)) {
            return <img src={url} alt={alt || ''} loading="lazy" />;
          }
          const label = `🖼 ${alt || url}`;
          return interactive ? (
            <a href={url} target="_blank" rel="noopener noreferrer nofollow">
              {label}
            </a>
          ) : (
            <span>{label}</span>
          );
        },
        // Links to our media proxy are agent-produced files → render the
        // download card (filename + type + download / preview). Other links keep
        // the normal anchor (interactive) or collapse to plain text inside a
        // clickable row (non-interactive).
        a: ({ href, children }) => {
          const url = href ? String(href) : '';
          if (interactive && url && isProxyMediaUrl(url)) {
            return <FileCard href={url}>{children}</FileCard>;
          }
          if (!interactive) return <span>{children}</span>;
          return (
            <a href={url} target="_blank" rel="noopener noreferrer nofollow">
              {children}
            </a>
          );
        },
        ...(interactive
          ? {}
          : {
              // GFM task lists render a checkbox <input>; even disabled, an
              // <input> nested in the sidebar row <button> is invalid interactive
              // content, so show the state as a plain glyph instead.
              input: ({ checked }: { checked?: boolean }) => (
                <span aria-hidden="true">{checked ? '☑ ' : '☐ '}</span>
              ),
            }),
      }}
    >
      {content}
    </ReactMarkdown>
  </div>
);
