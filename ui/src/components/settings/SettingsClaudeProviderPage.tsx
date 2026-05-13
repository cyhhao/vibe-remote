import React, { useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  AlertTriangle,
  ArrowLeft,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Download,
  Info,
  KeyRound,
  Pencil,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Sparkles,
  Trash2,
} from 'lucide-react';
import clsx from 'clsx';

import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { SettingsPageShell } from './SettingsPageShell';
import { BackendLifecycleChip } from './BackendLifecycleChip';
import { BackendOAuthPanel } from './BackendOAuthPanel';
import { BackendTestPanel } from './BackendTestPanel';
import { ToggleSwitch } from './SettingsPrimitives';
import { useApi } from '@/context/ApiContext';
import type { ClaudeAuthMode, ClaudeAuthState } from '@/context/ApiContext';
import { useToast } from '@/context/ToastContext';

type CliStatus = 'unknown' | 'ok' | 'missing';

const BACKEND_ID = 'claude';
const DEFAULT_CLI = 'claude';

// Mirrors the segmented-radio pattern from RoutingConfigPanel / Codex page.
// Kept inline rather than promoted to ui/* until a third caller appears —
// see plan doc "Cross-page consistency": SegmentedRadio is cloned for now,
// promote on Phase D consolidation.
const SegmentedRadio: React.FC<{
  value: ClaudeAuthMode;
  onChange: (next: ClaudeAuthMode) => void;
  options: ReadonlyArray<{ id: ClaudeAuthMode; label: string }>;
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

export const SettingsClaudeProviderPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();

  // Runtime state — CLI detection + lifecycle.
  const [loaded, setLoaded] = useState(false);
  const [enabled, setEnabled] = useState(true);
  const [cliPath, setCliPath] = useState(DEFAULT_CLI);
  const [savedCliPath, setSavedCliPath] = useState(DEFAULT_CLI);
  const [cliStatus, setCliStatus] = useState<CliStatus>('unknown');
  const [detecting, setDetecting] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [installResult, setInstallResult] = useState<{
    ok: boolean;
    message: string;
    output?: string | null;
  } | null>(null);
  const [installOutputOpen, setInstallOutputOpen] = useState(false);
  const [savingRuntime, setSavingRuntime] = useState(false);

  // Auth state — OAuth vs API-key.
  const [authState, setAuthState] = useState<ClaudeAuthState | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authSaving, setAuthSaving] = useState(false);
  const [removingKey, setRemovingKey] = useState(false);
  const [authMode, setAuthMode] = useState<ClaudeAuthMode>('oauth');
  const [apiKey, setApiKey] = useState('');
  // ``editingKey`` mirrors the Codex page convention: false = show the
  // saved key as a read-only mask (``sk-ant-•••cd34``) with a pencil to
  // replace it; true = empty editable input ready for a fresh secret.
  const [editingKey, setEditingKey] = useState(false);
  const [baseUrl, setBaseUrl] = useState('');
  // Snapshot the last loaded/saved auth-mode + base_url so we can hide
  // the Save button when nothing has changed (page feedback: no-op
  // Save buttons are noise).
  const [savedAuthMode, setSavedAuthMode] = useState<ClaudeAuthMode>('oauth');
  const [savedBaseUrl, setSavedBaseUrl] = useState('');

  useEffect(() => {
    let cancelled = false;

    // Load runtime config (enabled + cli_path) and kick off CLI detect.
    api
      .getConfig()
      .then((config) => {
        if (cancelled) return;
        const agent = config?.agents?.[BACKEND_ID];
        const initialEnabled = typeof agent?.enabled === 'boolean' ? agent.enabled : true;
        const initialPath = agent?.cli_path || DEFAULT_CLI;
        setEnabled(initialEnabled);
        setCliPath(initialPath);
        setSavedCliPath(initialPath);
        setLoaded(true);
        void detect(initialPath);
      })
      .catch(() => {
        if (!cancelled) setLoaded(true);
      });

    // Load auth state in parallel — independent of CLI detection.
    api
      .getClaudeAuth()
      .then((data) => {
        if (cancelled) return;
        setAuthState(data);
        // Prefer the live-effective tab. V2Config defaults to ``"oauth"``
        // even when settings.json carries the actual key, so reading
        // ``auth_mode`` alone would land the user on the wrong tab. Fall
        // back to V2Config only when nothing on disk is configured.
        const initialMode =
          data.active_auth_mode !== 'none' ? data.active_auth_mode : data.auth_mode;
        const initialBase = data.base_url || '';
        setAuthMode(initialMode);
        setBaseUrl(initialBase);
        setSavedAuthMode(initialMode);
        setSavedBaseUrl(initialBase);
        // The masked preview lives in ``authState.api_key_masked``;
        // ``apiKey`` stays empty until the user clicks "Replace".
        setApiKey('');
        setEditingKey(false);
      })
      .catch(() => {
        // ApiContext already toasted; leave the page on its defaults.
      })
      .finally(() => {
        if (!cancelled) setAuthLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api]);

  const detect = async (binary?: string) => {
    setDetecting(true);
    try {
      const result = await api.detectCli(binary || cliPath || DEFAULT_CLI);
      const nextPath = result.path || cliPath || DEFAULT_CLI;
      setCliPath(nextPath);
      setCliStatus(result.found ? 'ok' : 'missing');
    } catch (e: any) {
      setCliStatus('missing');
      showToast(e?.message || t('common.saveFailed'), 'error');
    } finally {
      setDetecting(false);
    }
  };

  const install = async () => {
    setInstalling(true);
    setInstallResult(null);
    setInstallOutputOpen(false);
    try {
      const result = await api.installAgent(BACKEND_ID);
      const installedPath = typeof result.path === 'string' && result.path ? result.path : null;
      setInstallResult({ ok: result.ok, message: result.message, output: result.output });
      if (result.ok) {
        if (installedPath) setCliPath(installedPath);
        await detect(installedPath || cliPath);
        showToast(result.message || t('agentDetection.installAgent'), 'success');
      } else {
        showToast(result.message || t('common.saveFailed'), 'error');
      }
    } catch (e: any) {
      setInstallResult({ ok: false, message: String(e), output: null });
      showToast(e?.message || String(e), 'error');
    } finally {
      setInstalling(false);
    }
  };

  const onSaveRuntime = async () => {
    setSavingRuntime(true);
    try {
      const config = await api.getConfig();
      const nextAgents = {
        ...(config?.agents || {}),
        [BACKEND_ID]: {
          ...(config?.agents?.[BACKEND_ID] || {}),
          enabled,
          cli_path: cliPath || DEFAULT_CLI,
        },
      };
      const defaultBackend =
        config?.default_backend || config?.agents?.default_backend || 'opencode';
      await api.saveConfig({ agents: { ...nextAgents, default_backend: defaultBackend } });
      setSavedCliPath(cliPath);
      showToast(t('common.saved'), 'success');
    } catch (e: any) {
      showToast(e?.message || t('common.saveFailed'), 'error');
    } finally {
      setSavingRuntime(false);
    }
  };

  const modeOptions = useMemo(
    () =>
      [
        { id: 'oauth' as const, label: t('settings.backends.claudeAuthModeOauth') },
        { id: 'api_key' as const, label: t('settings.backends.claudeAuthModeApiKey') },
      ] as const,
    [t]
  );

  const apiKeyStatus = authState?.has_api_key
    ? t('settings.backends.claudeApiKeyConfigured', { length: authState.api_key_length })
    : t('settings.backends.claudeApiKeyMissing');

  const onRemoveApiKey = async () => {
    // Drop just the API key from V2Config; leaves any OAuth tokens in
    // ``~/.claude/credentials.json`` (or the OS keychain) alone. Lets
    // the user clear a stale key without having to also re-do OAuth.
    const confirmed = window.confirm(
      t('settings.backends.claudeApiKeyRemoveConfirm') as string,
    );
    if (!confirmed) return;
    setRemovingKey(true);
    try {
      const result = await api.removeBackendApiKey('claude');
      if (!result.ok) {
        showToast(
          t('settings.backends.claudeApiKeyRemoveFailed', {
            detail: result.error || result.detail || 'unknown',
          }),
          'error',
        );
        return;
      }
      const fresh = await api.getClaudeAuth();
      setAuthState(fresh);
      setBaseUrl(fresh.base_url || '');
      setSavedBaseUrl(fresh.base_url || '');
      setApiKey('');
      setEditingKey(false);
      showToast(t('settings.backends.claudeApiKeyRemoved'), 'success');
    } catch (err: any) {
      showToast(
        t('settings.backends.claudeApiKeyRemoveFailed', { detail: err?.message || 'unknown' }),
        'error',
      );
    } finally {
      setRemovingKey(false);
    }
  };

  const onSaveAuth = async () => {
    setAuthSaving(true);
    try {
      // Omit base_url when in OAuth mode so toggling auth_mode does not
      // clear a relay URL the user configured in api_key mode — the
      // backend treats "absent" as "preserve stored value".
      const payload: Record<string, unknown> = {
        auth_mode: authMode,
        api_key: authMode === 'api_key' ? (apiKey || undefined) : null,
      };
      if (authMode === 'api_key') {
        payload.base_url = baseUrl.trim() || null;
      }
      const result = await api.saveClaudeAuth(payload as any);
      if (result.ok === false) {
        showToast(result.message || t('settings.backends.claudeSaveFailed'), 'error');
        return;
      }
      setAuthState(result);
      const nextMode =
        result.active_auth_mode !== 'none' ? result.active_auth_mode : result.auth_mode;
      const nextBase = result.base_url || '';
      setAuthMode(nextMode);
      setBaseUrl(nextBase);
      setSavedAuthMode(nextMode);
      setSavedBaseUrl(nextBase);
      setApiKey('');
      setEditingKey(false);
      // Claude restart is synthetic (one-shot CLI) so result.restart.ok
      // is always true; treat any falsy state defensively just in case.
      if (result.restart?.ok === false) {
        showToast(result.restart.message || t('settings.backends.claudeSaveSuccess'), 'warning');
      } else {
        showToast(t('settings.backends.claudeSaveSuccess'), 'success');
      }
    } catch (err: any) {
      showToast(err?.message || t('settings.backends.claudeSaveFailed'), 'error');
    } finally {
      setAuthSaving(false);
    }
  };

  const runtimeDirty = cliPath !== savedCliPath;

  return (
    <SettingsPageShell
      activeTab="backends"
      title={t('settings.backends.claudeTitle')}
      subtitle={t('settings.backends.claudeSubtitle')}
      breadcrumb={
        <Link to="/settings/backends" className="inline-flex items-center gap-1.5 hover:text-foreground">
          <ArrowLeft className="size-3" />
          {t('settings.backends.codexBackToBackends')}
        </Link>
      }
    >
      {!loaded ? (
        <div className="text-sm text-muted">{t('common.loading')}</div>
      ) : (
        <div className="flex flex-col gap-4">
          <Card>
            <CardContent className="flex flex-col gap-5 p-6">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex size-11 shrink-0 items-center justify-center rounded-[10px] bg-cyan-soft">
                    <Sparkles size={22} className="text-cyan" />
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <span className="text-[15px] font-semibold text-foreground">Claude Code</span>
                    <span className="text-[12px] text-muted">
                      {t('settings.backends.claudeDescription')}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <BackendLifecycleChip
                    name={BACKEND_ID}
                    enabled={enabled}
                    cliStatus={cliStatus}
                    onChanged={async (info) => {
                      const installedPath = info?.installedPath || null;
                      if (installedPath) setCliPath(installedPath);
                      await detect(installedPath || cliPath);
                    }}
                  />
                  <ToggleSwitch
                    enabled={enabled}
                    onClick={() => {
                      const next = !enabled;
                      setEnabled(next);
                      void (async () => {
                        try {
                          const config = await api.getConfig();
                          const nextAgents = {
                            ...(config?.agents || {}),
                            [BACKEND_ID]: {
                              ...(config?.agents?.[BACKEND_ID] || {}),
                              enabled: next,
                            },
                          };
                          const defaultBackend =
                            config?.default_backend ||
                            config?.agents?.default_backend ||
                            'opencode';
                          await api.saveConfig({
                            agents: { ...nextAgents, default_backend: defaultBackend },
                          });
                        } catch (e: any) {
                          showToast(e?.message || t('common.saveFailed'), 'error');
                          setEnabled(!next);
                        }
                      })();
                    }}
                  />
                </div>
              </div>

              <div className="flex flex-col gap-2">
                <Label htmlFor="claude-cli-path" className="text-xs font-medium uppercase text-muted">
                  {t('agentDetection.cliPath')}
                </Label>
                <div className="flex gap-2">
                  <Input
                    id="claude-cli-path"
                    type="text"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder={t('agentDetection.cliPathPlaceholder', { name: BACKEND_ID }) as string}
                    value={cliPath}
                    onChange={(e) => setCliPath(e.target.value)}
                    className="font-mono"
                  />
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => void detect(cliPath)}
                    disabled={detecting}
                  >
                    {detecting ? <RefreshCw className="size-3.5 animate-spin" /> : <Search className="size-3.5" />}
                    {t('common.detect')}
                  </Button>
                </div>
                <p className="text-[12px] text-muted">{t('settings.backends.cliPathHint')}</p>
              </div>

              {cliStatus === 'missing' && (
                <div className="space-y-2 rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2.5">
                  <p className="text-[12px] text-cyan">{t('agentDetection.installHint')}</p>
                  <div className="flex flex-wrap items-center gap-3">
                    <Button
                      variant="brand-cyan"
                      size="xs"
                      onClick={() => void install()}
                      disabled={installing}
                    >
                      {installing ? (
                        <RefreshCw className="size-3.5 animate-spin" />
                      ) : (
                        <Download className="size-3.5" />
                      )}
                      {installing ? t('agentDetection.installing') : t('agentDetection.installAgent')}
                    </Button>
                    {installResult?.message && (
                      <span
                        className={clsx(
                          'text-[12px]',
                          installResult.ok ? 'text-mint' : 'text-destructive'
                        )}
                      >
                        {installResult.message}
                      </span>
                    )}
                  </div>
                  {installResult?.output && (
                    <div>
                      <button
                        type="button"
                        onClick={() => setInstallOutputOpen((v) => !v)}
                        className="inline-flex items-center gap-1 text-[11px] text-cyan transition hover:text-cyan/80"
                      >
                        {installOutputOpen ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                        {t('agentDetection.showOutput')}
                      </button>
                      {installOutputOpen && (
                        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded border border-border bg-background px-3 py-2 font-mono text-[11px] text-muted">
                          {installResult.output}
                        </pre>
                      )}
                    </div>
                  )}
                </div>
              )}

              {runtimeDirty && (
                <div className="flex justify-end">
                  <Button
                    variant="brand"
                    size="default"
                    onClick={() => void onSaveRuntime()}
                    disabled={savingRuntime}
                  >
                    <Save className="size-3.5" />
                    {savingRuntime ? t('common.saving') : t('common.save')}
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>

          {authLoading ? (
            <div className="text-sm text-muted">{t('common.loading')}</div>
          ) : (
            <Card>
              <CardContent className="flex flex-col gap-5 p-6">
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between gap-3">
                    <Label className="text-xs font-medium uppercase text-muted">
                      {t('settings.backends.claudeAuthModeLabel')}
                    </Label>
                    {authState?.active_auth_mode && authState.active_auth_mode !== 'none' && (
                      <Badge
                        variant={authState.active_auth_mode === 'oauth' ? 'success' : 'info'}
                        className="font-mono uppercase tracking-[0.06em]"
                      >
                        <CheckCircle2 className="size-3" />
                        {authState.active_auth_mode === 'oauth'
                          ? t('settings.backends.activeAuthOauth')
                          : t('settings.backends.activeAuthApiKey')}
                      </Badge>
                    )}
                    {authState?.active_auth_mode === 'none' && (
                      <Badge variant="secondary" className="font-mono uppercase tracking-[0.06em]">
                        {t('settings.backends.activeAuthNone')}
                      </Badge>
                    )}
                  </div>
                  <SegmentedRadio
                    value={authMode}
                    onChange={setAuthMode}
                    options={modeOptions}
                    ariaLabel={t('settings.backends.claudeAuthModeLabel') as string}
                  />
                  <p className="text-[12px] leading-relaxed text-muted">
                    {authMode === 'api_key'
                      ? t('settings.backends.claudeAuthModeApiKeyHint')
                      : t('settings.backends.claudeAuthModeOauthHint')}
                  </p>
                </div>

                {authMode === 'oauth' && (
                  <BackendOAuthPanel
                    backend={BACKEND_ID}
                    // ``has_oauth_credentials`` flips when ``~/.claude/credentials.json``
                    // carries a usable token bundle — that's a real signal we can
                    // trust on Linux/Docker. macOS keychain installs still report
                    // false, but a successful in-session login flips the panel's
                    // own state anyway.
                    signedIn={!!authState?.has_oauth_credentials}
                    title={t('settings.backends.claudeOauthPanelTitle')}
                    subtitle={t('settings.backends.claudeOauthPanelSubtitle')}
                    onSuccess={() => {
                      // Re-fetch the auth state so the "Signed in" pill and
                      // any masked-key indicators reflect the fresh login
                      // immediately rather than waiting for a page reload.
                      void api
                        .getClaudeAuth()
                        .then((data) => {
                          // Refresh the underlying state (active badge,
                          // masked key, settings.json conflict warning)
                          // but DO NOT clobber ``authMode``. The radio
                          // tab is a *user-controlled* affordance after
                          // first load — re-syncing it from the server
                          // here would force a tab change every time
                          // OAuth completes / Sign out fires, even when
                          // the user wants to stay on the OAuth tab to
                          // re-authenticate.
                          setAuthState(data);
                          setBaseUrl(data.base_url || '');
                        })
                        .catch(() => {
                          // Already toasted upstream; leave UI on previous state.
                        });
                    }}
                  />
                )}

                {authMode === 'api_key' && (
                  <>
                    <div className="flex flex-col gap-2">
                      <Label htmlFor="claude-api-key" className="text-xs font-medium uppercase text-muted">
                        {t('settings.backends.claudeApiKeyLabel')}
                      </Label>
                      {authState?.has_api_key && !editingKey ? (
                        // Same masked-preview affordance the Codex page uses;
                        // keeps the user from re-typing the secret when they
                        // are only changing the Base URL.
                        <div className="flex items-center gap-2 rounded-md border border-border bg-foreground/[0.04] px-3 py-2">
                          <KeyRound className="size-4 shrink-0 text-muted" />
                          <code className="flex-1 truncate font-mono text-[13px] text-foreground">
                            {authState.api_key_masked || '••••••••'}
                          </code>
                          <Button
                            type="button"
                            variant="ghost"
                            size="xs"
                            onClick={() => {
                              setEditingKey(true);
                              setApiKey('');
                            }}
                            disabled={removingKey}
                          >
                            <Pencil className="size-3" />
                            {t('settings.backends.replaceApiKey')}
                          </Button>
                          {/* Symmetric to OpenCode's Remove affordance:
                              clear the saved API key while leaving OAuth
                              tokens in ``~/.claude/credentials.json`` /
                              keychain intact. Without this, a stuck key
                              keeps forcing the env-var path even after
                              the user signed in via OAuth. */}
                          <Button
                            type="button"
                            variant="ghost"
                            size="xs"
                            onClick={() => void onRemoveApiKey()}
                            disabled={removingKey || editingKey}
                            className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                          >
                            <Trash2 className="size-3" />
                            {removingKey
                              ? t('common.removing')
                              : t('settings.backends.claudeApiKeyRemove')}
                          </Button>
                        </div>
                      ) : (
                        <div className="relative">
                          <KeyRound className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
                          <Input
                            id="claude-api-key"
                            type="password"
                            autoComplete="off"
                            spellCheck={false}
                            placeholder={t('settings.backends.claudeApiKeyPlaceholder') as string}
                            value={apiKey}
                            onChange={(e) => setApiKey(e.target.value)}
                            className="pl-9 font-mono"
                            disabled={authSaving}
                            autoFocus={editingKey}
                          />
                        </div>
                      )}
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-[12px] text-muted">{apiKeyStatus}</p>
                        {authState?.has_api_key && editingKey && (
                          <button
                            type="button"
                            className="text-[12px] text-muted underline-offset-2 transition hover:text-foreground hover:underline"
                            onClick={() => {
                              setEditingKey(false);
                              setApiKey('');
                            }}
                          >
                            {t('common.cancel')}
                          </button>
                        )}
                      </div>
                    </div>

                    <div className="flex flex-col gap-2">
                      <Label htmlFor="claude-base-url" className="text-xs font-medium uppercase text-muted">
                        {t('settings.backends.claudeBaseUrlLabel')}
                      </Label>
                      <div className="flex gap-2">
                        <Input
                          id="claude-base-url"
                          type="url"
                          autoComplete="off"
                          spellCheck={false}
                          placeholder={t('settings.backends.claudeBaseUrlPlaceholder') as string}
                          value={baseUrl}
                          onChange={(e) => setBaseUrl(e.target.value)}
                          className="font-mono"
                          disabled={authSaving}
                        />
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => setBaseUrl('')}
                          disabled={!baseUrl || authSaving}
                        >
                          <RotateCcw className="size-3.5" />
                          {t('settings.backends.claudeBaseUrlReset')}
                        </Button>
                      </div>
                      <p className="text-[12px] text-muted">{t('settings.backends.claudeBaseUrlHint')}</p>
                    </div>
                  </>
                )}

                {/* The settings.json conflict warning + Info hint only
                    speak to the API-key flow (where saving collides with
                    a hand-edited ``env`` block, or writes into V2Config).
                    Showing them under OAuth is just clutter. */}
                {authMode === 'api_key' && authState?.settings_conflict && (
                  // Claude Code applies its own ``env`` block on top of inherited
                  // env, so a hand-edited ``settings.json`` will override the
                  // V2Config-injected key at launch. Warn loudly — silently
                  // letting a saved key be ignored is the worst outcome.
                  <div className="flex items-start gap-2 rounded-lg border border-gold/30 bg-gold/[0.08] px-3 py-2.5">
                    <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-gold" />
                    <div className="flex flex-col gap-1">
                      <p className="text-[12px] font-medium text-gold">
                        {t('settings.backends.claudeSettingsConflictTitle')}
                      </p>
                      <p className="text-[12px] leading-relaxed text-muted">
                        {t('settings.backends.claudeSettingsConflictBody', {
                          var: authState.settings_env_key_var || 'ANTHROPIC_API_KEY',
                          path: authState.settings_path || '~/.claude/settings.json',
                        })}
                      </p>
                    </div>
                  </div>
                )}

                {authMode === 'api_key' && (
                  <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2/60 px-3 py-2.5">
                    <Info className="mt-0.5 size-3.5 shrink-0 text-muted" />
                    <p className="text-[12px] leading-relaxed text-muted">
                      {t('settings.backends.claudeInfoHint')}
                    </p>
                  </div>
                )}

                {/* OAuth mode persists ``auth_mode=oauth`` automatically on
                    successful sign-in (see ``_invoke_post_web_success_hook``),
                    so the Save button only needs to surface in API-key mode
                    where the user still has to commit the key / Base URL —
                    and even there, only when something has actually changed. */}
                {authMode === 'api_key' && (() => {
                  const dirty =
                    authMode !== savedAuthMode ||
                    apiKey.trim().length > 0 ||
                    baseUrl.trim() !== savedBaseUrl.trim();
                  if (!dirty) return null;
                  return (
                    <div className="flex justify-end">
                      <Button variant="brand" size="default" onClick={onSaveAuth} disabled={authSaving}>
                        <Save className="size-3.5" />
                        {authSaving ? t('settings.backends.claudeSaving') : t('settings.backends.claudeSave')}
                      </Button>
                    </div>
                  );
                })()}
              </CardContent>
            </Card>
          )}

          {/* Connectivity probe — matches design.pen cdTest2 panel.
              Works in both OAuth and API-key modes because the underlying
              ``claude -p "Hi"`` subprocess inherits whichever auth source
              V2Config selects at launch. */}
          {!authLoading && <BackendTestPanel backend={BACKEND_ID} />}
        </div>
      )}
    </SettingsPageShell>
  );
};
