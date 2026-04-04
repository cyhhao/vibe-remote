import React, { createContext, useContext } from 'react';
import { useToast } from './ToastContext';
import { apiFetch } from '../lib/apiFetch';

export type ApiContextType = {
  getConfig: () => Promise<any>;
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
  slackAuthTest: (botToken: string) => Promise<any>;
  slackChannels: (botToken: string, browseAll?: boolean) => Promise<any>;
  slackManifest: () => Promise<{ ok: boolean; manifest?: string; manifest_compact?: string; error?: string }>;
  discordAuthTest: (botToken: string) => Promise<any>;
  discordGuilds: (botToken: string) => Promise<any>;
  discordChannels: (botToken: string, guildId: string) => Promise<any>;
  telegramAuthTest: (botToken: string) => Promise<any>;
  telegramChats: (includePrivate?: boolean) => Promise<any>;
  larkAuthTest: (appId: string, appSecret: string, domain?: string) => Promise<any>;
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
};

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

  const handleApiError = async (res: Response, path: string) => {
    let errorMessage = `Request failed: ${path} (${res.status})`;
    
    try {
      const data = await res.json();
      if (data.error) {
        errorMessage = data.error;
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

  const value: ApiContextType = {
    getConfig: () => getJson('/config'),
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
    slackAuthTest: (botToken) => postJson('/slack/auth_test', { bot_token: botToken }),
    slackChannels: (botToken, browseAll) => postJson('/slack/channels', { bot_token: botToken, browse_all: browseAll || false }),
    slackManifest: () => getJson('/slack/manifest'),
    discordAuthTest: (botToken) => postJson('/discord/auth_test', { bot_token: botToken }),
    discordGuilds: (botToken) => postJson('/discord/guilds', { bot_token: botToken }),
    discordChannels: (botToken, guildId) => postJson('/discord/channels', { bot_token: botToken, guild_id: guildId }),
    telegramAuthTest: (botToken) => postJson('/telegram/auth_test', { bot_token: botToken }),
    telegramChats: (includePrivate) => postJson('/telegram/chats', { include_private: includePrivate || false }),
    larkAuthTest: (appId, appSecret, domain) => postJson('/lark/auth_test', { app_id: appId, app_secret: appSecret, domain: domain || 'feishu' }),
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
  };

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
};
