import React, { useEffect, useMemo, useState } from 'react';
import { Shield, RefreshCw, Check, Server, KeyRound, Plus, ExternalLink, Settings, ChevronDown, ChevronUp, Copy } from 'lucide-react';
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
  const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({ 1: true, 2: false, 3: false, 4: false });
  const [inviteCopied, setInviteCopied] = useState(false);
  const [clientId, setClientId] = useState(data.discord_client_id || '');

  const inviteUrl = useMemo(() => {
    const normalized = clientId.trim();
    if (!normalized) return '';
    const scope = encodeURIComponent('bot applications.commands');
    return `https://discord.com/oauth2/authorize?client_id=${normalized}&permissions=534723808320&integration_type=0&scope=${scope}`;
  }, [clientId]);

  useEffect(() => {
    setAuthResult(null);
  }, [botToken]);

  useEffect(() => {
    if (botToken && !expandedSteps[4]) {
      setExpandedSteps(prev => ({ ...prev, 4: true }));
    }
  }, [botToken, expandedSteps]);

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

  const toggleStep = (step: number) => {
    setExpandedSteps(prev => ({ ...prev, [step]: !prev[step] }));
  };

  const openDiscordDeveloperPortal = () => {
    window.open('https://discord.com/developers/applications', '_blank');
  };

  const copyInviteUrl = async () => {
    if (!inviteUrl) return;
    try {
      await navigator.clipboard.writeText(inviteUrl);
      setInviteCopied(true);
      setTimeout(() => setInviteCopied(false), 2000);
    } catch {
      setInviteCopied(false);
    }
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
        <h2 className="text-3xl font-display font-bold text-text">{t('discordConfig.title')}</h2>
        <p className="text-muted mt-1">{t('discordConfig.subtitle')}</p>
      </div>

      <div className="space-y-3 overflow-y-auto flex-1 pr-1">
        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader
            step={1}
            title={t('discordConfig.step1Title')}
            icon={<Plus size={16} className="text-accent" />}
          />
          {expandedSteps[1] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('discordConfig.step1Description')}</p>
              <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                <li>{t('discordConfig.step1Item1')}</li>
                <li>{t('discordConfig.step1Item2')}</li>
                <li>{t('discordConfig.step1Item3')}</li>
              </ol>
              <div>
                <button
                  onClick={openDiscordDeveloperPortal}
                  className="flex items-center gap-2 px-4 py-2 bg-accent text-white rounded-lg hover:bg-accent/90 transition-colors font-medium shadow-sm"
                >
                  <ExternalLink size={16} />
                  {t('discordConfig.openDeveloperPortal')}
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader
            step={2}
            title={t('discordConfig.step2Title')}
            icon={<Settings size={16} className="text-accent" />}
          />
          {expandedSteps[2] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('discordConfig.step2Description')}</p>
              <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                <li>{t('discordConfig.step2Item1')}</li>
                <li>{t('discordConfig.step2Item2')}</li>
              </ol>
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-sm text-blue-800">
                <strong>{t('slackConfig.tip')}:</strong> {t('discordConfig.step2Tip')}
              </div>
            </div>
          )}
        </div>

        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader
            step={3}
            title={t('discordConfig.step3Title')}
            icon={<ExternalLink size={16} className="text-accent" />}
          />
          {expandedSteps[3] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('discordConfig.step3Description')}</p>
              <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                <li>{t('discordConfig.step3Item1')}</li>
                <li>{t('discordConfig.step3Item2')}</li>
                <li>{t('discordConfig.step3Item3')}</li>
                <li>{t('discordConfig.step3Item4')}</li>
              </ol>
              <div className="space-y-2">
                <label className="text-sm font-medium text-text">{t('discordConfig.clientId')}</label>
                <input
                  type="text"
                  value={clientId}
                  onChange={(e) => setClientId(e.target.value)}
                  placeholder={t('discordConfig.clientIdPlaceholder')}
                  className="w-full bg-bg border border-border rounded-lg p-3 text-text focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent font-mono transition-colors"
                />
                <p className="text-xs text-muted">{t('discordConfig.clientIdHint')}</p>
              </div>
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-muted uppercase">{t('discordConfig.inviteUrlLabel')}</span>
                  <button
                    onClick={copyInviteUrl}
                    disabled={!inviteUrl}
                    className="flex items-center gap-1 text-xs text-accent hover:text-accent/80 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {inviteCopied ? <Check size={12} /> : <Copy size={12} />}
                    {inviteCopied ? t('discordConfig.inviteUrlCopied') : t('discordConfig.inviteUrlCopy')}
                  </button>
                </div>
                <input
                  type="text"
                  value={inviteUrl}
                  readOnly
                  placeholder={t('discordConfig.inviteUrlPlaceholder')}
                  className="w-full bg-white border border-border rounded-md p-2 text-xs font-mono text-text"
                />
              </div>
              <div>
                <button
                  onClick={() => inviteUrl && window.open(inviteUrl, '_blank')}
                  disabled={!inviteUrl}
                  className="flex items-center gap-2 px-4 py-2 bg-accent text-white rounded-lg hover:bg-accent/90 transition-colors font-medium shadow-sm disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <ExternalLink size={16} />
                  {t('discordConfig.openInviteUrl')}
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="bg-panel border border-border rounded-xl overflow-hidden">
          <StepHeader
            step={4}
            title={t('discordConfig.step4Title')}
            icon={<KeyRound size={16} className="text-accent" />}
            completed={isValid}
          />
          {expandedSteps[4] && (
            <div className="p-4 space-y-4 border-t border-border">
              <p className="text-sm text-muted">{t('discordConfig.step4Description')}</p>
              <ol className="list-decimal list-inside space-y-1.5 text-sm text-muted pl-1">
                <li>{t('discordConfig.step4Item1')}</li>
                <li>{t('discordConfig.step4Item2')}</li>
                <li>{t('discordConfig.step4Item3')}</li>
              </ol>

              <div className="space-y-2 pt-2">
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
          )}
        </div>
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
