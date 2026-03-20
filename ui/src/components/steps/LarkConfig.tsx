import React, { useEffect, useMemo, useState } from 'react';
import { Shield, RefreshCw, Check, MessageSquare, KeyRound, Plus, ExternalLink, ChevronDown, ChevronUp, BookOpen, Copy, AlertTriangle, Radio, Globe, Wifi } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';
import { useToast } from '../../context/ToastContext';
import { copyTextToClipboard } from '../../lib/utils';

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
  const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({ 1: true, 2: false, 3: false, 4: false, 5: false });
  const [copiedJson, setCopiedJson] = useState(false);

  const platformBase = domain === 'lark' ? 'https://open.larksuite.com' : 'https://open.feishu.cn';
  // Dynamic links based on appId — only useful after credentials are entered
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
      setExpandedSteps(prev => ({ ...prev, 2: true }));
    }
  }, [appId, appSecret]);

  // Stop temp WS on unmount
  useEffect(() => {
    return () => {
      api.larkTempWsStop().catch(() => {});
    };
  }, []);

  const isValid = useMemo(() => appId.length > 0 && appSecret.length > 0 && authResult?.ok, [appId, appSecret, authResult]);

  const runAuthTest = async () => {
    setChecking(true);
    setWsStatus('idle');
    try {
      const result = await api.larkAuthTest(appId, appSecret, domain);
      setAuthResult(result);

      // On success, start temp WS so the Feishu console shows "Use Long Connection"
      if (result.ok) {
        setWsStatus('connecting');
        try {
          await api.larkTempWsStart(appId, appSecret, domain);
          await new Promise(resolve => setTimeout(resolve, 2000));
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
  }, [authResult?.ok]);

  const toggleStep = (step: number) => {
    setExpandedSteps(prev => ({ ...prev, [step]: !prev[step] }));
  };

  const copyPermissionsJson = async () => {
    const copiedToClipboard = await copyTextToClipboard(LARK_PERMISSIONS_JSON);
    if (copiedToClipboard) {
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
      className="flex items-center gap-2 px-4 py-2 bg-accent text-white rounded-lg hover:bg-accent/90 transition-colors font-medium shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
    >
      <ExternalLink size={16} />
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
        <h2 className="text-3xl font-display font-bold text-text">{t('larkConfig.title')}</h2>
        <p className="text-muted mt-1">{t('larkConfig.subtitle')}</p>
      </div>

      {/* Domain selector */}
      <div className="mb-4 bg-panel border border-border rounded-xl p-4 space-y-2">
        <label className="text-sm font-medium text-text flex items-center gap-2">
          <Globe size={16} className="text-accent" /> {t('larkConfig.domainLabel')}
        </label>
        <div className="flex gap-3">
          <button
            onClick={() => { setDomain('feishu'); setAuthResult(null); setWsStatus('idle'); }}
            className={clsx(
              'flex-1 px-4 py-2.5 rounded-lg border-2 text-sm font-medium transition-all',
              domain === 'feishu'
                ? 'border-accent bg-accent/10 text-accent'
                : 'border-border bg-bg text-muted hover:border-accent/30'
            )}
          >
            {t('larkConfig.domainFeishu')}
          </button>
          <button
            onClick={() => { setDomain('lark'); setAuthResult(null); setWsStatus('idle'); }}
            className={clsx(
              'flex-1 px-4 py-2.5 rounded-lg border-2 text-sm font-medium transition-all',
              domain === 'lark'
                ? 'border-accent bg-accent/10 text-accent'
                : 'border-border bg-bg text-muted hover:border-accent/30'
            )}
          >
            {t('larkConfig.domainLark')}
          </button>
        </div>
        <p className="text-xs text-muted">{t('larkConfig.domainHint')}</p>
      </div>

      <div className="space-y-3 overflow-y-auto flex-1 pr-1">
        {/* Step 1: Create App */}
        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader step={1} title={t('larkConfig.step1Title')} icon={<Plus size={16} className="text-accent" />} />
          {expandedSteps[1] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('larkConfig.step1Description')}</p>
              <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                <li>{t('larkConfig.step1Item1')}</li>
                <li>{t('larkConfig.step1Item2')}</li>
                <li>{t('larkConfig.step1Item3')}</li>
              </ol>
              <LinkButton url={`${platformBase}/app`} label={t('larkConfig.openPlatform')} />
            </div>
          )}
        </div>

        {/* Step 2: Enter Credentials & Validate */}
        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader step={2} title={t('larkConfig.step2Title')} icon={<KeyRound size={16} className="text-accent" />} completed={isValid} />
          {expandedSteps[2] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('larkConfig.step2Description')}</p>

              <div className="space-y-2 pt-2">
                <label className="text-sm font-medium text-text flex items-center gap-2">
                  <KeyRound size={16} className="text-accent" /> {t('larkConfig.appId')}
                </label>
                <input
                  type="text"
                  value={appId}
                  onChange={(e) => setAppId(e.target.value)}
                  placeholder={t('larkConfig.appIdPlaceholder')}
                  className="w-full bg-bg border border-border rounded-lg p-3 text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent font-mono transition-colors"
                />
                <p className="text-xs text-muted">{t('larkConfig.appIdHint')}</p>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-text flex items-center gap-2">
                  <KeyRound size={16} className="text-accent" /> {t('larkConfig.appSecret')}
                </label>
                <input
                  type="password"
                  value={appSecret}
                  onChange={(e) => setAppSecret(e.target.value)}
                  placeholder={t('larkConfig.appSecretPlaceholder')}
                  className="w-full bg-bg border border-border rounded-lg p-3 text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent font-mono transition-colors"
                />
                <p className="text-xs text-muted">{t('larkConfig.appSecretHint')}</p>
              </div>

              <div className="flex items-center gap-3">
                <button
                  onClick={runAuthTest}
                  disabled={!appId || !appSecret || checking}
                  className="px-4 py-2 bg-accent text-white rounded-lg flex items-center gap-2 transition-colors font-medium shadow-sm hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {checking ? <RefreshCw size={16} className="animate-spin" /> : <Shield size={16} />}
                  {t('larkConfig.validateCredentials')}
                </button>
                {authResult && (
                  <span
                    className={clsx(
                      'flex items-center gap-2 text-sm font-medium px-3 py-1.5 rounded-lg border',
                      authResult.ok ? 'text-success bg-success/10 border-success/20' : 'text-danger bg-danger/10 border-danger/20'
                    )}
                  >
                    {authResult.ok ? (
                      <><Check size={14} /><span>{t('larkConfig.credentialsValidated')}</span></>
                    ) : (
                      <span>{t('larkConfig.authFailed')}: {authResult.error}</span>
                    )}
                  </span>
                )}
              </div>

              {/* WS connection status */}
              {wsStatus !== 'idle' && (
                <div className={clsx(
                  'rounded-lg p-3 text-sm flex items-center gap-2 border',
                  wsStatus === 'connecting' ? 'bg-blue-50 border-blue-200 text-blue-800' :
                  wsStatus === 'connected' ? 'bg-success/10 border-success/20 text-success' :
                  'bg-danger/10 border-danger/20 text-danger'
                )}>
                  {wsStatus === 'connecting' && <RefreshCw size={14} className="animate-spin" />}
                  {wsStatus === 'connected' && <Wifi size={14} />}
                  {wsStatus === 'connecting' && t('larkConfig.step2ConnectingWs')}
                  {wsStatus === 'connected' && t('larkConfig.step2WsConnected')}
                </div>
              )}

              {authResult?.ok && chats.length > 0 && (
                <div className="space-y-2">
                  <label className="text-sm font-medium text-text flex items-center gap-2">
                    <MessageSquare size={16} className="text-accent" /> {t('larkConfig.chatListLabel')}
                  </label>
                  <p className="text-xs text-muted">{t('larkConfig.chatListHint')}</p>
                  <div className="bg-bg border border-border rounded-lg p-3 max-h-32 overflow-y-auto">
                    <ul className="space-y-1 text-sm text-text">
                      {chats.map((c: any) => (
                        <li key={c.id} className="flex items-center gap-2">
                          <Check size={12} className="text-success shrink-0" />
                          <span>{c.name}</span>
                          <span className="text-xs text-muted font-mono">({c.id})</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Step 3: Configure Permissions */}
        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader step={3} title={t('larkConfig.step3Title')} icon={<Shield size={16} className="text-accent" />} />
          {expandedSteps[3] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('larkConfig.step3Description')}</p>

              {permissionsUrl && (
                <LinkButton url={permissionsUrl} label={t('larkConfig.step3OpenLink')} />
              )}

              {/* Batch import JSON */}
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium text-blue-800">{t('larkConfig.step3BatchImport')}</span>
                  <button
                    onClick={copyPermissionsJson}
                    className="flex items-center gap-1.5 px-3 py-1 bg-blue-100 hover:bg-blue-200 text-blue-700 rounded text-xs font-medium transition-colors"
                  >
                    {copiedJson ? <Check size={12} /> : <Copy size={12} />}
                    {copiedJson ? t('larkConfig.step3Copied') : t('larkConfig.step3CopyJson')}
                  </button>
                </div>
                <pre className="text-xs bg-white/70 rounded p-2 overflow-x-auto font-mono text-blue-900 whitespace-pre">{LARK_PERMISSIONS_JSON}</pre>
                <p className="text-xs text-blue-700">{t('larkConfig.step3BatchImportHint')}</p>
              </div>

              <details className="text-sm text-muted">
                <summary className="cursor-pointer font-medium text-text hover:text-accent transition-colors">{t('larkConfig.step3ManualList')}</summary>
                <ul className="space-y-1.5 mt-2 pl-1">
                  {[1,2,3,4,5,6,7,8,9,10,11,12,13,14].map(i => (
                    <li key={i} className="flex items-start gap-2">
                      <code className="text-xs bg-neutral-100 px-1.5 py-0.5 rounded font-mono shrink-0">{i}</code>
                      {t(`larkConfig.step3Item${i}`)}
                    </li>
                  ))}
                </ul>
              </details>
            </div>
          )}
        </div>

        {/* Step 4: Configure Events, Callbacks & Long Connection */}
        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader step={4} title={t('larkConfig.step4Title')} icon={<Radio size={16} className="text-accent" />} />
          {expandedSteps[4] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('larkConfig.step4Description')}</p>

              {eventsUrl && (
                <LinkButton url={eventsUrl} label={t('larkConfig.step4OpenLink')} />
              )}

              {/* Part A: Event subscription */}
              <div className="space-y-2">
                <h4 className="text-sm font-semibold text-text">{t('larkConfig.step4EventTitle')}</h4>
                <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                  <li>{t('larkConfig.step4Item1')}</li>
                  <li>{t('larkConfig.step4Item2')}</li>
                  <li>{t('larkConfig.step4Item3')}</li>
                </ol>
              </div>

              {/* Part B: Callback subscription */}
              <div className="space-y-2">
                <h4 className="text-sm font-semibold text-text">{t('larkConfig.step4CallbackTitle')}</h4>
                <p className="text-sm text-muted">{t('larkConfig.step4CallbackDesc')}</p>
                <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                  <li>{t('larkConfig.step4CallbackItem1')}</li>
                  <li>{t('larkConfig.step4CallbackItem2')}</li>
                  <li>{t('larkConfig.step4CallbackItem3')}</li>
                </ol>
              </div>

              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">
                <strong>{t('slackConfig.important')}:</strong> {t('larkConfig.step4Tip')}
              </div>

              {/* FAQ callout */}
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 space-y-1.5">
                <div className="flex items-center gap-2 text-sm font-medium text-blue-800">
                  <AlertTriangle size={14} className="text-blue-600" />
                  {t('larkConfig.step4LongConnFaqTitle')}
                </div>
                <p className="text-xs text-blue-700">{t('larkConfig.step4LongConnFaqDesc')}</p>
                <p className="text-xs text-blue-700 font-medium">{t('larkConfig.step4LarkWsWarning')}</p>
              </div>
            </div>
          )}
        </div>

        {/* Step 5: Publish App */}
        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader step={5} title={t('larkConfig.step5Title')} icon={<BookOpen size={16} className="text-accent" />} />
          {expandedSteps[5] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('larkConfig.step5Description')}</p>

              {versionUrl && (
                <LinkButton url={versionUrl} label={t('larkConfig.step5OpenLink')} />
              )}

              <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                <li>{t('larkConfig.step5Item1')}</li>
              </ol>
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm text-amber-800">
                <strong>{t('slackConfig.important')}:</strong> {t('larkConfig.step5Tip')}
              </div>
            </div>
          )}
        </div>
      </div>

      <div className="mt-auto flex justify-between pt-6 border-t border-border">
        <button onClick={onBack} className="px-6 py-2 text-muted hover:text-text font-medium transition-colors">
          {t('common.back')}
        </button>
        <button
          onClick={() => {
            api.larkTempWsStop().catch(() => {});
            onNext({
              platform: 'lark',
              lark: {
                ...(data.lark || {}),
                app_id: appId,
                app_secret: appSecret,
                domain: domain,
              },
            });
          }}
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
