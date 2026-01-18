import React, { useEffect, useState } from 'react';
import { Hash, CheckSquare, Square, RefreshCw, Save } from 'lucide-react';
import { useApi } from '../../context/ApiContext';
import clsx from 'clsx';

interface ChannelListProps {
  data?: any;
  onNext?: (data: any) => void;
  onBack?: () => void;
  isPage?: boolean;
}

interface ChannelConfig {
  enabled: boolean;
  custom_cwd: string;
  routing: {
    agent_backend: string | null;
    opencode_agent?: string | null;
    opencode_model?: string | null;
    opencode_reasoning_effort?: string | null;
  };
}

export const ChannelList: React.FC<ChannelListProps> = ({ data = {}, onNext, onBack, isPage }) => {
  const api = useApi();
  const [loading, setLoading] = useState(false);
  const [channels, setChannels] = useState<any[]>([]);
  const [configs, setConfigs] = useState<Record<string, ChannelConfig>>(data.channelConfigs || {});
  const [config, setConfig] = useState<any>(data);
  const [saved, setSaved] = useState(false);
  const [opencodeOptions, setOpencodeOptions] = useState<any>(null);

  // Load config if in page mode
  useEffect(() => {
    if (isPage) {
       api.getConfig().then(c => {
           setConfig(c);
           // Also need settings for channelConfigs
           api.getSettings().then(s => {
               setConfigs(s.channels || {});
           });
       });
    }
  }, [isPage]);

  const botToken = config.slack?.bot_token || config.slackBotToken || '';

  const loadChannels = async () => {
    if (!botToken) return;
    setLoading(true);
    try {
      const result = await api.slackChannels(botToken);
      if (result.ok) {
        setChannels(result.channels || []);
      }
    } catch (e) {
      console.error("Failed to load channels:", e);
    } finally {
      setLoading(false);
    }
  };

  const loadOpenCodeOptions = async () => {
    try {
      const cwd = config.runtime?.default_cwd || '.';
      const result = await api.opencodeOptions(cwd);
      if (result.ok) {
        setOpencodeOptions(result.data);
      }
    } catch (e) {
      console.error("Failed to load OpenCode options:", e);
    }
  };

  useEffect(() => {
    if (botToken) {
      loadChannels();
    }
  }, [botToken]);

  useEffect(() => {
    if (config.agents?.opencode?.enabled) {
      loadOpenCodeOptions();
    }
  }, [config.agents?.opencode?.enabled]);

  const updateConfig = (channelId: string, patch: Partial<ChannelConfig>) => {
    setConfigs((prev) => {
      const base = prev[channelId] || defaultConfig();
      const next = { ...base, ...patch };
      if (!next.routing || typeof next.routing !== 'object') {
        next.routing = { agent_backend: config.agents?.default_backend || 'opencode' };
      }
      return { ...prev, [channelId]: next };
    });
    setSaved(false);
  };

  const handleSave = async () => {
      setLoading(true);
      try {
          await api.saveSettings({ channels: configs });
          setSaved(true);
          setTimeout(() => setSaved(false), 2000);
      } finally {
          setLoading(false);
      }
  };

  const defaultConfig = (): ChannelConfig => ({
    enabled: true,
    custom_cwd: '',
    routing: {
      agent_backend: config.agents?.default_backend || 'opencode',
      opencode_agent: null,
      opencode_model: null,
      opencode_reasoning_effort: null,
    },
  });

  const selectedCount = channels.filter((channel) => {
    const rawConfig = configs[channel.id];
    if (!rawConfig) {
      return true;
    }
    return rawConfig.enabled !== false;
  }).length;

  return (
    <div className={clsx("flex flex-col h-full", isPage ? "max-w-5xl mx-auto" : "")}>
      <div className="flex justify-between items-center mb-6">
          <div>
              <h2 className={clsx("font-display font-bold", isPage ? "text-3xl" : "text-2xl")}>Channel Settings</h2>
              <p className="text-muted">Enable channels and configure per-channel routing.</p>
          </div>
          {isPage && (
              <button
                  onClick={handleSave}
                  disabled={loading || saved}
                  className="flex items-center gap-2 px-4 py-2 bg-accent text-white rounded-lg hover:bg-accent/90 disabled:opacity-50 transition-colors"
              >
                  <Save size={16} /> {saved ? 'Saved' : 'Save Changes'}
              </button>
          )}
      </div>

      <div className="flex items-center justify-between mb-4 bg-panel border border-border p-3 rounded-lg">
        <div className="flex items-center gap-4">
            <button
              onClick={loadChannels}
              className="flex items-center gap-2 px-3 py-1.5 bg-neutral-100 hover:bg-neutral-200 text-text rounded text-sm font-medium transition-colors"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> Refresh List
            </button>
             {channels.length === 0 && !loading && <span className="text-sm text-warning">No channels found. Check token or invite bot.</span>}
        </div>
        <span className="text-sm text-muted font-mono">{selectedCount} enabled</span>
      </div>

      <div className="flex-1 overflow-y-auto border border-border rounded-xl divide-y divide-border bg-panel shadow-sm">
        {channels.map((channel) => {
          const rawConfig = configs[channel.id] || {};
          const def = defaultConfig();
          const channelConfig = {
            ...def,
            ...rawConfig,
            routing: {
              ...def.routing,
              ...(rawConfig.routing || {}),
            },
          };
          return (
            <div key={channel.id} className="p-4 hover:bg-neutral-50/50 transition-colors">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => updateConfig(channel.id, { enabled: !channelConfig.enabled })}
                    className={channelConfig.enabled ? "text-accent" : "text-muted"}
                  >
                    {channelConfig.enabled ? <CheckSquare size={20} /> : <Square size={20} />}
                  </button>
                  <div>
                    <div className="font-medium flex items-center gap-1 text-text">
                      <Hash size={14} className="text-muted" /> {channel.name}
                    </div>
                    <div className="text-xs text-muted font-mono">ID: {channel.id}</div>
                  </div>
                </div>
                <span className={clsx("text-xs px-2 py-0.5 rounded-full border", channel.is_private ? "bg-warning/10 text-warning border-warning/20" : "bg-success/10 text-success border-success/20")}>
                  {channel.is_private ? 'Private' : 'Public'}
                </span>
              </div>

              {channelConfig.enabled && (
                  <div className="mt-4 pl-8 space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                        <div className="space-y-1">
                            <label className="text-xs font-medium text-muted uppercase">Working Directory</label>
                            <input
                              type="text"
                              placeholder="Default (empty)"
                              value={channelConfig.custom_cwd}
                              onChange={(e) => updateConfig(channel.id, { custom_cwd: e.target.value })}
                              className="w-full bg-bg border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-accent text-text placeholder:text-muted/50 font-mono"
                            />
                        </div>
                        <div className="space-y-1">
                             <label className="text-xs font-medium text-muted uppercase">Backend</label>
                             <select
                                  value={channelConfig.routing.agent_backend || ''}
                                  onChange={(e) =>
                                    updateConfig(channel.id, {
                                      routing: { ...channelConfig.routing, agent_backend: e.target.value || null },
                                    })
                                  }
                                  className="w-full bg-bg border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-accent text-text"
                                >
                                  <option value="">Default ({config.agents?.default_backend || 'opencode'})</option>
                                  <option value="opencode">OpenCode</option>
                                  <option value="claude">Claude</option>
                                  <option value="codex">Codex</option>
                                </select>
                        </div>
                    </div>

                    {channelConfig.routing.agent_backend === 'opencode' && (
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 bg-bg/50 p-3 rounded border border-border">
                          <select
                            value={channelConfig.routing.opencode_agent || ''}
                            onChange={(e) =>
                              updateConfig(channel.id, {
                                routing: { ...channelConfig.routing, opencode_agent: e.target.value || null },
                              })
                            }
                            className="bg-panel border border-border rounded px-3 py-2 text-sm"
                          >
                            <option value="">Agent: Default</option>
                            {(opencodeOptions?.agents || []).map((agent: any) => (
                              <option key={agent.name} value={agent.name}>{agent.name}</option>
                            ))}
                          </select>
                          <select
                            value={channelConfig.routing.opencode_model || ''}
                            onChange={(e) =>
                              updateConfig(channel.id, {
                                routing: { ...channelConfig.routing, opencode_model: e.target.value || null },
                              })
                            }
                            className="bg-panel border border-border rounded px-3 py-2 text-sm"
                          >
                            <option value="">Model: Default</option>
                            {(opencodeOptions?.models?.providers || []).flatMap((provider: any) => {
                              const providerId = provider.id || provider.provider_id || provider.name;
                              const providerLabel = provider.name || providerId;
                              const models = provider.models || {};
                              if (Array.isArray(models)) {
                                return models.map((model: any) => {
                                  const modelId = typeof model === 'string' ? model : model.id;
                                  return (
                                    <option key={`${providerId}:${modelId}`} value={`${providerId}/${modelId}`}>
                                      {providerLabel}/{modelId}
                                    </option>
                                  );
                                });
                              }
                              return Object.keys(models).map((modelId) => (
                                <option key={`${providerId}:${modelId}`} value={`${providerId}/${modelId}`}>
                                  {providerLabel}/{modelId}
                                </option>
                              ));
                            })}
                          </select>
                          <select
                            value={channelConfig.routing.opencode_reasoning_effort || ''}
                            onChange={(e) =>
                              updateConfig(channel.id, {
                                routing: {
                                  ...channelConfig.routing,
                                  opencode_reasoning_effort: e.target.value || null,
                                },
                              })
                            }
                            className="bg-panel border border-border rounded px-3 py-2 text-sm"
                          >
                            <option value="">Reasoning: Default</option>
                            <option value="low">Low</option>
                            <option value="medium">Medium</option>
                            <option value="high">High</option>
                            <option value="xhigh">Extra High</option>
                          </select>
                        </div>
                      )}
                  </div>
              )}
            </div>
          );
        })}
        {channels.length === 0 && !loading && (
          <div className="p-8 text-center text-muted">
             No channels loaded.
          </div>
        )}
      </div>

      {!isPage && (
          <div className="mt-6 flex justify-between">
            <button onClick={onBack} className="px-6 py-2 text-muted hover:text-text font-medium">
              Back
            </button>
            <button
              onClick={() => onNext && onNext({ channelConfigs: configs })}
              className="px-6 py-2 bg-accent hover:bg-accent/90 text-white rounded-lg font-medium shadow-sm"
            >
              Continue
            </button>
          </div>
      )}
    </div>
  );
};
