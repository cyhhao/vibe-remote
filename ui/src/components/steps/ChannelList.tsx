import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  AtSign,
  CheckSquare,
  ChevronDown,
  ChevronUp,
  Globe,
  Hash,
  HelpCircle,
  MessageSquare,
  RefreshCw,
  Search,
  Square,
  Users,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useApi } from '../../context/ApiContext';
import { useToast } from '../../context/ToastContext';
import { DirectoryBrowser } from '../ui/directory-browser';
import { useNavigate } from 'react-router-dom';
import clsx from 'clsx';
import { getEnabledPlatforms, platformSupportsChannels } from '../../lib/platforms';
import { EyebrowBadge, PlatformIcon, WizardCard } from '../visual';
import { RoutingConfigPanel } from '../shared/RoutingConfigPanel';

const PLATFORM_BRAND_COLORS: Record<string, string> = {
  slack: '#4A154B',
  discord: '#5865F2',
  telegram: '#0088CC',
  lark: '#4C6EB5',
  wechat: '#07C160',
};

const platformBoxStyle = (platformId: string): React.CSSProperties => {
  const base = PLATFORM_BRAND_COLORS[platformId] || '#5BFFA0';
  return { backgroundColor: `${base}26`, borderColor: `${base}66` };
};

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
  // Page-mode tab/search/collapse state (only used when isPage is true)
  const [pageTab, setPageTab] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState('');
  const [expandedChannelId, setExpandedChannelId] = useState<string | null>(null);
  const [showInactive, setShowInactive] = useState(true);
  // All-channels aggregation: keyed by platform
  const [allChannelsByPlatform, setAllChannelsByPlatform] = useState<Record<string, any[]>>({});
  const [allConfigsByPlatform, setAllConfigsByPlatform] = useState<Record<string, Record<string, ChannelConfig>>>({});
  const [allLoading, setAllLoading] = useState(false);

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

  const selectedCount = channels.filter((channel) => isChannelEnabled(channel.id)).length;

  // ---- Page-mode all-platforms aggregation ----
  const updateConfigForPlatform = async (
    platformId: string,
    channelId: string,
    patch: Partial<ChannelConfig>
  ) => {
    const platformConfigs = (allConfigsByPlatform[platformId] || (platformId === platform ? configs : {})) as Record<
      string,
      ChannelConfig
    >;
    const base = platformConfigs[channelId] || defaultConfig();
    const next = { ...base, ...patch };
    if (!next.show_message_types) {
      next.show_message_types = defaultConfig().show_message_types;
    }
    if (!next.routing || typeof next.routing !== 'object') {
      next.routing = { agent_backend: config.agents?.default_backend || 'opencode' };
    }
    const nextPlatformConfigs = { ...platformConfigs, [channelId]: next };
    setAllConfigsByPlatform((prev) => ({ ...prev, [platformId]: nextPlatformConfigs }));
    if (platformId === platform) {
      setConfigs(nextPlatformConfigs);
    }
    if (!isPage) return;
    try {
      await api.saveSettings({ channels: nextPlatformConfigs }, platformId);
      showToast(t('channelList.settingsSaved'));
    } catch {
      showToast(t('channelList.settingsSaveFailed'), 'error');
    }
  };

  const loadAllPlatformsData = async () => {
    if (!isPage) return;
    const platforms = getEnabledPlatforms(config).filter((p) => platformSupportsChannels(config, p));
    if (platforms.length === 0) return;
    setAllLoading(true);
    try {
      const results = await Promise.all(
        platforms.map(async (p) => {
          let channelsList: any[] = [];
          let configsMap: Record<string, ChannelConfig> = {};
          try {
            const settings = await api.getSettings(p);
            configsMap = (settings.channels || {}) as Record<string, ChannelConfig>;
            if (p === 'lark') {
              const appId = config.lark?.app_id || '';
              const appSecret = config.lark?.app_secret || '';
              const domain = config.lark?.domain || 'feishu';
              if (appId && appSecret) {
                const result = await api.larkChats(appId, appSecret, domain);
                if (result.ok) channelsList = result.channels || [];
              }
            } else if (p === 'telegram') {
              if (config.telegram?.bot_token) {
                const result = await api.telegramChats(false);
                if (result.ok) channelsList = result.channels || [];
              }
            } else if (p === 'discord') {
              const allowlist = getDiscordGuildAllowlist(settings);
              const guildId = allowlist[0] || selectedGuildIdsRef.current[0] || selectedGuild;
              const token = config.discord?.bot_token || '';
              if (guildId && token) {
                const result = await api.discordChannels(token, guildId);
                if (result.ok) {
                  channelsList = (result.channels || []).filter((c: any) => c.type === 0 || c.type === 5);
                }
              }
            } else if (p === 'slack') {
              if (config.slack?.bot_token) {
                const result = await api.slackChannels(config.slack.bot_token, false);
                if (result.ok) channelsList = result.channels || [];
              }
            }
          } catch {
            // ignore individual platform failures
          }
          return { platform: p, channels: channelsList, configs: configsMap };
        })
      );
      const channelsByPlatform: Record<string, any[]> = {};
      const configsByPlatform: Record<string, Record<string, ChannelConfig>> = {};
      for (const r of results) {
        channelsByPlatform[r.platform] = r.channels;
        configsByPlatform[r.platform] = r.configs;
      }
      setAllChannelsByPlatform(channelsByPlatform);
      setAllConfigsByPlatform(configsByPlatform);
    } finally {
      setAllLoading(false);
    }
  };

  // Load aggregated data on first All-tab activation, and refresh when config keys change
  useEffect(() => {
    if (!isPage) return;
    if (pageTab !== 'all') return;
    void loadAllPlatformsData();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    isPage,
    pageTab,
    config.slack?.bot_token,
    config.discord?.bot_token,
    config.telegram?.bot_token,
    config.lark?.app_id,
    config.lark?.app_secret,
  ]);

  // When the user picks a per-platform tab, sync pagePlatform so existing flow loads it
  useEffect(() => {
    if (!isPage) return;
    if (pageTab === 'all') return;
    if (pageTab !== pagePlatform) {
      setPagePlatform(pageTab);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPage, pageTab]);

  type AggregatedRow = {
    platform: string;
    channel: any;
    config: ChannelConfig;
  };

  const platformChannelCounts = useMemo(() => {
    const counts: Record<string, { total: number; active: number }> = {};
    const platforms = getEnabledPlatforms(config).filter((p) => platformSupportsChannels(config, p));
    for (const p of platforms) {
      const channelList = p === platform ? channels : allChannelsByPlatform[p] || [];
      const configMap = p === platform ? configs : allConfigsByPlatform[p] || {};
      const total = channelList.length;
      const active = channelList.filter((c: any) => configMap[c.id]?.enabled !== false && configMap[c.id]?.enabled !== undefined && configMap[c.id]?.enabled).length;
      counts[p] = { total, active };
    }
    return counts;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config, platform, channels, configs, allChannelsByPlatform, allConfigsByPlatform]);

  const allTabCounts = useMemo(() => {
    let total = 0;
    let active = 0;
    for (const key of Object.keys(platformChannelCounts)) {
      total += platformChannelCounts[key].total;
      active += platformChannelCounts[key].active;
    }
    return { total, active };
  }, [platformChannelCounts]);

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
        <div className="flex w-full justify-center">
          <WizardCard className="gap-6">
            <div className="space-y-2">
              <EyebrowBadge tone="mint">Groups</EyebrowBadge>
              <h2 className="text-[28px] font-bold leading-tight tracking-[-0.4px] text-foreground">
                {t('channelList.title')}
              </h2>
              <p className="text-[14px] leading-[1.55] text-muted">{t('wechat.noChannels')}</p>
            </div>
            <div className="flex flex-col items-center gap-3 rounded-xl border border-cyan/30 bg-cyan/[0.06] px-6 py-10 text-center">
              <div className="flex size-14 items-center justify-center rounded-full border border-cyan/30 bg-cyan/[0.08] text-cyan">
                <MessageSquare size={26} />
              </div>
              <p className="text-[12px] text-muted">{t('wechat.noChannels')}</p>
            </div>
            <div className="flex items-center justify-between border-t border-border pt-4">
              <button
                type="button"
                onClick={onBack}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-4 py-2 text-[13px] font-semibold text-foreground transition hover:border-border-strong"
              >
                <ArrowLeft size={14} strokeWidth={2.25} />
                {t('common.back')}
              </button>
              <button
                type="button"
                onClick={() => onNext && onNext({})}
                className="inline-flex items-center gap-2 rounded-lg bg-mint px-5 py-2.5 text-[13px] font-bold text-[#080812] shadow-[0_0_32px_-6px_rgba(91,255,160,0.6)] transition hover:brightness-105"
              >
                {t('common.continue')}
                <ArrowRight size={14} strokeWidth={2.25} />
              </button>
            </div>
          </WizardCard>
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
        <div className="rounded-2xl border border-border bg-surface-2/40 p-8 text-center shadow-[0_18px_40px_-30px_rgba(0,0,0,0.8)]">
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

  // ====================================================================
  // PAGE MODE: redesigned layout (matches design.pen frame A7h8Wv)
  // - Header with title + 240px search + Rescan
  // - Tabs row (All channels + per-platform with PlatformIcon + counts)
  // - Per-tab notes (Discord guild picker, Telegram discovery info)
  // - List header (count summary + Show inactive toggle)
  // - Collapsible channel rows with platform-colored icon box + status badge
  // ====================================================================
  if (isPage) {
    const tabPlatforms = getEnabledPlatforms(config).filter((p) => platformSupportsChannels(config, p));

    const buildRowsForPlatform = (p: string): AggregatedRow[] => {
      const channelList = p === platform ? channels : (allChannelsByPlatform[p] || []);
      const configsMap = p === platform ? configs : (allConfigsByPlatform[p] || {});
      return channelList.map((c: any) => ({
        platform: p,
        channel: c,
        config: configsMap[c.id] || defaultConfig(),
      }));
    };

    const rawRows: AggregatedRow[] = pageTab === 'all'
      ? tabPlatforms.flatMap(buildRowsForPlatform)
      : buildRowsForPlatform(pageTab);

    const lowerQuery = searchQuery.trim().toLowerCase();
    const visibleRows = rawRows
      .filter((r) => {
        const enabled = r.config.enabled === true;
        if (!showInactive && !enabled) return false;
        if (!lowerQuery) return true;
        const name = String(r.channel.name || r.channel.id || '').toLowerCase();
        const id = String(r.channel.id || '').toLowerCase();
        return name.includes(lowerQuery) || id.includes(lowerQuery);
      })
      .sort((a, b) => {
        const aOn = a.config.enabled === true;
        const bOn = b.config.enabled === true;
        if (aOn !== bOn) return aOn ? -1 : 1;
        return String(a.channel.name || a.channel.id).localeCompare(String(b.channel.name || b.channel.id));
      });

    const handleRescan = () => {
      if (pageTab === 'all') {
        void loadAllPlatformsData();
      } else {
        void loadChannels(false);
      }
    };

    const summary = pageTab === 'all'
      ? { active: allTabCounts.active, total: allTabCounts.total }
      : platformChannelCounts[pageTab] || { active: 0, total: 0 };

    const isRescanLoading = pageTab === 'all' ? allLoading : (loading || loadingAll);

    return (
      <>
        <div className="flex h-full flex-col gap-5">
          {/* Page header — design.pen iXX59 */}
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="flex flex-col gap-1.5">
              <h1 className="text-[28px] font-bold leading-tight tracking-[-0.4px] text-foreground">
                {t('channelList.title')}
              </h1>
              <p className="text-[14px] leading-[1.55] text-muted">{t('channelList.subtitle')}</p>
            </div>
            <div className="flex items-center gap-2">
              <div className="relative">
                <Search size={14} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <input
                  type="search"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t('channelList.filterPlaceholder')}
                  className="h-9 w-[240px] rounded-lg border border-border bg-surface pl-9 pr-3 text-[13px] text-foreground placeholder:text-muted focus:border-mint/50 focus:outline-none"
                />
              </div>
              <button
                type="button"
                onClick={handleRescan}
                disabled={isRescanLoading}
                className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-3 text-[13px] font-medium text-foreground transition-colors hover:border-border-strong disabled:opacity-50"
              >
                <RefreshCw size={14} className={isRescanLoading ? 'animate-spin' : ''} />
                {t('channelList.rescan')}
              </button>
            </div>
          </div>

          {/* Platform Tabs — design.pen O2hb2M */}
          <div className="flex flex-wrap items-end gap-1 border-b border-border">
            <button
              type="button"
              onClick={() => setPageTab('all')}
              className={clsx(
                '-mb-px flex items-center gap-2 border-b-2 px-3 pb-2.5 pt-2 text-[13px] font-medium transition-colors',
                pageTab === 'all'
                  ? 'border-mint text-foreground'
                  : 'border-transparent text-muted hover:text-foreground'
              )}
            >
              <Globe size={16} className={pageTab === 'all' ? 'text-mint' : 'text-muted'} />
              <span>{t('channelList.allChannelsTab')}</span>
              <span
                className={clsx(
                  'rounded-full px-1.5 py-0.5 font-mono text-[10px]',
                  pageTab === 'all' ? 'bg-mint-soft text-mint' : 'bg-white/[0.06] text-muted'
                )}
              >
                {allTabCounts.active}
              </span>
            </button>
            {tabPlatforms.map((p) => {
              const counts = platformChannelCounts[p] || { active: 0, total: 0 };
              const active = pageTab === p;
              return (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPageTab(p)}
                  className={clsx(
                    '-mb-px flex items-center gap-2 border-b-2 px-3 pb-2.5 pt-2 text-[13px] font-medium transition-colors',
                    active
                      ? 'border-mint text-foreground'
                      : 'border-transparent text-muted hover:text-foreground'
                  )}
                >
                  <PlatformIcon platform={p} size={16} />
                  <span>{t(`platform.${p}.title`)}</span>
                  <span
                    className={clsx(
                      'rounded-full px-1.5 py-0.5 font-mono text-[10px]',
                      active ? 'bg-mint-soft text-mint' : 'bg-white/[0.06] text-muted'
                    )}
                  >
                    {counts.active}
                  </span>
                </button>
              );
            })}
          </div>

          {/* Discord guild picker — only on Discord tab */}
          {pageTab === 'discord' && (
            <div className="rounded-xl border border-border bg-surface-2/40 p-3 text-sm shadow-[0_18px_40px_-30px_rgba(0,0,0,0.8)]">
              <div className="grid gap-3 md:grid-cols-[minmax(220px,280px)_1fr]">
                <div className="space-y-1">
                  <label className="font-medium text-foreground">{t('channelList.guildBrowse')}</label>
                  <select
                    value={selectedGuild}
                    onChange={(e) => updateSelectedGuild(e.target.value)}
                    className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-foreground focus:border-cyan focus:outline-none"
                  >
                    <option value="">{t('channelList.guildPlaceholder')}</option>
                    {knownDiscordGuilds.map((g) => (
                      <option key={g.id} value={g.id}>{g.name}</option>
                    ))}
                  </select>
                </div>
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium text-foreground">{t('channelList.guildAccess')}</span>
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
                            'inline-flex items-center gap-2 rounded-lg border px-2 py-1 text-xs transition-colors',
                            selectedGuildIds.includes(g.id)
                              ? 'border-mint/40 bg-mint-soft text-foreground'
                              : 'border-border bg-surface text-muted hover:text-foreground'
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

          {/* Telegram discovery info — only on Telegram tab */}
          {pageTab === 'telegram' && (
            <div className="rounded-xl border border-cyan/30 bg-cyan-soft/20 p-3">
              <div className="text-sm font-medium text-foreground">{t('channelList.telegramDiscoveryTitle')}</div>
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

          {/* Browse all + Can't find hint — per-platform tabs only */}
          {pageTab !== 'all' && (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-0.5">
              {!['lark', 'telegram'].includes(pageTab) && (
                <button
                  type="button"
                  onClick={() => loadChannels(true)}
                  disabled={loadingAll}
                  className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-3 py-1.5 text-[12px] font-medium text-foreground transition-colors hover:border-cyan/40 disabled:opacity-50"
                >
                  <Globe size={13} className={loadingAll ? 'animate-spin' : ''} />
                  {loadingAll ? t('common.loading') : t('channelList.browseAll')}
                </button>
              )}
              {browseAll && pageTab === platform && (
                <span className="text-[11px] text-muted">{t('channelList.showingAll')}</span>
              )}
              <span className="group relative inline-flex">
                <span className="flex cursor-help items-center gap-1 text-[12px] text-muted">
                  <HelpCircle size={13} />
                  {t('channelList.cantFindChannel')}
                </span>
                <span className="pointer-events-none absolute bottom-full left-0 z-10 mb-2 w-64 whitespace-normal rounded bg-text px-3 py-2 text-xs text-bg opacity-0 shadow-lg transition-opacity group-hover:opacity-100">
                  {pageTab === 'discord'
                    ? t('channelList.discordInviteBotHint')
                    : pageTab === 'lark'
                      ? t('channelList.larkInviteBotHint')
                      : pageTab === 'telegram'
                        ? t('channelList.telegramInviteBotHint')
                        : t('channelList.inviteBotHint')}
                </span>
              </span>
            </div>
          )}

          {/* List header — design.pen xQq1G */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-[13px] text-muted">
              {pageTab === 'all'
                ? t('channelList.headerSummary', { active: summary.active, discovered: summary.total })
                : t('channelList.headerSummaryPlatform', {
                    platform: t(`platform.${pageTab}.title`),
                    active: summary.active,
                    discovered: summary.total,
                  })}
            </div>
            <label className="inline-flex cursor-pointer items-center gap-2">
              <span className="text-[12px] text-muted">{t('channelList.showInactive')}</span>
              <input
                type="checkbox"
                checked={showInactive}
                onChange={(e) => setShowInactive(e.target.checked)}
                className="sr-only"
              />
              <span
                className={clsx(
                  'relative inline-flex h-[18px] w-[30px] items-center rounded-full transition-colors',
                  showInactive ? 'bg-mint' : 'bg-neutral-300'
                )}
              >
                <span
                  className={clsx(
                    'inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform',
                    showInactive ? 'translate-x-[14px]' : 'translate-x-0.5'
                  )}
                />
              </span>
            </label>
          </div>

          {/* Channel rows — design.pen JrNBe / M8FRiG / KywfU */}
          <div className="flex-1 space-y-3 overflow-y-auto">
            {visibleRows.length === 0 && (
              <div className="rounded-xl border border-dashed border-border bg-surface-3/60 p-8 text-center text-muted">
                {searchQuery
                  ? t('channelList.noChannelsForSearch')
                  : pageTab === 'discord' && !selectedGuild
                    ? t('channelList.discordNeedsGuild')
                    : t('channelList.noChannelsLoaded')}
              </div>
            )}
            {visibleRows.map((row) => {
              const channel = row.channel;
              const channelPlatform = row.platform;
              const rawConfig = row.config;
              const def = defaultConfig();
              const defaultBackend = config.agents?.default_backend || 'opencode';
              const channelEnabled = rawConfig.enabled === true;
              const channelConfig = {
                ...def,
                ...rawConfig,
                enabled: channelEnabled,
                show_message_types: rawConfig.show_message_types || def.show_message_types,
                custom_cwd: rawConfig.custom_cwd ?? def.custom_cwd,
                routing: { ...def.routing, ...(rawConfig.routing || {}) },
                require_mention: rawConfig.require_mention !== undefined ? rawConfig.require_mention : def.require_mention,
              };
              const rowKey = `${channelPlatform}::${channel.id}`;
              const expanded = expandedChannelId === rowKey;
              const effectiveBackend = channelConfig.routing.agent_backend || defaultBackend;
              const effectiveCwd = channelConfig.custom_cwd || config.runtime?.default_cwd || '~/work';
              const opencodeOptions = opencodeOptionsByCwd[effectiveCwd];
              const claudeAgents = claudeAgentsByCwd[effectiveCwd] || [];
              const codexAgents = codexAgentsByCwd[effectiveCwd] || [];

              const updateRow = (patch: Partial<ChannelConfig>) => {
                void updateConfigForPlatform(channelPlatform, channel.id, patch);
              };

              const backendModel = effectiveBackend === 'claude'
                ? channelConfig.routing.claude_model
                : effectiveBackend === 'codex'
                  ? channelConfig.routing.codex_model
                  : channelConfig.routing.opencode_model;

              return (
                <div
                  key={rowKey}
                  className={clsx(
                    'rounded-xl border transition-colors',
                    expanded
                      ? 'border-mint/30 bg-surface-2/70 shadow-[0_0_32px_-8px_rgba(91,255,160,0.45)]'
                      : 'border-border bg-background hover:border-border-strong'
                  )}
                >
                  {/* Top row */}
                  <button
                    type="button"
                    onClick={() => setExpandedChannelId(expanded ? null : rowKey)}
                    className="flex w-full items-center gap-3.5 px-5 py-3.5 text-left"
                  >
                    {/* Toggle */}
                    <span
                      role="switch"
                      aria-checked={channelEnabled}
                      tabIndex={0}
                      onClick={(e) => {
                        e.stopPropagation();
                        updateRow({ enabled: !channelEnabled });
                      }}
                      onKeyDown={(e) => {
                        if (e.key === ' ' || e.key === 'Enter') {
                          e.preventDefault();
                          e.stopPropagation();
                          updateRow({ enabled: !channelEnabled });
                        }
                      }}
                      className={clsx(
                        'relative inline-flex h-[18px] w-8 shrink-0 cursor-pointer items-center rounded-full transition-colors',
                        channelEnabled ? 'bg-mint' : 'bg-neutral-300'
                      )}
                    >
                      <span
                        className={clsx(
                          'inline-block h-3.5 w-3.5 rounded-full bg-white shadow-sm transition-transform',
                          channelEnabled ? 'translate-x-[14px]' : 'translate-x-0.5'
                        )}
                      />
                    </span>

                    {/* Platform-tinted icon box */}
                    <span
                      className="flex size-[34px] shrink-0 items-center justify-center rounded-md border"
                      style={platformBoxStyle(channelPlatform)}
                    >
                      <PlatformIcon platform={channelPlatform} size={18} />
                    </span>

                    {/* Name + meta */}
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-mono text-[13px] font-semibold text-foreground">
                        {channel.name || channel.id}
                      </span>
                      <span className="block truncate text-[11px] text-muted">
                        {channelEnabled
                          ? `${effectiveBackend === 'claude' ? 'Claude' : effectiveBackend === 'codex' ? 'Codex' : 'OpenCode'}${backendModel ? ` / ${backendModel}` : ''} · ID: ${channel.id}`
                          : `ID: ${channel.id}`}
                      </span>
                    </span>

                    {/* Status badge */}
                    <span
                      className={clsx(
                        'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium',
                        channelEnabled
                          ? 'border-mint/40 bg-mint-soft text-mint'
                          : 'border-border bg-white/[0.04] text-muted'
                      )}
                    >
                      <span
                        className={clsx(
                          'size-1.5 rounded-full',
                          channelEnabled ? 'bg-mint shadow-[0_0_6px_rgba(91,255,160,0.7)]' : 'bg-muted'
                        )}
                      />
                      {channelEnabled ? t('common.enabled') : t('common.disabled')}
                    </span>

                    {/* Chevron */}
                    {expanded ? (
                      <ChevronUp size={18} className="shrink-0 text-muted" />
                    ) : (
                      <ChevronDown size={18} className="shrink-0 text-muted" />
                    )}
                  </button>

                  {/* Expanded body — design.pen asPXu (VR/RoutingConfig) — shared with /users */}
                  {expanded && (
                    <RoutingConfigPanel
                      value={channelConfig}
                      onChange={(patch) => updateRow(patch)}
                      onBrowseDirectory={() => setBrowsingCwdFor(rowKey)}
                      globalConfig={config}
                      showRequireMention={true}
                      inheritsFromKey={channelPlatform}
                      opencodeOptions={opencodeOptions}
                      claudeAgents={claudeAgents}
                      claudeModels={claudeModels}
                      claudeReasoningOptions={claudeReasoningOptions}
                      codexAgents={codexAgents}
                      codexModels={codexModels}
                    />
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Directory browser modal — page mode keys by `${platform}::${channelId}` */}
        {browsingCwdFor && (() => {
          const sep = browsingCwdFor.indexOf('::');
          if (sep < 0) return null;
          const browsePlatform = browsingCwdFor.slice(0, sep);
          const browseChannelId = browsingCwdFor.slice(sep + 2);
          const platformConfigs = browsePlatform === platform ? configs : (allConfigsByPlatform[browsePlatform] || {});
          return (
            <DirectoryBrowser
              initialPath={platformConfigs[browseChannelId]?.custom_cwd || config.runtime?.default_cwd || '~/work'}
              onSelect={(path) => {
                void updateConfigForPlatform(browsePlatform, browseChannelId, { custom_cwd: path });
                setBrowsingCwdFor(null);
              }}
              onClose={() => setBrowsingCwdFor(null)}
            />
          );
        })()}
      </>
    );
  }

  const Wrapper: React.FC<{ children: React.ReactNode }> = ({ children }) =>
    isPage ? (
      <div className="flex h-full flex-col mx-auto max-w-6xl">{children}</div>
    ) : (
      <div className="flex w-full justify-center">
        <WizardCard className="gap-6">{children}</WizardCard>
      </div>
    );

  return (
    <>
    <Wrapper>
      <div className={clsx('space-y-4', !isPage && 'space-y-2')}>
        <div>
          {!isPage && <EyebrowBadge tone="mint">Groups</EyebrowBadge>}
          <h2
            className={clsx(
              'font-bold tracking-[-0.4px] text-foreground',
              isPage ? 'text-3xl font-semibold tracking-tight' : 'mt-2 text-[28px] leading-tight'
            )}
          >
            {t('channelList.title')}
          </h2>
          <p className={clsx('text-muted', isPage ? '' : 'mt-2 max-w-[560px] text-[14px] leading-[1.55]')}>
            {t('channelList.subtitle')}
          </p>
        </div>

        {isPage && (
          <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-border bg-surface-2/40 px-4 py-3 text-sm text-muted shadow-[0_18px_40px_-30px_rgba(0,0,0,0.8)]">
            <span className="rounded-full border border-mint/40 bg-mint-soft px-2.5 py-1 text-xs font-medium text-mint">
              {t('channelList.enabledCount', { count: selectedCount })}
            </span>
            <span>{t('dashboard.metricGroupsHint', { count: channels.length })}</span>
            <span className="hidden h-1 w-1 rounded-full bg-border md:inline-block" />
            <span className="font-mono text-xs uppercase tracking-[0.18em]">{t(`platform.${platform}.title`)}</span>
          </div>
        )}
      </div>

      {((isPage && channelPlatforms.length > 1) || isWizardMultiPlatform) && (
        <div className="flex flex-wrap gap-2">
          {(isWizardMultiPlatform ? wizardPlatforms! : channelPlatforms).map((candidate) => (
            <button
              key={candidate}
              type="button"
              onClick={() => isWizardMultiPlatform ? switchWizardPlatform(candidate) : setPagePlatform(candidate)}
              className={clsx(
                'rounded-full border px-3 py-1.5 text-[12px] font-medium transition-colors',
                platform === candidate
                  ? 'border-mint/50 bg-mint/[0.16] text-mint shadow-[0_0_20px_-4px_rgba(91,255,160,0.4)]'
                  : 'border-border bg-white/[0.04] text-foreground hover:border-border-strong'
              )}
            >
              {t(`platform.${candidate}.title`)}
            </button>
          ))}
        </div>
      )}

      {/* Platform-level require @mention toggle (page mode only) */}
      {isPage && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-x-3 gap-y-2 rounded-xl border border-border bg-surface-2/40 p-3 shadow-[0_18px_40px_-30px_rgba(0,0,0,0.8)]">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <AtSign size={14} className="text-accent" />
            <span className="font-medium text-foreground">{t('dashboard.requireMention')}</span>
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

      <div className="mb-4 space-y-3 rounded-2xl border border-border bg-surface-2/40 p-4 shadow-[0_18px_40px_-30px_rgba(0,0,0,0.8)]">
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <button
              onClick={() => loadChannels(browseAll)}
              className="flex items-center gap-2 rounded-lg border border-border bg-surface-3/60 px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:border-cyan/40 hover:bg-surface-2/70"
            >
              <RefreshCw size={14} className={loading ? 'animate-spin' : ''} /> {t('channelList.refreshList')}
            </button>
            {!browseAll && !['lark', 'telegram'].includes(platform) && (
              <button
                onClick={() => loadChannels(true)}
                disabled={loadingAll}
                className="flex items-center gap-2 rounded-lg border border-border bg-surface-3/60 px-3 py-1.5 text-sm font-medium text-foreground transition-colors hover:border-cyan/40 hover:bg-surface-2/70 disabled:opacity-50"
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
          <div className="rounded-xl border border-border bg-surface-3/60 p-3 text-sm">
            <div className="grid gap-3 md:grid-cols-[minmax(220px,280px)_1fr]">
              <div className="space-y-1">
                <label className="font-medium text-foreground">{t('channelList.guildBrowse')}</label>
                <select
                  value={selectedGuild}
                  onChange={(e) => updateSelectedGuild(e.target.value)}
                   className="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-foreground focus:outline-none focus:border-cyan"
                >
                  <option value="">{t('channelList.guildPlaceholder')}</option>
                  {knownDiscordGuilds.map((g) => (
                    <option key={g.id} value={g.id}>{g.name}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-foreground">{t('channelList.guildAccess')}</span>
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
                           'inline-flex items-center gap-2 rounded-lg border px-2 py-1 text-xs transition-colors',
                           selectedGuildIds.includes(g.id)
                             ? 'border-mint/40 bg-mint-soft text-foreground'
                             : 'border-border bg-surface text-muted hover:text-foreground'
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
            <span className="text-muted">
              {platform === 'discord' ? t('channelList.accessPolicyDiscord') : t('channelList.accessPolicy')}
            </span>
            <span className="text-xs text-muted">
              {platform === 'discord' ? t('channelList.accessPolicyHintDiscord') : t('channelList.accessPolicyHint')}
            </span>
          </div>
        )}
        {platform === 'telegram' && (
          <div className="rounded-xl border border-cyan/30 bg-cyan-soft/20 p-3">
            <div className="text-sm font-medium text-foreground">{t('channelList.telegramDiscoveryTitle')}</div>
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

      <div className="flex-1 overflow-y-auto rounded-2xl border border-border bg-surface-2/40 p-3 shadow-[0_18px_40px_-30px_rgba(0,0,0,0.8)] space-y-3">
        {!loading && channels.length === 0 && !botToken && platform !== 'lark' && (
          <div className="rounded-xl border border-dashed border-border bg-surface-3/60 p-8 text-center text-muted">
            {t('channelList.addTokenFirst')}
          </div>
        )}
        {!loading && channels.length === 0 && platform === 'telegram' && !!botToken && (
          <div className="rounded-xl border border-dashed border-border bg-surface-3/60 p-8 text-center">
            <div className="text-sm font-medium text-foreground">{t('channelList.telegramDiscoveryEmptyTitle')}</div>
            <div className="mt-2 text-sm text-muted">{t('channelList.telegramDiscoveryEmptyDesc')}</div>
          </div>
        )}
        {sortedChannels.map((channel) => {
          const rawConfig = configs[channel.id] || {};
          const def = defaultConfig();
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
          const effectiveCwd = channelConfig.custom_cwd || config.runtime?.default_cwd || '~/work';
          const opencodeOptions = opencodeOptionsByCwd[effectiveCwd];
          const claudeAgents = claudeAgentsByCwd[effectiveCwd] || [];
          const codexAgents = codexAgentsByCwd[effectiveCwd] || [];
          return (
            <div key={channel.id} className="rounded-xl border border-border bg-surface-3/60 p-4 transition-colors hover:border-border-strong hover:bg-surface-2/70">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <button
                    onClick={() => updateConfig(channel.id, { enabled: !channelConfig.enabled })}
                    className={channelConfig.enabled ? 'text-accent' : 'text-muted'}
                  >
                    {channelConfig.enabled ? <CheckSquare size={20} /> : <Square size={20} />}
                  </button>
                  <div>
                    <div className="flex items-center gap-1 font-medium text-foreground">
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
                      ? 'bg-surface text-foreground border-border'
                      : platform === 'telegram'
                        ? channel.supports_topics
                          ? 'bg-accent/10 text-accent border-accent/20'
                          : channel.type === 'supergroup'
                            ? 'bg-success/10 text-success border-success/20'
                            : channel.is_private
                              ? 'bg-warning/10 text-warning border-warning/20'
                              : 'bg-surface text-foreground border-border'
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
                <RoutingConfigPanel
                  value={channelConfig}
                  onChange={(patch) => updateConfig(channel.id, patch)}
                  onBrowseDirectory={() => setBrowsingCwdFor(channel.id)}
                  globalConfig={config}
                  showRequireMention={true}
                  inheritsFromKey={platform}
                  opencodeOptions={opencodeOptions}
                  claudeAgents={claudeAgents}
                  claudeModels={claudeModels}
                  claudeReasoningOptions={claudeReasoningOptions}
                  codexAgents={codexAgents}
                  codexModels={codexModels}
                  containerClass="mt-4 pl-8"
                />
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
        <div className="flex items-center justify-between border-t border-border pt-4">
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-4 py-2 text-[13px] font-semibold text-foreground transition hover:border-border-strong"
          >
            <ArrowLeft size={14} strokeWidth={2.25} />
            {t('common.back')}
          </button>
          <button
            type="button"
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
            className="inline-flex items-center gap-2 rounded-lg bg-mint px-5 py-2.5 text-[13px] font-bold text-[#080812] shadow-[0_0_32px_-6px_rgba(91,255,160,0.6)] transition hover:brightness-105"
          >
            {t('common.continue')}
            <ArrowRight size={14} strokeWidth={2.25} />
          </button>
        </div>
      )}
    </Wrapper>

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
