import React, { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useTranslation } from 'react-i18next';
import { Welcome } from './steps/Welcome';
// import { ModeSelection } from './steps/ModeSelection'; // temporarily hidden — SaaS mode not yet available
import { PlatformSelection } from './steps/PlatformSelection';
import { AgentDetection } from './steps/AgentDetection';
import { SlackConfig } from './steps/SlackConfig';
import { DiscordConfig } from './steps/DiscordConfig';
import { TelegramConfig } from './steps/TelegramConfig';
import { LarkConfig } from './steps/LarkConfig';
import { WeChatConfig } from './steps/WeChatConfig';
import { ChannelList } from './steps/ChannelList';
import { Summary } from './steps/Summary';
import { useApi } from '../context/ApiContext';
import { LanguageSwitcher } from './LanguageSwitcher';
import clsx from 'clsx';
import { getEnabledPlatforms, getPrimaryPlatform, platformSupportsChannels } from '../lib/platforms';

const buildConfigPayload = (data: any) => {
  const enabledPlatforms = getEnabledPlatforms(data);
  const primaryPlatform = getPrimaryPlatform(data);

  return {
  platform: primaryPlatform,
  platforms: {
    enabled: enabledPlatforms,
    primary: primaryPlatform,
  },
  mode: data.mode || 'self_host',
  version: 'v2',
  slack: {
    // Preserve all existing slack fields
    ...data.slack,
    // Override only the fields that setup modifies
    bot_token: data.slack?.bot_token || '',
    app_token: data.slack?.app_token || '',
    require_mention: data.slack?.require_mention || false,
  },
  discord: {
    ...data.discord,
    bot_token: data.discord?.bot_token || '',
    guild_allowlist: data.discord?.guild_allowlist || [],
    guild_denylist: data.discord?.guild_denylist || [],
    require_mention: data.discord?.require_mention || false,
  },
  telegram: {
    ...data.telegram,
    bot_token: data.telegram?.bot_token || '',
    require_mention: data.telegram?.require_mention ?? true,
    forum_auto_topic: data.telegram?.forum_auto_topic ?? true,
    use_webhook: data.telegram?.use_webhook ?? false,
  },
  lark: {
    ...data.lark,
    app_id: data.lark?.app_id || '',
    app_secret: data.lark?.app_secret || '',
    domain: data.lark?.domain || 'feishu',
    require_mention: data.lark?.require_mention || false,
  },
  wechat: {
    ...data.wechat,
    bot_token: data.wechat?.bot_token || '',
    base_url: data.wechat?.base_url || 'https://ilinkai.weixin.qq.com',
    cdn_base_url: data.wechat?.cdn_base_url || 'https://novac2c.cdn.weixin.qq.com/c2c',
    require_mention: data.wechat?.require_mention || false,
  },
  runtime: {
    // Preserve existing runtime config
    ...data.runtime,
    default_cwd: data.default_cwd || data.runtime?.default_cwd || '.',
  },
  agents: {
    default_backend: data.default_backend || 'opencode',
    opencode: {
      // Preserve existing opencode config
      ...data.agents?.opencode,
      enabled: data.agents?.opencode?.enabled ?? true,
      cli_path: data.agents?.opencode?.cli_path || 'opencode',
      default_agent: data.opencode_default_agent ?? data.agents?.opencode?.default_agent ?? null,
      default_model: data.opencode_default_model ?? data.agents?.opencode?.default_model ?? null,
      default_reasoning_effort: data.opencode_default_reasoning_effort ?? data.agents?.opencode?.default_reasoning_effort ?? null,
    },
    claude: {
      // Preserve existing claude config
      ...data.agents?.claude,
      enabled: data.agents?.claude?.enabled ?? true,
      cli_path: data.agents?.claude?.cli_path || 'claude',
      default_model: data.claude_default_model ?? data.agents?.claude?.default_model ?? null,
    },
    codex: {
      // Preserve existing codex config
      ...data.agents?.codex,
      enabled: data.agents?.codex?.enabled ?? false,
      cli_path: data.agents?.codex?.cli_path || 'codex',
      default_model: data.codex_default_model ?? data.agents?.codex?.default_model ?? null,
    },
  },
  // Preserve gateway config entirely
  gateway: data.gateway,
  ui: {
    // Preserve existing ui config
    ...data.ui,
    setup_host: data.ui?.setup_host || '127.0.0.1',
    setup_port: data.ui?.setup_port || 5123,
  },
  // Preserve existing update config entirely
  update: data.update,
  // Preserve ack_mode
  ack_mode: data.ack_mode,
  show_duration: data.show_duration ?? false,
  // Preserve language
  language: data.language,
  };
};

export const Wizard: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const [currentStep, setCurrentStep] = useState(0);
  const [data, setData] = useState<any>({ show_duration: false });
  const [loaded, setLoaded] = useState(false);

  const steps = React.useMemo(() => {
    const enabledPlatforms = getEnabledPlatforms(data);
    const platformSteps = enabledPlatforms.map((platform) => {
      const component = platform === 'discord'
        ? DiscordConfig
        : platform === 'telegram'
          ? TelegramConfig
        : platform === 'lark'
          ? LarkConfig
          : platform === 'wechat'
            ? WeChatConfig
            : SlackConfig;
      return {
        id: `platform-${platform}`,
        title: platform,
        component,
      };
    });

    // Channel steps: merge into a single step with platform tabs (instead of one step per platform)
    const channelPlatforms = enabledPlatforms.filter((platform) => platformSupportsChannels(data, platform));
    const channelStep = channelPlatforms.length > 0
      ? [{ id: 'channels', title: t('nav.channels'), component: (props: any) => <ChannelList {...props} wizardPlatforms={channelPlatforms} /> }]
      : [];

    return [
      { id: 'welcome', title: 'Welcome', component: Welcome },
      // { id: 'mode', title: 'Mode', component: ModeSelection }, // temporarily hidden — SaaS mode not yet available
      { id: 'agents', title: 'Agents', component: AgentDetection },
      { id: 'platform', title: 'Platform', component: PlatformSelection },
      ...platformSteps,
      ...channelStep,
      { id: 'summary', title: 'Finish', component: Summary },
    ];
  }, [data, t]);

  useEffect(() => {
    const bootstrap = async () => {
      let platformCatalog: any[] = [];
      try {
        const catalog = await api.getPlatformCatalog();
        platformCatalog = catalog?.platforms || [];
      } catch {
        // Config payloads from newer backends also include the catalog.
      }

      try {
        const config = await api.getConfig();
        const configWithCatalog = {
          ...config,
          platform_catalog: config.platform_catalog || platformCatalog,
        };
        const enabledPlatforms = getEnabledPlatforms(configWithCatalog);
        const settingsEntries = await Promise.all(
          enabledPlatforms.map(async (platform) => [platform, await api.getSettings(platform)] as const)
        );
        const channelConfigsByPlatform = Object.fromEntries(
          settingsEntries.map(([platform, settings]) => [platform, settings.channels || {}])
        );
          setData({
            ...configWithCatalog,
            channelConfigsByPlatform,
            default_backend: config.agents?.default_backend,
            agents: {
              opencode: config.agents?.opencode,
              claude: config.agents?.claude,
              codex: config.agents?.codex,
            },
          });

      } catch {
        setData((current: any) => ({
          ...current,
          platform_catalog: platformCatalog,
        }));
      } finally {
        setLoaded(true);
      }
    };
    bootstrap();
  }, []);

  const next = async (stepData: any) => {
    const previousPlatforms = getEnabledPlatforms(data);
    const nextPlatforms = getEnabledPlatforms({ ...data, ...stepData });
    const platformsChanged = previousPlatforms.join(',') !== nextPlatforms.join(',');

    const nextData = {
      ...data,
      ...(platformsChanged ? { channelConfigsByPlatform: {} } : {}),
      ...(nextPlatforms.includes('wechat') ? { show_duration: false } : {}),
      ...stepData,
    };
    setData(nextData);
    await persistStep(stepData, nextData);
    if (currentStep < steps.length - 1) {
      setCurrentStep(currentStep + 1);
    }
  };

  const back = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1);
    }
  };

  const persistStep = async (stepData: any, mergedData: any) => {
    if (!mergedData) return;
    if (
      mergedData.agents ||
      mergedData.slack ||
      mergedData.discord ||
      mergedData.telegram ||
      mergedData.lark ||
      mergedData.wechat ||
      mergedData.mode ||
      mergedData.platforms ||
      mergedData.platform ||
      mergedData.channelConfigsByPlatform
    ) {
      await api.saveConfig(buildConfigPayload(mergedData));
    }
    if (stepData?.channelConfigsByPlatform) {
      const platforms = Object.keys(stepData.channelConfigsByPlatform);
      for (const p of platforms) {
        const channelConfigs = stepData.channelConfigsByPlatform[p];
        if (channelConfigs && Object.keys(channelConfigs).length > 0) {
          await api.saveSettings({ channels: channelConfigs }, p);
        }
      }
    }
  };

  const CurrentComponent = steps[currentStep].component;

  if (!loaded) return <div className="min-h-screen flex items-center justify-center bg-bg text-muted">{t('common.loading')}</div>;

  return (
    <div className="min-h-screen bg-bg flex flex-col items-center justify-center p-4 md:p-8">
      <div className="w-full max-w-4xl bg-panel rounded-2xl border border-border shadow-xl overflow-hidden flex flex-col min-h-[600px] max-h-[90vh]">
        {/* Header */}
        <div className="bg-panel border-b border-border p-6 flex justify-between items-center relative z-10">
          <div>
            <h1 className="text-xl font-bold text-text font-display">{t('wizard.title')}</h1>
          </div>
          <div className="flex items-center gap-4">
            <LanguageSwitcher />
            <div className="flex gap-2">
              {steps.map((s, i) => {
                  if (s.id === 'welcome') return null; // Skip welcome dot
                  const isCompleted = i < currentStep;
                  const isCurrent = i === currentStep;
                  return (
                      <div key={s.id} className="flex flex-col items-center gap-1">
                          <div
                            className={clsx(
                              "w-8 h-1 rounded-full transition-all duration-300",
                              isCompleted ? 'bg-success' : isCurrent ? 'bg-accent' : 'bg-neutral-200'
                            )}
                          />
                      </div>
                  );
              })}
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 p-8 relative overflow-y-auto">
          <AnimatePresence mode="wait">
            <motion.div
              key={currentStep}
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              transition={{ duration: 0.2 }}
              className="h-full"
            >
              <CurrentComponent
                data={data}
                onNext={next}
                onBack={back}
                isFirst={currentStep === 0}
                isLast={currentStep === steps.length - 1}
              />
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
};
