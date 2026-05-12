import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Play, Zap } from 'lucide-react';
import clsx from 'clsx';

import { Button } from '../ui/button';
import { useApi } from '@/context/ApiContext';
import type { BackendAuthTestResult } from '@/context/ApiContext';
import { useToast } from '@/context/ToastContext';

type Backend = 'claude' | 'codex';

export type BackendTestPanelProps = {
  backend: Backend;
};

/**
 * Settings → Backends connectivity probe. Mirrors the ``cdTest2`` /
 * ``cxTest`` panels in ``design.pen``: sends a single ``Hi`` prompt
 * through the backend CLI so the user can confirm both the credentials
 * and the endpoint (Base URL) round-trip end-to-end. Works in both
 * OAuth and API-Key modes — the underlying CLI uses whichever auth
 * source is configured at launch.
 */
export const BackendTestPanel: React.FC<BackendTestPanelProps> = ({ backend }) => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [testing, setTesting] = useState(false);
  const [lastResult, setLastResult] = useState<BackendAuthTestResult | null>(null);

  const runTest = async () => {
    setTesting(true);
    try {
      const result = await api.testBackendAuth(backend);
      setLastResult(result);
      if (result.ok) {
        showToast(
          t('settings.backends.testConnectionSuccessToast', { ms: result.duration_ms ?? '?' }),
          'success',
        );
      } else {
        showToast(
          t('settings.backends.testConnectionFailedToast', {
            detail: result.error || result.detail || 'unknown',
          }),
          'error',
        );
      }
    } catch (err: any) {
      const fallback = { ok: false, error: err?.message || 'test_failed' } as BackendAuthTestResult;
      setLastResult(fallback);
      showToast(t('settings.backends.testConnectionFailedToast', { detail: fallback.error }), 'error');
    } finally {
      setTesting(false);
    }
  };

  const resultLine = (() => {
    if (!lastResult) return null;
    if (lastResult.ok) {
      return t('settings.backends.testConnectionLastOk', {
        ms: lastResult.duration_ms ?? '?',
      });
    }
    const detail = lastResult.error || lastResult.detail || 'unknown';
    return t('settings.backends.testConnectionLastFail', { detail });
  })();

  return (
    <div
      className={clsx(
        'flex items-center justify-between gap-4 rounded-xl border border-border bg-foreground/[0.025] p-5',
      )}
    >
      <div className="flex items-center gap-3">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-[10px] border border-mint/30 bg-mint-soft">
          <Zap size={18} className="text-mint" />
        </div>
        <div className="flex flex-col gap-0.5">
          <p className="text-[14px] font-bold text-foreground">
            {t('settings.backends.testConnectionTitle')}
          </p>
          <p className="text-[12px] text-muted">
            {t('settings.backends.testConnectionSubtitle')}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3">
        {resultLine && (
          <span
            className={clsx(
              'font-mono text-[11px] font-semibold',
              lastResult?.ok ? 'text-mint' : 'text-destructive',
            )}
          >
            {resultLine}
          </span>
        )}
        <Button
          type="button"
          variant="brand"
          size="sm"
          onClick={() => void runTest()}
          disabled={testing}
        >
          <Play className="size-3" />
          {testing ? t('common.testing') : t('settings.backends.testConnectionRun')}
        </Button>
      </div>
    </div>
  );
};
