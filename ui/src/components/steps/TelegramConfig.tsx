import React, { useMemo, useState } from 'react';
import { Check, KeyRound, MessageSquare, RefreshCw, Shield, SplitSquareVertical } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';

interface TelegramConfigProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

export const TelegramConfig: React.FC<TelegramConfigProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const api = useApi();
  const [botToken, setBotToken] = useState(data.telegram?.bot_token || '');
  const [requireMention, setRequireMention] = useState(data.telegram?.require_mention ?? true);
  const [forumAutoTopic, setForumAutoTopic] = useState(data.telegram?.forum_auto_topic ?? true);
  const [checking, setChecking] = useState(false);
  const [authResult, setAuthResult] = useState<any>(null);

  const isValid = useMemo(() => {
    if (!botToken) return false;
    if (authResult?.ok) return true;
    return Boolean(data.telegram?.bot_token && botToken === data.telegram?.bot_token);
  }, [authResult, botToken, data.telegram?.bot_token]);

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

  return (
    <div className="flex flex-col h-full max-w-2xl mx-auto">
      <div className="mb-6">
        <h2 className="text-3xl font-display font-bold text-text">{t('telegramConfig.title')}</h2>
        <p className="text-muted mt-1">{t('telegramConfig.subtitle')}</p>
      </div>

      <div className="space-y-4 overflow-y-auto flex-1 pr-1">
        <div className="bg-panel border border-border rounded-xl p-5 space-y-4">
          <div>
            <div className="text-sm font-medium text-text flex items-center gap-2">
              <MessageSquare size={16} className="text-accent" />
              {t('telegramConfig.buttonFirstTitle')}
            </div>
            <p className="text-sm text-muted mt-2">{t('telegramConfig.buttonFirstDesc')}</p>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div className="rounded-lg border border-border bg-bg p-4">
              <div className="text-sm font-medium text-text">{t('telegramConfig.sessionPlainTitle')}</div>
              <p className="text-xs text-muted mt-2">{t('telegramConfig.sessionPlainDesc')}</p>
            </div>
            <div className="rounded-lg border border-border bg-bg p-4">
              <div className="text-sm font-medium text-text">{t('telegramConfig.sessionForumTitle')}</div>
              <p className="text-xs text-muted mt-2">{t('telegramConfig.sessionForumDesc')}</p>
            </div>
          </div>
        </div>

        <div className="bg-panel border border-border rounded-xl p-5 space-y-4">
          <label className="text-sm font-medium text-text flex items-center gap-2">
            <KeyRound size={16} className="text-accent" />
            {t('telegramConfig.botToken')}
          </label>
          <input
            type="password"
            value={botToken}
            onChange={(e) => {
              setBotToken(e.target.value);
              setAuthResult(null);
            }}
            placeholder={t('telegramConfig.botTokenPlaceholder')}
            className="w-full bg-bg border border-border rounded-lg p-3 text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent font-mono transition-colors"
          />
          <p className="text-xs text-muted">{t('telegramConfig.botTokenHint')}</p>

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

        <div className="bg-panel border border-border rounded-xl p-5 space-y-4">
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
