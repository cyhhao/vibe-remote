import React, { createContext, useContext, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useToast } from './ToastContext';
import { apiFetch } from '../lib/apiFetch';

export type ApiContextType = {
  getConfig: () => Promise<any>;
  getPlatformCatalog: () => Promise<any>;
  saveConfig: (payload: any) => Promise<any>;
  getSettings: (platform?: string) => Promise<any>;
  saveSettings: (payload: any, platform?: string) => Promise<any>;
  getUsers: (platform?: string) => Promise<any>;
  saveUsers: (payload: any, platform?: string) => Promise<any>;
  toggleAdmin: (userId: string, isAdmin: boolean, platform?: string) => Promise<any>;
  removeUser: (userId: string, platform?: string) => Promise<any>;
  getBindCodes: () => Promise<any>;
  createBindCode: (type: string, expiresAt?: string) => Promise<any>;
  deleteBindCode: (code: string) => Promise<any>;
  getFirstBindCode: () => Promise<any>;
  detectCli: (binary: string) => Promise<any>;
  installAgent: (name: string) => Promise<InstallResult>;
  getBackendRuntime: (name: string) => Promise<BackendRuntimeInfo>;
  restartBackend: (name: string) => Promise<BackendRestartResult>;
  getCodexAuth: () => Promise<CodexAuthState>;
  saveCodexAuth: (payload: CodexAuthPayload) => Promise<CodexAuthSaveResult>;
  getClaudeAuth: () => Promise<ClaudeAuthState>;
  saveClaudeAuth: (payload: ClaudeAuthPayload) => Promise<ClaudeAuthSaveResult>;
  startOAuthWeb: (backend: 'claude' | 'codex', forceReset?: boolean) => Promise<OAuthWebStartResult>;
  startOAuthWebForOpencodeProvider: (
    providerId: string,
    forceReset?: boolean,
  ) => Promise<OAuthWebStartResult>;
  getOAuthWebStatus: (
    backend: 'claude' | 'codex' | 'opencode',
    flowId: string,
  ) => Promise<OAuthWebStatus>;
  submitOAuthWebCode: (
    backend: 'claude' | 'codex' | 'opencode',
    flowId: string,
    code: string,
  ) => Promise<OAuthWebMutationResult>;
  cancelOAuthWeb: (
    backend: 'claude' | 'codex' | 'opencode',
    flowId: string,
  ) => Promise<OAuthWebMutationResult>;
  removeBackendAuth: (backend: 'claude' | 'codex') => Promise<OAuthWebMutationResult>;
  // Selectively clear just the stored API key — leave OAuth credentials
  // intact. Symmetric to OpenCode's per-provider DELETE: lets the user
  // drop a stale key without re-signing in. Codex also restarts its
  // persistent daemon so the cleared key takes effect on the next
  // request.
  removeBackendApiKey: (backend: 'claude' | 'codex') => Promise<OAuthWebMutationResult>;
  testBackendAuth: (
    backend: 'claude' | 'codex',
    options?: { model?: string },
  ) => Promise<BackendAuthTestResult>;
  testOpencodeProvider: (
    providerId: string,
    options?: { model?: string },
  ) => Promise<BackendAuthTestResult>;
  getOpencodeProviders: () => Promise<OpencodeProviderListResult>;
  setOpencodeProviderAuth: (
    providerId: string,
    apiKey: string,
    baseUrl?: string,
  ) => Promise<OpencodeMutationResult>;
  deleteOpencodeProviderAuth: (providerId: string) => Promise<OpencodeMutationResult>;
  setOpencodeDefaultProvider: (providerId: string) => Promise<OpencodeMutationResult>;
  slackAuthTest: (botToken: string, proxyUrl?: string) => Promise<any>;
  slackChannels: (botToken: string, browseAll?: boolean, force?: boolean) => Promise<any>;
  slackManifest: () => Promise<{ ok: boolean; manifest?: string; manifest_compact?: string; error?: string }>;
  discordAuthTest: (botToken: string, proxyUrl?: string) => Promise<any>;
  discordGuilds: (botToken: string) => Promise<any>;
  discordChannels: (botToken: string, guildId: string, force?: boolean) => Promise<any>;
  telegramAuthTest: (botToken: string, proxyUrl?: string) => Promise<any>;
  telegramChats: (includePrivate?: boolean) => Promise<any>;
  larkAuthTest: (appId: string, appSecret: string, domain?: string, proxyUrl?: string) => Promise<any>;
  larkChats: (appId: string, appSecret: string, domain?: string, force?: boolean) => Promise<any>;
  larkTempWsStart: (appId: string, appSecret: string, domain?: string) => Promise<any>;
  larkTempWsStop: () => Promise<any>;
  wechatStartLogin: () => Promise<any>;
  wechatPollLogin: (sessionKey: string) => Promise<any>;
  doctor: () => Promise<any>;
  opencodeOptions: (cwd: string) => Promise<any>;
  opencodeSetupPermission: () => Promise<{ ok: boolean; message: string; config_path: string }>;
  claudeAgents: (cwd?: string) => Promise<{ ok: boolean; agents?: { id: string; name: string; path: string; source?: string }[]; error?: string }>;
  claudeModels: () => Promise<{ ok: boolean; models?: string[]; reasoning_options?: Record<string, { value: string; label: string }[]>; error?: string }>;
  codexAgents: (cwd?: string) => Promise<{ ok: boolean; agents?: { id: string; name: string; path: string; source?: string; description?: string }[]; error?: string }>;
  codexModels: () => Promise<{ ok: boolean; models?: string[]; error?: string }>;
  getLogs: (lines?: number, source?: string) => Promise<{ logs: LogEntry[]; total: number; source: string; sources: LogSource[] }>;
  getVersion: () => Promise<VersionInfo>;
  doUpgrade: () => Promise<UpgradeResult>;
  browseDirectory: (path: string, showHidden?: boolean) => Promise<{ ok: boolean; path?: string; parent?: string | null; dirs?: { name: string; path: string }[]; error?: string }>;
  browseMkdir: (path: string) => Promise<{ path: string }>;
  listProjects: (includeArchived?: boolean) => Promise<{ projects: WorkbenchProject[] }>;
  createProject: (payload: { folder_path: string; display_name?: string }) => Promise<WorkbenchProject>;
  updateProject: (projectId: string, payload: { display_name?: string; folder_path?: string }) => Promise<WorkbenchProject>;
  archiveProject: (projectId: string) => Promise<WorkbenchProject>;
  listSessions: (params?: { projectId?: string; status?: 'active' | 'archived' | 'all'; limit?: number; beforeId?: string }) => Promise<{ sessions: WorkbenchSession[]; next_before_id: string | null }>;
  createSession: (payload: WorkbenchSessionCreate) => Promise<WorkbenchSession>;
  getSession: (sessionId: string) => Promise<WorkbenchSession>;
  updateSession: (sessionId: string, payload: Partial<WorkbenchSessionUpdate>) => Promise<WorkbenchSession>;
  archiveSession: (sessionId: string) => Promise<WorkbenchSession>;
  listSessionMessages: (sessionId: string, params?: { afterId?: string; limit?: number }) => Promise<{ messages: WorkbenchMessage[]; next_after_id: string | null }>;
  sendSessionMessage: (sessionId: string, payload: { text?: string; content?: Record<string, unknown>; metadata?: Record<string, unknown>; author_id?: string; author_name?: string }) => Promise<WorkbenchMessage>;
  markSessionRead: (sessionId: string, untilMessageId?: string) => Promise<{ updated: number; unread_counts: Record<string, number> }>;
  listInbox: (params?: { platform?: string; unreadOnly?: boolean; limit?: number; beforeId?: string }) => Promise<{ messages: WorkbenchMessage[]; next_before_id: string | null; unread_counts: Record<string, number> }>;
  connectWorkbenchEvents: (handlers: WorkbenchEventHandlers) => () => void;
  remoteAccessStatus: () => Promise<any>;
  pairVibeCloudRemoteAccess: (payload: { backend_url: string; pairing_key: string; device_name?: string }) => Promise<any>;
  startRemoteAccess: () => Promise<any>;
  stopRemoteAccess: () => Promise<any>;
  getAuthSession: () => Promise<SessionInfo>;
  signOut: () => Promise<{ ok: boolean }>;
};

// Workbench project — a scope row with platform='avibe' / scope_type='project'.
// ``folder_path`` mirrors ``scope_settings.workdir`` and is what Agent runs
// pick up as their cwd.
export type WorkbenchProject = {
  id: string;
  scope_id: string;
  display_name: string;
  folder_path: string;
  created_at: string;
  last_active_at: string | null;
  archived: boolean;
  metadata?: Record<string, unknown>;
};

// Workbench session — a row in ``agent_sessions`` created via /api/sessions.
// ``project_id`` is the short ``proj_<hex>`` suffix of ``scope_id``.
export type WorkbenchSession = {
  id: string;
  scope_id: string | null;
  project_id: string | null;
  title: string | null;
  agent_id: string | null;
  agent_name: string | null;
  agent_backend: string | null;
  agent_variant: string | null;
  model: string | null;
  reasoning_effort: string | null;
  status: string;
  workdir: string | null;
  native_session_id: string | null;
  created_at: string;
  updated_at: string;
  last_active_at: string | null;
  metadata: Record<string, unknown>;
};

export type WorkbenchSessionCreate = {
  project_id: string;
  agent_backend: string;
  agent_id?: string;
  agent_name?: string;
  agent_variant?: string;
  model?: string;
  reasoning_effort?: string;
  title?: string;
  metadata?: Record<string, unknown>;
};

export type WorkbenchSessionUpdate = {
  title: string | null;
  agent_id: string | null;
  agent_name: string | null;
  agent_backend: string;
  agent_variant: string;
  model: string | null;
  reasoning_effort: string | null;
};

// Events streamed by ``GET /api/events`` — the broker JSON-encodes each
// payload as ``{type, data, ts}``. ``connectWorkbenchEvents`` parses and
// dispatches to type-specific handlers; subscribers can also catch any
// event via ``onAny`` for logging/analytics.
export type WorkbenchEventEnvelope<T = unknown> = {
  type: string;
  data: T;
  ts: number;
};

export type WorkbenchEventHandlers = {
  onConnected?: (data: { sub_id: number }) => void;
  onMessageNew?: (data: WorkbenchMessage) => void;
  onSessionActivity?: (data: { session_id: string; scope_id: string | null; event: string }) => void;
  onInboxUnreadChanged?: (data: {
    session_id?: string;
    scope_id?: string | null;
    delta?: number;
    unread_counts: Record<string, number>;
  }) => void;
  onAny?: (event: WorkbenchEventEnvelope) => void;
  onError?: (err: Event) => void;
};

// One row from the platform-agnostic ``messages`` table.
export type WorkbenchMessage = {
  id: string;
  scope_id: string | null;
  session_id: string | null;
  platform: string;
  author: 'user' | 'agent' | 'system' | string;
  author_id: string | null;
  author_name: string | null;
  native_message_id: string | null;
  parent_native_message_id: string | null;
  text: string;
  content: Record<string, unknown>;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  delivered_at: string | null;
  read_at: string | null;
};

export type SessionInfo =
  | { remote: false }
  | { remote: true; authenticated: false }
  | { remote: true; authenticated: true; email: string };

export type LogEntry = {
  timestamp: string;
  level: string;
  logger: string;
  message: string;
  source: string;
};

export type LogSource = {
  key: string;
  filename: string;
  path: string;
  exists: boolean;
  total: number;
  logs?: LogEntry[];
};

export type VersionInfo = {
  current: string;
  latest: string | null;
  has_update: boolean;
  error: string | null;
};

export type UpgradeResult = {
  ok: boolean;
  message: string;
  output: string | null;
  restarting: boolean;
};

export type InstallResult = {
  ok: boolean;
  message: string;
  output: string | null;
  path?: string | null;
  job_id?: string;
  status?: 'running' | 'succeeded' | 'failed';
};

export type BackendRuntimeInfo = {
  ok: boolean;
  name?: string;
  enabled?: boolean;
  cli_path?: string;
  resolved_path?: string | null;
  installed?: boolean;
  current_version?: string | null;
  latest_version?: string | null;
  has_update?: boolean;
  supports_restart?: boolean;
  process_status?: 'running' | 'stopped' | 'unknown';
  error?: string;
};

export type BackendRestartResult = {
  ok: boolean;
  message: string;
};

export type CodexAuthMode = 'oauth' | 'api_key';

// Mirrors Codex CLI's ``cli_auth_credentials_store`` setting. ``auto`` is
// Codex's documented default and is treated as keyring-preferred — when
// the live store is not ``file`` the on-disk ``auth.json`` may not be
// the source of truth, so the UI must not interpret ``has_api_key=false``
// as "no key configured" in that case.
export type CodexCredentialsStore = 'file' | 'keyring' | 'auto' | (string & {});

export type ActiveAuthMode = 'oauth' | 'api_key' | 'none';

// Identity decoded from the ChatGPT JWT inside ``~/.codex/auth.json``.
// All fields are best-effort — the OAuth bundle may carry partial
// claims, in which case the panel renders only what's present.
export type CodexChatGptAccount = {
  email: string | null;
  name: string | null;
  plan_type: string | null;
  organizations: Array<{
    id: string | null;
    title: string | null;
    role: string | null;
    is_default: boolean;
  }> | null;
};

export type CodexAuthState = {
  ok: boolean;
  auth_mode: CodexAuthMode;
  // What the running Codex CLI is actually using at launch — separate
  // from ``auth_mode`` which is the user's saved intent. Lets the UI
  // surface "Currently active: …" so the two-radio choice is no longer
  // ambiguous about which mode is live.
  active_auth_mode: ActiveAuthMode;
  has_api_key: boolean;
  api_key_length: number;
  // Server-masked preview (e.g. ``sk-proj-•••••••••H8mN``). Used to
  // pre-fill the API Key input so the page reflects the saved state
  // instead of looking empty. Plaintext keys never leave the server.
  api_key_masked: string | null;
  base_url: string | null;
  has_chatgpt_tokens: boolean;
  chatgpt_account?: CodexChatGptAccount | null;
  credentials_store: CodexCredentialsStore;
  file_store_active: boolean;
  // True when Codex is in keyring-preferred mode and disk shows no
  // key/tokens — the live auth may live in the OS keychain (we cannot
  // portably read it). UI must not claim "no key configured" in that
  // case; it should prompt the user to choose a mode (saving will pin
  // file storage so subsequent reads work).
  auth_mode_uncertain?: boolean;
  message?: string;
};

export type CodexAuthPayload = {
  auth_mode: CodexAuthMode;
  api_key?: string | null;
  base_url?: string | null;
};

// Non-fatal warning the server attached to a config-mutation response.
// Used today for "we cleared a custom relay pointer because OAuth tokens
// won't validate against your custom base_url"; new codes can be added
// without touching the type.
export type BackendNotice = {
  code: string;
  provider_id?: string;
  base_url?: string;
  detail?: string;
};

export type CodexAuthSaveResult = CodexAuthState & {
  restart?: BackendRestartResult;
  notices?: BackendNotice[];
};

export type ClaudeAuthMode = 'oauth' | 'api_key';

// Claude Code reads ``~/.claude/settings.json`` at launch and its ``env``
// block wins over inherited process env. Vibe Remote therefore writes
// API-key auth into that file directly; ``v2config`` only appears for
// legacy installs that have not yet been migrated by the next save.
export type ClaudeApiKeySource = 'v2config' | 'settings_json' | null;

export type ClaudeAuthState = {
  ok: boolean;
  auth_mode: ClaudeAuthMode;
  // Live source the CLI is actually inheriting at launch (api_key when
  // V2Config injects ``ANTHROPIC_API_KEY`` and strips OAuth env vars,
  // oauth when ``~/.claude/credentials.json`` has a usable token).
  active_auth_mode: ActiveAuthMode;
  has_api_key: boolean;
  api_key_length: number;
  api_key_masked: string | null;
  api_key_source?: ClaudeApiKeySource;
  has_oauth_credentials: boolean;
  base_url: string | null;
  settings_path: string | null;
  settings_exists: boolean;
  settings_env_has_key: boolean;
  settings_env_key_length: number;
  settings_env_key_var: 'ANTHROPIC_API_KEY' | 'ANTHROPIC_AUTH_TOKEN' | null;
  settings_env_base_url: string | null;
  settings_conflict: boolean;
  message?: string;
};

export type ClaudeAuthPayload = {
  auth_mode: ClaudeAuthMode;
  api_key?: string | null;
  base_url?: string | null;
};

export type ClaudeAuthSaveResult = ClaudeAuthState & {
  restart?: BackendRestartResult;
};

// One entry in the OpenCode provider grid. The full catalog is built
// dynamically on the server by merging ``/provider`` + ``/provider/auth``
// + ``/config/providers`` — there is **no** hard-coded list in the UI.
// ``local`` is inferred from the absence of network auth methods (Ollama,
// LM Studio); the page renders its own "Local" badge for those rows.
export type OAuthWebState =
  | 'starting'
  | 'awaiting_code'
  | 'verifying'
  | 'success'
  | 'failed'
  | 'cancelled';

export type OAuthWebStartResult = {
  ok: boolean;
  flow_id?: string;
  backend?: 'claude' | 'codex';
  state?: OAuthWebState;
  url?: string | null;
  device_code?: string | null;
  awaiting_code?: boolean;
  error?: string;
  detail?: string;
};

export type OAuthWebStatus = {
  ok: boolean;
  flow_id?: string;
  backend?: 'claude' | 'codex';
  state?: OAuthWebState;
  url?: string | null;
  device_code?: string | null;
  awaiting_code?: boolean;
  error?: string | null;
};

export type OAuthWebMutationResult = {
  ok: boolean;
  error?: string;
  detail?: string;
  notices?: BackendNotice[];
  // ``partial: true`` rides on ``ok: true`` when the V2Config side of
  // the operation succeeded but the CLI subprocess (``codex logout`` /
  // ``claude auth logout``) reported a non-zero exit. The caller should
  // show a warning rather than a green success — credentials may still
  // be on disk. Pairs with ``warning`` (machine-readable code) and
  // ``detail`` (human-readable excerpt).
  partial?: boolean;
  warning?: string;
};

export type BackendAuthTestResult = {
  ok: boolean;
  duration_ms?: number;
  excerpt?: string;
  exit_code?: number;
  error?: string;
  detail?: string;
};

export type OpencodeProvider = {
  id: string;
  name: string;
  description: string;
  configured: boolean;
  oauth_available: boolean;
  local: boolean;
  models: string[];
  default_model: string | null;
  // Optional ``baseURL`` override persisted in opencode.json. Surfaced so
  // the Settings page can pre-populate the Base URL input with the last
  // saved value instead of starting empty on every reload.
  base_url?: string | null;
  // Server-masked preview of the api-type credential stored in
  // ``~/.local/share/opencode/auth.json`` (e.g. ``sk-proj-•••H8mN``).
  // ``null``/missing when the provider uses OAuth or hasn't been
  // configured yet. Mirrors Claude / Codex's ``api_key_masked`` so the
  // user can see at a glance which providers have a stored key without
  // having to expand each card.
  api_key_masked?: string | null;
  // ``api`` / ``oauth`` / null — the auth type currently stored for the
  // provider. OpenCode's ``auth.json`` only carries ONE entry per
  // provider at a time, so this is also the type that will be used at
  // launch. Lets the UI badge dual-mode providers (e.g. openai) with
  // which source is live, instead of leaving the user guessing.
  active_auth_type?: 'api' | 'oauth' | string | null;
};

export type OpencodeProviderListResult = {
  ok: boolean;
  message?: string;
  providers?: OpencodeProvider[];
  default_provider?: string;
  // True when ``opencode.json`` has ``permission: "allow"`` — the
  // setting that lets OpenCode skip the interactive tool-call approval
  // prompt Vibe Remote can't reply to. The Settings page hides the
  // "Allow tool calls" affordance when this is already true.
  permission_allowed?: boolean;
};

export type OpencodeMutationResult = {
  ok: boolean;
  message?: string;
  default_provider?: string;
};

const ApiContext = createContext<ApiContextType | undefined>(undefined);

export const useApi = () => {
  const context = useContext(ApiContext);
  if (!context) {
    throw new Error('useApi must be used within ApiProvider');
  }
  return context;
};

export const ApiProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { showToast } = useToast();
  const { t } = useTranslation();

  const handleApiError = async (res: Response, path: string) => {
    let errorMessage = `Request failed: ${path} (${res.status})`;
    
    try {
      const data = await res.json();
      if (data.error) {
        errorMessage = t(`errors.${data.error}`, { defaultValue: data.error });
      }
    } catch {
      // Response is not JSON, use status text
      errorMessage = `${path}: ${res.statusText || 'Unknown error'} (${res.status})`;
    }

    // Log error details to console
    console.error(`[API Error] ${path}`, {
      status: res.status,
      statusText: res.statusText,
      error: errorMessage,
    });

    // Show toast to user
    showToast(errorMessage, 'error');

    throw new Error(errorMessage);
  };

  const getJson = async (path: string) => {
    const res = await apiFetch(path);
    if (!res.ok) {
      await handleApiError(res, path);
    }
    return res.json();
  };

  const postJson = async (path: string, payload: any) => {
    const res = await apiFetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      await handleApiError(res, path);
    }
    return res.json();
  };

  // DELETE wrapper that routes 4xx/5xx through ``handleApiError`` so the
  // global toast and console-error surface stay consistent with
  // ``getJson``/``postJson``. Legacy callers (removeUser, deleteBindCode)
  // still call ``apiFetch().then(r => r.json())`` directly — that's a
  // separate cleanup; new endpoints should use this helper.
  const deleteJson = async (path: string) => {
    const res = await apiFetch(path, { method: 'DELETE' });
    if (!res.ok) {
      await handleApiError(res, path);
    }
    return res.json();
  };

  const sleep = (ms: number) => new Promise((resolve) => window.setTimeout(resolve, ms));

  const startAndPollAgentInstall = async (name: string): Promise<InstallResult> => {
    const started = await postJson(`/agent/${encodeURIComponent(name)}/install`, {});
    const jobId = typeof started?.job_id === 'string' ? started.job_id : null;
    if (!jobId) return started;

    const deadline = Date.now() + 310_000;
    let last = started;
    while (Date.now() < deadline) {
      await sleep(1000);
      last = await getJson(
        `/agent/${encodeURIComponent(name)}/install/${encodeURIComponent(jobId)}`,
      );
      if (last?.status === 'succeeded' || last?.status === 'failed') {
        return last;
      }
    }
    return {
      ...last,
      ok: false,
      status: 'failed',
      message: last?.message || t('backendLifecycle.upgradeFailed'),
    };
  };

  // ``useMemo`` is load-bearing here, not a perf tweak. Without it,
  // ``ApiProvider`` produces a fresh ``value`` object on every render
  // — including the renders triggered by ToastProvider's state
  // updates above us in the tree. Each new ``value`` flips the
  // identity of ``api`` for every ``useApi()`` consumer, so any
  // ``useEffect(..., [api])`` re-runs on every toast.
  //
  // Concrete failure that this fix addresses (reported on iOS Safari
  // for PR #282): clicking "Copy" on the Codex device-code block
  // calls ``showToast('copied')`` → ToastProvider re-renders →
  // ApiProvider re-renders → ``value`` identity changes →
  // SettingsCodexProviderPage's mount effect re-runs → calls
  // ``getCodexAuth()`` → reads the disk state (still ``apikey``
  // because the OAuth flow hasn't completed) → ``setAuthMode("api_
  // key")`` → the segmented radio flips back to API Key mid-login.
  // Defensive patches at the event boundary (preventDefault,
  // disabled buttons, setter guards) didn't help because the click
  // wasn't the trigger — the cascading re-render was.
  //
  // ``[showToast, t]`` are intentional deps: ``showToast`` is stable
  // (``useCallback`` in ToastContext) so it never invalidates by
  // itself; ``t`` only changes on locale switch — recomputing then
  // is correct (cached error messages would otherwise stay in the
  // old language).
  const value: ApiContextType = useMemo(() => ({
    getConfig: () => getJson('/config'),
    getPlatformCatalog: () => getJson('/platforms'),
    saveConfig: (payload) => postJson('/config', payload),
    getSettings: (platform) => getJson(platform ? `/settings?platform=${encodeURIComponent(platform)}` : '/settings'),
    saveSettings: (payload, platform) => postJson('/settings', platform ? { ...payload, platform } : payload),
    getUsers: (platform) => getJson(platform ? `/api/users?platform=${encodeURIComponent(platform)}` : '/api/users'),
    saveUsers: (payload, platform) => postJson('/api/users', platform ? { ...payload, platform } : payload),
    toggleAdmin: (userId, isAdmin, platform) => postJson(`/api/users/${encodeURIComponent(userId)}/admin`, platform ? { is_admin: isAdmin, platform } : { is_admin: isAdmin }),
    removeUser: (userId, platform) => apiFetch(platform ? `/api/users/${encodeURIComponent(userId)}?platform=${encodeURIComponent(platform)}` : `/api/users/${encodeURIComponent(userId)}`, { method: 'DELETE' }).then(r => r.json()),
    getBindCodes: () => getJson('/api/bind-codes'),
    createBindCode: (type, expiresAt) => postJson('/api/bind-codes', { type, expires_at: expiresAt }),
    deleteBindCode: (code) => apiFetch(`/api/bind-codes/${encodeURIComponent(code)}`, { method: 'DELETE' }).then(r => r.json()),
    getFirstBindCode: () => getJson('/api/setup/first-bind-code'),
    detectCli: (binary) => getJson(`/cli/detect?binary=${encodeURIComponent(binary)}`),
    installAgent: (name) => startAndPollAgentInstall(name),
    getBackendRuntime: (name) => getJson(`/backend/${encodeURIComponent(name)}/runtime`),
    restartBackend: (name) => postJson(`/backend/${encodeURIComponent(name)}/restart`, {}),
    getCodexAuth: () => getJson('/backend/codex/auth'),
    saveCodexAuth: (payload) => postJson('/backend/codex/auth', payload),
    getClaudeAuth: () => getJson('/backend/claude/auth'),
    saveClaudeAuth: (payload) => postJson('/backend/claude/auth', payload),
    startOAuthWeb: (backend, forceReset = true) =>
      postJson(`/backend/${encodeURIComponent(backend)}/auth/oauth/start`, {
        force_reset: forceReset,
      }),
    startOAuthWebForOpencodeProvider: (providerId, forceReset = true) =>
      postJson(
        `/backend/opencode/provider/${encodeURIComponent(providerId)}/auth/oauth/start`,
        { force_reset: forceReset },
      ),
    getOAuthWebStatus: (backend, flowId) =>
      getJson(
        `/backend/${encodeURIComponent(backend)}/auth/oauth/status/${encodeURIComponent(flowId)}`,
      ),
    submitOAuthWebCode: (backend, flowId, code) =>
      postJson(`/backend/${encodeURIComponent(backend)}/auth/oauth/submit-code`, {
        flow_id: flowId,
        code,
      }),
    cancelOAuthWeb: (backend, flowId) =>
      postJson(`/backend/${encodeURIComponent(backend)}/auth/oauth/cancel`, {
        flow_id: flowId,
      }),
    removeBackendAuth: (backend) =>
      postJson(`/backend/${encodeURIComponent(backend)}/auth/oauth/remove`, {}),
    removeBackendApiKey: (backend) =>
      postJson(`/backend/${encodeURIComponent(backend)}/auth/api-key/remove`, {}),
    testBackendAuth: (backend, options) =>
      postJson(`/backend/${encodeURIComponent(backend)}/auth/test`, {
        ...(options?.model ? { model: options.model } : {}),
      }),
    testOpencodeProvider: (providerId, options) =>
      postJson(`/backend/opencode/provider/${encodeURIComponent(providerId)}/test`, {
        ...(options?.model ? { model: options.model } : {}),
      }),
    getOpencodeProviders: () => getJson('/backend/opencode/providers'),
    setOpencodeProviderAuth: (providerId, apiKey, baseUrl) =>
      // Forward ``base_url`` only when the caller passed something
      // (including an explicit empty string for "clear"); omitting it
      // entirely tells the server to leave the stored value untouched,
      // which is the right default for callers that don't care about
      // the base-URL override.
      postJson(`/backend/opencode/provider/${encodeURIComponent(providerId)}/auth`, {
        api_key: apiKey,
        ...(baseUrl !== undefined ? { base_url: baseUrl } : {}),
      }),
    deleteOpencodeProviderAuth: (providerId) =>
      deleteJson(`/backend/opencode/provider/${encodeURIComponent(providerId)}/auth`),
    setOpencodeDefaultProvider: (providerId) =>
      postJson('/backend/opencode/default-provider', { provider_id: providerId }),
    slackAuthTest: (botToken, proxyUrl) => postJson('/slack/auth_test', { bot_token: botToken, proxy_url: proxyUrl || undefined }),
    slackChannels: (botToken, browseAll, force) => postJson('/slack/channels', { bot_token: botToken, browse_all: browseAll || false, force: force || false }),
    slackManifest: () => getJson('/slack/manifest'),
    discordAuthTest: (botToken, proxyUrl) => postJson('/discord/auth_test', { bot_token: botToken, proxy_url: proxyUrl || undefined }),
    discordGuilds: (botToken) => postJson('/discord/guilds', { bot_token: botToken }),
    discordChannels: (botToken, guildId, force) => postJson('/discord/channels', { bot_token: botToken, guild_id: guildId, force: force || false }),
    telegramAuthTest: (botToken, proxyUrl) => postJson('/telegram/auth_test', { bot_token: botToken, proxy_url: proxyUrl || undefined }),
    telegramChats: (includePrivate) => postJson('/telegram/chats', { include_private: includePrivate || false }),
    larkAuthTest: (appId, appSecret, domain, proxyUrl) => postJson('/lark/auth_test', { app_id: appId, app_secret: appSecret, domain: domain || 'feishu', proxy_url: proxyUrl || undefined }),
    larkChats: (appId, appSecret, domain, force) => postJson('/lark/chats', { app_id: appId, app_secret: appSecret, domain: domain || 'feishu', force: force || false }),
    larkTempWsStart: (appId, appSecret, domain) => postJson('/lark/temp_ws/start', { app_id: appId, app_secret: appSecret, domain: domain || 'feishu' }),
    larkTempWsStop: () => postJson('/lark/temp_ws/stop', {}),
    wechatStartLogin: () => postJson('/wechat/qr_login/start', {}),
    wechatPollLogin: (sessionKey) => postJson('/wechat/qr_login/poll', { session_key: sessionKey }),
    doctor: () => postJson('/doctor', {}),
    opencodeOptions: (cwd) => postJson('/opencode/options', { cwd }),
    opencodeSetupPermission: () => postJson('/opencode/setup-permission', {}),
    claudeAgents: (cwd) => cwd ? getJson(`/claude/agents?cwd=${encodeURIComponent(cwd)}`) : getJson('/claude/agents'),
    claudeModels: () => getJson('/claude/models'),
    codexAgents: (cwd) => cwd ? getJson(`/codex/agents?cwd=${encodeURIComponent(cwd)}`) : getJson('/codex/agents'),
    codexModels: () => getJson('/codex/models'),
    getLogs: (lines = 500, source) => postJson('/logs', source ? { lines, source } : { lines }),
    getVersion: () => getJson('/version'),
    doUpgrade: () => postJson('/upgrade', {}),
    browseDirectory: (path, showHidden) => postJson('/browse', { path, show_hidden: showHidden || false }),
    browseMkdir: (path) => postJson('/api/browse/mkdir', { path }),
    listProjects: (includeArchived) =>
      getJson(`/api/projects${includeArchived ? '?include_archived=1' : ''}`),
    createProject: (payload) => postJson('/api/projects', payload),
    updateProject: async (projectId, payload) => {
      const res = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        await handleApiError(res, `PATCH /api/projects/${projectId}`);
      }
      return res.json();
    },
    archiveProject: (projectId) => deleteJson(`/api/projects/${encodeURIComponent(projectId)}`),
    listSessions: (params) => {
      const search = new URLSearchParams();
      if (params?.projectId) search.set('project_id', params.projectId);
      if (params?.status) search.set('status', params.status);
      if (params?.limit) search.set('limit', String(params.limit));
      if (params?.beforeId) search.set('before_id', params.beforeId);
      const qs = search.toString();
      return getJson(qs ? `/api/sessions?${qs}` : '/api/sessions');
    },
    createSession: (payload) => postJson('/api/sessions', payload),
    getSession: (sessionId) => getJson(`/api/sessions/${encodeURIComponent(sessionId)}`),
    updateSession: async (sessionId, payload) => {
      const res = await apiFetch(`/api/sessions/${encodeURIComponent(sessionId)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        await handleApiError(res, `PATCH /api/sessions/${sessionId}`);
      }
      return res.json();
    },
    archiveSession: (sessionId) => deleteJson(`/api/sessions/${encodeURIComponent(sessionId)}`),
    listSessionMessages: (sessionId, params) => {
      const search = new URLSearchParams();
      if (params?.afterId) search.set('after_id', params.afterId);
      if (params?.limit) search.set('limit', String(params.limit));
      const qs = search.toString();
      const base = `/api/sessions/${encodeURIComponent(sessionId)}/messages`;
      return getJson(qs ? `${base}?${qs}` : base);
    },
    sendSessionMessage: (sessionId, payload) =>
      postJson(`/api/sessions/${encodeURIComponent(sessionId)}/messages`, payload),
    markSessionRead: (sessionId, untilMessageId) =>
      postJson(
        `/api/sessions/${encodeURIComponent(sessionId)}/mark-read`,
        untilMessageId ? { until_message_id: untilMessageId } : {},
      ),
    listInbox: (params) => {
      const search = new URLSearchParams();
      if (params?.platform) search.set('platform', params.platform);
      if (params?.unreadOnly) search.set('unread_only', '1');
      if (params?.limit) search.set('limit', String(params.limit));
      if (params?.beforeId) search.set('before_id', params.beforeId);
      const qs = search.toString();
      return getJson(qs ? `/api/inbox?${qs}` : '/api/inbox');
    },
    connectWorkbenchEvents: (handlers) => {
      // EventSource auto-reconnects on transient drops, so callers don't
      // have to implement their own retry. Returns a `disconnect` thunk so
      // React effects can clean up.
      const source = new EventSource('/api/events');
      const safeDispatch = <T,>(handler: ((data: T) => void) | undefined, raw: string) => {
        if (!handler) return;
        try {
          handler(JSON.parse(raw));
        } catch (err) {
          console.error('[workbench-events] parse failed', err, raw);
        }
      };
      source.addEventListener('connected', (e: MessageEvent) =>
        safeDispatch(handlers.onConnected, e.data),
      );
      source.addEventListener('message.new', (e: MessageEvent) => {
        const envelope = (() => {
          try {
            return JSON.parse(e.data) as WorkbenchEventEnvelope<WorkbenchMessage>;
          } catch {
            return null;
          }
        })();
        if (envelope) {
          handlers.onAny?.(envelope);
          handlers.onMessageNew?.(envelope.data);
        }
      });
      source.addEventListener('session.activity', (e: MessageEvent) => {
        const envelope = (() => {
          try {
            return JSON.parse(e.data) as WorkbenchEventEnvelope<any>;
          } catch {
            return null;
          }
        })();
        if (envelope) {
          handlers.onAny?.(envelope);
          handlers.onSessionActivity?.(envelope.data);
        }
      });
      source.addEventListener('inbox.unread.changed', (e: MessageEvent) => {
        const envelope = (() => {
          try {
            return JSON.parse(e.data) as WorkbenchEventEnvelope<any>;
          } catch {
            return null;
          }
        })();
        if (envelope) {
          handlers.onAny?.(envelope);
          handlers.onInboxUnreadChanged?.(envelope.data);
        }
      });
      source.onerror = (err) => handlers.onError?.(err);
      return () => source.close();
    },
    remoteAccessStatus: () => getJson('/remote-access/status'),
    pairVibeCloudRemoteAccess: (payload) => postJson('/remote-access/vibe-cloud/pair', payload),
    startRemoteAccess: () => postJson('/remote-access/start', {}),
    stopRemoteAccess: () => postJson('/remote-access/stop', {}),
    getAuthSession: () => getJson('/api/session'),
    signOut: () => postJson('/auth/logout', {}),
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [showToast, t]);

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
};
