import React, { useEffect, useState, useCallback, useRef } from 'react';
import { Folder, FolderOpen, ChevronRight, ArrowUp, X, Check, RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useApi } from '../../context/ApiContext';
import clsx from 'clsx';

interface DirectoryBrowserProps {
  /** Initial path to show when opening */
  initialPath?: string;
  /** Called when user confirms selection */
  onSelect: (path: string) => void;
  /** Called when user cancels / closes */
  onClose: () => void;
}

export const DirectoryBrowser: React.FC<DirectoryBrowserProps> = ({
  initialPath,
  onSelect,
  onClose,
}) => {
  const { t } = useTranslation();
  const api = useApi();

  const [currentPath, setCurrentPath] = useState('');
  const [parent, setParent] = useState<string | null>(null);
  const [dirs, setDirs] = useState<{ name: string; path: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Guard against setState after unmount / stale responses
  const mountedRef = useRef(true);
  const reqIdRef = useRef(0);

  useEffect(() => {
    return () => { mountedRef.current = false; };
  }, []);

  // Esc key to close
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const browse = useCallback(
    async (path: string) => {
      const id = ++reqIdRef.current;
      setLoading(true);
      setError(null);
      try {
        const result = await api.browseDirectory(path);
        if (!mountedRef.current || reqIdRef.current !== id) return;
        if (result.ok) {
          setCurrentPath(result.path ?? path);
          setParent(result.parent ?? null);
          setDirs(result.dirs ?? []);
        } else {
          setError(result.error ?? 'Unknown error');
        }
      } catch (e: any) {
        if (!mountedRef.current || reqIdRef.current !== id) return;
        setError(e.message ?? String(e));
      } finally {
        if (mountedRef.current && reqIdRef.current === id) {
          setLoading(false);
        }
      }
    },
    [api],
  );

  useEffect(() => {
    browse(initialPath || '~');
  }, []);

  // Build breadcrumb segments from currentPath
  const breadcrumbs = currentPath
    ? currentPath.split('/').reduce<{ label: string; path: string }[]>((acc, seg, i) => {
        if (i === 0 && seg === '') {
          acc.push({ label: '/', path: '/' });
        } else if (seg) {
          const prev = acc.length ? acc[acc.length - 1].path : '';
          const full = prev === '/' ? `/${seg}` : `${prev}/${seg}`;
          acc.push({ label: seg, path: full });
        }
        return acc;
      }, [])
    : [];

  const canConfirm = !!currentPath && !loading && !error;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      role="dialog"
      aria-modal="true"
      aria-label={t('directoryBrowser.title')}
      onClick={onClose}
    >
      <div
        className="bg-panel border border-border rounded-xl shadow-xl w-full max-w-lg max-h-[70vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <h3 className="font-semibold text-text text-sm">{t('directoryBrowser.title')}</h3>
          <button onClick={onClose} className="text-muted hover:text-text transition-colors">
            <X size={16} />
          </button>
        </div>

        {/* Breadcrumb */}
        <div className="px-4 py-2 border-b border-border flex items-center gap-1 text-xs overflow-x-auto min-h-[32px]">
          {breadcrumbs.map((crumb, i) => (
            <React.Fragment key={crumb.path}>
              {i > 0 && <ChevronRight size={10} className="text-muted shrink-0" />}
              <button
                onClick={() => browse(crumb.path)}
                className={clsx(
                  'shrink-0 px-1 py-0.5 rounded hover:bg-neutral-100 transition-colors font-mono',
                  i === breadcrumbs.length - 1 ? 'text-accent font-medium' : 'text-muted',
                )}
              >
                {crumb.label}
              </button>
            </React.Fragment>
          ))}
          {loading && <RefreshCw size={12} className="animate-spin text-muted ml-auto shrink-0" />}
        </div>

        {/* Directory list */}
        <div className="flex-1 overflow-y-auto px-2 py-1 min-h-[200px]">
          {error && (
            <div className="px-3 py-2 text-sm text-danger">{error}</div>
          )}

          {/* Parent directory */}
          {parent && (
            <button
              onClick={() => browse(parent)}
              className="flex items-center gap-2 w-full px-3 py-1.5 rounded-lg hover:bg-neutral-100 transition-colors text-sm text-muted"
            >
              <ArrowUp size={14} />
              <span>..</span>
            </button>
          )}

          {/* Sub-directories */}
          {dirs.map((dir) => (
            <button
              key={dir.path}
              onClick={() => browse(dir.path)}
              className="flex items-center gap-2 w-full px-3 py-1.5 rounded-lg hover:bg-neutral-100 transition-colors text-sm text-text group"
            >
              <Folder size={14} className="text-amber-500 group-hover:hidden shrink-0" />
              <FolderOpen size={14} className="text-amber-500 hidden group-hover:block shrink-0" />
              <span className="truncate text-left">{dir.name}</span>
            </button>
          ))}

          {!loading && !error && dirs.length === 0 && (
            <div className="px-3 py-4 text-sm text-muted text-center italic">
              {t('directoryBrowser.empty')}
            </div>
          )}
        </div>

        {/* Footer — current path + confirm */}
        <div className="px-4 py-3 border-t border-border flex items-center gap-2">
          <code className="flex-1 text-xs font-mono text-text bg-bg border border-border rounded px-2 py-1.5 truncate">
            {currentPath || '—'}
          </code>
          <button
            onClick={() => canConfirm && onSelect(currentPath)}
            disabled={!canConfirm}
            className={clsx(
              'flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-sm font-medium transition-colors shrink-0',
              canConfirm
                ? 'bg-accent hover:bg-accent/90 text-white'
                : 'bg-neutral-200 text-muted cursor-not-allowed',
            )}
          >
            <Check size={14} />
            {t('directoryBrowser.select')}
          </button>
        </div>
      </div>
    </div>
  );
};
