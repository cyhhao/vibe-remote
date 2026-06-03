import * as React from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Copy, Download, FileText } from 'lucide-react';
import JsonView from '@uiw/react-json-view';
import { githubDarkTheme } from '@uiw/react-json-view/githubDark';
import { githubLightTheme } from '@uiw/react-json-view/githubLight';
import Papa from 'papaparse';

import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog';
import { Markdown } from '@/components/ui/markdown';
import { useTheme } from '@/context/ThemeContext';
import { apiFetch } from '@/lib/apiFetch';
import { handleMediaDownloadClick } from '@/lib/downloadMedia';
import { isProxyMediaUrl } from '@/lib/mediaProxy';
import {
  CSV_MAX_ROWS,
  JSON_TREE_MAX_BYTES,
  PREVIEW_MAX_BYTES,
  codeLanguage,
  formatBytes,
  previewKind,
  type PreviewKind,
} from '@/lib/filePreview';
import { highlightCode } from '@/lib/highlighter';
import { copyTextToClipboard } from '@/lib/utils';
import type { FilePreviewTarget } from '@/components/ui/file-viewer';

// The lazy half of the file viewer: fetches the file text (size-capped) and
// renders it by kind in the shared Dialog. Default export so ``React.lazy`` can
// load it. All the heavy preview deps (Shiki, @uiw/react-json-view, papaparse)
// live here so they stay out of the main bundle.

type Status =
  | { phase: 'loading' }
  | { phase: 'error' }
  | { phase: 'toolarge' }
  | { phase: 'ready'; kind: PreviewKind; text: string };

type Info = { name: string; size: number | null };

// Code / source: highlight asynchronously (Shiki + grammar load), falling back
// to plain escaped text while pending or if highlighting fails.
const CodeBlock: React.FC<{ code: string; lang: string }> = ({ code, lang }) => {
  const { resolvedTheme } = useTheme();
  const [html, setHtml] = React.useState<string | null>(null);
  React.useEffect(() => {
    let alive = true;
    highlightCode(code, lang, resolvedTheme === 'light' ? 'github-light' : 'github-dark')
      .then((out) => alive && setHtml(out))
      .catch(() => alive && setHtml(null));
    return () => {
      alive = false;
    };
  }, [code, lang, resolvedTheme]);
  // Shiki escapes the code text, so the HTML is safe to inject.
  if (html) return <div className="vr-fileview-code" dangerouslySetInnerHTML={{ __html: html }} />;
  return <pre className="vr-fileview-pre">{code}</pre>;
};

const CsvTable: React.FC<{ text: string }> = ({ text }) => {
  const { t } = useTranslation();
  const { rows, total, cols } = React.useMemo(() => {
    const parsed = Papa.parse<string[]>(text.trim(), { skipEmptyLines: true });
    const all = (parsed.data || []) as string[][];
    const shown = all.slice(0, CSV_MAX_ROWS);
    // Width = the widest row, not the first — a later row with more fields (or
    // headerless data) must not have its extra cells truncated to the header.
    const cols = shown.reduce((max, r) => Math.max(max, r.length), 0);
    return { rows: shown, total: all.length, cols };
  }, [text]);
  if (rows.length === 0 || cols === 0) return <pre className="vr-fileview-pre">{text}</pre>;
  const [head, ...bodyRows] = rows;
  const colIdx = Array.from({ length: cols }, (_, i) => i);
  return (
    <div className="vr-fileview-csv">
      <table className="vr-fileview-table">
        <thead>
          <tr>{colIdx.map((ci) => <th key={ci}>{head[ci] ?? ''}</th>)}</tr>
        </thead>
        <tbody>
          {bodyRows.map((r, ri) => (
            <tr key={ri}>{colIdx.map((ci) => <td key={ci}>{r[ci] ?? ''}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {total > rows.length && (
        <div className="vr-fileview-note">{t('chat.viewer.csvTruncated', { shown: rows.length, total })}</div>
      )}
    </div>
  );
};

const JsonBlock: React.FC<{ text: string }> = ({ text }) => {
  const { resolvedTheme } = useTheme();
  const parsed = React.useMemo<{ ok: boolean; value: unknown }>(() => {
    try {
      return { ok: true, value: JSON.parse(text) };
    } catch {
      return { ok: false, value: null };
    }
  }, [text]);
  // The interactive tree mounts every node into the DOM (``collapsed`` only sets
  // the visual state), so a large JSON would freeze the main thread — fall back
  // to highlighted source above a threshold. Also fall back for a primitive root
  // (JsonView wants an object/array) or invalid JSON.
  if (text.length > JSON_TREE_MAX_BYTES || !parsed.ok || !parsed.value || typeof parsed.value !== 'object') {
    return <CodeBlock code={text} lang="json" />;
  }
  return (
    <JsonView
      value={parsed.value as object}
      style={resolvedTheme === 'light' ? githubLightTheme : githubDarkTheme}
      collapsed={2}
      displayDataTypes={false}
      shortenTextAfterLength={0}
      className="vr-fileview-json"
    />
  );
};

export default function FileViewerModal({
  target,
  onClose,
}: {
  target: FilePreviewTarget;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  // Defense in depth alongside FileCard's gate: only ever preview our own
  // same-origin proxy files. A non-proxy URL must never be auto-fetched (it'd
  // leak the viewer's network to a third-party host); start in ``error``.
  const [status, setStatus] = React.useState<Status>(() =>
    isProxyMediaUrl(target.url) ? { phase: 'loading' } : { phase: 'error' },
  );
  const [info, setInfo] = React.useState<Info>({ name: target.name || '', size: null });
  const [copied, setCopied] = React.useState(false);

  React.useEffect(() => {
    if (!isProxyMediaUrl(target.url)) return; // non-proxy → never fetch (state is already 'error')
    let alive = true;
    (async () => {
      try {
        // Cheap /meta first for the size guard + name, so a huge file never gets
        // pulled into the page just to refuse it.
        let size: number | null = null;
        let name = target.name || '';
        try {
          const metaRes = await apiFetch(`${target.url}/meta`, { headers: { Accept: 'application/json' } });
          if (metaRes.ok) {
            const m = (await metaRes.json()) as { name?: string; size?: number };
            if (typeof m?.size === 'number') size = m.size;
            if (typeof m?.name === 'string' && m.name) name = m.name;
          }
        } catch {
          /* meta is best-effort */
        }
        if (!alive) return;
        if (size != null && size > PREVIEW_MAX_BYTES) {
          setInfo({ name, size });
          setStatus({ phase: 'toolarge' });
          return;
        }
        const res = await apiFetch(target.url, { headers: { Accept: '*/*' } });
        if (!alive) return;
        if (!res.ok) {
          setStatus({ phase: 'error' });
          return;
        }
        // Backstop the size cap when /meta gave no size: refuse a huge body by
        // Content-Length before reading it in.
        const len = Number(res.headers.get('content-length'));
        const byteSize = size ?? (Number.isFinite(len) && len > 0 ? len : null);
        if (byteSize != null && byteSize > PREVIEW_MAX_BYTES) {
          setInfo({ name, size: byteSize });
          setStatus({ phase: 'toolarge' });
          return;
        }
        const mime = res.headers.get('content-type') || '';
        const text = await res.text();
        if (!alive) return;
        // Final guard for the (rare) chunked/no-Content-Length case.
        if (text.length > PREVIEW_MAX_BYTES) {
          setInfo({ name, size: byteSize });
          setStatus({ phase: 'toolarge' });
          return;
        }
        const kind = previewKind(name, mime);
        setInfo({ name, size: byteSize });
        if (!kind) {
          setStatus({ phase: 'error' });
          return;
        }
        setStatus({ phase: 'ready', kind, text });
      } catch {
        if (alive) setStatus({ phase: 'error' });
      }
    })();
    return () => {
      alive = false;
    };
  }, [target.url, target.name]);

  const copy = async () => {
    if (status.phase !== 'ready') return;
    if (await copyTextToClipboard(status.text)) {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    }
  };

  const ext = info.name.includes('.') ? (info.name.split('.').pop() || '').toUpperCase() : '';
  const metaLine = [ext || null, formatBytes(info.size) || null].filter(Boolean).join(' · ');

  let bodyNode: React.ReactNode;
  if (status.phase === 'loading') bodyNode = <div className="vr-fileview-msg">{t('chat.viewer.loading')}</div>;
  else if (status.phase === 'toolarge') bodyNode = <div className="vr-fileview-msg">{t('chat.viewer.tooLarge')}</div>;
  else if (status.phase === 'error') bodyNode = <div className="vr-fileview-msg">{t('chat.viewer.error')}</div>;
  else if (status.kind === 'markdown') bodyNode = <Markdown content={status.text} interactive={false} className="vr-fileview-md" />;
  else if (status.kind === 'json') bodyNode = <JsonBlock text={status.text} />;
  else if (status.kind === 'csv') bodyNode = <CsvTable text={status.text} />;
  else if (status.kind === 'code' || status.kind === 'source') bodyNode = <CodeBlock code={status.text} lang={codeLanguage(info.name)} />;
  else bodyNode = <pre className="vr-fileview-pre">{status.text}</pre>;

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      {/* Reuse the shared Dialog: overlay, focus-trap, scroll-lock, Escape,
          outside-click close, the built-in top-right close X, and the mobile
          bottom-sheet all come for free. ``pr-12`` on the header leaves room for
          that close X. */}
      <DialogContent
        aria-describedby={undefined}
        className="flex max-h-[85vh] w-full max-w-3xl flex-col gap-0 overflow-hidden p-0"
      >
        <div className="flex items-center gap-2 border-b border-border px-4 py-3 pr-12">
          <FileText className="size-4 shrink-0 text-muted" />
          <div className="min-w-0 flex-1">
            <DialogTitle className="truncate text-[13px] font-semibold text-foreground">
              {info.name || t('chat.media.preview')}
            </DialogTitle>
            {metaLine && <div className="font-mono text-[10px] text-muted">{metaLine}</div>}
          </div>
          {status.phase === 'ready' && (
            <Button variant="ghost" size="icon" className="size-8" onClick={copy} aria-label={t('common.copy')}>
              {copied ? <Check className="size-4 text-mint" /> : <Copy className="size-4" />}
            </Button>
          )}
          <Button asChild variant="ghost" size="icon" className="size-8 text-mint" aria-label={t('chat.media.download')}>
            <a
              href={`${target.url}?download=1`}
              download
              onClick={(e) => handleMediaDownloadClick(e, target.url, info.name || undefined)}
            >
              <Download className="size-4" />
            </a>
          </Button>
        </div>
        <div className="vr-fileview-body min-h-0 flex-1 overflow-auto">{bodyNode}</div>
      </DialogContent>
    </Dialog>
  );
}
