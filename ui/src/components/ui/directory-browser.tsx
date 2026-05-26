import React, { useEffect, useState, useCallback, useRef } from 'react';
import {
  Check,
  ChevronLeft,
  ChevronRight,
  Download,
  Eye,
  EyeOff,
  FileText,
  Folder,
  FolderOpen,
  FolderPlus,
  Home,
  Monitor,
  RefreshCw,
  X,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';

interface DirectoryBrowserProps {
  /** Initial path to show when opening */
  initialPath?: string;
  /** Called when user confirms selection */
  onSelect: (path: string) => void;
  /** Called when user cancels / closes */
  onClose: () => void;
}

// Mirrors design.pen y5cQ5 — macOS Finder-style folder picker: traffic
// lights + toolbar (history nav + breadcrumb + show-hidden + new-folder)
// + favorites sidebar + folder list + footer (path + cancel + select).
export const DirectoryBrowser: React.FC<DirectoryBrowserProps> = ({
  initialPath,
  onSelect,
  onClose,
}) => {
  const { t } = useTranslation();
  const api = useApi();

  const [currentPath, setCurrentPath] = useState('');
  // Resolved user home — captured on the first browse('~') response so the
  // breadcrumb collapse to ⌂ has a real prefix to match against. Falls back
  // to '' (no collapse) if the user opened the picker with an absolute path.
  const [homePath, setHomePath] = useState<string>('');
  const [parent, setParent] = useState<string | null>(null);
  const [dirs, setDirs] = useState<{ name: string; path: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  // Linear history so the < / > arrows behave like a real file browser
  // instead of just walking the directory tree.
  const [history, setHistory] = useState<string[]>([]);
  const [historyIndex, setHistoryIndex] = useState(-1);
  // Inline "create new folder" prompt — shown at the end of the list so
  // it feels like a continuation of the directory rather than a modal.
  const [creating, setCreating] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [createError, setCreateError] = useState<string | null>(null);
  const newFolderInputRef = useRef<HTMLInputElement | null>(null);

  const mountedRef = useRef(true);
  const reqIdRef = useRef(0);

  useEffect(() => {
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Esc closes the picker, ⌘N opens the new-folder prompt — both familiar
  // shortcuts from Finder.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (creating) {
          setCreating(false);
          setNewFolderName('');
          setCreateError(null);
        } else {
          onClose();
        }
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'n' && !creating) {
        e.preventDefault();
        setCreating(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [creating, onClose]);

  useEffect(() => {
    if (creating) {
      newFolderInputRef.current?.focus();
    }
  }, [creating]);

  const fetchPath = useCallback(
    async (path: string, hidden?: boolean) => {
      const id = ++reqIdRef.current;
      setLoading(true);
      setError(null);
      const useHidden = hidden ?? showHidden;
      try {
        const result = await api.browseDirectory(path, useHidden);
        if (!mountedRef.current || reqIdRef.current !== id) {
          return null;
        }
        if (result.ok) {
          const resolvedPath = result.path ?? path;
          setCurrentPath(resolvedPath);
          setParent(result.parent ?? null);
          setDirs(result.dirs ?? []);
          // Snapshot the resolved user home the first time the backend
          // expands a tilde-prefixed path. Backend behavior: passing "~"
          // returns the home directory in result.path; passing "~/x"
          // returns "<home>/x". We derive home by trimming the requested
          // suffix off the resolved path.
          if (!homePath && path.startsWith('~')) {
            const suffix = path === '~' ? '' : path.slice(1);
            const derived = suffix && resolvedPath.endsWith(suffix)
              ? resolvedPath.slice(0, resolvedPath.length - suffix.length)
              : resolvedPath;
            const cleaned = derived.replace(/\/+$/, '');
            if (cleaned) setHomePath(cleaned);
          }
          return resolvedPath;
        }
        setError(result.error ?? 'Unknown error');
        return null;
      } catch (e: any) {
        if (!mountedRef.current || reqIdRef.current !== id) return null;
        setError(e.message ?? String(e));
        return null;
      } finally {
        if (mountedRef.current && reqIdRef.current === id) {
          setLoading(false);
        }
      }
    },
    [api, showHidden, homePath],
  );

  const navigate = useCallback(
    async (path: string, hidden?: boolean) => {
      // Reset the inline new-folder state whenever the user moves around —
      // a half-typed name shouldn't survive navigating away.
      setCreating(false);
      setNewFolderName('');
      setCreateError(null);
      const resolved = await fetchPath(path, hidden);
      if (resolved) {
        setHistory((prev) => [...prev.slice(0, historyIndex + 1), resolved]);
        setHistoryIndex((prev) => prev + 1);
      }
    },
    [fetchPath, historyIndex],
  );

  const goBack = useCallback(() => {
    if (historyIndex <= 0) return;
    const next = historyIndex - 1;
    setHistoryIndex(next);
    fetchPath(history[next]);
  }, [history, historyIndex, fetchPath]);

  const goForward = useCallback(() => {
    if (historyIndex >= history.length - 1) return;
    const next = historyIndex + 1;
    setHistoryIndex(next);
    fetchPath(history[next]);
  }, [history, historyIndex, fetchPath]);

  useEffect(() => {
    navigate(initialPath || '~');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleHidden = () => {
    const next = !showHidden;
    setShowHidden(next);
    fetchPath(currentPath, next);
  };

  const submitNewFolder = async () => {
    const name = newFolderName.trim();
    if (!name) return;
    setCreateError(null);
    try {
      const target = currentPath ? `${currentPath.replace(/\/$/, '')}/${name}` : name;
      await api.browseMkdir(target);
      setCreating(false);
      setNewFolderName('');
      await fetchPath(currentPath);
    } catch (err: any) {
      const message: string = err?.message ?? String(err);
      if (/exists/i.test(message)) {
        setCreateError(t('directoryBrowser.newFolderExists'));
      } else {
        setCreateError(message);
      }
    }
  };

  // Breadcrumb: collapse the home prefix to ⌂ so deeply-nested paths still fit
  // in the toolbar without scrolling. ``homePath`` is captured on the first
  // tilde-expanded browse response (see fetchPath).
  const breadcrumbs = (() => {
    if (!currentPath) return [] as { label: string; path: string; isHome?: boolean }[];
    const startsAtHome = !!homePath && currentPath.startsWith(homePath);
    const segments = (startsAtHome ? currentPath.slice(homePath.length) : currentPath)
      .split('/')
      .filter(Boolean);
    const out: { label: string; path: string; isHome?: boolean }[] = [];
    if (startsAtHome) {
      out.push({ label: '⌂', path: homePath, isHome: true });
    } else {
      out.push({ label: '/', path: '/' });
    }
    let acc = startsAtHome ? homePath : '';
    for (const seg of segments) {
      acc = acc === '/' || acc === '' ? `${acc}${seg}` : `${acc}/${seg}`;
      if (!acc.startsWith('/')) acc = `/${acc}`;
      out.push({ label: seg, path: acc });
    }
    return out;
  })();

  const canConfirm = !!currentPath && !loading && !error;
  const canBack = historyIndex > 0;
  const canForward = historyIndex < history.length - 1;

  // Static shortcuts mirroring Finder's Favorites column. We just hand
  // these to the existing `browseDirectory` endpoint — the backend
  // expands ``~`` so we don't need to resolve them client-side.
  const favorites: { i18nKey: string; path: string; icon: React.ReactNode }[] = [
    { i18nKey: 'directoryBrowser.favoritesHome', path: '~', icon: <Home className="size-3.5" /> },
    { i18nKey: 'directoryBrowser.favoritesDesktop', path: '~/Desktop', icon: <Monitor className="size-3.5" /> },
    { i18nKey: 'directoryBrowser.favoritesDocuments', path: '~/Documents', icon: <FileText className="size-3.5" /> },
    { i18nKey: 'directoryBrowser.favoritesDownloads', path: '~/Downloads', icon: <Download className="size-3.5" /> },
  ];

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
      role="dialog"
      aria-modal="true"
      aria-label={t('directoryBrowser.title')}
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-border-strong bg-surface shadow-[0_24px_64px_-12px_rgba(0,0,0,0.6)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Traffic-light header */}
        <div className="flex items-center gap-3 border-b border-border bg-surface-2 px-4 py-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              aria-label="Close"
              onClick={onClose}
              className="size-3 rounded-full bg-[#FF5F57] transition hover:brightness-110"
            />
            <span className="size-3 rounded-full bg-[#FFBD2E]" />
            <span className="size-3 rounded-full bg-[#28C840]" />
          </div>
          <div className="flex flex-1 items-center justify-center gap-2 text-[13px] font-semibold text-foreground">
            <FolderOpen className="size-3.5 text-mint" />
            {t('directoryBrowser.title')}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t('directoryBrowser.cancel')}
            className="text-muted transition hover:text-foreground"
          >
            <X className="size-4" />
          </button>
        </div>

        {/* Toolbar — history arrows + breadcrumb + show-hidden + new-folder */}
        <div className="flex items-center gap-2 border-b border-border px-4 py-2.5">
          <button
            type="button"
            onClick={goBack}
            disabled={!canBack}
            aria-label={t('directoryBrowser.back')}
            className={clsx(
              'flex size-7 items-center justify-center rounded-md border border-border-strong transition',
              canBack ? 'text-foreground hover:bg-foreground/[0.06]' : 'cursor-not-allowed text-muted opacity-50',
            )}
          >
            <ChevronLeft className="size-3.5" />
          </button>
          <button
            type="button"
            onClick={goForward}
            disabled={!canForward}
            aria-label={t('directoryBrowser.forward')}
            className={clsx(
              'flex size-7 items-center justify-center rounded-md border border-border-strong transition',
              canForward ? 'text-foreground hover:bg-foreground/[0.06]' : 'cursor-not-allowed text-muted opacity-50',
            )}
          >
            <ChevronRight className="size-3.5" />
          </button>

          <div className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto rounded-md border border-border-strong bg-surface-2 px-2 py-1 font-mono text-[11px]">
            {breadcrumbs.map((crumb, i) => (
              <React.Fragment key={`${crumb.path}-${i}`}>
                {i > 0 && <ChevronRight className="size-3 shrink-0 text-muted" />}
                <button
                  type="button"
                  onClick={() => navigate(crumb.path)}
                  className={clsx(
                    'shrink-0 rounded px-1 py-0.5 transition hover:bg-foreground/[0.04]',
                    i === breadcrumbs.length - 1 ? 'font-semibold text-cyan' : 'text-muted',
                  )}
                >
                  {crumb.label}
                </button>
              </React.Fragment>
            ))}
            {loading && <RefreshCw className="ml-auto size-3 shrink-0 animate-spin text-muted" />}
          </div>

          <button
            type="button"
            onClick={toggleHidden}
            className={clsx(
              'flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[11px] font-semibold transition',
              showHidden
                ? 'border-cyan/40 bg-cyan/[0.08] text-cyan'
                : 'border-border-strong text-muted hover:text-foreground',
            )}
          >
            {showHidden ? <Eye className="size-3" /> : <EyeOff className="size-3" />}
            {showHidden ? t('directoryBrowser.showHidden') : t('directoryBrowser.hideHidden')}
          </button>

          <button
            type="button"
            onClick={() => {
              setCreating(true);
              setNewFolderName('');
              setCreateError(null);
            }}
            className="flex items-center gap-1.5 rounded-md border border-border-strong px-2.5 py-1.5 text-[11px] font-medium text-foreground transition hover:bg-foreground/[0.04]"
          >
            <FolderPlus className="size-3" />
            {t('directoryBrowser.newFolder')}
          </button>
        </div>

        {/* Body — favorites sidebar + folder list */}
        <div className="flex min-h-0 flex-1">
          {/* Favorites sidebar */}
          <aside className="hidden w-[180px] shrink-0 flex-col gap-1 border-r border-border bg-surface-2 px-2 py-3 sm:flex">
            <div className="px-2 pb-1 font-mono text-[9px] font-bold uppercase tracking-[0.16em] text-muted">
              {t('directoryBrowser.favorites')}
            </div>
            {favorites.map((fav) => (
              <button
                key={fav.path}
                type="button"
                onClick={() => navigate(fav.path)}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12px] text-foreground transition hover:bg-foreground/[0.04]"
              >
                <span className="text-muted">{fav.icon}</span>
                <span className="truncate">{t(fav.i18nKey)}</span>
              </button>
            ))}
          </aside>

          {/* Folder list */}
          <div className="flex min-w-0 flex-1 flex-col overflow-y-auto px-2 py-2">
            {error && (
              <div className="mx-2 mb-2 rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-sm text-destructive">
                {error}
              </div>
            )}

            {parent && (
              <button
                onClick={() => navigate(parent)}
                className="flex items-center gap-2.5 rounded-md px-3 py-2 text-[13px] text-muted transition hover:bg-foreground/[0.04]"
              >
                <ChevronLeft className="size-3.5" />
                <span className="font-mono">..</span>
              </button>
            )}

            {dirs.map((dir) => (
              <button
                key={dir.path}
                onClick={() => navigate(dir.path)}
                className="group flex items-center gap-2.5 rounded-md px-3 py-2 text-left text-[13px] text-foreground transition hover:bg-foreground/[0.04]"
              >
                <Folder className="size-4 shrink-0 text-gold group-hover:hidden" />
                <FolderOpen className="hidden size-4 shrink-0 text-gold group-hover:block" />
                <span className="truncate">{dir.name}</span>
              </button>
            ))}

            {!loading && !error && dirs.length === 0 && !creating && (
              <div className="px-3 py-6 text-center text-[12px] italic text-muted">
                {t('directoryBrowser.empty')}
              </div>
            )}

            {/* Inline new-folder prompt — sits at the bottom of the list so
                the user keeps context of which directory they're creating in */}
            {creating ? (
              <div className="mt-2 flex flex-col gap-1.5 rounded-md border border-dashed border-border-strong bg-foreground/[0.03] px-3 py-2.5">
                <div className="flex items-center gap-2">
                  <FolderPlus className="size-4 shrink-0 text-mint" />
                  <input
                    ref={newFolderInputRef}
                    value={newFolderName}
                    onChange={(e) => setNewFolderName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') submitNewFolder();
                    }}
                    placeholder={t('directoryBrowser.newFolderPlaceholder')}
                    className="flex-1 bg-transparent text-[13px] text-foreground outline-none placeholder:text-muted"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      setCreating(false);
                      setNewFolderName('');
                      setCreateError(null);
                    }}
                    className="rounded-md px-2 py-0.5 text-[11px] text-muted transition hover:text-foreground"
                  >
                    {t('directoryBrowser.cancel')}
                  </button>
                  <button
                    type="button"
                    onClick={submitNewFolder}
                    disabled={!newFolderName.trim()}
                    className={clsx(
                      'rounded-md px-2.5 py-0.5 text-[11px] font-semibold transition',
                      newFolderName.trim()
                        ? 'bg-mint text-[#080812] hover:brightness-110'
                        : 'bg-muted-soft text-muted',
                    )}
                  >
                    {t('directoryBrowser.newFolder')}
                  </button>
                </div>
                {createError && <div className="pl-6 text-[11px] text-destructive">{createError}</div>}
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setCreating(true)}
                className="mt-2 flex items-center gap-2.5 rounded-md border border-dashed border-border-strong bg-foreground/[0.02] px-3 py-2 text-[12px] italic text-muted transition hover:bg-foreground/[0.04]"
              >
                <FolderPlus className="size-4 shrink-0" />
                {t('directoryBrowser.createHere')}
              </button>
            )}
          </div>
        </div>

        {/* Footer — current path + cancel + select */}
        <div className="flex items-center gap-3 border-t border-border bg-surface-2 px-4 py-3">
          <code className="flex-1 truncate rounded-md border border-border-strong bg-surface-3 px-2.5 py-1.5 font-mono text-[11px] text-foreground">
            {currentPath || '—'}
          </code>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border-strong px-4 py-1.5 text-[12px] font-medium text-foreground transition hover:bg-foreground/[0.04]"
          >
            {t('directoryBrowser.cancel')}
          </button>
          <button
            type="button"
            onClick={() => canConfirm && onSelect(currentPath)}
            disabled={!canConfirm}
            className={clsx(
              'flex items-center gap-1.5 rounded-md px-4 py-1.5 text-[12px] font-semibold transition',
              canConfirm
                ? 'bg-mint text-[#080812] shadow-[0_0_14px_-4px_rgba(91,255,160,0.6)] hover:brightness-110'
                : 'cursor-not-allowed bg-muted-soft text-muted',
            )}
          >
            <Check className="size-3.5" />
            {t('directoryBrowser.select')}
          </button>
        </div>
      </div>
    </div>
  );
};
