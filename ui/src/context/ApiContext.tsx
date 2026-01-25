import React, { createContext, useContext } from 'react';

export type ApiContextType = {
  getConfig: () => Promise<any>;
  saveConfig: (payload: any) => Promise<any>;
  getSettings: () => Promise<any>;
  saveSettings: (payload: any) => Promise<any>;
  detectCli: (binary: string) => Promise<any>;
  slackAuthTest: (botToken: string) => Promise<any>;
  slackChannels: (botToken: string) => Promise<any>;
  slackManifest: () => Promise<{ ok: boolean; manifest?: string; manifest_compact?: string; error?: string }>;
  doctor: () => Promise<any>;
  opencodeOptions: (cwd: string) => Promise<any>;
  opencodeSetupPermission: () => Promise<{ ok: boolean; message: string; config_path: string }>;
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

const ApiContext = createContext<ApiContextType | undefined>(undefined);

export const useApi = () => {
  const context = useContext(ApiContext);
  if (!context) {
    throw new Error('useApi must be used within ApiProvider');
  }
  return context;
};

export const ApiProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const getJson = async (path: string) => {
    const res = await fetch(path);
    if (!res.ok) throw new Error(`Request failed: ${path}`);
    return res.json();
  };

  const postJson = async (path: string, payload: any) => {
    const res = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error(`Request failed: ${path}`);
    return res.json();
  };

  const value: ApiContextType = {
    getConfig: () => getJson('/config'),
    saveConfig: (payload) => postJson('/config', payload),
    getSettings: () => getJson('/settings'),
    saveSettings: (payload) => postJson('/settings', payload),
    detectCli: (binary) => getJson(`/cli/detect?binary=${encodeURIComponent(binary)}`),
    slackAuthTest: (botToken) => postJson('/slack/auth_test', { bot_token: botToken }),
    slackChannels: (botToken) => postJson('/slack/channels', { bot_token: botToken }),
    slackManifest: () => getJson('/slack/manifest'),
    doctor: () => postJson('/doctor', {}),
    opencodeOptions: (cwd) => postJson('/opencode/options', { cwd }),
    opencodeSetupPermission: () => postJson('/opencode/setup-permission', {}),
    getLogs: (lines = 500) => postJson('/logs', { lines }),
    getVersion: () => getJson('/version'),
    doUpgrade: () => postJson('/upgrade', {}),
  };

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
};
