import React, { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Welcome } from './steps/Welcome';
import { ModeSelection } from './steps/ModeSelection';
import { AgentDetection } from './steps/AgentDetection';
import { SlackConfig } from './steps/SlackConfig';
import { ChannelList } from './steps/ChannelList';
import { Summary } from './steps/Summary';
import { useApi } from '../context/ApiContext';
import clsx from 'clsx';

const buildConfigPayload = (data: any) => ({
  mode: data.mode || 'self_host',
  version: 'v2',
  slack: {
    bot_token: data.slack?.bot_token || '',
    app_token: data.slack?.app_token || '',
  },
  runtime: {
    default_cwd: data.default_cwd || '.',
    log_level: 'INFO',
    require_mention: false,
    target_channels: Object.keys(data.channelConfigs || {}).filter(
      (id) => data.channelConfigs[id]?.enabled
    ),
  },
  agents: {
    default_backend: data.default_backend || 'opencode',
    opencode: {
      enabled: data.agents?.opencode?.enabled ?? true,
      cli_path: data.agents?.opencode?.cli_path || 'opencode',
      default_agent: data.opencode_default_agent || null,
      default_model: data.opencode_default_model || null,
      default_reasoning_effort: data.opencode_default_reasoning_effort || null,
    },
    claude: {
      enabled: data.agents?.claude?.enabled ?? true,
      cli_path: data.agents?.claude?.cli_path || 'claude',
      default_model: data.claude_default_model || null,
    },
    codex: {
      enabled: data.agents?.codex?.enabled ?? false,
      cli_path: data.agents?.codex?.cli_path || 'codex',
      default_model: data.codex_default_model || null,
    },
  },
  ui: {
    setup_port: 5123,
    open_browser: true,
  },
});

const steps = [
  { id: 'welcome', title: 'Welcome', component: Welcome },
  { id: 'mode', title: 'Mode', component: ModeSelection },
  { id: 'agents', title: 'Agents', component: AgentDetection },
  { id: 'slack', title: 'Slack', component: SlackConfig },
  { id: 'channels', title: 'Channels', component: ChannelList },
  { id: 'summary', title: 'Finish', component: Summary },
];

export const Wizard: React.FC = () => {
  const api = useApi();
  const [currentStep, setCurrentStep] = useState(0);
  const [data, setData] = useState<any>({});
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    const bootstrap = async () => {
      try {
        const config = await api.getConfig();
        const settings = await api.getSettings();
          setData({
            ...config,
            channelConfigs: settings.channels || {},
            default_backend: config.agents?.default_backend,
            agents: {
              opencode: config.agents?.opencode,
              claude: config.agents?.claude,
              codex: config.agents?.codex,
            },
          });

      } catch {
        // ignore
      } finally {
        setLoaded(true);
      }
    };
    bootstrap();
  }, []);

  const next = async (stepData: any) => {
    const nextData = { ...data, ...stepData };
    setData(nextData);
    await persistStep(nextData);
    if (currentStep < steps.length - 1) {
      setCurrentStep(currentStep + 1);
    }
  };

  const back = () => {
    if (currentStep > 0) {
      setCurrentStep(currentStep - 1);
    }
  };

  const persistStep = async (payload: any) => {
    if (!payload) return;
    if (payload.agents || payload.slack || payload.mode || payload.channelConfigs) {
      await api.saveConfig(buildConfigPayload(payload));
    }
    if (payload.channelConfigs) {
      await api.saveSettings({ channels: payload.channelConfigs });
    }
  };

  const CurrentComponent = steps[currentStep].component;

  if (!loaded) return <div className="min-h-screen flex items-center justify-center bg-bg text-muted">Loading...</div>;

  return (
    <div className="min-h-screen bg-bg flex flex-col items-center justify-center p-4 md:p-8">
      <div className="w-full max-w-4xl bg-panel rounded-2xl border border-border shadow-xl overflow-hidden flex flex-col min-h-[600px] max-h-[90vh]">
        {/* Header */}
        <div className="bg-panel border-b border-border p-6 flex justify-between items-center relative z-10">
          <div>
            <h1 className="text-xl font-bold text-text font-display">Vibe Remote Setup</h1>
          </div>
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
