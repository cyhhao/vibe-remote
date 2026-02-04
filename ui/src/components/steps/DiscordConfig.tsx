import React, { useEffect, useMemo, useState } from 'react';
import { Shield, RefreshCw, Check, Server, KeyRound } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';

interface DiscordConfigProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

export const DiscordConfig: React.FC<DiscordConfigProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const api = useApi();
  const [botToken, setBotToken] = useState(data.discord?.bot_token || '');
  const [checking, setChecking] = useState(false);
  const [authResult, setAuthResult] = useState<any>(null);
  const [guilds, setGuilds] = useState<any[]>([]);
  const [selectedGuild, setSelectedGuild] = useState<string>(data.discord?.guild_allowlist?.[0] || '');

  const isValid = useMemo(() => botToken.length > 0 && authResult?.ok, [botToken, authResult]);

  const runAuthTest = async () => {
    setChecking(true);
    try {
      const result = await api.discordAuthTest(botToken);
      setAuthResult(result);
    } catch (err: any) {
      setAuthResult({ ok: false, error: err?.message || 'Request failed' });
    } finally {
      setChecking(false);
    }
  };

  const loadGuilds = async () => {
    if (!botToken) return;
    try {
      const result = await api.discordGuilds(botToken);
      if (result.ok) {
        setGuilds(result.guilds || []);
      }
    } catch {
      // ignore
    }
  };

  useEffect(() => {
    if (authResult?.ok) {
      loadGuilds();
    }
  }, [authResult?.ok]);

  return (
    <div className="flex flex-col h-full max-w-2xl mx-auto">
      <div className="mb-4">
        <h2 className="text-3xl font-display font-bold text-text">{t('discordConfig.title')}</h2>
        <p className="text-muted mt-1">{t('discordConfig.subtitle')}</p>
      </div>

      <div className="space-y-6 flex-1">
        <div className="space-y-2">
          <label className="text-sm font-medium text-text flex items-center gap-2">
            <KeyRound size={16} className="text-accent" /> {t('discordConfig.botToken')}
          </label>
          <input
            type="password"
            value={botToken}
            onChange={(e) => setBotToken(e.target.value)}
            placeholder={t('discordConfig.botTokenPlaceholder')}
            className="w-full bg-bg border border-border rounded-lg p-3 text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent font-mono transition-colors"
          />
          <p className="text-xs text-muted">{t('discordConfig.botTokenHint')}</p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={runAuthTest}
            disabled={!botToken || checking}
            className="px-4 py-2 bg-accent text-white rounded-lg flex items-center gap-2 transition-colors font-medium shadow-sm hover:bg-accent/90 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {checking ? <RefreshCw size={16} className="animate-spin" /> : <Shield size={16} />}
            {t('discordConfig.validateToken')}
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
                  <span>{t('discordConfig.tokenValidated')}</span>
                </>
              ) : (
                <span>{t('discordConfig.authFailed')}: {authResult.error}</span>
              )}
            </span>
          )}
        </div>

        {authResult?.ok && (
          <div className="space-y-2">
            <label className="text-sm font-medium text-text flex items-center gap-2">
              <Server size={16} className="text-accent" /> {t('discordConfig.guild')}
            </label>
            <select
              value={selectedGuild}
              onChange={(e) => setSelectedGuild(e.target.value)}
              className="w-full bg-bg border border-border rounded-lg p-3 text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent"
            >
              <option value="">{t('discordConfig.guildPlaceholder')}</option>
              {guilds.map((g) => (
                <option key={g.id} value={g.id}>{g.name}</option>
              ))}
            </select>
            <p className="text-xs text-muted">{t('discordConfig.guildHint')}</p>
          </div>
        )}
      </div>

      <div className="mt-auto flex justify-between pt-6 border-t border-border">
        <button onClick={onBack} className="px-6 py-2 text-muted hover:text-text font-medium transition-colors">
          {t('common.back')}
        </button>
        <button
          onClick={() => onNext({
            platform: 'discord',
            discord: {
              ...(data.discord || {}),
              bot_token: botToken,
              guild_allowlist: selectedGuild ? [selectedGuild] : [],
            },
          })}
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
