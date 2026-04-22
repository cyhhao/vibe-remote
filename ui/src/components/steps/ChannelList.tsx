import React, { useEffect, useRef, useState } from 'react';
import { Hash, CheckSquare, Square, RefreshCw, HelpCircle, Globe, FolderOpen, MessageSquare, Users, AtSign } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useApi } from '../../context/ApiContext';
import { useToast } from '../../context/ToastContext';
import { Combobox } from '../ui/combobox';
import { DirectoryBrowser } from '../ui/directory-browser';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import { getEnabledPlatforms, platformSupportsChannels } from '../../lib/platforms';

/** Input that only commits value on blur */
function BlurInput({
  value,
  onCommit,
  ...props
}: { value: string; onCommit: (v: string) => void } & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'onBlur'>) {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);
  return (
    <input
      {...props}
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={() => { if (local !== value) onCommit(local); }}
    />
  );
}

interface ChannelListProps {
  data?: any;
  onNext?: (data: any) => void;
  onBack?: () => void;
  isPage?: boolean;
  forcedPlatform?: string;
  /** When set in wizard mode, show platform tabs to switch between platforms in a single step */
  wizardPlatforms?: string[];
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
    claude_agent?: string | null;
    claude_model?: string | null;
    claude_reasoning_effort?: string | null;
    codex_agent?: string | null;
    codex_model?: string | null;
    codex_reasoning_effort?: string | null;
  };
  require_mention?: boolean | null;  // null=use global default, true=require, false=don't require
}

interface TelegramDiscoverySummary {
  discovered_count: number;
  visible_count: number;
  hidden_private_count: number;
  forum_count: number;
}

const getDiscordGuildAllowlist = (source: any): string[] => {
  const allowlist = source?.discordGuildAllowlist || source?.guild_allowlist || source?.discord?.guild_allowlist;
  return Array.isArray(allowlist) ? allowlist : [];
};

const buildDiscordGuildSettings = (allowlist: string[]) =>
  Object.fromEntries(allowlist.map((guildId) => [guildId, { enabled: true }]));

const addDiscordGuildToAllowlist = (allowlist: string[], selectedGuild: string): string[] => {
  const merged = [...allowlist];
  if (selectedGuild && !merged.includes(selectedGuild)) {
    merged.push(selectedGuild);
  }
  return merged;
};

export const ChannelList: React.FC<ChannelListProps> = ({ data = {}, onNext, onBack, isPage, forcedPlatform, wizardPlatforms }) => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [loading, setLoading] = useState(false);
  const [channels, setChannels] = useState<any[]>([]);
  const [browseAll, setBrowseAll] = useState(false);
  const [loadingAll, setLoadingAll] = useState(false);
  // Wizard multi-platform mode: show tabs instead of separate steps
  const isWizardMultiPlatform = !isPage && Array.isArray(wizardPlatforms) && wizardPlatforms.length > 1;
  const [wizardActivePlatform, setWizardActivePlatform] = useState(forcedPlatform || wizardPlatforms?.[0] || 'slack');
  const [wizardConfigsMap, setWizardConfigsMap] = useState<Record<string, Record<string, ChannelConfig>>>({});
  const scopedInitialPlatform = forcedPlatform || wizardPlatforms?.[0] || data.platform || 'slack';
  const [configs, setConfigs] = useState<Record<string, ChannelConfig>>(
    data.channelConfigsByPlatform?.[scopedInitialPlatform] || data.channelConfigs || {}
  );
  const [config, setConfig] = useState<any>(data);
  const [pagePlatform, setPagePlatform] = useState<string>(forcedPlatform || data.platform || 'slack');
  const [opencodeOptionsByCwd, setOpencodeOptionsByCwd] = useState<Record<string, any>>({});
  const [claudeAgentsByCwd, setClaudeAgentsByCwd] = useState<Record<string, { id: string; name: string; path: string; source?: string }[]>>({});
  const [codexAgentsByCwd, setCodexAgentsByCwd] = useState<Record<string, { id: string; name: string; path: string; source?: string; description?: string }[]>>({});
  const [claudeModels, setClaudeModels] = useState<string[]>([]);
  const [claudeReasoningOptions, setClaudeReasoningOptions] = useState<Record<string, { value: string; label: string }[]>>({});
  const [codexModels, setCodexModels] = useState<string[]>([]);
  const [selectedModels, setSelectedModels] = useState<Record<string, string>>({});
  const [guilds, setGuilds] = useState<any[]>([]);
  const [selectedGuildIds, setSelectedGuildIds] = useState<string[]>(getDiscordGuildAllowlist(data));
  const [selectedGuild, setSelectedGuild] = useState<string>(getDiscordGuildAllowlist(data)[0] || '');
  const selectedGuildIdsRef = useRef<string[]>(getDiscordGuildAllowlist(data));
  const confirmedGuildAllowlistRef = useRef<string[]>(getDiscordGuildAllowlist(data));
  const allowlistSaveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const allowlistSaveVersionRef = useRef(0);
  const configSaveQueueRef = useRef<Promise<void>>(Promise.resolve());
  const configVersionRef = useRef(0);
  const configRef = useRef<any>(data);
  const [telegramSummary, setTelegramSummary] = useState<TelegramDiscoverySummary | null>(null);
  // Directory browser state — tracks which channel's cwd picker is open
  const [browsingCwdFor, setBrowsingCwdFor] = useState<string | null>(null);

  const applySelectedGuildIds = (allowlist: string[]) => {
    const normalized = [...allowlist];
    selectedGuildIdsRef.current = normalized;
    setSelectedGuildIds(normalized);
  };

  const applyConfig = (nextConfig: any): number => {
    configRef.current = nextConfig;
    configVersionRef.current += 1;
    setConfig(nextConfig);
    return configVersionRef.current;
  };

  const saveLatestConfig = async (): Promise<boolean> => {
    const saveTask = configSaveQueueRef.current.then(async () => {
      await api.saveConfig(configRef.current);
    });
    configSaveQueueRef.current = saveTask.catch(() => {});
    try {
      await saveTask;
      return true;
    } catch {
      return false;
    }
  };

  useEffect(() => {
    configRef.current = config;
  }, [config]);

  useEffect(() => {
    if (isPage) return;
    let cancelled = false;

    const targetPlatform = isWizardMultiPlatform ? wizardActivePlatform : (forcedPlatform || data.platform);
    const loadWizardPlatformSettings = async () => {
      // If we already have locally saved configs for this platform, use them
      if (isWizardMultiPlatform && wizardConfigsMap[targetPlatform]) {
        if (!cancelled) setConfigs(wizardConfigsMap[targetPlatform]);
        return;
      }
      try {
        const settings = await api.getSettings(targetPlatform);
        if (!cancelled) {
          setConfigs(settings.channels || {});
        }
      } catch {
        if (!cancelled) {
          // Fallback to wizard-local state if API fetch fails.
          setConfigs(data.channelConfigsByPlatform?.[targetPlatform || 'slack'] || data.channelConfigs || {});
        }
      }
    };

    loadWizardPlatformSettings();
    return () => {
      cancelled = true;
    };
  }, [isPage, data.platform, data.channelConfigsByPlatform, forcedPlatform]);

  useEffect(() => {
    if (!isPage) {
      const allowlist = getDiscordGuildAllowlist(data);
      confirmedGuildAllowlistRef.current = allowlist;
      applySelectedGuildIds(allowlist);
      setSelectedGuild(allowlist[0] || '');
    }
  }, [data.discordGuildAllowlist, data.discord?.guild_allowlist, isPage]);


  useEffect(() => {
    if (isPage) {
      api.getConfig().then(c => {
        applyConfig(c);
        const allowlist = getDiscordGuildAllowlist(c);
        confirmedGuildAllowlistRef.current = allowlist;
        applySelectedGuildIds(allowlist);
        setSelectedGuild(allowlist[0] || '');
        const defaultPlatform = forcedPlatform || getEnabledPlatforms(c).find((p) => platformSupportsChannels(c, p)) || c?.platform || 'slack';
        setPagePlatform(defaultPlatform);
        api.getSettings(defaultPlatform).then(s => {
          setConfigs(s.channels || {});
          if (defaultPlatform === 'discord') {
            const allowlist = getDiscordGuildAllowlist(s);
            confirmedGuildAllowlistRef.current = allowlist;
            applySelectedGuildIds(allowlist);
            setSelectedGuild(allowlist[0] || '');
          }
        });
      });
    }
  }, [forcedPlatform, isPage]);

  const platform = isWizardMultiPlatform
    ? wizardActivePlatform
    : (forcedPlatform || pagePlatform || config.platform || data.platform || 'slack');
  const channelPlatforms = getEnabledPlatforms(config).filter((p) => platformSupportsChannels(config, p));
  const knownDiscordGuilds = [
    ...guilds,
    ...selectedGuildIds
      .filter((id) => !guilds.some((guild) => guild.id === id))
      .map((id) => ({ id, name: id })),
  ];

  // Switch platforms in wizard multi-platform mode
  const switchWizardPlatform = (newPlatform: string) => {
    if (newPlatform === wizardActivePlatform) return;
    // Save current platform's configs to map
    setWizardConfigsMap(prev => ({ ...prev, [wizardActivePlatform]: configs }));
    // Load new platform's configs from map, then from data, then empty
    const saved = wizardConfigsMap[newPlatform];
    setConfigs(saved || data.channelConfigsByPlatform?.[newPlatform] || {});
    setWizardActivePlatform(newPlatform);
    setChannels([]);
    setBrowseAll(false);
    setTelegramSummary(null);
  };

  useEffect(() => {
    if (!isPage) return;
    if (!platform) return;
    api.getSettings(platform).then((settings) => {
      setConfigs(settings.channels || {});
      if (platform === 'discord') {
        const allowlist = getDiscordGuildAllowlist(settings);
        confirmedGuildAllowlistRef.current = allowlist;
        applySelectedGuildIds(allowlist);
        setSelectedGuild((current) => current || allowlist[0] || '');
      }
    }).catch(() => {});
  }, [api, isPage, platform]);
  const botToken = platform === 'discord'
    ? (config.discord?.bot_token || data.discord?.bot_token || '')
    : platform === 'telegram'
      ? (config.telegram?.bot_token || data.telegram?.bot_token || '')
    : platform === 'lark'
      ? '' // Lark uses app_id + app_secret, not bot_token
      : (config.slack?.bot_token || config.slackBotToken || '');
  const larkAppId = config.lark?.app_id || data.lark?.app_id || '';
  const larkAppSecret = config.lark?.app_secret || data.lark?.app_secret || '';
  const larkDomain = config.lark?.domain || data.lark?.domain || 'feishu';

  useEffect(() => {
    if (platform !== 'discord') return;
    if (selectedGuild) return;
    const preferredGuild = selectedGuildIdsRef.current[0] || getDiscordGuildAllowlist(data)[0] || '';
    if (preferredGuild) {
      setSelectedGuild(preferredGuild);
    }
  }, [platform, data.discordGuildAllowlist, selectedGuild]);

  const updateSelectedGuild = (guildId: string) => {
    setSelectedGuild(guildId);
  };

  const toggleAllowedGuild = async (guildId: string, checked: boolean) => {
    const current = selectedGuildIdsRef.current;
    const next = checked
      ? addDiscordGuildToAllowlist(current, guildId)
      : current.filter(id => id !== guildId);
    const version = allowlistSaveVersionRef.current + 1;
    allowlistSaveVersionRef.current = version;
    applySelectedGuildIds(next);
    if (!isPage) return;

    const saveTask = allowlistSaveQueueRef.current.then(async () => {
      const saved = await persistDiscordGuildAllowlist(next);
      if (saved) {
        confirmedGuildAllowlistRef.current = next;
      } else if (allowlistSaveVersionRef.current === version) {
        const confirmed = confirmedGuildAllowlistRef.current;
        applySelectedGuildIds(confirmed);
      }
    });
    allowlistSaveQueueRef.current = saveTask.catch(() => {});
    await saveTask;
  };

  const persistDiscordGuildAllowlist = async (allowlist: string[]): Promise<boolean> => {
    if (!isPage) return true;
    try {
      await api.saveSettings({ guilds: buildDiscordGuildSettings(allowlist) }, 'discord');
      showToast(t('common.saved'), 'success');
      return true;
    } catch {
      showToast(t('channelList.settingsSaveFailed'), 'error');
      return false;
    }
  };

  const loadGuilds = async () => {
    if (!botToken) return;
    try {
      const result = await api.discordGuilds(botToken);
      if (result.ok) {
        setGuilds(result.guilds || []);
      }
    } catch (e) {
      console.error('Failed to load guilds:', e);
    }
  };

  const loadChannels = async (all?: boolean) => {
    if (platform === 'lark') {
      if (!larkAppId || !larkAppSecret) return;
    } else if (!botToken) {
      return;
    }
    const isAll = all ?? browseAll;
    if (isAll) {
      setLoadingAll(true);
    } else {
      setLoading(true);
    }
    try {
      if (platform === 'lark') {
        const result = await api.larkChats(larkAppId, larkAppSecret, larkDomain);
        if (result.ok) {
          setChannels(result.channels || []);
        }
      } else if (platform === 'telegram') {
        const result = await api.telegramChats(false);
        if (result.ok) {
          setChannels(result.channels || []);
          setTelegramSummary(result.summary || null);
        }
      } else if (platform === 'discord') {
        if (!selectedGuild) {
          setLoading(false);
          return;
        }
        const result = await api.discordChannels(botToken, selectedGuild);
        if (result.ok) {
          const filtered = (result.channels || []).filter((c: any) => c.type === 0 || c.type === 5);
          setChannels(filtered);
        }
      } else {
        const result = await api.slackChannels(botToken, isAll);
        if (result.ok) {
          setChannels(result.channels || []);
          if (isAll) setBrowseAll(true);
        }
      }
    } catch (e) {
      console.error('Failed to load channels:', e);
    } finally {
      setLoading(false);
      setLoadingAll(false);
    }
  };

  const loadOpenCodeOptions = async (cwd: string) => {
    try {
      const result = await api.opencodeOptions(cwd);
      if (result.ok) {
        setOpencodeOptionsByCwd((prev) => ({ ...prev, [cwd]: result.data }));
      }
    } catch (e) {
      console.error('Failed to load OpenCode options:', e);
    }
  };

  const loadClaudeAgents = async (cwd: string) => {
    try {
      const result = await api.claudeAgents(cwd);
      if (result.ok) {
        setClaudeAgentsByCwd((prev) => ({ ...prev, [cwd]: result.agents || [] }));
      }
    } catch (e) {
      console.error('Failed to load Claude agents:', e);
    }
  };

  const loadClaudeModels = async () => {
    try {
      const result = await api.claudeModels();
      if (result.ok) {
        setClaudeModels(result.models || []);
        setClaudeReasoningOptions(result.reasoning_options || {});
      }
    } catch (e) {
      console.error('Failed to load Claude models:', e);
    }
  };

  const loadCodexModels = async () => {
    try {
      const result = await api.codexModels();
      if (result.ok) {
        setCodexModels(result.models || []);
      }
    } catch (e) {
      console.error('Failed to load Codex models:', e);
    }
  };

  const loadCodexAgents = async (cwd: string) => {
    try {
      const result = await api.codexAgents(cwd);
      if (result.ok) {
        setCodexAgentsByCwd((prev) => ({ ...prev, [cwd]: result.agents || [] }));
      }
    } catch (e) {
      console.error('Failed to load Codex agents:', e);
    }
  };

  useEffect(() => {
    if (platform === 'lark') {
      if (larkAppId && larkAppSecret) {
        loadChannels();
      }
      return;
    }
    if (!botToken) return;
    if (platform === 'discord') {
      loadGuilds();
      if (selectedGuild) {
        loadChannels();
      }
    } else {
      loadChannels();
    }
  }, [botToken, platform, selectedGuild, larkAppId, larkAppSecret]);

  useEffect(() => {
    if (config.agents?.claude?.enabled) {
      loadClaudeModels();
    }
  }, [config.agents?.claude?.enabled]);

  useEffect(() => {
    if (config.agents?.codex?.enabled) {
      loadCodexModels();
    }
  }, [config.agents?.codex?.enabled]);

  useEffect(() => {
    if (!channels.length) return;
    const defaultCwd = config.runtime?.default_cwd || '~/work';
    const defaultBackend = config.agents?.default_backend || 'opencode';

    const neededOpenCodeCwds = new Set<string>();
    const neededClaudeCwds = new Set<string>();
    const neededCodexCwds = new Set<string>();

    channels.forEach((channel) => {
      const raw = configs[channel.id];
      if (!raw || raw.enabled === false) return;
      const effectiveCwd = (raw.custom_cwd ?? '') || defaultCwd;
      const backend = raw.routing?.agent_backend || defaultBackend;

      if (backend === 'opencode' && config.agents?.opencode?.enabled) {
        neededOpenCodeCwds.add(effectiveCwd);
      }
      if (backend === 'claude' && config.agents?.claude?.enabled) {
        neededClaudeCwds.add(effectiveCwd);
      }
      if (backend === 'codex' && config.agents?.codex?.enabled) {
        neededCodexCwds.add(effectiveCwd);
      }
    });

    neededOpenCodeCwds.forEach((cwd) => {
      if (!opencodeOptionsByCwd[cwd]) {
        void loadOpenCodeOptions(cwd);
      }
    });

    neededClaudeCwds.forEach((cwd) => {
      if (!claudeAgentsByCwd[cwd]) {
        void loadClaudeAgents(cwd);
      }
    });

    neededCodexCwds.forEach((cwd) => {
      if (!codexAgentsByCwd[cwd]) {
        void loadCodexAgents(cwd);
      }
    });
  }, [channels, configs, config.runtime?.default_cwd, config.agents?.default_backend, config.agents?.opencode?.enabled, config.agents?.claude?.enabled, config.agents?.codex?.enabled]);

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
      await api.saveSettings({ channels: nextConfigs }, platform);
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
    show_message_types: ['assistant'],
    custom_cwd: '',
      routing: {
        agent_backend: null,
        opencode_agent: null,
        opencode_model: null,
        opencode_reasoning_effort: null,
        claude_agent: null,
        claude_model: null,
        claude_reasoning_effort: null,
        codex_agent: null,
        codex_model: null,
        codex_reasoning_effort: null,
      },
    require_mention: null,
  });

  const getReasoningOptions = (cwd: string, modelKey: string) => {
    const lookup = opencodeOptionsByCwd[cwd]?.reasoning_options || {};
    if (lookup && typeof lookup === 'object') {
      return (lookup as Record<string, { value: string; label: string }[]>)[modelKey] || [];
    }
    return [];
  };

  const getClaudeReasoningOptions = (model: string) => {
    const modelKey = model || '';
    const cached = claudeReasoningOptions[modelKey];
    if (cached?.length) return cached;

    const fallback = claudeReasoningOptions[''] || [];
    const normalizedModel = modelKey.toLowerCase();
    if (normalizedModel.includes('claude-opus-4-7') || normalizedModel === 'opus' || normalizedModel === 'opus[1m]') {
      const options = [...fallback];
      if (!options.some((option) => option.value === 'xhigh')) {
        options.push({ value: 'xhigh', label: 'Extra High' });
      }
      if (!options.some((option) => option.value === 'max')) {
        options.push({ value: 'max', label: 'Max' });
      }
      return options;
    }
    if (normalizedModel.includes('claude-opus-4-6') || normalizedModel.includes('claude-sonnet-4-6')) {
      return fallback.some((option) => option.value === 'max')
        ? fallback
        : [...fallback, { value: 'max', label: 'Max' }];
    }

    return fallback;
  };

  const getReasoningLabel = (value: string, fallback: string) => {
    switch (value) {
      case 'low':
        return t('channelList.reasoningLow');
      case 'medium':
        return t('channelList.reasoningMedium');
      case 'high':
        return t('channelList.reasoningHigh');
      case 'xhigh':
        return t('channelList.reasoningXHigh');
      case 'max':
        return t('channelList.reasoningMax');
      default:
        return fallback;
    }
  };

  const selectedCount = channels.filter((channel) => isChannelEnabled(channel.id)).length;

  // Sort channels: enabled channels first
  const sortedChannels = React.useMemo(() => {
    return [...channels].sort((a, b) => {
      const enabledDelta = Number(isChannelEnabled(b.id)) - Number(isChannelEnabled(a.id));
      if (enabledDelta !== 0) return enabledDelta;

      if (platform === 'telegram') {
        const topicDelta = Number(Boolean(b.supports_topics)) - Number(Boolean(a.supports_topics));
        if (topicDelta !== 0) return topicDelta;

        const rank = (channel: any) => {
          if (channel.supports_topics) return 3;
          if (channel.type === 'supergroup') return 2;
          if (channel.type === 'group') return 1;
          return 0;
        };
        const rankDelta = rank(b) - rank(a);
        if (rankDelta !== 0) return rankDelta;

        const timeDelta = (Date.parse(b.last_seen_at || '') || 0) - (Date.parse(a.last_seen_at || '') || 0);
        if (timeDelta !== 0) return timeDelta;
      }

      return String(a.name || a.id).localeCompare(String(b.name || b.id));
    });
  }, [channels, configs, platform]);

  const formatTelegramLastSeen = (value?: string) => {
    if (!value) return '';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return '';
    return parsed.toLocaleString();
  };

  const navigate = useNavigate();

  // WeChat: no channels, redirect to user settings
  if (platform === 'wechat') {
    // In wizard mode, skip channel step entirely
    if (!isPage) {
      return (
        <div className="flex flex-col h-full items-center justify-center">
          <div className="w-16 h-16 bg-accent/10 text-accent rounded-full flex items-center justify-center border border-accent/20 mb-6">
            <MessageSquare size={32} />
          </div>
          <h2 className="text-2xl font-display font-bold text-text mb-2">{t('channelList.title')}</h2>
          <p className="text-muted mb-8">{t('wechat.noChannels')}</p>
          <div className="mt-auto flex justify-between w-full">
            <button onClick={onBack} className="px-6 py-2 text-muted hover:text-text font-medium">
              {t('common.back')}
            </button>
            <button
              onClick={() => onNext && onNext({})}
              className="px-6 py-2 bg-accent hover:bg-accent/90 text-white rounded-lg font-medium shadow-sm"
            >
              {t('common.continue')}
            </button>
          </div>
        </div>
      );
    }

    // In page mode, show notice and link to user settings
    return (
      <div className="max-w-5xl mx-auto flex flex-col h-full">
        <div className="flex justify-between items-center mb-6">
          <div>
            <h2 className="text-3xl font-display font-bold">{t('channelList.title')}</h2>
            <p className="text-muted">{t('wechat.noChannels')}</p>
          </div>
        </div>
        <div className="bg-panel border border-border rounded-xl p-8 text-center shadow-sm">
          <div className="w-16 h-16 bg-accent/10 text-accent rounded-full flex items-center justify-center border border-accent/20 mx-auto mb-4">
            <MessageSquare size={32} />
          </div>
          <p className="text-muted mb-6">{t('wechat.noChannels')}</p>
          <button
            onClick={() => navigate('/users')}
            className="inline-flex items-center gap-2 px-6 py-3 bg-accent hover:bg-accent/90 text-white rounded-lg font-medium transition-colors shadow-sm"
          >
            <Users size={18} />
            {t('wechat.manageUserSettings')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <>
    <div className={clsx('flex flex-col h-full', isPage ? 'max-w-5xl mx-auto' : '')}>
      <div className="flex justify-between items-center mb-6">
        <div>
          <h2 className={clsx('font-display font-bold', isPage ? 'text-3xl' : 'text-2xl')}>{t('channelList.title')}</h2>
          <p className="text-muted">{t('channelList.subtitle')}</p>
        </div>
      </div>

      {((isPage && channelPlatforms.length > 1) || isWizardMultiPlatform) && (
        <div className="mb-4 flex flex-wrap gap-2">
          {(isWizardMultiPlatform ? wizardPlatforms! : channelPlatforms).map((candidate) => (
            <button
              key={candidate}
              onClick={() => isWizardMultiPlatform ? switchWizardPlatform(candidate) : setPagePlatform(candidate)}
              className={clsx(
                'px-3 py-1.5 rounded-full text-sm border transition-colors',
                platform === candidate ? 'bg-accent text-white border-accent' : 'bg-panel text-text border-border hover:border-accent/60'
              )}
            >
              {t(`platform.${candidate}.title`)}
            </button>
          ))}
        </div>
      )}

      {/* Platform-level require @mention toggle (page mode only) */}
      {isPage && (
        <div className="mb-4 bg-panel border border-border p-3 rounded-lg flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm">
            <AtSign size={14} className="text-accent" />
            <span className="font-medium text-text">{t('dashboard.requireMention')}</span>
            <span className="text-xs text-muted">{t('dashboard.requireMentionHint')}</span>
          </div>
          <button
            onClick={async () => {
              const key = platform as 'slack' | 'discord' | 'telegram' | 'lark' | 'wechat';
              const currentConfig = configRef.current;
              const current = !!(currentConfig as any)[key]?.require_mention;
              const updated = {
                ...currentConfig,
                [key]: { ...(currentConfig as any)[key], require_mention: !current },
              };
              const version = applyConfig(updated);
              const saved = await saveLatestConfig();
              if (saved) {
                showToast(t('common.saved'), 'success');
              } else if (configVersionRef.current === version) {
                applyConfig(currentConfig);
                showToast(t('channelList.settingsSaveFailed'), 'error');
              } else {
                showToast(t('channelList.settingsSaveFailed'), 'error');
              }
            }}
            className={clsx(
              'relative inline-flex h-6 w-11 items-center rounded-full transition-colors',
              (config as any)[platform]?.require_mention ? 'bg-accent' : 'bg-neutral-300'
            )}
          >
            <span
              className={clsx(
                'inline-block h-4 w-4 rounded-full bg-white transition-transform shadow-sm',
                (config as any)[platform]?.require_mention ? 'translate-x-6' : 'translate-x-1'
              )}
            />
          </button>
        </div>
      )}

      <div className="mb-4 bg-panel border border-border p-4 rounded-lg space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            <button
              onClick={() => loadChannels(browseAll)}
              className="flex items-center gap-2 px-3 py-1.5 bg-neutral-100 hover:bg-neutral-200 text-text rounded text-sm font-medium transition-colors"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> {t('channelList.refreshList')}
            </button>
            {!browseAll && !['lark', 'telegram'].includes(platform) && (
              <button
                onClick={() => loadChannels(true)}
                disabled={loadingAll}
                className="flex items-center gap-2 px-3 py-1.5 bg-neutral-100 hover:bg-neutral-200 text-text rounded text-sm font-medium transition-colors disabled:opacity-50"
              >
                <Globe size={14} className={loadingAll ? 'animate-spin' : ''} />
                {loadingAll ? t('common.loading') : t('channelList.browseAll')}
              </button>
            )}
            {browseAll && (
              <span className="text-xs text-muted">{t('channelList.showingAll')}</span>
            )}
            <span className="relative group">
              <span className="flex items-center gap-1 text-sm text-muted cursor-help">
                <HelpCircle size={14} />
                {t('channelList.cantFindChannel')}
              </span>
              <span className="absolute bottom-full left-0 mb-2 px-3 py-2 bg-text text-bg text-xs rounded shadow-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10 w-64 whitespace-normal">
                {platform === 'discord'
                  ? t('channelList.discordInviteBotHint')
                  : platform === 'lark'
                    ? t('channelList.larkInviteBotHint')
                    : platform === 'telegram'
                      ? t('channelList.telegramInviteBotHint')
                      : t('channelList.inviteBotHint')}
              </span>
            </span>
            {channels.length === 0 && !loading && (
              <span className="text-sm text-warning">{t('channelList.noChannelsFound')}</span>
            )}
          </div>
          <span className="text-sm text-muted font-mono">{t('channelList.enabledCount', { count: selectedCount })}</span>
        </div>
        {platform === 'discord' && (
          <div className="rounded-lg border border-border bg-panel p-3 text-sm">
            <div className="grid gap-3 md:grid-cols-[minmax(220px,280px)_1fr]">
              <div className="space-y-1">
                <label className="font-medium text-text">{t('channelList.guildBrowse')}</label>
                <select
                  value={selectedGuild}
                  onChange={(e) => updateSelectedGuild(e.target.value)}
                  className="w-full bg-bg border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-accent text-text"
                >
                  <option value="">{t('channelList.guildPlaceholder')}</option>
                  {knownDiscordGuilds.map((g) => (
                    <option key={g.id} value={g.id}>{g.name}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-text">{t('channelList.guildAccess')}</span>
                  <span className="text-xs text-muted">
                    {t('channelList.guildAccessCount', { count: selectedGuildIds.length })}
                  </span>
                </div>
                {knownDiscordGuilds.length > 0 && (
                  <div className="flex flex-wrap gap-2">
                    {knownDiscordGuilds.map((g) => (
                      <label
                        key={g.id}
                        className={clsx(
                          'inline-flex items-center gap-2 rounded border px-2 py-1 text-xs transition-colors',
                          selectedGuildIds.includes(g.id)
                            ? 'border-accent bg-accent/10 text-text'
                            : 'border-border bg-bg text-muted hover:text-text'
                        )}
                      >
                        <input
                          type="checkbox"
                          checked={selectedGuildIds.includes(g.id)}
                          onChange={(e) => toggleAllowedGuild(g.id, e.target.checked)}
                          className="h-3.5 w-3.5 accent-accent"
                        />
                        <span>{g.name}</span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
        {isPage && (
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-muted">{t('channelList.accessPolicy')}</span>
            <span className="text-xs text-muted">{t('channelList.accessPolicyHint')}</span>
          </div>
        )}
        {platform === 'telegram' && (
          <div className="rounded-lg border border-accent/20 bg-accent/5 p-3">
            <div className="text-sm font-medium text-text">{t('channelList.telegramDiscoveryTitle')}</div>
            <div className="mt-1 text-sm text-muted">{t('channelList.telegramDiscoveryInfo')}</div>
            <div className="mt-2 text-xs text-muted">
              {t('channelList.telegramDiscoveryStats', {
                visible: telegramSummary?.visible_count || 0,
                forum: telegramSummary?.forum_count || 0,
                hidden: telegramSummary?.hidden_private_count || 0,
              })}
            </div>
          </div>
        )}
      </div>

      <div className="flex-1 overflow-y-auto border border-border rounded-xl divide-y divide-border bg-panel shadow-sm">
        {!loading && channels.length === 0 && !botToken && platform !== 'lark' && (
          <div className="p-8 text-center text-muted">
            {t('channelList.addTokenFirst')}
          </div>
        )}
        {!loading && channels.length === 0 && platform === 'telegram' && !!botToken && (
          <div className="p-8 text-center">
            <div className="text-sm font-medium text-text">{t('channelList.telegramDiscoveryEmptyTitle')}</div>
            <div className="mt-2 text-sm text-muted">{t('channelList.telegramDiscoveryEmptyDesc')}</div>
          </div>
        )}
        {sortedChannels.map((channel) => {
          const rawConfig = configs[channel.id] || {};
          const def = defaultConfig();
          const defaultBackend = config.agents?.default_backend || 'opencode';
          const channelConfig = {
            ...def,
            ...rawConfig,
            enabled: isChannelEnabled(channel.id),
            show_message_types: rawConfig.show_message_types || def.show_message_types,
            custom_cwd: rawConfig.custom_cwd ?? def.custom_cwd,
            routing: {
              ...def.routing,
              ...(rawConfig.routing || {}),
            },
            // Preserve require_mention from rawConfig (can be null, true, or false)
            require_mention: rawConfig.require_mention !== undefined ? rawConfig.require_mention : def.require_mention,
          };
          const effectiveBackend = channelConfig.routing.agent_backend || defaultBackend;

          const effectiveCwd = channelConfig.custom_cwd || config.runtime?.default_cwd || '~/work';
          const opencodeOptions = opencodeOptionsByCwd[effectiveCwd];
          const claudeAgents = claudeAgentsByCwd[effectiveCwd] || [];
          const codexAgents = codexAgentsByCwd[effectiveCwd] || [];
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
                    {platform === 'telegram' && (channel.username || channel.last_seen_at) && (
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
                        {channel.username && <span>@{channel.username}</span>}
                        {channel.last_seen_at && (
                          <span>{t('channelList.telegramLastSeen', { time: formatTelegramLastSeen(channel.last_seen_at) })}</span>
                        )}
                      </div>
                    )}
                  </div>
                </div>
                <span
                  className={clsx(
                    'text-xs px-2 py-0.5 rounded-full border',
                    platform === 'discord'
                      ? 'bg-neutral-100 text-text border-border'
                      : platform === 'telegram'
                        ? channel.supports_topics
                          ? 'bg-accent/10 text-accent border-accent/20'
                          : channel.type === 'supergroup'
                            ? 'bg-success/10 text-success border-success/20'
                            : channel.is_private
                              ? 'bg-warning/10 text-warning border-warning/20'
                              : 'bg-neutral-100 text-text border-border'
                      : channel.is_private
                        ? 'bg-warning/10 text-warning border-warning/20'
                        : 'bg-success/10 text-success border-success/20'
                  )}
                >
                  {platform === 'discord'
                    ? (channel.type === 5 ? t('channelList.discordNews') : t('channelList.discordText'))
                    : platform === 'telegram'
                      ? channel.supports_topics
                        ? t('channelList.telegramForum')
                        : channel.type === 'supergroup'
                          ? t('channelList.telegramSupergroup')
                          : channel.type === 'group'
                            ? t('channelList.telegramGroup')
                            : channel.is_private
                              ? t('common.private')
                              : t('channelList.telegramChat')
                    : channel.is_private ? t('common.private') : t('common.public')}
                </span>
              </div>

              {channelConfig.enabled && (
                <div className="mt-4 pl-8 space-y-4">
                  {/* Basic Settings */}
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-muted uppercase">{t('channelList.workingDirectory')}</label>
                      <div className="flex gap-1.5">
                        <BlurInput
                          type="text"
                          placeholder={config.runtime?.default_cwd || t('channelList.useGlobalDefault')}
                          value={channelConfig.custom_cwd}
                          onCommit={(v) => updateConfig(channel.id, { custom_cwd: v })}
                          className="flex-1 bg-bg border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-accent text-text placeholder:text-muted/50 font-mono"
                        />
                        <button
                          type="button"
                          onClick={() => setBrowsingCwdFor(channel.id)}
                          title={t('directoryBrowser.title')}
                          className="px-2 py-2 bg-neutral-100 hover:bg-neutral-200 border border-border rounded text-muted hover:text-text transition-colors shrink-0"
                        >
                          <FolderOpen size={14} />
                        </button>
                      </div>
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-muted uppercase">{t('channelList.backend')}</label>
                      <select
                        value={effectiveBackend}
                        onChange={(e) =>
                          updateConfig(channel.id, {
                            routing: { ...channelConfig.routing, agent_backend: e.target.value },
                          })
                        }
                        className="w-full bg-bg border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-accent text-text"
                      >
                        <option value="opencode">OpenCode</option>
                        <option value="claude">ClaudeCode</option>
                        <option value="codex">Codex</option>
                      </select>
                    </div>
                    <div className="space-y-1">
                      <label className="text-xs font-medium text-muted uppercase">{t('channelList.requireMention')}</label>
                      <select
                        value={channelConfig.require_mention === null || channelConfig.require_mention === undefined ? '' : channelConfig.require_mention ? 'true' : 'false'}
                        onChange={(e) => {
                          const val = e.target.value;
                          updateConfig(channel.id, {
                            require_mention: val === '' ? null : val === 'true',
                          });
                        }}
                        className="w-full bg-bg border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-accent text-text"
                      >
                        <option value="">
                          {t('common.default')} ({platform === 'discord'
                            ? (config.discord?.require_mention ? t('channelList.mentionStatusOn') : t('channelList.mentionStatusOff'))
                            : platform === 'telegram'
                              ? (config.telegram?.require_mention ? t('channelList.mentionStatusOn') : t('channelList.mentionStatusOff'))
                            : platform === 'lark'
                              ? (config.lark?.require_mention ? t('channelList.mentionStatusOn') : t('channelList.mentionStatusOff'))
                              : (config.slack?.require_mention ? t('channelList.mentionStatusOn') : t('channelList.mentionStatusOff'))})
                        </option>
                        <option value="true">{t('channelList.requireMentionOn')}</option>
                        <option value="false">{t('channelList.requireMentionOff')}</option>
                      </select>
                    </div>
                  </div>

                  {/* Show Message Types */}
                  <div className="space-y-2">
                    <div className="text-xs font-medium text-muted uppercase flex items-center gap-1">
                      {t('channelList.showMessageTypes')}
                      <span className="relative group">
                        <HelpCircle size={12} className="text-muted/50 cursor-help" />
                        <span className="absolute bottom-full left-0 mb-2 px-3 py-2 bg-text text-bg text-xs rounded shadow-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10 w-64 whitespace-normal font-normal normal-case">
                          {t('channelList.showMessageTypesHint')}
                        </span>
                      </span>
                    </div>
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
                  {effectiveBackend === 'opencode' && (
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
                            disabled={!getReasoningOptions(
                              effectiveCwd,
                              selectedModels[channel.id] || channelConfig.routing.opencode_model || ''
                            ).length}
                            className="w-full bg-panel border border-border rounded px-3 py-2 text-sm disabled:opacity-50"
                          >
                            <option value="">{t('common.default')}</option>
                            {getReasoningOptions(
                              effectiveCwd,
                              selectedModels[channel.id] || channelConfig.routing.opencode_model || ''
                            ).map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Claude Settings */}
                  {effectiveBackend === 'claude' && (
                    <div className="space-y-3">
                      <div className="text-xs font-medium text-muted uppercase">{t('channelList.claudeSettings')}</div>
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 bg-bg/50 p-3 rounded border border-border">
                        <div className="space-y-1">
                          <label className="text-xs text-muted">{t('channelList.agent')}</label>
                          <select
                            value={channelConfig.routing.claude_agent || ''}
                            onChange={(e) =>
                              updateConfig(channel.id, {
                                routing: { ...channelConfig.routing, claude_agent: e.target.value || null },
                              })
                            }
                            className="w-full bg-panel border border-border rounded px-3 py-2 text-sm"
                          >
                            <option value="">{t('common.default')}</option>
                            {claudeAgents.map((agent) => (
                              <option key={agent.id} value={agent.id}>{agent.name}</option>
                            ))}
                          </select>
                        </div>
                        <div className="space-y-1">
                          <label className="text-xs text-muted">{t('channelList.model')}</label>
                          <Combobox
                            options={[
                              { value: '', label: t('common.default') },
                              ...claudeModels.map(m => ({ value: m, label: m }))
                            ]}
                            value={channelConfig.routing.claude_model || ''}
                            onValueChange={(v) =>
                              updateConfig(channel.id, {
                                routing: {
                                  ...channelConfig.routing,
                                  claude_model: v || null,
                                  claude_reasoning_effort: null,
                                },
                              })
                            }
                            placeholder={t('channelList.claudeModelPlaceholder')}
                            searchPlaceholder={t('channelList.searchModel')}
                            allowCustomValue={true}
                          />
                        </div>
                        <div className="space-y-1">
                          <label className="text-xs text-muted">{t('channelList.reasoningEffort')}</label>
                          <select
                            value={channelConfig.routing.claude_reasoning_effort || ''}
                            onChange={(e) =>
                              updateConfig(channel.id, {
                                routing: {
                                  ...channelConfig.routing,
                                  claude_reasoning_effort: e.target.value || null,
                                },
                              })
                            }
                            className="w-full bg-panel border border-border rounded px-3 py-2 text-sm"
                          >
                            <option value="">{t('common.default')}</option>
                            {getClaudeReasoningOptions(channelConfig.routing.claude_model || '')
                              .filter((option) => option.value !== '__default__')
                              .map((option) => (
                                <option key={option.value} value={option.value}>
                                  {getReasoningLabel(option.value, option.label)}
                                </option>
                              ))}
                          </select>
                        </div>
                      </div>
                    </div>
                  )}

                  {/* Codex Settings */}
                  {effectiveBackend === 'codex' && (
                    <div className="space-y-3">
                      <div className="text-xs font-medium text-muted uppercase">{t('channelList.codexSettings')}</div>
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 bg-bg/50 p-3 rounded border border-border">
                        <div className="space-y-1">
                          <label className="text-xs text-muted">{t('channelList.agent')}</label>
                          <select
                            value={channelConfig.routing.codex_agent || ''}
                            onChange={(e) =>
                              updateConfig(channel.id, {
                                routing: { ...channelConfig.routing, codex_agent: e.target.value || null },
                              })
                            }
                            className="w-full bg-panel border border-border rounded px-3 py-2 text-sm"
                          >
                            <option value="">{t('common.default')}</option>
                            {codexAgents.map((agent) => (
                              <option key={agent.id} value={agent.id}>{agent.name}</option>
                            ))}
                          </select>
                        </div>
                        <div className="space-y-1">
                          <label className="text-xs text-muted">{t('channelList.model')}</label>
                          <Combobox
                            options={[
                              { value: '', label: t('common.default') },
                              ...codexModels.map(m => ({ value: m, label: m }))
                            ]}
                            value={channelConfig.routing.codex_model || ''}
                            onValueChange={(v) =>
                              updateConfig(channel.id, {
                                routing: { ...channelConfig.routing, codex_model: v || null },
                              })
                            }
                            placeholder={t('channelList.codexModelPlaceholder')}
                            searchPlaceholder={t('channelList.searchModel')}
                            allowCustomValue={true}
                          />
                        </div>
                        <div className="space-y-1">
                          <label className="text-xs text-muted">{t('channelList.reasoningEffort')}</label>
                          <select
                            value={channelConfig.routing.codex_reasoning_effort || ''}
                            onChange={(e) =>
                              updateConfig(channel.id, {
                                routing: {
                                  ...channelConfig.routing,
                                  codex_reasoning_effort: e.target.value || null,
                                },
                              })
                            }
                            className="w-full bg-panel border border-border rounded px-3 py-2 text-sm"
                          >
                            <option value="">{t('common.default')}</option>
                            <option value="low">{t('channelList.reasoningLow')}</option>
                            <option value="medium">{t('channelList.reasoningMedium')}</option>
                            <option value="high">{t('channelList.reasoningHigh')}</option>
                            <option value="xhigh">{t('channelList.reasoningXHigh')}</option>
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
            onClick={() => {
              const discordGuildAllowlist = selectedGuildIds;
              if (isWizardMultiPlatform) {
                // Merge configs from all visited platforms
                const allConfigs = { ...wizardConfigsMap, [wizardActivePlatform]: configs };
                onNext && onNext({
                  channelConfigsByPlatform: {
                    ...(data.channelConfigsByPlatform || {}),
                    ...allConfigs,
                  },
                  ...(getEnabledPlatforms(data).includes('discord') ? { discordGuildAllowlist } : {}),
                });
              } else {
                onNext && onNext({
                  channelConfigsByPlatform: {
                    ...(data.channelConfigsByPlatform || {}),
                    [platform]: configs,
                  },
                  settingsPlatform: platform,
                  ...(platform === 'discord' ? { discordGuildAllowlist } : {}),
                });
              }
            }}
            className="px-6 py-2 bg-accent hover:bg-accent/90 text-white rounded-lg font-medium shadow-sm"
          >
            {t('common.continue')}
          </button>
        </div>
      )}
    </div>

    {/* Directory browser modal */}
    {browsingCwdFor && (
      <DirectoryBrowser
        initialPath={configs[browsingCwdFor]?.custom_cwd || config.runtime?.default_cwd || '~/work'}
        onSelect={(path) => {
          updateConfig(browsingCwdFor, { custom_cwd: path });
          setBrowsingCwdFor(null);
        }}
        onClose={() => setBrowsingCwdFor(null)}
      />
    )}
    </>
  );
};
