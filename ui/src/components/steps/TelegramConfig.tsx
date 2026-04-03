import React, { useEffect, useMemo, useState } from 'react';
import {
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  ExternalLink,
  KeyRound,
  MessageSquare,
  RefreshCw,
  Settings2,
  Shield,
  SplitSquareVertical,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';
import { useToast } from '../../context/ToastContext';
import { copyTextToClipboard } from '../../lib/utils';

interface TelegramConfigProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

export const TelegramConfig: React.FC<TelegramConfigProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [botToken, setBotToken] = useState(data.telegram?.bot_token || '');
  const [requireMention, setRequireMention] = useState(data.telegram?.require_mention ?? true);
  const [forumAutoTopic, setForumAutoTopic] = useState(data.telegram?.forum_auto_topic ?? true);
  const [checking, setChecking] = useState(false);
  const [authResult, setAuthResult] = useState<any>(null);
  const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({ 1: true, 2: true, 3: false, 4: false, 5: false });
  const [copiedCommand, setCopiedCommand] = useState<string | null>(null);

  const hasSavedToken = Boolean(data.telegram?.bot_token && botToken === data.telegram?.bot_token);

  useEffect(() => {
    setAuthResult(null);
  }, [botToken]);

  useEffect(() => {
    if ((authResult?.ok || hasSavedToken) && !expandedSteps[3]) {
      setExpandedSteps((prev) => ({ ...prev, 2: false, 3: true, 4: true, 5: true }));
    }
  }, [authResult?.ok, hasSavedToken, expandedSteps]);

  const isValid = useMemo(() => {
    if (!botToken) return false;
    if (authResult?.ok) return true;
    return hasSavedToken;
  }, [authResult, botToken, hasSavedToken]);

  const runAuthTest = async () => {
    setChecking(true);
    try {
      const result = await api.telegramAuthTest(botToken);
      setAuthResult(result);
    } catch (err: any) {
      setAuthResult({ ok: false, error: err?.message || 'Request failed' });
    } finally {
      setChecking(false);
    }
  };

  const openBotFather = () => {
    window.open('https://t.me/BotFather', '_blank');
  };

  const copyCommand = async (command: string) => {
    const copied = await copyTextToClipboard(command);
    if (!copied) {
      showToast(t('common.copyFailed'), 'error');
      return;
    }
    setCopiedCommand(command);
    setTimeout(() => setCopiedCommand((current) => (current === command ? null : current)), 2000);
  };

  const toggleStep = (step: number) => {
    setExpandedSteps((prev) => ({ ...prev, [step]: !prev[step] }));
  };

  const StepHeader: React.FC<{ step: number; title: string; icon: React.ReactNode; completed?: boolean }> = ({
    step,
    title,
    icon,
    completed,
  }) => (
    <button
      onClick={() => toggleStep(step)}
      className="w-full px-4 py-3 flex items-center justify-between bg-neutral-50 hover:bg-neutral-100 transition-colors"
    >
      <div className="flex items-center gap-3">
        <span
          className={clsx(
            'w-7 h-7 rounded-full text-sm font-bold flex items-center justify-center transition-colors',
            completed ? 'bg-success text-white' : 'bg-accent text-white'
          )}
        >
          {completed ? <Check size={14} /> : step}
        </span>
        <span className="flex items-center gap-2 font-semibold text-text">
          {icon}
          {title}
        </span>
      </div>
      {expandedSteps[step] ? <ChevronUp size={18} className="text-muted" /> : <ChevronDown size={18} className="text-muted" />}
    </button>
  );

  return (
    <div className="flex flex-col h-full max-w-2xl mx-auto">
      <div className="mb-4">
        <h2 className="text-3xl font-display font-bold text-text">{t('telegramConfig.title')}</h2>
        <p className="text-muted mt-1">{t('telegramConfig.subtitle')}</p>
      </div>

      <div className="space-y-3 overflow-y-auto flex-1 pr-1">
        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader
            step={1}
            title={t('telegramConfig.step1Title')}
            icon={<MessageSquare size={16} className="text-accent" />}
          />
          {expandedSteps[1] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('telegramConfig.step1Description')}</p>
              <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                <li>{t('telegramConfig.step1Item1')}</li>
                <li>{t('telegramConfig.step1Item2')}</li>
                <li>{t('telegramConfig.step1Item3')}</li>
                <li>{t('telegramConfig.step1Item4')}</li>
              </ol>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={openBotFather}
                  className="flex items-center gap-2 px-4 py-2 bg-accent text-white rounded-lg hover:bg-accent/90 transition-colors font-medium shadow-sm"
                >
                  <ExternalLink size={16} />
                  {t('telegramConfig.openBotFather')}
                </button>
                <button
                  onClick={() => copyCommand('/newbot')}
                  className="flex items-center gap-2 px-4 py-2 border border-border rounded-lg text-text hover:bg-neutral-50 transition-colors font-medium"
                >
                  {copiedCommand === '/newbot' ? <Check size={16} className="text-success" /> : <Copy size={16} className="text-accent" />}
                  {copiedCommand === '/newbot' ? t('telegramConfig.copiedCommand') : t('telegramConfig.copyNewBotCommand')}
                </button>
              </div>
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800">
                <strong>{t('slackConfig.tip')}:</strong> {t('telegramConfig.step1Tip')}
              </div>
            </div>
          )}
        </div>

        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader
            step={2}
            title={t('telegramConfig.step2Title')}
            icon={<KeyRound size={16} className="text-accent" />}
            completed={isValid}
          />
          {expandedSteps[2] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('telegramConfig.step2Description')}</p>
              <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                <li>{t('telegramConfig.step2Item1')}</li>
                <li>{t('telegramConfig.step2Item2')}</li>
                <li>{t('telegramConfig.step2Item3')}</li>
              </ol>

              <div className="space-y-2 pt-2">
                <label className="text-sm font-medium text-text flex items-center gap-2">
                  <KeyRound size={16} className="text-accent" />
                  {t('telegramConfig.botToken')}
                </label>
                <input
                  type="password"
                  value={botToken}
                  onChange={(e) => setBotToken(e.target.value)}
                  placeholder={t('telegramConfig.botTokenPlaceholder')}
                  className="w-full bg-bg border border-border rounded-lg p-3 text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent font-mono transition-colors"
                />
                <p className="text-xs text-muted">{t('telegramConfig.botTokenHint')}</p>
              </div>

              <div className="flex items-center gap-3">
                <button
                  onClick={runAuthTest}
                  disabled={!botToken || checking}
                  className="px-4 py-2 bg-accent text-white rounded-lg flex items-center gap-2 transition-colors font-medium shadow-sm hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {checking ? <RefreshCw size={16} className="animate-spin" /> : <Shield size={16} />}
                  {t('telegramConfig.validateToken')}
                </button>
                {authResult && (
                  <span
                    className={clsx(
                      'flex items-center gap-2 text-sm font-medium px-3 py-1.5 rounded-lg border',
                      authResult.ok ? 'text-success bg-success/10 border-success/20' : 'text-danger bg-danger/10 border-danger/20'
                    )}
                  >
                    {authResult.ok ? (
                      <>
                        <Check size={14} />
                        <span>{t('telegramConfig.tokenValidated')}</span>
                      </>
                    ) : (
                      <span>{t('telegramConfig.authFailed')}: {authResult.error}</span>
                    )}
                  </span>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader
            step={3}
            title={t('telegramConfig.step3Title')}
            icon={<Shield size={16} className="text-accent" />}
          />
          {expandedSteps[3] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('telegramConfig.step3Description')}</p>
              <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                <li>{t('telegramConfig.step3Item1')}</li>
                <li>{t('telegramConfig.step3Item2')}</li>
                <li>{t('telegramConfig.step3Item3')}</li>
                <li>{t('telegramConfig.step3Item4')}</li>
              </ol>
              <div className="grid gap-3">
                {[
                  {
                    command: '/setprivacy',
                    title: t('telegramConfig.step3Command1Title'),
                    description: t('telegramConfig.step3Command1Description'),
                  },
                  {
                    command: '/setjoingroups',
                    title: t('telegramConfig.step3Command2Title'),
                    description: t('telegramConfig.step3Command2Description'),
                  },
                  {
                    command: '/setcommands',
                    title: t('telegramConfig.step3Command3Title'),
                    description: t('telegramConfig.step3Command3Description'),
                  },
                ].map((item) => (
                  <div key={item.command} className="rounded-lg border border-border bg-neutral-50 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div>
                        <div className="text-sm font-semibold text-text">{item.title}</div>
                        <p className="mt-1 text-xs text-muted">{item.description}</p>
                      </div>
                      <button
                        onClick={() => copyCommand(item.command)}
                        className="inline-flex items-center gap-2 rounded-lg border border-border bg-white px-3 py-1.5 text-sm font-medium text-text hover:bg-neutral-100 transition-colors"
                      >
                        {copiedCommand === item.command ? (
                          <Check size={14} className="text-success" />
                        ) : (
                          <Copy size={14} className="text-accent" />
                        )}
                        <code>{copiedCommand === item.command ? t('telegramConfig.copiedCommand') : item.command}</code>
                      </button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-900">
                <strong>{t('telegramConfig.step3TipTitle')}:</strong> {t('telegramConfig.step3Tip')}
              </div>
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800">
                <strong>{t('slackConfig.tip')}:</strong> {t('telegramConfig.step3ExtraTip')}
              </div>
            </div>
          )}
        </div>

        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader
            step={4}
            title={t('telegramConfig.step4Title')}
            icon={<ExternalLink size={16} className="text-accent" />}
          />
          {expandedSteps[4] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('telegramConfig.step4Description')}</p>
              <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                <li>{t('telegramConfig.step4Item1')}</li>
                <li>{t('telegramConfig.step4Item2')}</li>
                <li>{t('telegramConfig.step4Item3')}</li>
                <li>{t('telegramConfig.step4Item4')}</li>
              </ol>
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800">
                <strong>{t('slackConfig.tip')}:</strong> {t('telegramConfig.step4Tip')}
              </div>
            </div>
          )}
        </div>

        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader
            step={5}
            title={t('telegramConfig.step5Title')}
            icon={<Settings2 size={16} className="text-accent" />}
          />
          {expandedSteps[5] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('telegramConfig.step5Description')}</p>

              <label className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-sm font-medium text-text">{t('telegramConfig.requireMention')}</div>
                  <p className="text-xs text-muted mt-1">{t('telegramConfig.requireMentionHint')}</p>
                </div>
                <input
                  type="checkbox"
                  checked={requireMention}
                  onChange={(e) => setRequireMention(e.target.checked)}
                  className="mt-1 h-4 w-4"
                />
              </label>

              <label className="flex items-start justify-between gap-4">
                <div className="pr-4">
                  <div className="text-sm font-medium text-text flex items-center gap-2">
                    <SplitSquareVertical size={16} className="text-accent" />
                    {t('telegramConfig.forumAutoTopic')}
                  </div>
                  <p className="text-xs text-muted mt-1">{t('telegramConfig.forumAutoTopicHint')}</p>
                </div>
                <input
                  type="checkbox"
                  checked={forumAutoTopic}
                  onChange={(e) => setForumAutoTopic(e.target.checked)}
                  className="mt-1 h-4 w-4"
                />
              </label>
            </div>
          )}
        </div>
      </div>

      <div className="mt-auto flex justify-between pt-6 border-t border-border">
        <button onClick={onBack} className="px-6 py-2 text-muted hover:text-text font-medium transition-colors">
          {t('common.back')}
        </button>
        <button
          onClick={() =>
            onNext({
              platform: 'telegram',
              telegram: {
                ...(data.telegram || {}),
                bot_token: botToken,
                require_mention: requireMention,
                forum_auto_topic: forumAutoTopic,
              },
            })
          }
          disabled={!isValid}
          className={clsx(
            'px-8 py-3 rounded-lg font-medium transition-colors shadow-sm',
            isValid ? 'bg-accent hover:bg-accent/90 text-white' : 'bg-neutral-200 text-muted cursor-not-allowed'
          )}
        >
          {t('common.continue')}
        </button>
      </div>
    </div>
  );
};
