# In-app file preview for agent-reply attachments

## Background

A file card's "eye" button currently opens the media proxy URL with
`target="_blank"`, kicking the user out to a browser/OS preview. We want a
generic, in-app file viewer for readable text-based files, and to hide the eye
for formats we can't render.

## Goal

- One reusable viewer that renders previewable files in a modal.
- Supported kinds: Markdown, plain text/log, CSV (table), JSON
  (collapsible + highlighted), code (syntax-highlighted), markup source
  (HTML/XML/SVG shown as highlighted source, never executed).
- Copy button for text-based content.
- The eye icon only shows when the file is previewable.
- No bundle-size regression on the main app.

## Solution

- `ui/src/lib/filePreview.ts` — `previewKind(name, mime)` allowlist (single
  source of truth for eye-gating) + `codeLanguage(name)` (ext → Shiki lang id)
  + `formatBytes` (shared with FileCard) + size/row caps.
- `ui/src/lib/highlighter.ts` — lean Shiki: `shiki/core` + JS RegExp engine (no
  WASM), grammars/themes dynamically imported per language. Only imported by the
  lazy modal, so nothing reaches the main bundle.
- `ui/src/components/ui/file-viewer.tsx` — light `FileViewerProvider` + context
  (`useFileViewer().open({url,name})`) + `React.lazy` modal. Mirrors the
  existing `ImageViewerProvider`.
- `ui/src/components/ui/file-viewer-modal.tsx` — heavy, lazy: fetch via
  `apiFetch` (≤1MB cap, 401 recovery), header (name/type/size · copy · download ·
  close), renders by kind. Markdown reuses `<Markdown interactive={false}>`;
  JSON uses `@uiw/react-json-view`; CSV uses `papaparse` (first
  `CSV_MAX_ROWS=500` rows); code/source/yaml use the Shiki helper.
- `FileCard` — show the eye only when `previewKind(meta) !== null`; the eye opens
  the viewer (falls back to `target="_blank"` if no provider mounted).
- Mount `FileViewerProvider` alongside `ImageViewerProvider` in `ChatPage`.

## Decisions (confirmed)

- Code highlighter: **Shiki**, lazy-loaded, JS engine.
- JSON viewer: **@uiw/react-json-view**.
- CSV: **papaparse**. Markdown: reuse existing renderer.
- Unsupported formats (incl. PDF): hide the eye. PDF preview is a possible
  later phase (pdf.js).
- Caps: preview ≤ 1 MB; CSV first 500 rows.

## Security

- HTML/XML/SVG render as highlighted **source only**, never executed.
- Markdown reuses the existing renderer's same-origin-only `<img>` handling.
- Shiki output escapes the code text; JSON/CSV/text are inert.

## Todo

- [ ] filePreview.ts + highlighter.ts
- [ ] FileViewer provider + lazy modal + renderers
- [ ] index.css: shiki / table / viewer styles
- [ ] Gate FileCard eye; mount provider in ChatPage
- [ ] i18n keys (en/zh)
- [ ] `npm run build` + lint; reviewer; PR
