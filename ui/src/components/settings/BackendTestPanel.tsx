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

  // Map the backend's structured error codes to UI sentences. Anything
  // we don't recognise falls back to the generic ``cli_failed`` line,
  // and the raw ``detail`` is still surfaced in the toast for inspection.
  const failureSentence = (result: BackendAuthTestResult): string => {
    const detail = (result.detail || '').trim();
    const code = (result.error || '').trim();
    const map: Record<string, string> = {
      invalid_credentials: 'settings.backends.testFailureInvalidCredentials',
      forbidden: 'settings.backends.testFailureForbidden',
      model_not_found: 'settings.backends.testFailureModelNotFound',
      rate_limited: 'settings.backends.testFailureRateLimited',
      endpoint_unreachable: 'settings.backends.testFailureEndpointUnreachable',
      server_error: 'settings.backends.testFailureServerError',
      trust_check_failed: 'settings.backends.testFailureTrustCheck',
      cli_not_found: 'settings.backends.testFailureCliNotFound',
      spawn_failed: 'settings.backends.testFailureSpawnFailed',
      timed_out: 'settings.backends.testFailureTimedOut',
      cli_failed: 'settings.backends.testFailureCliFailed',
    };
    const key = map[code];
    if (key) {
      return t(key, { detail: detail || code });
    }
    return t('settings.backends.testConnectionFailedToast', {
      detail: detail || code || 'unknown',
    });
  };

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
        showToast(failureSentence(result), 'error');
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
    return t('settings.backends.testConnectionLastFail', {
      detail: failureSentence(lastResult),
    });
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
