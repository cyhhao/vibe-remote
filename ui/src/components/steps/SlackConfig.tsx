import React, { useMemo, useState } from 'react';
import { Lock, Shield, RefreshCw } from 'lucide-react';
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
  const [mode] = useState<'self_host' | 'saas'>(data.mode || 'saas'); // Default to saas if undefined, though Mode step should set it
  const [checking, setChecking] = useState(false);
  const [authResult, setAuthResult] = useState<any>(null);

  const isValid = useMemo(() => {
    if (mode === 'saas') return true; // For now, SaaS assumes valid if we proceed
    return botToken.startsWith('xoxb-') && appToken.startsWith('xapp-');
  }, [mode, botToken, appToken]);

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

  return (
    <div className="flex flex-col h-full max-w-2xl mx-auto">
      <h2 className="text-3xl font-display font-bold mb-2 text-text">{t('slackConfig.title')}</h2>
      <p className="text-muted mb-8">
        {mode === 'saas'
          ? t('slackConfig.saasDescription')
          : t('slackConfig.selfHostDescription')}
      </p>

      {mode === 'saas' ? (
        <div className="flex flex-col items-center justify-center h-64 border-2 border-dashed border-border rounded-xl bg-neutral-50">
          <p className="text-muted mb-6 text-center max-w-xs">{t('slackConfig.oauthPrompt')}</p>
          <button className="px-6 py-3 bg-accent text-white rounded-lg font-medium hover:bg-accent/90 transition-colors shadow-sm">
            {t('slackConfig.openSlackOAuth')}
          </button>
          <p className="text-xs text-muted mt-6">{t('slackConfig.afterAuth')}</p>
        </div>
      ) : (
        <div className="space-y-6">
          <div className="space-y-2">
            <label className="text-sm font-medium text-text flex items-center gap-2">
              <Shield size={16} className="text-accent" /> {t('slackConfig.botToken')}
            </label>
            <input
              type="password"
              value={botToken}
              onChange={(e) => setBotToken(e.target.value)}
              placeholder="xoxb-..."
              className="w-full bg-bg border border-border rounded-lg p-3 text-text focus:outline-none focus:border-accent font-mono transition-colors shadow-sm"
            />
            <p className="text-xs text-muted pl-1">{t('slackConfig.botTokenHint')} <code className="bg-neutral-100 px-1 rounded">xoxb-</code></p>
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium text-text flex items-center gap-2">
              <Lock size={16} className="text-accent" /> {t('slackConfig.appToken')}
            </label>
            <input
              type="password"
              value={appToken}
              onChange={(e) => setAppToken(e.target.value)}
              placeholder="xapp-..."
              className="w-full bg-bg border border-border rounded-lg p-3 text-text focus:outline-none focus:border-accent font-mono transition-colors shadow-sm"
            />
             <p className="text-xs text-muted pl-1">{t('slackConfig.botTokenHint')} <code className="bg-neutral-100 px-1 rounded">xapp-</code></p>
          </div>

          <div className="flex items-center gap-3 pt-2">
            <button
              onClick={runAuthTest}
              className="px-4 py-2 bg-neutral-100 hover:bg-neutral-200 text-text rounded-lg flex items-center gap-2 transition-colors font-medium border border-border"
            >
              {checking ? <RefreshCw size={16} className="animate-spin" /> : <RefreshCw size={16} />}
              {t('slackConfig.validateTokens')}
            </button>
            {authResult && (
              <span className={clsx('flex items-center gap-2 text-sm font-medium px-3 py-1.5 rounded-lg border', authResult.ok ? 'text-success bg-success/10 border-success/20' : 'text-danger bg-danger/10 border-danger/20')}>
                {authResult.ok ? (
                    <>
                        <Shield size={14} />
                        <span>{t('slackConfig.tokenValidated')}</span>
                    </>
                ) : (
                    <>
                         <Shield size={14} />
                         <span>{t('slackConfig.authFailed')}: {authResult.error}</span>
                    </>
                )}
              </span>
            )}
          </div>
        </div>
      )}

      <div className="mt-auto flex justify-between pt-6">
        <button
          onClick={onBack}
          className="px-6 py-2 text-muted hover:text-text font-medium transition-colors"
        >
          {t('common.back')}
        </button>
        <button
          onClick={() => onNext({ slack: { bot_token: botToken, app_token: appToken }, mode })}
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
