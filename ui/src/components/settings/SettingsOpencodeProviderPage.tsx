import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import {
  AlertCircle,
  ArrowLeft,
  Check,
  ChevronDown,
  ChevronUp,
  Cpu,
  Download,
  Info,
  KeyRound,
  Pencil,
  RefreshCw,
  RotateCcw,
  Save,
  Search,
  Server,
  Settings,
  Star,
  Terminal,
  Trash2,
  X,
} from 'lucide-react';
import clsx from 'clsx';

import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Card, CardContent } from '../ui/card';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import { SettingsPageShell } from './SettingsPageShell';
import { BackendLifecycleChip } from './BackendLifecycleChip';
import { ToggleSwitch } from './SettingsPrimitives';
import { useApi } from '@/context/ApiContext';
import type { OpencodeProvider } from '@/context/ApiContext';
import { useToast } from '@/context/ToastContext';

type CliStatus = 'unknown' | 'ok' | 'missing';
type PermissionState = 'idle' | 'loading' | 'success' | 'error';
type FilterMode = 'all' | 'configured' | 'oauth' | 'local';

// Per-provider edit state — kept in a record so the page can render the
// grid statelessly and only allocate inputs for the expanded card.
type ProviderEditState = {
  apiKey: string;
  baseUrl: string;
  saving: boolean;
  removing: boolean;
  error: string | null;
  // Mirrors the Codex / Claude pattern: false = show ``api_key_masked``
  // read-only with a Replace button; true = empty editable input ready
  // for a fresh key. Toggled by the pencil button next to the masked
  // preview. Reset to false on successful save / remove / reload.
  editingKey: boolean;
};

const BACKEND_ID = 'opencode';
const DEFAULT_CLI = 'opencode';
// Server startup is asynchronous (OpenCode app spawn + port bind). Retry
// the introspection fan-out a handful of times before falling back to the
// "server not reachable" banner so the user can still configure values.
const SERVER_START_MAX_RETRIES = 5;
const SERVER_START_RETRY_DELAY_MS = 3000;

const FILTER_MODES: ReadonlyArray<FilterMode> = ['all', 'configured', 'oauth', 'local'];

const emptyEdit = (): ProviderEditState => ({
  apiKey: '',
  baseUrl: '',
  saving: false,
  removing: false,
  error: null,
  editingKey: false,
});

const providerMatchesFilter = (provider: OpencodeProvider, mode: FilterMode): boolean => {
  switch (mode) {
    case 'configured':
      return provider.configured;
    case 'oauth':
      return provider.oauth_available;
    case 'local':
      return provider.local;
    case 'all':
    default:
      return true;
  }
};

const providerMatchesSearch = (provider: OpencodeProvider, q: string): boolean => {
  if (!q) return true;
  const needle = q.toLowerCase();
  if (provider.id.toLowerCase().includes(needle)) return true;
  if (provider.name.toLowerCase().includes(needle)) return true;
  if (provider.description.toLowerCase().includes(needle)) return true;
  return provider.models.some((m) => m.toLowerCase().includes(needle));
};

export const SettingsOpencodeProviderPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();

  // Runtime state (mirrors Claude / Codex page conventions).
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
  const [permissionState, setPermissionState] = useState<PermissionState>('idle');
  const [permissionMessage, setPermissionMessage] = useState('');
  const [savingRuntime, setSavingRuntime] = useState(false);

  // Provider catalog state.
  const [providers, setProviders] = useState<OpencodeProvider[] | null>(null);
  const [defaultProvider, setDefaultProvider] = useState<string | null>(null);
  const [providersLoading, setProvidersLoading] = useState(false);
  const [providersError, setProvidersError] = useState<string | null>(null);
  const [serverStartAttempts, setServerStartAttempts] = useState(0);

  // Toolbar state.
  const [searchQuery, setSearchQuery] = useState('');
  const [filterMode, setFilterMode] = useState<FilterMode>('all');

  // Inline-expansion state — only one card open at a time.
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [editByProvider, setEditByProvider] = useState<Record<string, ProviderEditState>>({});

  // Default-provider popover state.
  const [defaultPopoverOpen, setDefaultPopoverOpen] = useState(false);
  const [defaultSearchQuery, setDefaultSearchQuery] = useState('');
  const [settingDefault, setSettingDefault] = useState(false);

  // Server-start retry timer cleanup.
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // useEffect closure captures the initial loadProviders; ref lets the
  // retry timer call the latest implementation without re-arming on every
  // state update.
  const loadProvidersRef = useRef<(() => Promise<void>) | null>(null);

  const detect = useCallback(
    async (binary?: string) => {
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
    },
    [api, cliPath, showToast, t]
  );

  const loadProviders = useCallback(async () => {
    setProvidersLoading(true);
    setProvidersError(null);
    try {
      const result = await api.getOpencodeProviders();
      if (result.ok && result.providers) {
        setProviders(result.providers);
        setDefaultProvider(result.default_provider || null);
        setServerStartAttempts(0);
        setProvidersError(null);
      } else {
        setProviders((prev) => prev ?? []);
        setProvidersError(result.message || t('settings.backends.opencodeProvidersError'));
      }
    } catch (e: any) {
      setProviders((prev) => prev ?? []);
      setProvidersError(e?.message || t('settings.backends.opencodeProvidersError'));
    } finally {
      setProvidersLoading(false);
    }
  }, [api, t]);

  useEffect(() => {
    loadProvidersRef.current = loadProviders;
  }, [loadProviders]);

  useEffect(() => {
    let cancelled = false;
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
        if (initialEnabled) {
          void loadProviders();
        }
      })
      .catch(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api]);

  // Auto-retry server-start failures — common when the user has just
  // enabled the backend. After SERVER_START_MAX_RETRIES the user can hit
  // Refresh manually; we stop the implicit retry to avoid background
  // request loops.
  useEffect(() => {
    if (retryTimerRef.current) {
      clearTimeout(retryTimerRef.current);
      retryTimerRef.current = null;
    }
    if (!enabled) return;
    if (!providersError) return;
    if (providersLoading) return;
    if (serverStartAttempts >= SERVER_START_MAX_RETRIES) return;
    retryTimerRef.current = setTimeout(() => {
      setServerStartAttempts((n) => n + 1);
      void loadProvidersRef.current?.();
    }, SERVER_START_RETRY_DELAY_MS);
    return () => {
      if (retryTimerRef.current) {
        clearTimeout(retryTimerRef.current);
        retryTimerRef.current = null;
      }
    };
  }, [enabled, providersError, providersLoading, serverStartAttempts]);

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

  const setupPermission = async () => {
    setPermissionState('loading');
    setPermissionMessage('');
    try {
      const result = await api.opencodeSetupPermission();
      setPermissionState(result.ok ? 'success' : 'error');
      setPermissionMessage(result.message);
      showToast(result.message, result.ok ? 'success' : 'error');
    } catch (e: any) {
      setPermissionState('error');
      const msg = e?.message || String(e);
      setPermissionMessage(msg);
      showToast(msg, 'error');
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
        config?.default_backend || config?.agents?.default_backend || BACKEND_ID;
      await api.saveConfig({ agents: { ...nextAgents, default_backend: defaultBackend } });
      setSavedCliPath(cliPath);
      showToast(t('common.saved'), 'success');
    } catch (e: any) {
      showToast(e?.message || t('common.saveFailed'), 'error');
    } finally {
      setSavingRuntime(false);
    }
  };

  const toggleEnabled = () => {
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
          config?.default_backend || config?.agents?.default_backend || BACKEND_ID;
        await api.saveConfig({
          agents: { ...nextAgents, default_backend: defaultBackend },
        });
        if (next) {
          setServerStartAttempts(0);
          void loadProviders();
        } else {
          setProviders(null);
          setProvidersError(null);
        }
      } catch (e: any) {
        showToast(e?.message || t('common.saveFailed'), 'error');
        setEnabled(!next);
      }
    })();
  };

  // ---- Expansion / per-provider editing ----

  const openProvider = (provider: OpencodeProvider) => {
    setExpandedId(provider.id);
    setEditByProvider((prev) => {
      if (prev[provider.id]) return prev;
      // Pre-populate baseUrl from the persisted opencode.json override so
      // the input round-trips: open card → see existing value → edit →
      // save. Leaving it blank on every open would let a re-save wipe the
      // value because the form is the only baseURL signal we forward.
      const seeded = emptyEdit();
      if (provider.base_url) {
        seeded.baseUrl = provider.base_url;
      }
      return { ...prev, [provider.id]: seeded };
    });
  };

  const closeProvider = () => {
    // Wipe the in-memory plaintext key/base-url for the closing card so
    // collapsing acts as a panic-revert: defense-in-depth in case the
    // user typed a key but did not save it.
    if (expandedId) {
      setEditByProvider((prev) => {
        if (!prev[expandedId]) return prev;
        return { ...prev, [expandedId]: emptyEdit() };
      });
    }
    setExpandedId(null);
  };

  const onToggleProvider = (provider: OpencodeProvider) => {
    if (expandedId === provider.id) {
      closeProvider();
    } else {
      openProvider(provider);
    }
  };

  const updateEdit = (providerId: string, patch: Partial<ProviderEditState>) => {
    setEditByProvider((prev) => ({
      ...prev,
      [providerId]: { ...(prev[providerId] || emptyEdit()), ...patch },
    }));
  };

  const onSaveProviderAuth = async (provider: OpencodeProvider) => {
    const state = editByProvider[provider.id] || emptyEdit();
    const key = state.apiKey.trim();
    if (!key) {
      updateEdit(provider.id, {
        error: t('settings.backends.opencodeProviderApiKeyRequired') as string,
      });
      return;
    }
    // The Base URL field is the only signal the form sends for the
    // ``provider.<id>.options.baseURL`` override in ``opencode.json``.
    // Forwarding the trimmed value verbatim — including the empty
    // string for "clear" — is critical: if we dropped to ``undefined``
    // for blanks, the server would interpret it as "leave unchanged"
    // and a user who removed the value in the form would silently keep
    // the old override on disk.
    const baseUrl = state.baseUrl.trim();
    updateEdit(provider.id, { saving: true, error: null });
    try {
      const result = await api.setOpencodeProviderAuth(provider.id, key, baseUrl);
      if (!result.ok) {
        updateEdit(provider.id, {
          saving: false,
          error: result.message || (t('settings.backends.opencodeProviderSaveFailed') as string),
        });
        return;
      }
      updateEdit(provider.id, {
        saving: false,
        apiKey: '',
        editingKey: false,
        error: null,
      });
      showToast(t('settings.backends.opencodeProviderSaved'), 'success');
      await loadProviders();
    } catch (e: any) {
      updateEdit(provider.id, {
        saving: false,
        error: e?.message || (t('settings.backends.opencodeProviderSaveFailed') as string),
      });
    }
  };

  const onRemoveProviderAuth = async (provider: OpencodeProvider) => {
    const confirmed = window.confirm(
      t('settings.backends.opencodeProviderRemoveConfirm', { name: provider.name }) as string
    );
    if (!confirmed) return;
    updateEdit(provider.id, { removing: true, error: null });
    try {
      const result = await api.deleteOpencodeProviderAuth(provider.id);
      if (!result.ok) {
        updateEdit(provider.id, {
          removing: false,
          error: result.message || (t('settings.backends.opencodeProviderRemoveFailed') as string),
        });
        return;
      }
      updateEdit(provider.id, { removing: false, error: null });
      showToast(t('settings.backends.opencodeProviderRemoved'), 'success');
      await loadProviders();
    } catch (e: any) {
      updateEdit(provider.id, {
        removing: false,
        error: e?.message || (t('settings.backends.opencodeProviderRemoveFailed') as string),
      });
    }
  };

  // ---- Default-provider selection ----

  const pickDefaultProvider = async (provider: OpencodeProvider) => {
    if (!provider.configured) {
      // Surfacing "set the key first" is more discoverable than silently
      // ignoring the click — expand the card and let the user fill it in.
      setDefaultPopoverOpen(false);
      setDefaultSearchQuery('');
      openProvider(provider);
      showToast(
        t('settings.backends.opencodeDefaultNeedsKey', { name: provider.name }),
        'warning'
      );
      return;
    }
    if (provider.id === defaultProvider) {
      setDefaultPopoverOpen(false);
      setDefaultSearchQuery('');
      return;
    }
    setSettingDefault(true);
    try {
      const result = await api.setOpencodeDefaultProvider(provider.id);
      if (!result.ok) {
        showToast(
          result.message || (t('settings.backends.opencodeDefaultSaveFailed') as string),
          'error'
        );
        return;
      }
      setDefaultProvider(result.default_provider || provider.id);
      showToast(t('settings.backends.opencodeDefaultSaved', { name: provider.name }), 'success');
      setDefaultPopoverOpen(false);
      setDefaultSearchQuery('');
    } catch (e: any) {
      showToast(e?.message || (t('settings.backends.opencodeDefaultSaveFailed') as string), 'error');
    } finally {
      setSettingDefault(false);
    }
  };

  // ---- Derived collections ----

  const visibleProviders = useMemo(() => {
    if (!providers) return [];
    return providers.filter(
      (p) => providerMatchesFilter(p, filterMode) && providerMatchesSearch(p, searchQuery)
    );
  }, [providers, filterMode, searchQuery]);

  const popoverProviders = useMemo(() => {
    if (!providers) return [];
    return providers.filter((p) => providerMatchesSearch(p, defaultSearchQuery));
  }, [providers, defaultSearchQuery]);

  const configuredCount = providers?.filter((p) => p.configured).length || 0;
  const totalCount = providers?.length || 0;
  const defaultProviderObj = useMemo(
    () => providers?.find((p) => p.id === defaultProvider) || null,
    [providers, defaultProvider]
  );

  const runtimeDirty = cliPath !== savedCliPath;

  const filterLabel = (mode: FilterMode) => {
    switch (mode) {
      case 'all':
        return t('settings.backends.opencodeFilterAll');
      case 'configured':
        return t('settings.backends.opencodeFilterConfigured');
      case 'oauth':
        return t('settings.backends.opencodeFilterOauth');
      case 'local':
        return t('settings.backends.opencodeFilterLocal');
    }
  };

  // ---- Status badge selection ----
  //
  // The order here matches the plan doc: configured wins, then OAuth
  // availability, then local, finally "not set" for unconfigured cloud
  // providers. Multi-flag providers therefore surface the most useful
  // signal first.
  const renderProviderBadge = (provider: OpencodeProvider) => {
    if (provider.configured) {
      return (
        <Badge variant="success" className="gap-1">
          <Check className="size-3" />
          {t('settings.backends.opencodeBadgeConfigured')}
        </Badge>
      );
    }
    if (provider.oauth_available) {
      return (
        <Badge variant="info">{t('settings.backends.opencodeBadgeOauth')}</Badge>
      );
    }
    if (provider.local) {
      return (
        <Badge variant="secondary">{t('settings.backends.opencodeBadgeLocal')}</Badge>
      );
    }
    return <Badge variant="outline">{t('settings.backends.opencodeBadgeUnset')}</Badge>;
  };

  // ---- Render ----

  return (
    <SettingsPageShell
      activeTab="backends"
      title={t('settings.backends.opencodeTitle')}
      subtitle={t('settings.backends.opencodeSubtitle')}
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
          {/* Card 1 — runtime / lifecycle. Mirrors Claude page shape. */}
          <Card>
            <CardContent className="flex flex-col gap-5 p-6">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex size-11 shrink-0 items-center justify-center rounded-[10px] bg-violet-soft">
                    <Terminal size={22} className="text-violet" />
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <span className="text-[15px] font-semibold text-foreground">OpenCode</span>
                    <span className="text-[12px] text-muted">
                      {t('settings.backends.opencodeDescription')}
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
                  <ToggleSwitch enabled={enabled} onClick={toggleEnabled} />
                </div>
              </div>

              <div className="flex flex-col gap-2">
                <Label htmlFor="opencode-cli-path" className="text-xs font-medium uppercase text-muted">
                  {t('agentDetection.cliPath')}
                </Label>
                <div className="flex gap-2">
                  <Input
                    id="opencode-cli-path"
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

              {cliStatus === 'ok' && (
                <div className="rounded-lg border border-gold/30 bg-gold/10 px-3 py-2.5">
                  <p className="mb-2 text-[12px] text-gold">{t('agentDetection.permissionHint')}</p>
                  <div className="flex flex-wrap items-center gap-3">
                    <Button
                      variant="brand-gold"
                      size="xs"
                      onClick={() => void setupPermission()}
                      disabled={permissionState === 'loading'}
                    >
                      {permissionState === 'loading' ? (
                        <RefreshCw className="size-3.5 animate-spin" />
                      ) : (
                        <Settings className="size-3.5" />
                      )}
                      {t('agentDetection.setupPermission')}
                    </Button>
                    {permissionState === 'success' && (
                      <span className="text-[12px] text-mint">{permissionMessage}</span>
                    )}
                    {permissionState === 'error' && (
                      <span className="text-[12px] text-destructive">{permissionMessage}</span>
                    )}
                  </div>
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

          {/* Card 2 — provider catalog. */}
          <Card>
            <CardContent className="flex flex-col gap-5 p-6">
              <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex size-11 shrink-0 items-center justify-center rounded-[10px] bg-cyan-soft">
                    <Server size={22} className="text-cyan" />
                  </div>
                  <div className="flex flex-col gap-0.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-[15px] font-semibold text-foreground">
                        {t('settings.backends.opencodeProvidersTitle')}
                      </span>
                      {enabled && providers && (
                        <Badge variant={providersError ? 'warning' : 'success'}>
                          {providersError
                            ? t('settings.backends.opencodeServerStopped')
                            : t('settings.backends.opencodeServerRunning')}
                        </Badge>
                      )}
                      {!enabled && (
                        <Badge variant="secondary">
                          {t('settings.backends.opencodeBackendDisabled')}
                        </Badge>
                      )}
                      {enabled && providers && providers.length > 0 && (
                        <Badge variant="outline">
                          {t('settings.backends.opencodeProvidersCount', {
                            configured: configuredCount,
                            total: totalCount,
                          })}
                        </Badge>
                      )}
                    </div>
                    <span className="text-[12px] text-muted">
                      {t('settings.backends.opencodeProvidersSubtitle')}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={() => {
                      setServerStartAttempts(0);
                      void loadProviders();
                    }}
                    disabled={!enabled || providersLoading}
                  >
                    <RefreshCw
                      className={clsx('size-3.5', providersLoading && 'animate-spin')}
                    />
                    {t('settings.backends.opencodeRefresh')}
                  </Button>
                </div>
              </div>

              {!enabled && (
                <div className="rounded-lg border border-border bg-surface-2/60 px-3 py-2.5 text-[12px] text-muted">
                  {t('settings.backends.opencodeDisabledBanner')}
                </div>
              )}

              {enabled && (
                <>
                  {/* Toolbar — search + filter chips + default-provider pill. */}
                  <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
                    <div className="flex flex-1 flex-col gap-3 sm:flex-row sm:items-center">
                      <div className="relative w-full sm:max-w-xs">
                        <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted" />
                        <Input
                          type="text"
                          placeholder={t('settings.backends.opencodeSearchPlaceholder') as string}
                          value={searchQuery}
                          onChange={(e) => setSearchQuery(e.target.value)}
                          className="h-9 pl-8 text-[12px]"
                          autoComplete="off"
                          spellCheck={false}
                        />
                      </div>
                      <div className="flex flex-wrap items-center gap-1.5">
                        {FILTER_MODES.map((mode) => {
                          const active = filterMode === mode;
                          return (
                            <button
                              key={mode}
                              type="button"
                              onClick={() => setFilterMode(mode)}
                              className={clsx(
                                'rounded-full border px-3 py-1 text-[11px] font-semibold transition-colors',
                                active
                                  ? 'border-mint/40 bg-mint-soft text-mint'
                                  : 'border-border bg-surface text-muted hover:text-foreground'
                              )}
                            >
                              {filterLabel(mode)}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    {/* Default-provider pill. */}
                    <Popover
                      open={defaultPopoverOpen}
                      onOpenChange={(open) => {
                        setDefaultPopoverOpen(open);
                        if (!open) setDefaultSearchQuery('');
                      }}
                    >
                      <PopoverTrigger asChild>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={!providers || providers.length === 0}
                          className="justify-between gap-2 text-[12px]"
                        >
                          <Star className="size-3.5 text-mint" />
                          <span className="text-muted">
                            {t('settings.backends.opencodeDefaultLabel')}:
                          </span>
                          <span className="font-mono text-[12px] text-foreground">
                            {defaultProviderObj
                              ? defaultProviderObj.name
                              : defaultProvider
                                ? defaultProvider
                                : t('settings.backends.opencodeDefaultUnset')}
                          </span>
                          <ChevronDown className="size-3.5 text-muted" />
                        </Button>
                      </PopoverTrigger>
                      <PopoverContent align="end" className="w-80 p-0">
                        <div className="flex flex-col gap-2 p-3">
                          <Label className="text-xs font-medium uppercase text-muted">
                            {t('settings.backends.opencodeDefaultPopoverTitle')}
                          </Label>
                          <div className="relative">
                            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted" />
                            <Input
                              type="text"
                              placeholder={
                                t('settings.backends.opencodeSearchPlaceholder') as string
                              }
                              value={defaultSearchQuery}
                              onChange={(e) => setDefaultSearchQuery(e.target.value)}
                              className="h-8 pl-8 text-[12px]"
                              autoComplete="off"
                              spellCheck={false}
                            />
                          </div>
                        </div>
                        <div className="max-h-72 overflow-y-auto border-t border-border">
                          {popoverProviders.length === 0 ? (
                            <div className="px-3 py-2 text-[12px] text-muted">
                              {t('settings.backends.opencodeDefaultPopoverEmpty')}
                            </div>
                          ) : (
                            <ul className="flex flex-col">
                              {popoverProviders.map((provider) => {
                                const isCurrent = provider.id === defaultProvider;
                                return (
                                  <li key={provider.id}>
                                    <button
                                      type="button"
                                      onClick={() => void pickDefaultProvider(provider)}
                                      disabled={settingDefault}
                                      className={clsx(
                                        'flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-[12px] transition-colors',
                                        'hover:bg-surface-2',
                                        isCurrent && 'bg-mint-soft/40'
                                      )}
                                    >
                                      <span className="flex flex-col gap-0.5">
                                        <span className="font-medium text-foreground">
                                          {provider.name}
                                        </span>
                                        <span className="font-mono text-[11px] text-muted">
                                          {provider.id}
                                        </span>
                                      </span>
                                      <span className="flex items-center gap-2">
                                        {provider.configured ? (
                                          <Badge variant="success" className="gap-1">
                                            <Check className="size-3" />
                                            {t('settings.backends.opencodeBadgeConfigured')}
                                          </Badge>
                                        ) : provider.oauth_available ? (
                                          <Badge variant="info">
                                            {t('settings.backends.opencodeBadgeOauth')}
                                          </Badge>
                                        ) : provider.local ? (
                                          <Badge variant="secondary">
                                            {t('settings.backends.opencodeBadgeLocal')}
                                          </Badge>
                                        ) : (
                                          <Badge variant="outline">
                                            {t('settings.backends.opencodeBadgeUnset')}
                                          </Badge>
                                        )}
                                        {isCurrent && <Check className="size-3.5 text-mint" />}
                                      </span>
                                    </button>
                                  </li>
                                );
                              })}
                            </ul>
                          )}
                        </div>
                      </PopoverContent>
                    </Popover>
                  </div>

                  {/* Server-starting / error banner. */}
                  {providersError && (
                    <div className="flex items-start gap-2 rounded-lg border border-gold/30 bg-gold/[0.08] px-3 py-2.5">
                      <AlertCircle className="mt-0.5 size-3.5 shrink-0 text-gold" />
                      <div className="flex flex-1 flex-col gap-1">
                        <p className="text-[12px] font-medium text-gold">
                          {serverStartAttempts < SERVER_START_MAX_RETRIES
                            ? t('settings.backends.opencodeServerStarting')
                            : t('settings.backends.opencodeServerUnreachable')}
                        </p>
                        <p className="text-[12px] leading-relaxed text-muted">{providersError}</p>
                      </div>
                      {serverStartAttempts < SERVER_START_MAX_RETRIES && (
                        <span className="shrink-0 text-[11px] text-muted">
                          {t('settings.backends.opencodeServerRetryCount', {
                            attempt: serverStartAttempts + 1,
                            max: SERVER_START_MAX_RETRIES,
                          })}
                        </span>
                      )}
                    </div>
                  )}

                  {/* Initial loading skeleton. */}
                  {providersLoading && !providers && (
                    <div className="rounded-lg border border-border bg-surface-2/60 px-3 py-6 text-center text-[12px] text-muted">
                      <RefreshCw className="mx-auto mb-2 size-4 animate-spin text-cyan" />
                      {t('settings.backends.opencodeProvidersLoading')}
                    </div>
                  )}

                  {/* Empty state — server reports no providers at all. */}
                  {!providersLoading && providers && providers.length === 0 && !providersError && (
                    <div className="rounded-lg border border-border bg-surface-2/60 px-3 py-6 text-center text-[12px] text-muted">
                      {t('settings.backends.opencodeProvidersEmpty')}
                    </div>
                  )}

                  {/* Empty state — search/filter excluded everything. */}
                  {!providersLoading &&
                    providers &&
                    providers.length > 0 &&
                    visibleProviders.length === 0 && (
                      <div className="rounded-lg border border-border bg-surface-2/60 px-3 py-6 text-center text-[12px] text-muted">
                        {t('settings.backends.opencodeProvidersFilterEmpty')}
                      </div>
                    )}

                  {/* Grid. */}
                  {visibleProviders.length > 0 && (
                    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                      {visibleProviders.map((provider) => {
                        const expanded = expandedId === provider.id;
                        const edit = editByProvider[provider.id] || emptyEdit();
                        const isDefault = defaultProvider === provider.id;
                        return (
                          <div
                            key={provider.id}
                            className={clsx(
                              'flex flex-col rounded-lg border bg-surface transition-colors',
                              expanded ? 'border-mint/40 shadow-[0_0_24px_-12px_rgba(91,255,160,0.6)]' : 'border-border hover:border-border-strong',
                              expanded && 'md:col-span-2'
                            )}
                          >
                            <button
                              type="button"
                              onClick={() => onToggleProvider(provider)}
                              className="flex w-full items-start justify-between gap-3 px-4 py-3 text-left"
                            >
                              <div className="flex flex-1 flex-col gap-1">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="text-[13px] font-semibold text-foreground">
                                    {provider.name}
                                  </span>
                                  {renderProviderBadge(provider)}
                                  {isDefault && (
                                    <Badge variant="warning" className="gap-1">
                                      <Star className="size-3" />
                                      {t('settings.backends.opencodeDefaultBadge')}
                                    </Badge>
                                  )}
                                </div>
                                <span className="font-mono text-[11px] text-muted">
                                  {provider.id} ·{' '}
                                  {t('settings.backends.opencodeProviderModelsCount', {
                                    count: provider.models.length,
                                  })}
                                </span>
                                {provider.description && (
                                  <span className="text-[12px] leading-relaxed text-muted">
                                    {provider.description}
                                  </span>
                                )}
                              </div>
                              {expanded ? (
                                <ChevronUp className="mt-0.5 size-4 shrink-0 text-muted" />
                              ) : (
                                <ChevronDown className="mt-0.5 size-4 shrink-0 text-muted" />
                              )}
                            </button>

                            {expanded && (
                              <div className="flex flex-col gap-4 border-t border-border bg-background px-4 py-4">
                                <div className="flex flex-wrap items-center gap-2">
                                  <Button
                                    type="button"
                                    variant="secondary"
                                    size="xs"
                                    onClick={closeProvider}
                                  >
                                    <X className="size-3.5" />
                                    {t('settings.backends.opencodeProviderCollapse')}
                                  </Button>
                                  {provider.configured && (
                                    <Button
                                      type="button"
                                      variant="outline"
                                      size="xs"
                                      onClick={() => void onRemoveProviderAuth(provider)}
                                      disabled={edit.removing || edit.saving}
                                      className="text-destructive"
                                    >
                                      {edit.removing ? (
                                        <RefreshCw className="size-3.5 animate-spin" />
                                      ) : (
                                        <Trash2 className="size-3.5" />
                                      )}
                                      {t('settings.backends.opencodeProviderRemove')}
                                    </Button>
                                  )}
                                  {!provider.configured && !isDefault && provider.local && (
                                    <span className="text-[11px] text-muted">
                                      {t('settings.backends.opencodeProviderLocalHint')}
                                    </span>
                                  )}
                                </div>

                                <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                                  <div className="flex flex-col gap-3">
                                    {provider.oauth_available && (
                                      <div className="rounded-md border border-cyan/30 bg-cyan/[0.06] px-3 py-2">
                                        <p className="text-[11px] font-medium text-cyan">
                                          {t('settings.backends.opencodeProviderOauthAvailable')}
                                        </p>
                                        <p className="text-[11px] leading-relaxed text-muted">
                                          {t('settings.backends.opencodeProviderOauthHint', {
                                            id: provider.id,
                                          })}
                                        </p>
                                      </div>
                                    )}

                                    <div className="flex flex-col gap-1.5">
                                      <Label
                                        htmlFor={`opencode-key-${provider.id}`}
                                        className="text-[11px] font-medium uppercase text-muted"
                                      >
                                        {t('settings.backends.opencodeProviderApiKey')}
                                      </Label>
                                      {provider.configured && provider.api_key_masked && !edit.editingKey ? (
                                        // Masked-preview affordance ported from
                                        // the Claude / Codex pages: show the
                                        // saved key as a read-only mono-typed
                                        // value with a pencil to swap in a
                                        // fresh one. Saves the user from
                                        // re-typing the secret on baseURL-only
                                        // edits.
                                        <div className="flex items-center gap-2 rounded-md border border-border bg-foreground/[0.04] px-3 py-2">
                                          <KeyRound className="size-4 shrink-0 text-muted" />
                                          <code className="flex-1 truncate font-mono text-[12px] text-foreground">
                                            {provider.api_key_masked}
                                          </code>
                                          <Button
                                            type="button"
                                            variant="ghost"
                                            size="xs"
                                            onClick={() =>
                                              updateEdit(provider.id, {
                                                editingKey: true,
                                                apiKey: '',
                                              })
                                            }
                                            disabled={edit.saving || edit.removing}
                                          >
                                            <Pencil className="size-3" />
                                            {t('settings.backends.replaceApiKey')}
                                          </Button>
                                        </div>
                                      ) : (
                                        <div className="relative">
                                          <KeyRound className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
                                          <Input
                                            id={`opencode-key-${provider.id}`}
                                            type="password"
                                            autoComplete="off"
                                            spellCheck={false}
                                            placeholder={
                                              provider.configured
                                                ? (t(
                                                    'settings.backends.opencodeProviderApiKeyPlaceholderStored'
                                                  ) as string)
                                                : (t(
                                                    'settings.backends.opencodeProviderApiKeyPlaceholder'
                                                  ) as string)
                                            }
                                            value={edit.apiKey}
                                            onChange={(e) =>
                                              updateEdit(provider.id, { apiKey: e.target.value })
                                            }
                                            className="pl-9 font-mono"
                                            disabled={edit.saving}
                                            autoFocus={edit.editingKey}
                                          />
                                        </div>
                                      )}
                                      <div className="flex items-center justify-between gap-2">
                                        <p className="text-[11px] text-muted">
                                          {provider.configured
                                            ? t('settings.backends.opencodeProviderApiKeyStored')
                                            : t('settings.backends.opencodeProviderApiKeyMissing')}
                                        </p>
                                        {provider.configured && edit.editingKey && (
                                          <button
                                            type="button"
                                            className="text-[11px] text-muted underline-offset-2 transition hover:text-foreground hover:underline"
                                            onClick={() =>
                                              updateEdit(provider.id, {
                                                editingKey: false,
                                                apiKey: '',
                                              })
                                            }
                                          >
                                            {t('common.cancel')}
                                          </button>
                                        )}
                                      </div>
                                    </div>

                                    <div className="flex flex-col gap-1.5">
                                      <Label
                                        htmlFor={`opencode-base-url-${provider.id}`}
                                        className="text-[11px] font-medium uppercase text-muted"
                                      >
                                        {t('settings.backends.opencodeProviderBaseUrl')}
                                      </Label>
                                      <div className="flex gap-2">
                                        <Input
                                          id={`opencode-base-url-${provider.id}`}
                                          type="url"
                                          autoComplete="off"
                                          spellCheck={false}
                                          placeholder={
                                            t(
                                              'settings.backends.opencodeProviderBaseUrlPlaceholder'
                                            ) as string
                                          }
                                          value={edit.baseUrl}
                                          onChange={(e) =>
                                            updateEdit(provider.id, { baseUrl: e.target.value })
                                          }
                                          className="font-mono"
                                          disabled={edit.saving}
                                        />
                                        <Button
                                          type="button"
                                          variant="secondary"
                                          size="sm"
                                          onClick={() => updateEdit(provider.id, { baseUrl: '' })}
                                          disabled={!edit.baseUrl || edit.saving}
                                        >
                                          <RotateCcw className="size-3.5" />
                                        </Button>
                                      </div>
                                      <p className="text-[11px] text-muted">
                                        {t('settings.backends.opencodeProviderBaseUrlHint')}
                                      </p>
                                    </div>

                                    {edit.error && (
                                      <div className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-[12px] text-destructive">
                                        {edit.error}
                                      </div>
                                    )}

                                    <div className="flex flex-wrap items-center justify-end gap-2">
                                      <Button
                                        type="button"
                                        variant="brand"
                                        size="sm"
                                        onClick={() => void onSaveProviderAuth(provider)}
                                        disabled={edit.saving}
                                      >
                                        {edit.saving ? (
                                          <RefreshCw className="size-3.5 animate-spin" />
                                        ) : (
                                          <Save className="size-3.5" />
                                        )}
                                        {edit.saving
                                          ? t('common.saving')
                                          : t('settings.backends.opencodeProviderSave')}
                                      </Button>
                                    </div>
                                  </div>

                                  <div className="flex flex-col gap-2">
                                    <Label className="text-[11px] font-medium uppercase text-muted">
                                      {t('settings.backends.opencodeProviderModels')}
                                    </Label>
                                    <div className="max-h-56 overflow-y-auto rounded-md border border-border bg-surface px-3 py-2">
                                      {provider.models.length === 0 ? (
                                        <p className="text-[12px] text-muted">
                                          {t('settings.backends.opencodeProviderModelsEmpty')}
                                        </p>
                                      ) : (
                                        <ul className="flex flex-col gap-1">
                                          {provider.models.map((model) => (
                                            <li
                                              key={model}
                                              className="flex items-center gap-2 font-mono text-[12px] text-foreground"
                                            >
                                              <Cpu className="size-3 text-muted" />
                                              <span className="truncate">{model}</span>
                                              {provider.default_model === model && (
                                                <Badge variant="success" className="ml-auto">
                                                  {t(
                                                    'settings.backends.opencodeProviderDefaultModel'
                                                  )}
                                                </Badge>
                                              )}
                                            </li>
                                          ))}
                                        </ul>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}

                  <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2/60 px-3 py-2.5">
                    <Info className="mt-0.5 size-3.5 shrink-0 text-muted" />
                    <p className="text-[12px] leading-relaxed text-muted">
                      {t('settings.backends.opencodeProvidersInfo')}
                    </p>
                  </div>
                </>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </SettingsPageShell>
  );
};
