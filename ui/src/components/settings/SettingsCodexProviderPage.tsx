import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, Info, KeyRound, RotateCcw, Save } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { useApi } from '@/context/ApiContext';
import type { CodexAuthMode, CodexAuthState } from '@/context/ApiContext';
import { useToast } from '@/context/ToastContext';
import { SettingsPageShell } from './SettingsPageShell';

// Mirrors the segmented-radio pattern in shared/RoutingConfigPanel.tsx so
// Codex's auth-mode toggle reads like the rest of the Settings surface.
const SegmentedRadio: React.FC<{
  value: CodexAuthMode;
  onChange: (next: CodexAuthMode) => void;
  options: ReadonlyArray<{ id: CodexAuthMode; label: string }>;
  ariaLabel: string;
}> = ({ value, onChange, options, ariaLabel }) => (
  <div
    role="radiogroup"
    aria-label={ariaLabel}
    className="flex h-9 items-stretch gap-0.5 rounded-md border border-border bg-foreground/[0.03] p-0.5"
  >
    {options.map((opt) => {
      const active = value === opt.id;
      return (
        <button
          key={opt.id}
          type="button"
          role="radio"
          aria-checked={active}
          onClick={() => onChange(opt.id)}
          className={clsx(
            'flex-1 rounded-[4px] px-3 text-[12px] transition-colors',
            active
              ? 'border border-mint/30 bg-mint-soft font-bold text-mint'
              : 'font-medium text-muted hover:text-foreground'
          )}
        >
          {opt.label}
        </button>
      );
    })}
  </div>
);

export const SettingsCodexProviderPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();

  const [state, setState] = useState<CodexAuthState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [authMode, setAuthMode] = useState<CodexAuthMode>('oauth');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');

  useEffect(() => {
    let cancelled = false;
    api
      .getCodexAuth()
      .then((data) => {
        if (cancelled) return;
        setState(data);
        setAuthMode(data.auth_mode);
        setBaseUrl(data.base_url || '');
        // Never preload the api_key field: the server only returns its
        // length, and an empty input here is interpreted as "keep what's
        // stored" on save unless the user types a fresh value.
        setApiKey('');
      })
      .catch(() => {
        // Errors are already surfaced via ToastContext by ApiContext;
        // leave the page on the default oauth state so the user can still
        // make a choice rather than seeing a broken UI.
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  const modeOptions = useMemo(
    () =>
      [
        { id: 'oauth' as const, label: t('settings.backends.codexAuthModeOauth') },
        { id: 'api_key' as const, label: t('settings.backends.codexAuthModeApiKey') },
      ] as const,
    [t]
  );

  const apiKeyStatus = state?.has_api_key
    ? t('settings.backends.codexApiKeyConfigured', { length: state.api_key_length })
    : t('settings.backends.codexApiKeyMissing');

  const onSave = async () => {
    setSaving(true);
    try {
      const payload = {
        auth_mode: authMode,
        // Send a fresh key only when the user typed one; an empty string
        // lets the server reuse the stored key (useful when the user is
        // just updating the base URL).
        api_key: authMode === 'api_key' ? (apiKey || undefined) : null,
        base_url: baseUrl.trim() || null,
      };
      const result = await api.saveCodexAuth(payload);
      if (result.ok === false) {
        // The server returns ok:false for validation/persist failures
        // (e.g. missing api_key when auth_mode is "api_key") with HTTP 200,
        // so we must not advance into the success branch here — applying
        // ``result`` into state would overwrite the user's in-progress
        // edits with a malformed response that omits auth_mode / base_url.
        showToast(result.message || t('settings.backends.codexSaveFailed'), 'error');
        return;
      }
      setState(result);
      setAuthMode(result.auth_mode);
      setBaseUrl(result.base_url || '');
      setApiKey('');
      if (result.restart?.ok === false) {
        // Config saved, restart failed — make the partial success visible.
        showToast(result.restart.message || result.message || t('settings.backends.codexSaveSuccess'), 'warning');
      } else {
        showToast(t('settings.backends.codexSaveSuccess'), 'success');
      }
    } catch (err: any) {
      showToast(err?.message || t('settings.backends.codexSaveFailed'), 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsPageShell
      activeTab="backends"
      title={t('settings.backends.codexTitle')}
      subtitle={t('settings.backends.codexSubtitle')}
      breadcrumb={
        <Link to="/settings/backends" className="inline-flex items-center gap-1.5 hover:text-foreground">
          <ArrowLeft className="size-3" />
          {t('settings.backends.codexBackToBackends')}
        </Link>
      }
    >
      {loading ? (
        <div className="text-sm text-muted">{t('common.loading')}</div>
      ) : (
        <Card>
          <CardContent className="flex flex-col gap-5 p-6">
            <div className="flex flex-col gap-2">
              <Label className="text-xs font-medium uppercase text-muted">
                {t('settings.backends.codexAuthModeLabel')}
              </Label>
              <SegmentedRadio
                value={authMode}
                onChange={setAuthMode}
                options={modeOptions}
                ariaLabel={t('settings.backends.codexAuthModeLabel') as string}
              />
              <p className="text-[12px] leading-relaxed text-muted">
                {authMode === 'api_key'
                  ? t('settings.backends.codexAuthModeApiKeyHint')
                  : t('settings.backends.codexAuthModeOauthHint')}
              </p>
              {authMode === 'oauth' && state?.has_chatgpt_tokens && (
                <p className="text-[12px] text-mint">
                  {t('settings.backends.codexHasChatgptTokens')}
                </p>
              )}
            </div>

            {authMode === 'api_key' && (
              <div className="flex flex-col gap-2">
                <Label htmlFor="codex-api-key" className="text-xs font-medium uppercase text-muted">
                  {t('settings.backends.codexApiKeyLabel')}
                </Label>
                <div className="relative">
                  <KeyRound className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
                  <Input
                    id="codex-api-key"
                    type="password"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={t('settings.backends.codexApiKeyPlaceholder') as string}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    className="pl-9 font-mono"
                  />
                </div>
                <p className="text-[12px] text-muted">{apiKeyStatus}</p>
              </div>
            )}

            <div className="flex flex-col gap-2">
              <Label htmlFor="codex-base-url" className="text-xs font-medium uppercase text-muted">
                {t('settings.backends.codexBaseUrlLabel')}
              </Label>
              <div className="flex gap-2">
                <Input
                  id="codex-base-url"
                  type="url"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder={t('settings.backends.codexBaseUrlPlaceholder') as string}
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                  className="font-mono"
                />
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => setBaseUrl('')}
                  disabled={!baseUrl}
                >
                  <RotateCcw className="size-3.5" />
                  {t('settings.backends.codexBaseUrlReset')}
                </Button>
              </div>
              <p className="text-[12px] text-muted">{t('settings.backends.codexBaseUrlHint')}</p>
            </div>

            <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2/60 px-3 py-2.5">
              <Info className="mt-0.5 size-3.5 shrink-0 text-muted" />
              <p className="text-[12px] leading-relaxed text-muted">
                {t('settings.backends.codexInfoHint')}
              </p>
            </div>

            <div className="flex justify-end">
              <Button variant="brand" size="default" onClick={onSave} disabled={saving}>
                <Save className="size-3.5" />
                {saving ? t('settings.backends.codexSaving') : t('settings.backends.codexSave')}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </SettingsPageShell>
  );
};
