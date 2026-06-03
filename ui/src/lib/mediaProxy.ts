// A same-origin agent-media proxy URL, minted server-side by
// ``core/workbench_media`` (``/api/media/<token>``). These are the ONLY
// image/file URLs the workbench trusts to fetch or render without an explicit
// user click — they hit our own server, never a third-party host. Used by the
// Markdown renderer (inline images), the FileCard (metadata fetch), and the
// user-message attachment renderer to enforce one same-origin policy.
const MEDIA_PROXY_RE = /^\/api\/media\/[^/?#]+/;

export function isProxyMediaUrl(url: string | null | undefined): boolean {
  return !!url && MEDIA_PROXY_RE.test(url);
}

// Agent-reply images carry their pixel dimensions as ``?w=&h=`` on the proxy URL
// (the rewrite step appends them when known; the proxy ignores the query and serves
// by token). The renderer reads them to reserve the image's box BEFORE it loads, so
// a late-loading image causes zero layout shift. Returns ``{}`` when absent/invalid.
export function readMediaDims(url: string | null | undefined): { width?: number; height?: number } {
  if (!url) return {};
  const q = url.indexOf('?');
  if (q < 0) return {};
  const params = new URLSearchParams(url.slice(q + 1));
  const w = Number(params.get('w'));
  const h = Number(params.get('h'));
  return {
    width: Number.isFinite(w) && w > 0 ? w : undefined,
    height: Number.isFinite(h) && h > 0 ? h : undefined,
  };
}
