import React, { createContext, useContext } from 'react';
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
  slackChannels: (botToken: string, browseAll?: boolean) => Promise<any>;
  slackManifest: () => Promise<{ ok: boolean; manifest?: string; manifest_compact?: string; error?: string }>;
  discordAuthTest: (botToken: string, proxyUrl?: string) => Promise<any>;
  discordGuilds: (botToken: string) => Promise<any>;
  discordChannels: (botToken: string, guildId: string) => Promise<any>;
  telegramAuthTest: (botToken: string, proxyUrl?: string) => Promise<any>;
  telegramChats: (includePrivate?: boolean) => Promise<any>;
  larkAuthTest: (appId: string, appSecret: string, domain?: string, proxyUrl?: string) => Promise<any>;
  larkChats: (appId: string, appSecret: string, domain?: string) => Promise<any>;
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
  remoteAccessStatus: () => Promise<any>;
  pairVibeCloudRemoteAccess: (payload: { backend_url: string; pairing_key: string; device_name?: string }) => Promise<any>;
  startRemoteAccess: () => Promise<any>;
  stopRemoteAccess: () => Promise<any>;
  getSession: () => Promise<SessionInfo>;
  signOut: () => Promise<{ ok: boolean }>;
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

// Claude's auth surface differs structurally from Codex: V2Config is the
// sole writer (no disk-side ``apply_claude_auth``) and the CLI inherits
// env vars per request rather than via a persistent daemon. We still
// inspect ``~/.claude/settings.json`` so the UI can warn when a
// hand-edited ``env`` block would override the V2Config-injected key at
// launch (Claude Code layers settings.json env on top of inherited env).
// Which on-disk source the live API key came from. ``v2config`` means
// the user (or a prior save) put it in Vibe Remote's V2Config and we
// inject it as ``ANTHROPIC_API_KEY`` at launch. ``settings_json`` means
// ``~/.claude/settings.json``'s ``env`` block already carries the key —
// the live CLI reads it directly and our V2Config is empty (typically a
// pre-existing setup that predates our Settings UI). ``null`` = no key.
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

  const value: ApiContextType = {
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
    installAgent: (name) => postJson(`/agent/${encodeURIComponent(name)}/install`, {}),
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
    slackChannels: (botToken, browseAll) => postJson('/slack/channels', { bot_token: botToken, browse_all: browseAll || false }),
    slackManifest: () => getJson('/slack/manifest'),
    discordAuthTest: (botToken, proxyUrl) => postJson('/discord/auth_test', { bot_token: botToken, proxy_url: proxyUrl || undefined }),
    discordGuilds: (botToken) => postJson('/discord/guilds', { bot_token: botToken }),
    discordChannels: (botToken, guildId) => postJson('/discord/channels', { bot_token: botToken, guild_id: guildId }),
    telegramAuthTest: (botToken, proxyUrl) => postJson('/telegram/auth_test', { bot_token: botToken, proxy_url: proxyUrl || undefined }),
    telegramChats: (includePrivate) => postJson('/telegram/chats', { include_private: includePrivate || false }),
    larkAuthTest: (appId, appSecret, domain, proxyUrl) => postJson('/lark/auth_test', { app_id: appId, app_secret: appSecret, domain: domain || 'feishu', proxy_url: proxyUrl || undefined }),
    larkChats: (appId, appSecret, domain) => postJson('/lark/chats', { app_id: appId, app_secret: appSecret, domain: domain || 'feishu' }),
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
    remoteAccessStatus: () => getJson('/remote-access/status'),
    pairVibeCloudRemoteAccess: (payload) => postJson('/remote-access/vibe-cloud/pair', payload),
    startRemoteAccess: () => postJson('/remote-access/start', {}),
    stopRemoteAccess: () => postJson('/remote-access/stop', {}),
    getSession: () => getJson('/api/session'),
    signOut: () => postJson('/auth/logout', {}),
  };

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
};
