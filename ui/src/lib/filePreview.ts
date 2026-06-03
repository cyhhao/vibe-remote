// Single source of truth for "can we preview this file, and how". Used to gate
// the FileCard preview ("eye") icon and to pick the renderer + syntax-highlight
// language in the FileViewer. Keep the language ids here in sync with the
// grammar loaders in ``highlighter.ts``.

export type PreviewKind = 'markdown' | 'text' | 'json' | 'csv' | 'code' | 'source';

// Refuse to preview files larger than this (fetch the bytes into the page) —
// offer download instead. CSV is additionally capped by row count.
export const PREVIEW_MAX_BYTES = 1024 * 1024;
export const CSV_MAX_ROWS = 500;
// The interactive JSON tree mounts every node into the DOM (collapsed only sets
// the visual state), so above this size we render highlighted JSON source
// instead to avoid freezing the main thread.
export const JSON_TREE_MAX_BYTES = 256 * 1024;

const MARKDOWN_EXT = new Set(['md', 'markdown', 'mdx', 'mkd', 'mdown']);
const TEXT_EXT = new Set(['txt', 'text', 'log']);
const CSV_EXT = new Set(['csv', 'tsv']);

// Markup rendered as highlighted SOURCE only — never executed (XSS / the server
// already forces these to attachment).
const SOURCE_LANG: Record<string, string> = {
  html: 'html', htm: 'html', xml: 'xml', svg: 'xml', vue: 'vue', svelte: 'svelte',
};

// ext (lowercase, no dot) → Shiki language id.
const CODE_LANG: Record<string, string> = {
  ts: 'typescript', mts: 'typescript', cts: 'typescript', tsx: 'tsx',
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'jsx',
  py: 'python', pyi: 'python', rb: 'ruby', go: 'go', rs: 'rust',
  java: 'java', kt: 'kotlin', kts: 'kotlin', swift: 'swift',
  c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', cxx: 'cpp', hpp: 'cpp', hh: 'cpp',
  cs: 'csharp', php: 'php',
  sh: 'bash', bash: 'bash', zsh: 'bash',
  sql: 'sql', css: 'css', scss: 'scss', sass: 'scss', less: 'less',
  lua: 'lua', r: 'r', dart: 'dart', scala: 'scala', sc: 'scala', pl: 'perl', pm: 'perl',
  diff: 'diff', patch: 'diff',
  yaml: 'yaml', yml: 'yaml', toml: 'toml',
  ini: 'ini', conf: 'ini', cfg: 'ini', properties: 'ini', env: 'ini',
};

// Files whose meaningful name has no useful extension.
const NAME_LANG: Record<string, string> = {
  dockerfile: 'docker', makefile: 'make', '.gitignore': 'ini', '.gitattributes': 'ini',
  '.env': 'ini', '.bashrc': 'bash', '.zshrc': 'bash', '.profile': 'bash',
};

function baseName(name: string): string {
  return (name || '').split(/[\\/]/).pop() || '';
}

function extOf(name: string): string {
  const b = baseName(name);
  const i = b.lastIndexOf('.');
  return i > 0 ? b.slice(i + 1).toLowerCase() : '';
}

export function previewKind(name: string, mime?: string | null): PreviewKind | null {
  const b = baseName(name).toLowerCase();
  const ext = extOf(name);
  const m = (mime || '').split(';')[0].trim().toLowerCase();

  if (MARKDOWN_EXT.has(ext)) return 'markdown';
  if (ext === 'json' || ext === 'jsonc' || ext === 'json5' || m === 'application/json') return 'json';
  if (CSV_EXT.has(ext) || m === 'text/csv' || m === 'text/tab-separated-values') return 'csv';
  if (ext in SOURCE_LANG) return 'source';
  if (ext in CODE_LANG || b in NAME_LANG) return 'code';
  if (TEXT_EXT.has(ext) || m.startsWith('text/')) return 'text';
  return null;
}

// Shiki language id for the 'code'/'source' kinds; 'text' (no highlight) otherwise.
export function codeLanguage(name: string): string {
  const b = baseName(name).toLowerCase();
  const ext = extOf(name);
  return SOURCE_LANG[ext] || CODE_LANG[ext] || NAME_LANG[b] || 'text';
}

// Human byte size. Shared with FileCard so the card label and the viewer header
// format sizes identically.
export function formatBytes(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return `${value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}
