import React, { useEffect, useState } from 'react';
import { Hash, CheckSquare, Square, RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useApi } from '../../context/ApiContext';
import { useToast } from '../../context/ToastContext';
import clsx from 'clsx';

interface ChannelListProps {
  data?: any;
  onNext?: (data: any) => void;
  onBack?: () => void;
  isPage?: boolean;
}

interface ChannelConfig {
  enabled: boolean;
  show_message_types: string[];
  custom_cwd: string;
  routing: {
    agent_backend: string | null;
    opencode_agent?: string | null;
    opencode_model?: string | null;
    opencode_reasoning_effort?: string | null;
  };
}

export const ChannelList: React.FC<ChannelListProps> = ({ data = {}, onNext, onBack, isPage }) => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [loading, setLoading] = useState(false);
  const [channels, setChannels] = useState<any[]>([]);
  const [configs, setConfigs] = useState<Record<string, ChannelConfig>>(data.channelConfigs || {});
  const [config, setConfig] = useState<any>(data);
  const [opencodeOptions, setOpencodeOptions] = useState<any>(null);
  const [selectedModels, setSelectedModels] = useState<Record<string, string>>({});

  useEffect(() => {
    if (!isPage) {
      setConfigs(data.channelConfigs || {});
    }
  }, [data.channelConfigs, isPage]);

  useEffect(() => {
    if (isPage) {
      api.getConfig().then(c => {
        setConfig(c);
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
      console.error('Failed to load channels:', e);
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
      console.error('Failed to load OpenCode options:', e);
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

  useEffect(() => {
    if (!channels.length) return;
    setSelectedModels((prev) => {
      const next = { ...prev };
      channels.forEach((channel) => {
        const model = configs[channel.id]?.routing?.opencode_model || '';
        if (next[channel.id] !== model) {
          next[channel.id] = model;
        }
      });
      return next;
    });
  }, [channels, configs]);

  const isChannelEnabled = (channelId: string) => {
    const channel = configs[channelId];
    return channel ? channel.enabled !== false : false;
  };

  const persistConfigs = async (nextConfigs: Record<string, ChannelConfig>) => {
    if (!isPage) {
      setConfigs(nextConfigs);
      return;
    }

    setLoading(true);
    try {
      await api.saveSettings({ channels: nextConfigs });
      showToast(t('channelList.settingsSaved'));
    } catch {
      showToast(t('channelList.settingsSaveFailed'), 'error');
    } finally {
      setLoading(false);
    }
  };

  const updateConfig = (channelId: string, patch: Partial<ChannelConfig>) => {
    const base = configs[channelId] || defaultConfig();
    const next = { ...base, ...patch };
    if (!next.show_message_types) {
      next.show_message_types = defaultConfig().show_message_types;
    }
    if (!next.routing || typeof next.routing !== 'object') {
      next.routing = { agent_backend: config.agents?.default_backend || 'opencode' };
    }
    const nextConfigs = { ...configs, [channelId]: next };
    setConfigs(nextConfigs);
    void persistConfigs(nextConfigs);
  };

  const defaultConfig = (): ChannelConfig => ({
    enabled: false,
    show_message_types: [],
    custom_cwd: '',
    routing: {
      agent_backend: config.agents?.default_backend || 'opencode',
      opencode_agent: null,
      opencode_model: null,
      opencode_reasoning_effort: null,
    },
  });

  const reasoningOptionsByModel = React.useMemo(() => {
    const lookup = opencodeOptions?.reasoning_options || {};
    if (lookup && typeof lookup === 'object') {
      return lookup as Record<string, { value: string; label: string }[]>;
    }
    return {} as Record<string, { value: string; label: string }[]>;
  }, [opencodeOptions]);

  const getReasoningOptions = (modelKey: string) => reasoningOptionsByModel[modelKey] || [];

  const selectedCount = channels.filter((channel) => isChannelEnabled(channel.id)).length;

  return (
    <div className={clsx('flex flex-col h-full', isPage ? 'max-w-5xl mx-auto' : '')}>
      <div className="flex justify-between items-center mb-6">
        <div>
          <h2 className={clsx('font-display font-bold', isPage ? 'text-3xl' : 'text-2xl')}>{t('channelList.title')}</h2>
          <p className="text-muted">{t('channelList.subtitle')}</p>
        </div>
      </div>

      <div className="mb-4 bg-panel border border-border p-4 rounded-lg space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={loadChannels}
              className="flex items-center gap-2 px-3 py-1.5 bg-neutral-100 hover:bg-neutral-200 text-text rounded text-sm font-medium transition-colors"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> {t('channelList.refreshList')}
            </button>
            {channels.length === 0 && !loading && (
              <span className="text-sm text-warning">{t('channelList.noChannelsFound')}</span>
            )}
          </div>
          <span className="text-sm text-muted font-mono">{t('channelList.enabledCount', { count: selectedCount })}</span>
        </div>
        {isPage && (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-muted">{t('channelList.accessPolicy')}</span>
            <span className="text-xs text-muted">{t('channelList.accessPolicyHint')}</span>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto border border-border rounded-xl divide-y divide-border bg-panel shadow-sm">
        {!loading && channels.length === 0 && !botToken && (
          <div className="p-8 text-center text-muted">
            {t('channelList.addTokenFirst')}
          </div>
        )}
        {channels.map((channel) => {
          const rawConfig = configs[channel.id] || {};
          const def = defaultConfig();
          const channelConfig = {
            ...def,
            ...rawConfig,
            enabled: isChannelEnabled(channel.id),
            show_message_types: rawConfig.show_message_types || def.show_message_types,
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
                    className={channelConfig.enabled ? 'text-accent' : 'text-muted'}
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
                <span
                  className={clsx(
                    'text-xs px-2 py-0.5 rounded-full border',
                    channel.is_private
                      ? 'bg-warning/10 text-warning border-warning/20'
                      : 'bg-success/10 text-success border-success/20'
                  )}
                >
                  {channel.is_private ? t('common.private') : t('common.public')}
                </span>
              </div>

              {channelConfig.enabled && (
                <div className="mt-4 pl-8 space-y-4">
                  {/* Basic Settings */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-muted uppercase">{t('channelList.workingDirectory')}</label>
                      <input
                        type="text"
                        placeholder={config.runtime?.default_cwd || t('channelList.useGlobalDefault')}
                        value={channelConfig.custom_cwd}
                        onChange={(e) => updateConfig(channel.id, { custom_cwd: e.target.value })}
                        className="w-full bg-bg border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-accent text-text placeholder:text-muted/50 font-mono"
                      />
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-muted uppercase">{t('channelList.backend')}</label>
                      <select
                        value={channelConfig.routing.agent_backend || ''}
                        onChange={(e) =>
                          updateConfig(channel.id, {
                            routing: { ...channelConfig.routing, agent_backend: e.target.value || null },
                          })
                        }
                        className="w-full bg-bg border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-accent text-text"
                      >
                        <option value="">{t('common.default')} ({config.agents?.default_backend || 'opencode'})</option>
                        <option value="opencode">OpenCode</option>
                        <option value="claude">Claude</option>
                        <option value="codex">Codex</option>
                      </select>
                    </div>
                  </div>

                  {/* Show Message Types */}
                  <div className="space-y-2">
                    <div className="text-xs font-medium text-muted uppercase">{t('channelList.showMessageTypes')}</div>
                    <div className="flex flex-wrap gap-3 text-sm">
                      {['system', 'assistant', 'toolcall'].map((msgType) => {
                        const checked = channelConfig.show_message_types.includes(msgType);
                        return (
                          <label key={msgType} className="flex items-center gap-2 text-text">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => {
                                const next = checked
                                  ? channelConfig.show_message_types.filter((value) => value !== msgType)
                                  : [...channelConfig.show_message_types, msgType];
                                updateConfig(channel.id, { show_message_types: next });
                              }}
                              className="h-4 w-4 rounded border-border text-accent focus:ring-accent"
                            />
                            <span className="capitalize">{msgType === 'toolcall' ? 'Toolcall' : msgType}</span>
                          </label>
                        );
                      })}
                    </div>
                  </div>

                  {/* OpenCode Settings */}
                  {channelConfig.routing.agent_backend === 'opencode' && (
                    <div className="space-y-3">
                      <div className="text-xs font-medium text-muted uppercase">{t('channelList.opencodeSettings')}</div>
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 bg-bg/50 p-3 rounded border border-border">
                        <div className="space-y-1">
                          <label className="text-xs text-muted">{t('channelList.agent')}</label>
                          <select
                            value={channelConfig.routing.opencode_agent || ''}
                            onChange={(e) =>
                              updateConfig(channel.id, {
                                routing: { ...channelConfig.routing, opencode_agent: e.target.value || null },
                              })
                            }
                            className="w-full bg-panel border border-border rounded px-3 py-2 text-sm"
                          >
                            <option value="">{t('common.default')}</option>
                            {(opencodeOptions?.agents || []).map((agent: any) => (
                              <option key={agent.name} value={agent.name}>{agent.name}</option>
                            ))}
                          </select>
                        </div>
                        <div className="space-y-1">
                          <label className="text-xs text-muted">{t('channelList.model')}</label>
                          <select
                            value={channelConfig.routing.opencode_model || ''}
                            onChange={(e) => {
                              const modelKey = e.target.value || '';
                              setSelectedModels((prev) => ({ ...prev, [channel.id]: modelKey }));
                              updateConfig(channel.id, {
                                routing: {
                                  ...channelConfig.routing,
                                  opencode_model: modelKey || null,
                                  opencode_reasoning_effort: null,
                                },
                              });
                            }}
                            className="w-full bg-panel border border-border rounded px-3 py-2 text-sm"
                          >
                            <option value="">{t('common.default')}</option>
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
                        </div>
                        <div className="space-y-1">
                          <label className="text-xs text-muted">{t('channelList.reasoningEffort')}</label>
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
                            disabled={!getReasoningOptions(selectedModels[channel.id] || channelConfig.routing.opencode_model || '').length}
                            className="w-full bg-panel border border-border rounded px-3 py-2 text-sm disabled:opacity-50"
                          >
                            <option value="">{t('common.default')}</option>
                            {getReasoningOptions(selectedModels[channel.id] || channelConfig.routing.opencode_model || '').map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
        {channels.length === 0 && !loading && (
          <div className="p-8 text-center text-muted">
            {t('channelList.noChannelsLoaded')}
          </div>
        )}
      </div>

      {!isPage && (
        <div className="mt-6 flex justify-between">
          <button onClick={onBack} className="px-6 py-2 text-muted hover:text-text font-medium">
            {t('common.back')}
          </button>
          <button
            onClick={() => onNext && onNext({ channelConfigs: configs })}
            className="px-6 py-2 bg-accent hover:bg-accent/90 text-white rounded-lg font-medium shadow-sm"
          >
            {t('common.continue')}
          </button>
        </div>
      )}
    </div>
  );
};
