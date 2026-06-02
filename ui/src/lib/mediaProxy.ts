// A same-origin agent-media proxy URL, minted server-side by
// ``core/workbench_media`` (``/api/sessions/<id>/media/<token>``). These are the
// ONLY image/file URLs the workbench trusts to fetch or render without an
// explicit user click — they hit our own server, never a third-party host. Used
// by the Markdown renderer (inline images), the FileCard (metadata fetch), and
// the user-message attachment renderer to enforce one same-origin policy.
const MEDIA_PROXY_RE = /^\/api\/sessions\/[^/]+\/media\/[^/?#]+/;

export function isProxyMediaUrl(url: string | null | undefined): boolean {
  return !!url && MEDIA_PROXY_RE.test(url);
}
