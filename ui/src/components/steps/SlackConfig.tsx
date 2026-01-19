import React, { useEffect, useMemo, useState } from 'react';
import { Lock, Shield, RefreshCw, Copy, ExternalLink, Check, ChevronDown, ChevronUp, Key, Hash, Plus } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';

interface SlackConfigProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

export const SlackConfig: React.FC<SlackConfigProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const api = useApi();
  const [botToken, setBotToken] = useState(data.slack?.bot_token || data.slackBotToken || '');
  const [appToken, setAppToken] = useState(data.slack?.app_token || data.slackAppToken || '');
  const [mode] = useState<'self_host' | 'saas'>(data.mode || 'saas');
  const [checking, setChecking] = useState(false);
  const [authResult, setAuthResult] = useState<any>(null);
  const [manifest, setManifest] = useState<string>('');
  const [manifestCompact, setManifestCompact] = useState<string>('');
  const [manifestLoading, setManifestLoading] = useState(false);
  const [copied, setCopied] = useState(false);
  
  // Collapsible states for each step
  const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({ 1: true, 2: false, 3: false, 4: false });

  const isValid = useMemo(() => {
    if (mode === 'saas') return true;
    return botToken.startsWith('xoxb-') && appToken.startsWith('xapp-');
  }, [mode, botToken, appToken]);

  // Load manifest on mount for self-host mode
  useEffect(() => {
    if (mode === 'self_host') {
      loadManifest();
    }
  }, [mode]);

  // Auto-expand next step when token is entered
  useEffect(() => {
    if (botToken.startsWith('xoxb-') && !expandedSteps[3]) {
      setExpandedSteps(prev => ({ ...prev, 2: false, 3: true }));
    }
  }, [botToken]);

  useEffect(() => {
    if (appToken.startsWith('xapp-') && !expandedSteps[4]) {
      setExpandedSteps(prev => ({ ...prev, 3: false, 4: true }));
    }
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
    try {
      await navigator.clipboard.writeText(manifest);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
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
    setExpandedSteps(prev => ({ ...prev, [step]: !prev[step] }));
  };

  const StepHeader: React.FC<{ step: number; title: string; icon: React.ReactNode; completed?: boolean }> = ({ step, title, icon, completed }) => (
    <button
      onClick={() => toggleStep(step)}
      className="w-full px-4 py-3 flex items-center justify-between bg-neutral-50 hover:bg-neutral-100 transition-colors"
    >
      <div className="flex items-center gap-3">
        <span className={clsx(
          'w-7 h-7 rounded-full text-sm font-bold flex items-center justify-center transition-colors',
          completed ? 'bg-success text-white' : 'bg-accent text-white'
        )}>
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
        <h2 className="text-3xl font-display font-bold text-text">{t('slackConfig.title')}</h2>
        <p className="text-muted mt-1">
          {mode === 'saas' ? t('slackConfig.saasDescription') : t('slackConfig.selfHostDescription')}
        </p>
      </div>

      {mode === 'saas' ? (
        <div className="flex flex-col items-center justify-center h-64 border-2 border-dashed border-border rounded-xl bg-neutral-50">
          <p className="text-muted mb-6 text-center max-w-xs">{t('slackConfig.oauthPrompt')}</p>
          <button className="px-6 py-3 bg-accent text-white rounded-lg font-medium hover:bg-accent/90 transition-colors shadow-sm">
            {t('slackConfig.openSlackOAuth')}
          </button>
          <p className="text-xs text-muted mt-6">{t('slackConfig.afterAuth')}</p>
        </div>
      ) : (
        <div className="space-y-3 overflow-y-auto flex-1 pr-1">
          {/* Step 1: Create Slack App */}
          <div className="bg-panel border border-border rounded-xl overflow-hidden">
            <StepHeader 
              step={1} 
              title={t('slackConfig.step1Title')} 
              icon={<Plus size={16} className="text-accent" />}
            />
            {expandedSteps[1] && (
              <div className="p-4 space-y-4 border-t border-border">
                <p className="text-sm text-muted">{t('slackConfig.step1Description')}</p>
                
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={openSlackCreateApp}
                    disabled={!manifestCompact || manifestLoading}
                    className="flex items-center gap-2 px-4 py-2 bg-accent text-white rounded-lg hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors font-medium shadow-sm"
                  >
                    <ExternalLink size={16} />
                    {t('slackConfig.createSlackApp')}
                  </button>
                  <button
                    onClick={copyManifest}
                    disabled={!manifest || manifestLoading}
                    className="flex items-center gap-2 px-4 py-2 bg-neutral-100 hover:bg-neutral-200 text-text rounded-lg transition-colors font-medium border border-border"
                  >
                    {copied ? <Check size={16} /> : <Copy size={16} />}
                    {copied ? t('slackConfig.copied') : t('slackConfig.copyManifest')}
                  </button>
                </div>

                {manifest && (
                  <details className="group">
                    <summary className="cursor-pointer text-sm text-accent hover:text-accent/80 flex items-center gap-1">
                      <ChevronDown size={14} className="transition-transform group-open:rotate-180" />
                      {t('slackConfig.viewManifest')}
                    </summary>
                    <pre className="mt-2 bg-neutral-900 text-neutral-100 p-3 rounded-lg text-xs overflow-x-auto max-h-40 overflow-y-auto font-mono">
                      {manifest}
                    </pre>
                  </details>
                )}

                {manifestLoading && (
                  <div className="flex items-center gap-2 text-sm text-muted">
                    <RefreshCw size={14} className="animate-spin" />
                    {t('common.loading')}
                  </div>
                )}

                <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800">
                  <strong>{t('slackConfig.tip')}:</strong> {t('slackConfig.step1Tip')}
                </div>
              </div>
            )}
          </div>

          {/* Step 2: Get Bot Token */}
          <div className="bg-panel border border-border rounded-xl overflow-hidden">
            <StepHeader 
              step={2} 
              title={t('slackConfig.step2Title')} 
              icon={<Shield size={16} className="text-accent" />}
              completed={botToken.startsWith('xoxb-')}
            />
            {expandedSteps[2] && (
              <div className="p-4 space-y-4 border-t border-border">
                <p className="text-sm text-muted">{t('slackConfig.step2Description')}</p>
                
                <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                  <li>{t('slackConfig.step2Item1')}</li>
                  <li>{t('slackConfig.step2Item2')}</li>
                  <li>{t('slackConfig.step2Item3')}</li>
                  <li>{t('slackConfig.step2Item4')}</li>
                </ol>

                <div className="space-y-2 pt-2">
                  <label className="text-sm font-medium text-text flex items-center gap-2">
                    <Key size={16} className="text-accent" /> {t('slackConfig.botToken')}
                  </label>
                  <input
                    type="password"
                    value={botToken}
                    onChange={(e) => setBotToken(e.target.value)}
                    placeholder="xoxb-..."
                    className="w-full bg-bg border border-border rounded-lg p-3 text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent font-mono transition-colors"
                  />
                  <p className="text-xs text-muted">{t('slackConfig.botTokenHint')} <code className="bg-neutral-100 px-1.5 py-0.5 rounded text-text">xoxb-</code></p>
                </div>
              </div>
            )}
          </div>

          {/* Step 3: Get App Token */}
          <div className="bg-panel border border-border rounded-xl overflow-hidden">
            <StepHeader 
              step={3} 
              title={t('slackConfig.step3Title')} 
              icon={<Lock size={16} className="text-accent" />}
              completed={appToken.startsWith('xapp-')}
            />
            {expandedSteps[3] && (
              <div className="p-4 space-y-4 border-t border-border">
                <p className="text-sm text-muted">{t('slackConfig.step3Description')}</p>
                
                <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                  <li>{t('slackConfig.step3Item1')}</li>
                  <li>{t('slackConfig.step3Item2')}</li>
                  <li>{t('slackConfig.step3Item3')}</li>
                  <li>{t('slackConfig.step3Item4')}</li>
                  <li>{t('slackConfig.step3Item5')}</li>
                </ol>

                <div className="space-y-2 pt-2">
                  <label className="text-sm font-medium text-text flex items-center gap-2">
                    <Key size={16} className="text-accent" /> {t('slackConfig.appToken')}
                  </label>
                  <input
                    type="password"
                    value={appToken}
                    onChange={(e) => setAppToken(e.target.value)}
                    placeholder="xapp-..."
                    className="w-full bg-bg border border-border rounded-lg p-3 text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent font-mono transition-colors"
                  />
                  <p className="text-xs text-muted">{t('slackConfig.appTokenHint')} <code className="bg-neutral-100 px-1.5 py-0.5 rounded text-text">xapp-</code></p>
                </div>

                <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-sm text-yellow-800">
                  <strong>{t('slackConfig.important')}:</strong> {t('slackConfig.step3Warning')}
                </div>
              </div>
            )}
          </div>

          {/* Step 4: Validate & Invite */}
          <div className="bg-panel border border-border rounded-xl overflow-hidden">
            <StepHeader 
              step={4} 
              title={t('slackConfig.step4Title')} 
              icon={<Hash size={16} className="text-accent" />}
              completed={authResult?.ok}
            />
            {expandedSteps[4] && (
              <div className="p-4 space-y-4 border-t border-border">
                <p className="text-sm text-muted">{t('slackConfig.step4Description')}</p>

                <div className="flex items-center gap-3">
                  <button
                    onClick={runAuthTest}
                    disabled={!botToken || !appToken || checking}
                    className="px-4 py-2 bg-accent text-white rounded-lg flex items-center gap-2 transition-colors font-medium shadow-sm hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {checking ? <RefreshCw size={16} className="animate-spin" /> : <Shield size={16} />}
                    {t('slackConfig.validateTokens')}
                  </button>
                  {authResult && (
                    <span className={clsx(
                      'flex items-center gap-2 text-sm font-medium px-3 py-1.5 rounded-lg border',
                      authResult.ok ? 'text-success bg-success/10 border-success/20' : 'text-danger bg-danger/10 border-danger/20'
                    )}>
                      {authResult.ok ? (
                        <>
                          <Check size={14} />
                          <span>{t('slackConfig.tokenValidated')}</span>
                        </>
                      ) : (
                        <span>{t('slackConfig.authFailed')}: {authResult.error}</span>
                      )}
                    </span>
                  )}
                </div>

                {authResult?.ok && (
                  <div className="space-y-3 pt-2">
                    <p className="text-sm font-medium text-text">{t('slackConfig.inviteBotTitle')}</p>
                    <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                      <li>{t('slackConfig.step4Item1')}</li>
                      <li>{t('slackConfig.step4Item2')}</li>
                      <li>{t('slackConfig.step4Item3')}</li>
                    </ol>
                    <div className="bg-green-50 border border-green-200 rounded-lg p-3 text-sm text-green-800">
                      <strong>{t('slackConfig.tip')}:</strong> {t('slackConfig.step4Tip')}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      <div className="mt-auto flex justify-between pt-6 border-t border-border">
        <button
          onClick={onBack}
          className="px-6 py-2 text-muted hover:text-text font-medium transition-colors"
        >
          {t('common.back')}
        </button>
        <button
          onClick={() => onNext({ slack: { ...data.slack, bot_token: botToken, app_token: appToken }, mode })}
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
