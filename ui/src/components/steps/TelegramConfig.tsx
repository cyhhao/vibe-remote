import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
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
import { EyebrowBadge, WizardCard } from '../visual';

interface TelegramConfigProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

// Mirrors design.pen XCWAT (Slack creds wizard step) adapted for Telegram.
// 920-wide WizardCard, mint eyebrow, 5-step accordion with mint accent on active row.
export const TelegramConfig: React.FC<TelegramConfigProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [botToken, setBotToken] = useState(data.telegram?.bot_token || '');
  const [requireMention, setRequireMention] = useState(data.telegram?.require_mention ?? true);
  const [forumAutoTopic, setForumAutoTopic] = useState(data.telegram?.forum_auto_topic ?? true);
  const [checking, setChecking] = useState(false);
  const [authResult, setAuthResult] = useState<any>(null);
  const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({
    1: true,
    2: true,
    3: false,
    4: false,
    5: false,
  });
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
      className="flex w-full items-center justify-between gap-3 px-5 py-4 text-left transition-colors hover:bg-white/[0.02]"
    >
      <div className="flex items-center gap-3">
        <span
          className={clsx(
            'flex size-7 items-center justify-center rounded-full text-[12px] font-bold transition-colors',
            completed ? 'bg-mint text-[#080812]' : 'bg-cyan/15 text-cyan'
          )}
        >
          {completed ? <Check size={14} /> : step}
        </span>
        <span className="flex items-center gap-2 text-[14px] font-semibold text-foreground">
          {icon}
          {title}
        </span>
      </div>
      {expandedSteps[step] ? <ChevronUp size={18} className="text-muted" /> : <ChevronDown size={18} className="text-muted" />}
    </button>
  );

  const StepShell: React.FC<{ active: boolean; children: React.ReactNode }> = ({ active, children }) => (
    <div
      className={clsx(
        'overflow-hidden rounded-xl border transition-colors',
        active
          ? 'border-mint/35 bg-surface-2 shadow-[0_8px_32px_-8px_rgba(91,255,160,0.078)]'
          : 'border-border bg-background'
      )}
    >
      {children}
    </div>
  );

  const completedCount = [
    Boolean(botToken),
    isValid,
    Boolean(copiedCommand),
    expandedSteps[4],
    Boolean(requireMention || forumAutoTopic),
  ].filter(Boolean).length;

  const botCommands: Array<{ command: string; title: string; description: string }> = [
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
  ];

  return (
    <div className="flex w-full justify-center">
      <WizardCard className="gap-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-2">
            <EyebrowBadge tone="mint">Telegram</EyebrowBadge>
            <h2 className="text-[28px] font-bold leading-tight tracking-[-0.4px] text-foreground">
              {t('telegramConfig.title')}
            </h2>
            <p className="max-w-[560px] text-[14px] leading-[1.55] text-muted">
              {t('telegramConfig.subtitle')}
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-border bg-white/[0.04] px-3 py-1.5">
            <span className="font-mono text-[11px] font-bold uppercase tracking-[0.16em] text-mint">
              {completedCount} / 5
            </span>
            <div className="flex gap-1">
              {[0, 1, 2, 3, 4].map((i) => (
                <span
                  key={i}
                  className={clsx(
                    'h-1 w-4 rounded-full',
                    i < completedCount ? 'bg-mint shadow-[0_0_8px_rgba(91,255,160,0.6)]' : 'bg-white/[0.08]'
                  )}
                />
              ))}
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          {/* Step 1: Open BotFather */}
          <StepShell active={expandedSteps[1]}>
            <StepHeader
              step={1}
              title={t('telegramConfig.step1Title')}
              icon={<MessageSquare size={16} className="text-cyan" />}
            />
            {expandedSteps[1] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('telegramConfig.step1Description')}</p>
                <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                  <li>{t('telegramConfig.step1Item1')}</li>
                  <li>{t('telegramConfig.step1Item2')}</li>
                  <li>{t('telegramConfig.step1Item3')}</li>
                  <li>{t('telegramConfig.step1Item4')}</li>
                </ol>
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={openBotFather}
                    className="inline-flex items-center gap-2 rounded-lg bg-mint px-4 py-2 text-[13px] font-bold text-[#080812] shadow-[0_0_24px_-4px_rgba(91,255,160,0.6)] transition hover:brightness-105"
                  >
                    <ExternalLink size={14} strokeWidth={2.25} />
                    {t('telegramConfig.openBotFather')}
                  </button>
                  <button
                    onClick={() => copyCommand('/newbot')}
                    className="inline-flex items-center gap-2 rounded-lg border border-border bg-white/[0.04] px-3 py-2 text-[12px] font-medium text-foreground transition hover:border-border-strong"
                  >
                    {copiedCommand === '/newbot' ? <Check size={14} className="text-mint" /> : <Copy size={14} />}
                    {copiedCommand === '/newbot' ? t('telegramConfig.copiedCommand') : t('telegramConfig.copyNewBotCommand')}
                  </button>
                </div>
                <div className="rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2 text-[12px] leading-[1.55] text-cyan">
                  <strong>{t('slackConfig.tip')}:</strong> {t('telegramConfig.step1Tip')}
                </div>
              </div>
            )}
          </StepShell>

          {/* Step 2: Paste token & validate */}
          <StepShell active={expandedSteps[2]}>
            <StepHeader
              step={2}
              title={t('telegramConfig.step2Title')}
              icon={<KeyRound size={16} className="text-cyan" />}
              completed={isValid}
            />
            {expandedSteps[2] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('telegramConfig.step2Description')}</p>
                <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                  <li>{t('telegramConfig.step2Item1')}</li>
                  <li>{t('telegramConfig.step2Item2')}</li>
                  <li>{t('telegramConfig.step2Item3')}</li>
                </ol>

                <div className="space-y-2 pt-1">
                  <label className="flex items-center gap-2 text-[12px] font-medium text-foreground">
                    <KeyRound size={14} className="text-cyan" />
                    {t('telegramConfig.botToken')}
                  </label>
                  <input
                    type="password"
                    value={botToken}
                    onChange={(e) => setBotToken(e.target.value)}
                    placeholder={t('telegramConfig.botTokenPlaceholder')}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2.5 font-mono text-[12px] text-foreground outline-none transition placeholder:text-muted/55 focus:border-cyan focus:ring-1 focus:ring-cyan/40"
                  />
                  <p className="text-[11px] text-muted">{t('telegramConfig.botTokenHint')}</p>
                </div>

                <div className="flex flex-wrap items-center gap-3">
                  <button
                    onClick={runAuthTest}
                    disabled={!botToken || checking}
                    className="inline-flex items-center gap-2 rounded-lg bg-mint px-4 py-2 text-[13px] font-bold text-[#080812] shadow-[0_0_24px_-4px_rgba(91,255,160,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {checking ? <RefreshCw size={14} className="animate-spin" /> : <Shield size={14} strokeWidth={2.25} />}
                    {t('telegramConfig.validateToken')}
                  </button>
                  {authResult && (
                    <span
                      className={clsx(
                        'inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[12px] font-medium',
                        authResult.ok
                          ? 'border-mint/30 bg-mint/[0.08] text-mint'
                          : 'border-danger/30 bg-danger/10 text-danger'
                      )}
                    >
                      {authResult.ok ? <Check size={14} /> : null}
                      {authResult.ok
                        ? t('telegramConfig.tokenValidated')
                        : `${t('telegramConfig.authFailed')}: ${authResult.error}`}
                    </span>
                  )}
                </div>
              </div>
            )}
          </StepShell>

          {/* Step 3: Bot commands */}
          <StepShell active={expandedSteps[3]}>
            <StepHeader
              step={3}
              title={t('telegramConfig.step3Title')}
              icon={<Shield size={16} className="text-cyan" />}
            />
            {expandedSteps[3] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('telegramConfig.step3Description')}</p>
                <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                  <li>{t('telegramConfig.step3Item1')}</li>
                  <li>{t('telegramConfig.step3Item2')}</li>
                  <li>{t('telegramConfig.step3Item3')}</li>
                  <li>{t('telegramConfig.step3Item4')}</li>
                </ol>
                <div className="grid gap-2">
                  {botCommands.map((item) => (
                    <div
                      key={item.command}
                      className="rounded-lg border border-border bg-background px-3 py-2.5"
                    >
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                          <div className="text-[12px] font-semibold text-foreground">{item.title}</div>
                          <p className="mt-0.5 text-[11px] text-muted">{item.description}</p>
                        </div>
                        <button
                          onClick={() => copyCommand(item.command)}
                          className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-2.5 py-1.5 text-[11px] font-medium text-foreground transition hover:border-border-strong"
                        >
                          {copiedCommand === item.command ? <Check size={12} className="text-mint" /> : <Copy size={12} />}
                          <code className="font-mono">
                            {copiedCommand === item.command ? t('telegramConfig.copiedCommand') : item.command}
                          </code>
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="rounded-lg border border-gold/30 bg-gold/10 px-3 py-2 text-[12px] leading-[1.55] text-gold">
                  <strong>{t('telegramConfig.step3TipTitle')}:</strong> {t('telegramConfig.step3Tip')}
                </div>
                <div className="rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2 text-[12px] leading-[1.55] text-cyan">
                  <strong>{t('slackConfig.tip')}:</strong> {t('telegramConfig.step3ExtraTip')}
                </div>
              </div>
            )}
          </StepShell>

          {/* Step 4: Add bot to group */}
          <StepShell active={expandedSteps[4]}>
            <StepHeader
              step={4}
              title={t('telegramConfig.step4Title')}
              icon={<ExternalLink size={16} className="text-cyan" />}
            />
            {expandedSteps[4] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('telegramConfig.step4Description')}</p>
                <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                  <li>{t('telegramConfig.step4Item1')}</li>
                  <li>{t('telegramConfig.step4Item2')}</li>
                  <li>{t('telegramConfig.step4Item3')}</li>
                  <li>{t('telegramConfig.step4Item4')}</li>
                </ol>
                <div className="rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2 text-[12px] leading-[1.55] text-cyan">
                  <strong>{t('slackConfig.tip')}:</strong> {t('telegramConfig.step4Tip')}
                </div>
              </div>
            )}
          </StepShell>

          {/* Step 5: Behavior toggles */}
          <StepShell active={expandedSteps[5]}>
            <StepHeader
              step={5}
              title={t('telegramConfig.step5Title')}
              icon={<Settings2 size={16} className="text-cyan" />}
            />
            {expandedSteps[5] && (
              <div className="space-y-3 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('telegramConfig.step5Description')}</p>

                <label className="flex items-start justify-between gap-4 rounded-lg border border-border bg-background px-3 py-2.5">
                  <div>
                    <div className="text-[12px] font-semibold text-foreground">
                      {t('telegramConfig.requireMention')}
                    </div>
                    <p className="mt-0.5 text-[11px] text-muted">{t('telegramConfig.requireMentionHint')}</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={requireMention}
                    onChange={(e) => setRequireMention(e.target.checked)}
                    className="mt-1 size-4 accent-mint"
                  />
                </label>

                <label className="flex items-start justify-between gap-4 rounded-lg border border-border bg-background px-3 py-2.5">
                  <div className="pr-4">
                    <div className="flex items-center gap-2 text-[12px] font-semibold text-foreground">
                      <SplitSquareVertical size={14} className="text-cyan" />
                      {t('telegramConfig.forumAutoTopic')}
                    </div>
                    <p className="mt-0.5 text-[11px] text-muted">{t('telegramConfig.forumAutoTopicHint')}</p>
                  </div>
                  <input
                    type="checkbox"
                    checked={forumAutoTopic}
                    onChange={(e) => setForumAutoTopic(e.target.checked)}
                    className="mt-1 size-4 accent-mint"
                  />
                </label>
              </div>
            )}
          </StepShell>
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
            className="inline-flex items-center gap-2 rounded-lg bg-mint px-5 py-2.5 text-[13px] font-bold text-[#080812] shadow-[0_0_32px_-6px_rgba(91,255,160,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-40 disabled:shadow-none"
          >
            {t('common.continue')}
            <ArrowRight size={14} strokeWidth={2.25} />
          </button>
        </div>
      </WizardCard>
    </div>
  );
};
