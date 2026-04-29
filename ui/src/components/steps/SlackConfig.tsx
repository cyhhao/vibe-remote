import React, { useEffect, useMemo, useState } from 'react';
import { Lock, Shield, RefreshCw, Copy, ExternalLink, Check, ChevronDown, ChevronUp, Key, Hash, Plus, ArrowLeft, ArrowRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';
import { useToast } from '../../context/ToastContext';
import { copyTextToClipboard } from '../../lib/utils';
import { EyebrowBadge, WizardCard } from '../visual';

interface SlackConfigProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

// Mirrors design.pen XCWAT (Slack creds wizard step): WizardCard 920 wide,
// sStep accordion rows with mint-bordered active row + faint border collapsed
// rows, navigation row with chevron-left back and mint pill next.
export const SlackConfig: React.FC<SlackConfigProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [botToken, setBotToken] = useState(data.slack?.bot_token || data.slackBotToken || '');
  const [appToken, setAppToken] = useState(data.slack?.app_token || data.slackAppToken || '');
  const [checking, setChecking] = useState(false);
  const [authResult, setAuthResult] = useState<any>(null);
  const [manifest, setManifest] = useState<string>('');
  const [manifestCompact, setManifestCompact] = useState<string>('');
  const [manifestLoading, setManifestLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  // Collapsible states for each step
  const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({ 1: true, 2: false, 3: false, 4: false });

  const isValid = useMemo(() => botToken.startsWith('xoxb-') && appToken.startsWith('xapp-'), [botToken, appToken]);

  useEffect(() => {
    void loadManifest();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-expand next step when token is entered
  useEffect(() => {
    if (botToken.startsWith('xoxb-') && !expandedSteps[3]) {
      setExpandedSteps((prev) => ({ ...prev, 2: false, 3: true }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [botToken]);

  useEffect(() => {
    if (appToken.startsWith('xapp-') && !expandedSteps[4]) {
      setExpandedSteps((prev) => ({ ...prev, 3: false, 4: true }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appToken]);

  const loadManifest = async () => {
    setManifestLoading(true);
    try {
      const result = await api.slackManifest();
      if (result.ok) {
        if (result.manifest) setManifest(result.manifest);
        if (result.manifest_compact) setManifestCompact(result.manifest_compact);
      }
    } catch (err) {
      console.error('Failed to load manifest:', err);
    } finally {
      setManifestLoading(false);
    }
  };

  const copyManifest = async () => {
    if (!manifest) return;
    const ok = await copyTextToClipboard(manifest);
    if (ok) {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      return;
    }
    showToast(t('common.copyFailed'), 'error');
  };

  const openSlackCreateApp = () => {
    if (manifestCompact) {
      const url = `https://api.slack.com/apps?new_app=1&manifest_json=${encodeURIComponent(manifestCompact)}`;
      window.open(url, '_blank');
    } else {
      window.open('https://api.slack.com/apps?new_app=1', '_blank');
    }
  };

  const runAuthTest = async () => {
    setChecking(true);
    try {
      const result = await api.slackAuthTest(botToken);
      setAuthResult(result);
    } catch (err: any) {
      setAuthResult({ ok: false, error: err?.message || 'Request failed' });
    } finally {
      setChecking(false);
    }
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
    !!manifest, // step 1 done as soon as manifest is available (best heuristic without backend signal)
    botToken.startsWith('xoxb-'),
    appToken.startsWith('xapp-'),
    Boolean(authResult?.ok),
  ].filter(Boolean).length;

  return (
    <div className="flex w-full justify-center">
      <WizardCard className="gap-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-2">
            <EyebrowBadge tone="mint">04 — Slack</EyebrowBadge>
            <h2 className="text-[28px] font-bold leading-tight tracking-[-0.4px] text-foreground">
              {t('slackConfig.title')}
            </h2>
            <p className="max-w-[560px] text-[14px] leading-[1.55] text-muted">
              {t('slackConfig.selfHostDescription')}
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-border bg-white/[0.04] px-3 py-1.5">
            <span className="font-mono text-[11px] font-bold uppercase tracking-[0.16em] text-mint">
              {completedCount} / 4
            </span>
            <div className="flex gap-1">
              {[0, 1, 2, 3].map((i) => (
                <span
                  key={i}
                  className={clsx(
                    'h-1 w-5 rounded-full',
                    i < completedCount ? 'bg-mint shadow-[0_0_8px_rgba(91,255,160,0.6)]' : 'bg-white/[0.08]'
                  )}
                />
              ))}
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-3">
          {/* Step 1 */}
          <StepShell active={expandedSteps[1]}>
            <StepHeader step={1} title={t('slackConfig.step1Title')} icon={<Plus size={16} className="text-cyan" />} />
            {expandedSteps[1] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('slackConfig.step1Description')}</p>
                <div className="flex flex-wrap items-center gap-3">
                  <button
                    onClick={openSlackCreateApp}
                    disabled={!manifestCompact || manifestLoading}
                    className="inline-flex items-center gap-2 rounded-lg bg-mint px-4 py-2 text-[13px] font-bold text-[#080812] shadow-[0_0_24px_-4px_rgba(91,255,160,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    <ExternalLink size={14} strokeWidth={2.25} />
                    {t('slackConfig.createSlackApp')}
                  </button>
                  {manifest && (
                    <button
                      onClick={copyManifest}
                      disabled={manifestLoading}
                      className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-white/[0.04] px-3 py-2 text-[12px] font-medium text-foreground transition hover:border-border-strong disabled:opacity-50"
                    >
                      {copied ? <Check size={14} /> : <Copy size={14} />}
                      {copied ? t('slackConfig.copied') : t('slackConfig.copyManifest')}
                    </button>
                  )}
                </div>
                {manifest && (
                  <details className="group">
                    <summary className="flex cursor-pointer items-center gap-1 text-[12px] text-cyan hover:text-cyan/80">
                      <ChevronDown size={14} className="transition-transform group-open:rotate-180" />
                      {t('slackConfig.viewManifest')}
                    </summary>
                    <pre className="mt-2 max-h-40 overflow-auto rounded-lg border border-border bg-background px-3 py-3 font-mono text-[11px] text-foreground">
                      {manifest}
                    </pre>
                  </details>
                )}
                {manifestLoading && (
                  <div className="flex items-center gap-2 text-[12px] text-muted">
                    <RefreshCw size={14} className="animate-spin" />
                    {t('common.loading')}
                  </div>
                )}
                <div className="rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2 text-[12px] leading-[1.55] text-cyan">
                  <strong>{t('slackConfig.tip')}:</strong> {t('slackConfig.step1Tip')}
                </div>
              </div>
            )}
          </StepShell>

          {/* Step 2 */}
          <StepShell active={expandedSteps[2]}>
            <StepHeader
              step={2}
              title={t('slackConfig.step2Title')}
              icon={<Shield size={16} className="text-cyan" />}
              completed={botToken.startsWith('xoxb-')}
            />
            {expandedSteps[2] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('slackConfig.step2Description')}</p>
                <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                  <li>{t('slackConfig.step2Item1')}</li>
                  <li>{t('slackConfig.step2Item2')}</li>
                  <li>{t('slackConfig.step2Item3')}</li>
                  <li>{t('slackConfig.step2Item4')}</li>
                </ol>
                <div className="space-y-2 pt-1">
                  <label className="flex items-center gap-2 text-[12px] font-medium text-foreground">
                    <Key size={14} className="text-cyan" /> {t('slackConfig.botToken')}
                  </label>
                  <input
                    type="password"
                    value={botToken}
                    onChange={(e) => setBotToken(e.target.value)}
                    placeholder={t('slackConfig.botTokenPlaceholder')}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2.5 font-mono text-[12px] text-foreground outline-none transition placeholder:text-muted/55 focus:border-cyan focus:ring-1 focus:ring-cyan/40"
                  />
                  <p className="text-[11px] text-muted">
                    {t('slackConfig.botTokenHint')}{' '}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 text-foreground">xoxb-</code>
                  </p>
                </div>
              </div>
            )}
          </StepShell>

          {/* Step 3 */}
          <StepShell active={expandedSteps[3]}>
            <StepHeader
              step={3}
              title={t('slackConfig.step3Title')}
              icon={<Lock size={16} className="text-cyan" />}
              completed={appToken.startsWith('xapp-')}
            />
            {expandedSteps[3] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('slackConfig.step3Description')}</p>
                <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                  <li>{t('slackConfig.step3Item1')}</li>
                  <li>{t('slackConfig.step3Item2')}</li>
                  <li>{t('slackConfig.step3Item3')}</li>
                  <li>{t('slackConfig.step3Item4')}</li>
                  <li>{t('slackConfig.step3Item5')}</li>
                </ol>
                <div className="space-y-2 pt-1">
                  <label className="flex items-center gap-2 text-[12px] font-medium text-foreground">
                    <Key size={14} className="text-cyan" /> {t('slackConfig.appToken')}
                  </label>
                  <input
                    type="password"
                    value={appToken}
                    onChange={(e) => setAppToken(e.target.value)}
                    placeholder={t('slackConfig.appTokenPlaceholder')}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2.5 font-mono text-[12px] text-foreground outline-none transition placeholder:text-muted/55 focus:border-cyan focus:ring-1 focus:ring-cyan/40"
                  />
                  <p className="text-[11px] text-muted">
                    {t('slackConfig.appTokenHint')}{' '}
                    <code className="rounded bg-white/[0.06] px-1.5 py-0.5 text-foreground">xapp-</code>
                  </p>
                </div>
                <div className="rounded-lg border border-gold/30 bg-gold/10 px-3 py-2 text-[12px] leading-[1.55] text-gold">
                  <strong>{t('slackConfig.important')}:</strong> {t('slackConfig.step3Warning')}
                </div>
              </div>
            )}
          </StepShell>

          {/* Step 4 */}
          <StepShell active={expandedSteps[4]}>
            <StepHeader
              step={4}
              title={t('slackConfig.step4Title')}
              icon={<Hash size={16} className="text-cyan" />}
              completed={!!authResult?.ok}
            />
            {expandedSteps[4] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('slackConfig.step4Description')}</p>
                <div className="flex flex-wrap items-center gap-3">
                  <button
                    onClick={runAuthTest}
                    disabled={!botToken || !appToken || checking}
                    className="inline-flex items-center gap-2 rounded-lg bg-mint px-4 py-2 text-[13px] font-bold text-[#080812] shadow-[0_0_24px_-4px_rgba(91,255,160,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {checking ? <RefreshCw size={14} className="animate-spin" /> : <Shield size={14} strokeWidth={2.25} />}
                    {t('slackConfig.validateTokens')}
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
                      {authResult.ok ? t('slackConfig.tokenValidated') : `${t('slackConfig.authFailed')}: ${authResult.error}`}
                    </span>
                  )}
                </div>
                {authResult?.ok && (
                  <div className="space-y-2.5 pt-1">
                    <p className="text-[12px] font-semibold text-foreground">{t('slackConfig.inviteBotTitle')}</p>
                    <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                      <li>{t('slackConfig.step4Item1')}</li>
                      <li>{t('slackConfig.step4Item2')}</li>
                      <li>{t('slackConfig.step4Item3')}</li>
                    </ol>
                    <div className="rounded-lg border border-mint/30 bg-mint/[0.08] px-3 py-2 text-[12px] leading-[1.55] text-mint">
                      <strong>{t('slackConfig.tip')}:</strong> {t('slackConfig.step4Tip')}
                    </div>
                  </div>
                )}
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
              onNext({ slack: { ...data.slack, bot_token: botToken, app_token: appToken }, mode: 'self_host' })
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
