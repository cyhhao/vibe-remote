import { createHighlighterCore, type HighlighterCore } from 'shiki/core';
import { createJavaScriptRegexEngine } from 'shiki/engine/javascript';

// Lean, lazy code highlighter for the FileViewer. Only imported by the
// (already lazy) viewer modal, so neither Shiki nor any grammar reaches the main
// app bundle. Uses the JavaScript RegExp engine (no WASM asset to serve) and
// loads each theme + grammar on demand via dynamic import — Vite code-splits
// them per language, so we only ship the languages a user actually opens.
//
// ``forgiving`` lets the JS engine skip the occasional Oniguruma-only regex in a
// grammar instead of throwing, degrading to partial highlight rather than error.

let corePromise: Promise<HighlighterCore> | null = null;
const loadedLangs = new Set<string>();
const loadedThemes = new Set<string>();

type ShikiTheme = 'github-dark' | 'github-light';

// Lang id (matches filePreview's CODE_LANG / SOURCE_LANG / NAME_LANG values) →
// dynamic grammar import.
const LANG_LOADERS: Record<string, () => Promise<unknown>> = {
  typescript: () => import('@shikijs/langs/typescript'),
  tsx: () => import('@shikijs/langs/tsx'),
  javascript: () => import('@shikijs/langs/javascript'),
  jsx: () => import('@shikijs/langs/jsx'),
  python: () => import('@shikijs/langs/python'),
  ruby: () => import('@shikijs/langs/ruby'),
  go: () => import('@shikijs/langs/go'),
  rust: () => import('@shikijs/langs/rust'),
  java: () => import('@shikijs/langs/java'),
  kotlin: () => import('@shikijs/langs/kotlin'),
  swift: () => import('@shikijs/langs/swift'),
  c: () => import('@shikijs/langs/c'),
  cpp: () => import('@shikijs/langs/cpp'),
  csharp: () => import('@shikijs/langs/csharp'),
  php: () => import('@shikijs/langs/php'),
  bash: () => import('@shikijs/langs/bash'),
  sql: () => import('@shikijs/langs/sql'),
  css: () => import('@shikijs/langs/css'),
  scss: () => import('@shikijs/langs/scss'),
  less: () => import('@shikijs/langs/less'),
  lua: () => import('@shikijs/langs/lua'),
  r: () => import('@shikijs/langs/r'),
  dart: () => import('@shikijs/langs/dart'),
  scala: () => import('@shikijs/langs/scala'),
  perl: () => import('@shikijs/langs/perl'),
  diff: () => import('@shikijs/langs/diff'),
  yaml: () => import('@shikijs/langs/yaml'),
  toml: () => import('@shikijs/langs/toml'),
  ini: () => import('@shikijs/langs/ini'),
  html: () => import('@shikijs/langs/html'),
  xml: () => import('@shikijs/langs/xml'),
  vue: () => import('@shikijs/langs/vue'),
  svelte: () => import('@shikijs/langs/svelte'),
  docker: () => import('@shikijs/langs/docker'),
  make: () => import('@shikijs/langs/make'),
  json: () => import('@shikijs/langs/json'),
};

const THEME_LOADERS: Record<ShikiTheme, () => Promise<unknown>> = {
  'github-dark': () => import('@shikijs/themes/github-dark'),
  'github-light': () => import('@shikijs/themes/github-light'),
};

async function getCore(): Promise<HighlighterCore> {
  if (!corePromise) {
    // Don't cache a rejected init promise — a one-off failure (e.g. a chunk-load
    // error after a deploy) would otherwise disable highlighting for the whole
    // session. On reject, clear the cache so the next call retries.
    corePromise = createHighlighterCore({ engine: createJavaScriptRegexEngine({ forgiving: true }) }).catch((err) => {
      corePromise = null;
      throw err;
    });
  }
  return corePromise;
}

// Highlight ``code`` for ``langId`` using a single theme picked from the current
// app mode; returns Shiki's HTML (the code text is escaped, so it's safe to
// inject). Falls back to plain (escaped) text for unknown languages.
export async function highlightCode(code: string, langId: string, theme: ShikiTheme): Promise<string> {
  const hl = await getCore();

  if (!loadedThemes.has(theme)) {
    await hl.loadTheme(THEME_LOADERS[theme]() as Parameters<HighlighterCore['loadTheme']>[0]);
    loadedThemes.add(theme);
  }

  let lang = 'text';
  const loader = LANG_LOADERS[langId];
  if (loader) {
    try {
      if (!loadedLangs.has(langId)) {
        await hl.loadLanguage(loader() as Parameters<HighlighterCore['loadLanguage']>[0]);
        loadedLangs.add(langId);
      }
      lang = langId;
    } catch {
      lang = 'text'; // bad/unsupported grammar → render as plain escaped text
    }
  }

  return hl.codeToHtml(code, { lang, theme });
}
