import React, { createContext, useContext } from 'react';
import { useToast } from './ToastContext';

export type ApiContextType = {
  getConfig: () => Promise<any>;
  saveConfig: (payload: any) => Promise<any>;
  getSettings: () => Promise<any>;
  saveSettings: (payload: any) => Promise<any>;
  detectCli: (binary: string) => Promise<any>;
  installAgent: (name: string) => Promise<InstallResult>;
  slackAuthTest: (botToken: string) => Promise<any>;
  slackChannels: (botToken: string) => Promise<any>;
  slackManifest: () => Promise<{ ok: boolean; manifest?: string; manifest_compact?: string; error?: string }>;
  discordAuthTest: (botToken: string) => Promise<any>;
  discordGuilds: (botToken: string) => Promise<any>;
  discordChannels: (botToken: string, guildId: string) => Promise<any>;
  doctor: () => Promise<any>;
  opencodeOptions: (cwd: string) => Promise<any>;
  opencodeSetupPermission: () => Promise<{ ok: boolean; message: string; config_path: string }>;
  claudeAgents: (cwd?: string) => Promise<{ ok: boolean; agents?: { id: string; name: string; path: string; source?: string }[]; error?: string }>;
  claudeModels: () => Promise<{ ok: boolean; models?: string[]; error?: string }>;
  codexModels: () => Promise<{ ok: boolean; models?: string[]; error?: string }>;
  getLogs: (lines?: number) => Promise<{ logs: LogEntry[]; total: number }>;
  getVersion: () => Promise<VersionInfo>;
  doUpgrade: () => Promise<UpgradeResult>;
};

export type LogEntry = {
  timestamp: string;
  level: string;
  logger: string;
  message: string;
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
    const res = await fetch(path);
    if (!res.ok) {
      await handleApiError(res, path);
    }
    return res.json();
  };

  const postJson = async (path: string, payload: any) => {
    const res = await fetch(path, {
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
    getSettings: () => getJson('/settings'),
    saveSettings: (payload) => postJson('/settings', payload),
    detectCli: (binary) => getJson(`/cli/detect?binary=${encodeURIComponent(binary)}`),
    installAgent: (name) => postJson(`/agent/${encodeURIComponent(name)}/install`, {}),
    slackAuthTest: (botToken) => postJson('/slack/auth_test', { bot_token: botToken }),
    slackChannels: (botToken) => postJson('/slack/channels', { bot_token: botToken }),
    slackManifest: () => getJson('/slack/manifest'),
    discordAuthTest: (botToken) => postJson('/discord/auth_test', { bot_token: botToken }),
    discordGuilds: (botToken) => postJson('/discord/guilds', { bot_token: botToken }),
    discordChannels: (botToken, guildId) => postJson('/discord/channels', { bot_token: botToken, guild_id: guildId }),
    doctor: () => postJson('/doctor', {}),
    opencodeOptions: (cwd) => postJson('/opencode/options', { cwd }),
    opencodeSetupPermission: () => postJson('/opencode/setup-permission', {}),
    claudeAgents: (cwd) => cwd ? getJson(`/claude/agents?cwd=${encodeURIComponent(cwd)}`) : getJson('/claude/agents'),
    claudeModels: () => getJson('/claude/models'),
    codexModels: () => getJson('/codex/models'),
    getLogs: (lines = 500) => postJson('/logs', { lines }),
    getVersion: () => getJson('/version'),
    doUpgrade: () => postJson('/upgrade', {}),
  };

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
};
