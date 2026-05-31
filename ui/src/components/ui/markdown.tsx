import * as React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

import { cn } from '@/lib/utils';

// Shared markdown renderer. react-markdown + remark-gfm (tables, strikethrough,
// task lists, autolinks); the element styling lives in index.css under
// ``.vr-markdown`` because the project doesn't ship the Tailwind typography
// plugin. Promoted out of ChatPage once a second caller (the agent-config
// editor preview) needed the same renderer — one home for "render markdown the
// Vibe Remote way", so the security-conscious <img> handling is shared too.
export const Markdown: React.FC<{ content: string; className?: string }> = ({ content, className }) => (
  <div className={cn('vr-markdown', className)}>
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        // Markdown here is untrusted (agent replies, user-authored prompts) and
        // can embed images. The default <img> renderer would auto-fetch any URL
        // the moment the view opens (``![](http://attacker/x)``), leaking the
        // viewer's IP / network metadata to an attacker-chosen host. Render
        // images as click-through links instead so nothing is fetched without
        // an explicit user action.
        img: ({ src, alt }) =>
          src ? (
            <a href={String(src)} target="_blank" rel="noopener noreferrer nofollow">
              {`🖼 ${alt || String(src)}`}
            </a>
          ) : null,
      }}
    >
      {content}
    </ReactMarkdown>
  </div>
);
