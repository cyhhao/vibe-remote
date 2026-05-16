import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

import { useApi } from '@/context/ApiContext';
import { useToast } from '@/context/ToastContext';

export type CliStatus = 'unknown' | 'ok' | 'missing';

export type BackendId = 'claude' | 'codex' | 'opencode';

export interface InstallResult {
  ok: boolean;
  message: string;
  output?: string | null;
}

export interface UseBackendRuntimeOptions {
  /** Backend identifier used in V2Config keys and install_agent dispatch. */
  backend: BackendId;
  /** Fallback CLI binary name when V2Config carries no override. */
  defaultCli: string;
  /**
   * Default backend used by the routing layer when the user has not picked
   * one explicitly. The Save path persists this into ``agents.default_
   * backend`` so toggling enabled-state never wipes the global default.
   */
  fallbackDefaultBackend?: BackendId;
}

export interface BackendRuntimeState {
  /** Initial V2Config load attempt has finished (success or failure). */
  loaded: boolean;
  /**
   * ``true`` when the initial ``getConfig()`` rejected. In that state,
   * ``enabled`` / ``cliPath`` are still their pre-load defaults rather
   * than reflecting persisted state, so consumers MUST treat
   * ``loaded && !configError`` — not just ``loaded`` — as the gate for
   * any side-effect that depends on actual backend state (e.g.
   * OpenCode's providers fan-out).
   */
  configError: boolean;
  enabled: boolean;
  cliPath: string;
  cliStatus: CliStatus;
  detecting: boolean;
  installing: boolean;
  installResult: InstallResult | null;
  installOutputOpen: boolean;
  savingRuntime: boolean;
  /** True once the user has typed a path different from the saved one. */
  runtimeDirty: boolean;

  setCliPath: (next: string) => void;
  setInstallOutputOpen: (open: boolean | ((prev: boolean) => boolean)) => void;
  /** Runs ``detectCli`` and updates ``cliPath`` + ``cliStatus``. */
  detect: (binary?: string) => Promise<void>;
  /** Calls ``installAgent`` then re-runs detect with the resolved path. */
  install: () => Promise<void>;
  /** Persists ``enabled`` + ``cliPath`` into V2Config. */
  onSaveRuntime: () => Promise<void>;
  /** Optimistically flip ``enabled`` + persist; rolls back on save failure. */
  toggleEnabled: () => void;
  /**
   * Pass to ``BackendLifecycleChip.onChanged``. Updates ``cliPath`` when
   * the chip reports a fresh install path and re-runs detect.
   */
  handleLifecycleChanged: (info: { installedPath?: string | null } | undefined | null) => Promise<void>;
}

/**
 * Encapsulates the runtime (CLI lifecycle) state shared by every
 * Settings → Backends provider page. Previously each page (Claude /
 * OpenCode / Codex) duplicated ~120 lines of state declarations + the
 * same detect / install / save / toggle handlers; differences were the
 * backend id and the fallback CLI name. Pulling it into a hook means
 * the next backend gets the same lifecycle for free, and any bug fix
 * lands once.
 */
export function useBackendRuntime({
  backend,
  defaultCli,
  fallbackDefaultBackend = 'opencode',
}: UseBackendRuntimeOptions): BackendRuntimeState {
  const api = useApi();
  const { showToast } = useToast();
  const { t } = useTranslation();

  const [loaded, setLoaded] = useState(false);
  const [configError, setConfigError] = useState(false);
  const [enabled, setEnabled] = useState(true);
  const [cliPath, setCliPath] = useState(defaultCli);
  const [savedCliPath, setSavedCliPath] = useState(defaultCli);
  const [cliStatus, setCliStatus] = useState<CliStatus>('unknown');
  const [detecting, setDetecting] = useState(false);
  const [installing, setInstalling] = useState(false);
  const [installResult, setInstallResult] = useState<InstallResult | null>(null);
  const [installOutputOpen, setInstallOutputOpen] = useState(false);
  const [savingRuntime, setSavingRuntime] = useState(false);

  const detect = useCallback(
    async (binary?: string) => {
      setDetecting(true);
      try {
        const result = await api.detectCli(binary || cliPath || defaultCli);
        const nextPath = result.path || cliPath || defaultCli;
        setCliPath(nextPath);
        setCliStatus(result.found ? 'ok' : 'missing');
      } catch (e: any) {
        setCliStatus('missing');
        showToast(e?.message || t('common.saveFailed'), 'error');
      } finally {
        setDetecting(false);
      }
    },
    [api, cliPath, defaultCli, showToast, t],
  );

  // Initial load: read V2Config for ``enabled`` + ``cli_path`` and then
  // probe the CLI. Independent of any auth-state fetch the page also
  // performs — that's owned by the page, not the runtime hook.
  useEffect(() => {
    let cancelled = false;
    api
      .getConfig()
      .then((config) => {
        if (cancelled) return;
        const agent = config?.agents?.[backend];
        const initialEnabled = typeof agent?.enabled === 'boolean' ? agent.enabled : true;
        const initialPath = agent?.cli_path || defaultCli;
        setEnabled(initialEnabled);
        setCliPath(initialPath);
        setSavedCliPath(initialPath);
        setConfigError(false);
        setLoaded(true);
        void detect(initialPath);
      })
      .catch((e: any) => {
        if (cancelled) return;
        // We still flip ``loaded`` so the page can drop its loading
        // skeleton, but ``configError`` tells consumers the persisted
        // state was never read — gate any side-effect that depends on
        // ``enabled`` on ``!configError`` to avoid acting on a default
        // we never confirmed (e.g. firing provider fetches when the
        // backend may actually be disabled).
        setConfigError(true);
        setLoaded(true);
        showToast(e?.message || t('common.saveFailed'), 'error');
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, backend, defaultCli]);

  const install = useCallback(async () => {
    setInstalling(true);
    setInstallResult(null);
    setInstallOutputOpen(false);
    try {
      const result = await api.installAgent(backend);
      const installedPath =
        typeof result.path === 'string' && result.path ? result.path : null;
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
  }, [api, backend, cliPath, detect, showToast, t]);

  const onSaveRuntime = useCallback(async () => {
    setSavingRuntime(true);
    try {
      const config = await api.getConfig();
      const nextAgents = {
        ...(config?.agents || {}),
        [backend]: {
          ...(config?.agents?.[backend] || {}),
          enabled,
          cli_path: cliPath || defaultCli,
        },
      };
      const defaultBackend =
        config?.default_backend ||
        config?.agents?.default_backend ||
        fallbackDefaultBackend;
      await api.saveConfig({ agents: { ...nextAgents, default_backend: defaultBackend } });
      const restart = await api.restartBackend(backend);
      if (!restart?.ok) {
        throw new Error(restart?.message || t('common.saveFailed'));
      }
      setSavedCliPath(cliPath);
      showToast(t('common.saved'), 'success');
    } catch (e: any) {
      showToast(e?.message || t('common.saveFailed'), 'error');
    } finally {
      setSavingRuntime(false);
    }
  }, [api, backend, cliPath, defaultCli, enabled, fallbackDefaultBackend, showToast, t]);

  const toggleEnabled = useCallback(() => {
    const next = !enabled;
    setEnabled(next);
    // Persist immediately so the routing layer picks up the flip
    // without forcing the user to also click Save (the Save button
    // is reserved for cli_path edits). Roll back on failure.
    void (async () => {
      try {
        const config = await api.getConfig();
        const nextAgents = {
          ...(config?.agents || {}),
          [backend]: {
            ...(config?.agents?.[backend] || {}),
            enabled: next,
          },
        };
        const defaultBackend =
          config?.default_backend ||
          config?.agents?.default_backend ||
          fallbackDefaultBackend;
        await api.saveConfig({
          agents: { ...nextAgents, default_backend: defaultBackend },
        });
      } catch (e: any) {
        showToast(e?.message || t('common.saveFailed'), 'error');
        setEnabled(!next);
      }
    })();
  }, [api, backend, enabled, fallbackDefaultBackend, showToast, t]);

  const handleLifecycleChanged = useCallback(
    async (info: { installedPath?: string | null } | undefined | null) => {
      const installedPath = info?.installedPath || null;
      if (installedPath) setCliPath(installedPath);
      await detect(installedPath || cliPath);
    },
    [cliPath, detect],
  );

  const runtimeDirty = cliPath !== savedCliPath;

  return {
    loaded,
    configError,
    enabled,
    cliPath,
    cliStatus,
    detecting,
    installing,
    installResult,
    installOutputOpen,
    savingRuntime,
    runtimeDirty,
    setCliPath,
    setInstallOutputOpen,
    detect,
    install,
    onSaveRuntime,
    toggleEnabled,
    handleLifecycleChanged,
  };
}
