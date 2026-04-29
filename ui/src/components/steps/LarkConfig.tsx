import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  ExternalLink,
  Globe,
  KeyRound,
  MessageSquare,
  Plus,
  Radio,
  RefreshCw,
  Shield,
  Wifi,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';
import { useToast } from '../../context/ToastContext';
import { copyTextToClipboard } from '../../lib/utils';
import { EyebrowBadge, WizardCard } from '../visual';

const LARK_PERMISSIONS_JSON = `{
  "scopes": {
    "tenant": [
      "contact:contact.base:readonly",
      "contact:user.base:readonly",
      "im:chat",
      "im:message",
      "im:message.group_at_msg:readonly",
      "im:message.group_msg",
      "im:message.p2p_msg:readonly",
      "im:message.reactions:read",
      "im:message.reactions:write_only",
      "im:message:send_as_bot",
      "im:message:update",
      "im:message:recall",
      "im:resource",
      "cardkit:card:write",
      "cardkit:card:read"
    ],
    "user": []
  }
}`;

interface LarkConfigProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

// Mirrors design.pen XCWAT (Slack creds wizard step) adapted for Lark/Feishu.
// Adds a domain selector ahead of the 5-step accordion. WizardCard 920, mint eyebrow.
export const LarkConfig: React.FC<LarkConfigProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [domain, setDomain] = useState<'feishu' | 'lark'>(data.lark?.domain || 'feishu');
  const [appId, setAppId] = useState(data.lark?.app_id || '');
  const [appSecret, setAppSecret] = useState(data.lark?.app_secret || '');
  const [checking, setChecking] = useState(false);
  const [authResult, setAuthResult] = useState<any>(null);
  const [wsStatus, setWsStatus] = useState<'idle' | 'connecting' | 'connected' | 'error'>('idle');
  const [chats, setChats] = useState<any[]>([]);
  const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({
    1: true,
    2: false,
    3: false,
    4: false,
    5: false,
  });
  const [copiedJson, setCopiedJson] = useState(false);

  const platformBase = domain === 'lark' ? 'https://open.larksuite.com' : 'https://open.feishu.cn';
  const appBase = appId ? `${platformBase}/app/${appId}` : '';
  const permissionsUrl = appBase ? `${appBase}/auth` : '';
  const eventsUrl = appBase ? `${appBase}/event` : '';
  const versionUrl = appBase ? `${appBase}/version` : '';

  useEffect(() => {
    setAuthResult(null);
    setWsStatus('idle');
  }, [appId, appSecret]);

  useEffect(() => {
    if (appId && appSecret && !expandedSteps[2]) {
      setExpandedSteps((prev) => ({ ...prev, 2: true }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [appId, appSecret]);

  useEffect(() => {
    return () => {
      api.larkTempWsStop().catch(() => {});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const isValid = useMemo(() => {
    if (!appId || !appSecret) return false;
    if (authResult?.ok) return true;
    return Boolean(
      data.lark?.app_id &&
        data.lark?.app_secret &&
        appId === data.lark?.app_id &&
        appSecret === data.lark?.app_secret
    );
  }, [appId, appSecret, authResult, data.lark?.app_id, data.lark?.app_secret]);

  const runAuthTest = async () => {
    setChecking(true);
    setWsStatus('idle');
    try {
      const result = await api.larkAuthTest(appId, appSecret, domain);
      setAuthResult(result);

      if (result.ok) {
        setWsStatus('connecting');
        try {
          await api.larkTempWsStart(appId, appSecret, domain);
          await new Promise((resolve) => setTimeout(resolve, 2000));
          setWsStatus('connected');
        } catch {
          setWsStatus('error');
        }
      }
    } catch (err: any) {
      setAuthResult({ ok: false, error: err?.message || 'Request failed' });
    } finally {
      setChecking(false);
    }
  };

  const loadChats = async () => {
    if (!appId || !appSecret) return;
    try {
      const result = await api.larkChats(appId, appSecret, domain);
      if (result.ok) {
        setChats(result.channels || []);
      }
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    if (authResult?.ok) {
      loadChats();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authResult?.ok]);

  const toggleStep = (step: number) => {
    setExpandedSteps((prev) => ({ ...prev, [step]: !prev[step] }));
  };

  const copyPermissionsJson = async () => {
    const ok = await copyTextToClipboard(LARK_PERMISSIONS_JSON);
    if (ok) {
      setCopiedJson(true);
      setTimeout(() => setCopiedJson(false), 2000);
      return;
    }
    showToast(t('common.copyFailed'), 'error');
  };

  const LinkButton: React.FC<{ url: string; label: string }> = ({ url, label }) => (
    <button
      onClick={() => window.open(url, '_blank')}
      disabled={!url}
      className="inline-flex items-center gap-2 rounded-lg bg-mint px-4 py-2 text-[13px] font-bold text-[#080812] shadow-[0_0_24px_-4px_rgba(91,255,160,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
    >
      <ExternalLink size={14} strokeWidth={2.25} />
      {label}
    </button>
  );

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
    Boolean(appId),
    isValid,
    Boolean(authResult?.ok),
    expandedSteps[4],
    expandedSteps[5],
  ].filter(Boolean).length;

  return (
    <div className="flex w-full justify-center">
      <WizardCard className="gap-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-2">
            <EyebrowBadge tone="mint">Lark / Feishu</EyebrowBadge>
            <h2 className="text-[28px] font-bold leading-tight tracking-[-0.4px] text-foreground">
              {t('larkConfig.title')}
            </h2>
            <p className="max-w-[560px] text-[14px] leading-[1.55] text-muted">
              {t('larkConfig.subtitle')}
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

        {/* Domain selector */}
        <div className="space-y-2 rounded-xl border border-border bg-background px-5 py-4">
          <label className="flex items-center gap-2 text-[12px] font-medium text-foreground">
            <Globe size={14} className="text-cyan" /> {t('larkConfig.domainLabel')}
          </label>
          <div className="flex gap-2">
            {(['feishu', 'lark'] as const).map((opt) => (
              <button
                key={opt}
                onClick={() => {
                  setDomain(opt);
                  setAuthResult(null);
                  setWsStatus('idle');
                }}
                className={clsx(
                  'flex-1 rounded-lg border px-3 py-2 text-[12px] font-semibold transition',
                  domain === opt
                    ? 'border-mint/45 bg-mint/[0.08] text-mint shadow-[0_0_24px_-4px_rgba(91,255,160,0.4)]'
                    : 'border-border bg-white/[0.04] text-muted hover:border-border-strong hover:text-foreground'
                )}
              >
                {opt === 'feishu' ? t('larkConfig.domainFeishu') : t('larkConfig.domainLark')}
              </button>
            ))}
          </div>
          <p className="text-[11px] text-muted">{t('larkConfig.domainHint')}</p>
        </div>

        <div className="flex flex-col gap-3">
          {/* Step 1 — Create app */}
          <StepShell active={expandedSteps[1]}>
            <StepHeader step={1} title={t('larkConfig.step1Title')} icon={<Plus size={16} className="text-cyan" />} />
            {expandedSteps[1] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('larkConfig.step1Description')}</p>
                <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                  <li>{t('larkConfig.step1Item1')}</li>
                  <li>{t('larkConfig.step1Item2')}</li>
                  <li>{t('larkConfig.step1Item3')}</li>
                </ol>
                <LinkButton url={`${platformBase}/app`} label={t('larkConfig.openPlatform')} />
              </div>
            )}
          </StepShell>

          {/* Step 2 — Credentials */}
          <StepShell active={expandedSteps[2]}>
            <StepHeader
              step={2}
              title={t('larkConfig.step2Title')}
              icon={<KeyRound size={16} className="text-cyan" />}
              completed={isValid}
            />
            {expandedSteps[2] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('larkConfig.step2Description')}</p>

                <div className="space-y-2 pt-1">
                  <label className="flex items-center gap-2 text-[12px] font-medium text-foreground">
                    <KeyRound size={14} className="text-cyan" /> {t('larkConfig.appId')}
                  </label>
                  <input
                    type="text"
                    value={appId}
                    onChange={(e) => setAppId(e.target.value)}
                    placeholder={t('larkConfig.appIdPlaceholder')}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2.5 font-mono text-[12px] text-foreground outline-none transition placeholder:text-muted/55 focus:border-cyan focus:ring-1 focus:ring-cyan/40"
                  />
                  <p className="text-[11px] text-muted">{t('larkConfig.appIdHint')}</p>
                </div>

                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-[12px] font-medium text-foreground">
                    <KeyRound size={14} className="text-cyan" /> {t('larkConfig.appSecret')}
                  </label>
                  <input
                    type="password"
                    value={appSecret}
                    onChange={(e) => setAppSecret(e.target.value)}
                    placeholder={t('larkConfig.appSecretPlaceholder')}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2.5 font-mono text-[12px] text-foreground outline-none transition placeholder:text-muted/55 focus:border-cyan focus:ring-1 focus:ring-cyan/40"
                  />
                  <p className="text-[11px] text-muted">{t('larkConfig.appSecretHint')}</p>
                </div>

                <div className="flex flex-wrap items-center gap-3">
                  <button
                    onClick={runAuthTest}
                    disabled={!appId || !appSecret || checking}
                    className="inline-flex items-center gap-2 rounded-lg bg-mint px-4 py-2 text-[13px] font-bold text-[#080812] shadow-[0_0_24px_-4px_rgba(91,255,160,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {checking ? <RefreshCw size={14} className="animate-spin" /> : <Shield size={14} strokeWidth={2.25} />}
                    {t('larkConfig.validateCredentials')}
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
                        ? t('larkConfig.credentialsValidated')
                        : `${t('larkConfig.authFailed')}: ${authResult.error}`}
                    </span>
                  )}
                </div>

                {wsStatus !== 'idle' && (
                  <div
                    className={clsx(
                      'inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-[12px]',
                      wsStatus === 'connecting' && 'border-cyan/30 bg-cyan/[0.06] text-cyan',
                      wsStatus === 'connected' && 'border-mint/30 bg-mint/[0.08] text-mint',
                      wsStatus === 'error' && 'border-danger/30 bg-danger/10 text-danger'
                    )}
                  >
                    {wsStatus === 'connecting' && <RefreshCw size={14} className="animate-spin" />}
                    {wsStatus === 'connected' && <Wifi size={14} />}
                    {wsStatus === 'connecting' && t('larkConfig.step2ConnectingWs')}
                    {wsStatus === 'connected' && t('larkConfig.step2WsConnected')}
                  </div>
                )}

                {authResult?.ok && chats.length > 0 && (
                  <div className="space-y-2 pt-1">
                    <label className="flex items-center gap-2 text-[12px] font-medium text-foreground">
                      <MessageSquare size={14} className="text-cyan" /> {t('larkConfig.chatListLabel')}
                    </label>
                    <p className="text-[11px] text-muted">{t('larkConfig.chatListHint')}</p>
                    <div className="max-h-32 overflow-y-auto rounded-lg border border-border bg-background px-3 py-2.5">
                      <ul className="space-y-1 text-[12px] text-foreground">
                        {chats.map((c: any) => (
                          <li key={c.id} className="flex items-center gap-2">
                            <Check size={12} className="shrink-0 text-mint" />
                            <span>{c.name}</span>
                            <span className="font-mono text-[11px] text-muted">({c.id})</span>
                          </li>
                        ))}
                      </ul>
                    </div>
                  </div>
                )}
              </div>
            )}
          </StepShell>

          {/* Step 3 — Permissions */}
          <StepShell active={expandedSteps[3]}>
            <StepHeader step={3} title={t('larkConfig.step3Title')} icon={<Shield size={16} className="text-cyan" />} />
            {expandedSteps[3] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('larkConfig.step3Description')}</p>

                {permissionsUrl && <LinkButton url={permissionsUrl} label={t('larkConfig.step3OpenLink')} />}

                <div className="space-y-2 rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2.5">
                  <div className="flex items-center justify-between">
                    <span className="text-[12px] font-semibold text-cyan">{t('larkConfig.step3BatchImport')}</span>
                    <button
                      onClick={copyPermissionsJson}
                      className="inline-flex items-center gap-1.5 rounded border border-cyan/30 bg-cyan/[0.08] px-2 py-0.5 text-[11px] font-medium text-cyan transition hover:bg-cyan/[0.16]"
                    >
                      {copiedJson ? <Check size={12} /> : <Copy size={12} />}
                      {copiedJson ? t('larkConfig.step3Copied') : t('larkConfig.step3CopyJson')}
                    </button>
                  </div>
                  <pre className="overflow-x-auto whitespace-pre rounded bg-background/80 px-3 py-2 font-mono text-[11px] text-foreground">
                    {LARK_PERMISSIONS_JSON}
                  </pre>
                  <p className="text-[11px] text-cyan/85">{t('larkConfig.step3BatchImportHint')}</p>
                </div>

                <details className="text-[12px] text-muted">
                  <summary className="cursor-pointer font-medium text-foreground transition hover:text-cyan">
                    {t('larkConfig.step3ManualList')}
                  </summary>
                  <ul className="mt-2 space-y-1.5 pl-1">
                    {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14].map((i) => (
                      <li key={i} className="flex items-start gap-2">
                        <code className="shrink-0 rounded bg-white/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-foreground">
                          {i}
                        </code>
                        {t(`larkConfig.step3Item${i}`)}
                      </li>
                    ))}
                  </ul>
                </details>
              </div>
            )}
          </StepShell>

          {/* Step 4 — Events / callbacks / WS */}
          <StepShell active={expandedSteps[4]}>
            <StepHeader step={4} title={t('larkConfig.step4Title')} icon={<Radio size={16} className="text-cyan" />} />
            {expandedSteps[4] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('larkConfig.step4Description')}</p>

                {eventsUrl && <LinkButton url={eventsUrl} label={t('larkConfig.step4OpenLink')} />}

                <div className="space-y-2">
                  <h4 className="text-[12px] font-semibold text-foreground">{t('larkConfig.step4EventTitle')}</h4>
                  <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                    <li>{t('larkConfig.step4Item1')}</li>
                    <li>{t('larkConfig.step4Item2')}</li>
                    <li>{t('larkConfig.step4Item3')}</li>
                  </ol>
                </div>

                <div className="space-y-2">
                  <h4 className="text-[12px] font-semibold text-foreground">{t('larkConfig.step4CallbackTitle')}</h4>
                  <p className="text-[12px] text-muted">{t('larkConfig.step4CallbackDesc')}</p>
                  <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                    <li>{t('larkConfig.step4CallbackItem1')}</li>
                    <li>{t('larkConfig.step4CallbackItem2')}</li>
                    <li>{t('larkConfig.step4CallbackItem3')}</li>
                  </ol>
                </div>

                <div className="rounded-lg border border-gold/30 bg-gold/10 px-3 py-2 text-[12px] leading-[1.55] text-gold">
                  <strong>{t('slackConfig.important')}:</strong> {t('larkConfig.step4Tip')}
                </div>

                <div className="space-y-1.5 rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2.5">
                  <div className="flex items-center gap-2 text-[12px] font-semibold text-cyan">
                    <AlertTriangle size={14} />
                    {t('larkConfig.step4LongConnFaqTitle')}
                  </div>
                  <p className="text-[11px] text-cyan/85">{t('larkConfig.step4LongConnFaqDesc')}</p>
                  <p className="text-[11px] font-medium text-cyan">{t('larkConfig.step4LarkWsWarning')}</p>
                </div>
              </div>
            )}
          </StepShell>

          {/* Step 5 — Publish */}
          <StepShell active={expandedSteps[5]}>
            <StepHeader step={5} title={t('larkConfig.step5Title')} icon={<BookOpen size={16} className="text-cyan" />} />
            {expandedSteps[5] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('larkConfig.step5Description')}</p>

                {versionUrl && <LinkButton url={versionUrl} label={t('larkConfig.step5OpenLink')} />}

                <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                  <li>{t('larkConfig.step5Item1')}</li>
                </ol>
                <div className="rounded-lg border border-gold/30 bg-gold/10 px-3 py-2 text-[12px] leading-[1.55] text-gold">
                  <strong>{t('slackConfig.important')}:</strong> {t('larkConfig.step5Tip')}
                </div>
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
            onClick={() => {
              api.larkTempWsStop().catch(() => {});
              onNext({
                platform: 'lark',
                lark: {
                  ...(data.lark || {}),
                  app_id: appId,
                  app_secret: appSecret,
                  domain,
                },
              });
            }}
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
