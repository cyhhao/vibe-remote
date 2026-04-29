import React, { useEffect, useMemo, useState } from 'react';
import {
  Shield,
  RefreshCw,
  Check,
  Server,
  KeyRound,
  Plus,
  ExternalLink,
  Settings,
  ChevronDown,
  ChevronUp,
  Copy,
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';
import { useToast } from '../../context/ToastContext';
import { copyTextToClipboard } from '../../lib/utils';
import { EyebrowBadge, WizardCard } from '../visual';

interface DiscordConfigProps {
  data: any;
  onNext: (data: any) => void;
  onBack: () => void;
}

const getDiscordGuildAllowlist = (source: any): string[] => {
  const allowlist = source?.discordGuildAllowlist || source?.guild_allowlist || source?.discord?.guild_allowlist;
  return Array.isArray(allowlist) ? allowlist : [];
};

// Mirrors design.pen XCWAT (Slack creds wizard step) adapted for Discord.
// 920-wide WizardCard, mint eyebrow, accordion rows with mint-bordered active row.
export const DiscordConfig: React.FC<DiscordConfigProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [botToken, setBotToken] = useState(data.discord?.bot_token || '');
  const [checking, setChecking] = useState(false);
  const [authResult, setAuthResult] = useState<any>(null);
  const [guilds, setGuilds] = useState<any[]>([]);
  const [selectedGuilds, setSelectedGuilds] = useState<string[]>(getDiscordGuildAllowlist(data));
  const [guildSelectionTouched, setGuildSelectionTouched] = useState(false);
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
      setExpandedSteps((prev) => ({ ...prev, 4: true }));
    }
  }, [botToken, expandedSteps]);

  const isValid = useMemo(() => {
    if (!botToken) return false;
    if (authResult?.ok) return true;
    return Boolean(data.discord?.bot_token && botToken === data.discord?.bot_token);
  }, [botToken, authResult, data.discord?.bot_token]);

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
    setExpandedSteps((prev) => ({ ...prev, [step]: !prev[step] }));
  };

  const toggleGuild = (guildId: string, checked: boolean) => {
    setGuildSelectionTouched(true);
    setSelectedGuilds((prev) => {
      if (checked) return prev.includes(guildId) ? prev : [...prev, guildId];
      return prev.filter((id) => id !== guildId);
    });
  };

  const selectAllGuilds = () => {
    setGuildSelectionTouched(true);
    setSelectedGuilds(guilds.map((g) => g.id));
  };

  const openDiscordDeveloperPortal = () => {
    window.open('https://discord.com/developers/applications', '_blank');
  };

  const copyInviteUrl = async () => {
    if (!inviteUrl) return;
    const ok = await copyTextToClipboard(inviteUrl);
    if (ok) {
      setInviteCopied(true);
      setTimeout(() => setInviteCopied(false), 2000);
      return;
    }
    setInviteCopied(false);
    showToast(t('common.copyFailed'), 'error');
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
    !!clientId,
    Boolean(authResult?.ok || (data.discord?.bot_token && botToken === data.discord?.bot_token)),
    !!inviteUrl,
    isValid,
  ].filter(Boolean).length;

  return (
    <div className="flex w-full justify-center">
      <WizardCard className="gap-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-2">
            <EyebrowBadge tone="mint">Discord</EyebrowBadge>
            <h2 className="text-[28px] font-bold leading-tight tracking-[-0.4px] text-foreground">
              {t('discordConfig.title')}
            </h2>
            <p className="max-w-[560px] text-[14px] leading-[1.55] text-muted">
              {t('discordConfig.subtitle')}
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
          {/* Step 1: Create application */}
          <StepShell active={expandedSteps[1]}>
            <StepHeader step={1} title={t('discordConfig.step1Title')} icon={<Plus size={16} className="text-cyan" />} />
            {expandedSteps[1] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('discordConfig.step1Description')}</p>
                <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                  <li>{t('discordConfig.step1Item1')}</li>
                  <li>{t('discordConfig.step1Item2')}</li>
                  <li>{t('discordConfig.step1Item3')}</li>
                </ol>
                <button
                  onClick={openDiscordDeveloperPortal}
                  className="inline-flex items-center gap-2 rounded-lg bg-mint px-4 py-2 text-[13px] font-bold text-[#080812] shadow-[0_0_24px_-4px_rgba(91,255,160,0.6)] transition hover:brightness-105"
                >
                  <ExternalLink size={14} strokeWidth={2.25} />
                  {t('discordConfig.openDeveloperPortal')}
                </button>
              </div>
            )}
          </StepShell>

          {/* Step 2: Configure bot */}
          <StepShell active={expandedSteps[2]}>
            <StepHeader step={2} title={t('discordConfig.step2Title')} icon={<Settings size={16} className="text-cyan" />} />
            {expandedSteps[2] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('discordConfig.step2Description')}</p>
                <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                  <li>{t('discordConfig.step2Item1')}</li>
                  <li>{t('discordConfig.step2Item2')}</li>
                </ol>
                <div className="rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2 text-[12px] leading-[1.55] text-cyan">
                  <strong>{t('slackConfig.tip')}:</strong> {t('discordConfig.step2Tip')}
                </div>
              </div>
            )}
          </StepShell>

          {/* Step 3: Invite bot */}
          <StepShell active={expandedSteps[3]}>
            <StepHeader step={3} title={t('discordConfig.step3Title')} icon={<ExternalLink size={16} className="text-cyan" />} />
            {expandedSteps[3] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('discordConfig.step3Description')}</p>
                <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                  <li>{t('discordConfig.step3Item1')}</li>
                  <li>{t('discordConfig.step3Item2')}</li>
                  <li>{t('discordConfig.step3Item3')}</li>
                  <li>{t('discordConfig.step3Item4')}</li>
                </ol>
                <div className="space-y-2">
                  <label className="flex items-center gap-2 text-[12px] font-medium text-foreground">
                    <KeyRound size={14} className="text-cyan" /> {t('discordConfig.clientId')}
                  </label>
                  <input
                    type="text"
                    value={clientId}
                    onChange={(e) => setClientId(e.target.value)}
                    placeholder={t('discordConfig.clientIdPlaceholder')}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2.5 font-mono text-[12px] text-foreground outline-none transition placeholder:text-muted/55 focus:border-cyan focus:ring-1 focus:ring-cyan/40"
                  />
                  <p className="text-[11px] text-muted">{t('discordConfig.clientIdHint')}</p>
                </div>
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.16em] text-muted">
                      {t('discordConfig.inviteUrlLabel')}
                    </span>
                    <button
                      onClick={copyInviteUrl}
                      disabled={!inviteUrl}
                      className="inline-flex items-center gap-1 text-[11px] text-cyan transition hover:text-cyan/80 disabled:cursor-not-allowed disabled:opacity-50"
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
                    className="w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-[11px] text-foreground"
                  />
                </div>
                <button
                  onClick={() => inviteUrl && window.open(inviteUrl, '_blank')}
                  disabled={!inviteUrl}
                  className="inline-flex items-center gap-2 rounded-lg bg-mint px-4 py-2 text-[13px] font-bold text-[#080812] shadow-[0_0_24px_-4px_rgba(91,255,160,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <ExternalLink size={14} strokeWidth={2.25} />
                  {t('discordConfig.openInviteUrl')}
                </button>

                <div className="space-y-2 rounded-lg border border-gold/30 bg-gold/10 px-3 py-2.5">
                  <div className="flex items-center gap-2 text-[12px] font-semibold text-gold">
                    <AlertTriangle size={14} className="text-gold" />
                    {t('discordConfig.dmTroubleshootTitle')}
                  </div>
                  <ol className="list-inside list-decimal space-y-1 pl-1 text-[11px] leading-[1.55] text-gold/90">
                    <li>{t('discordConfig.dmTroubleshoot1')}</li>
                    <li>{t('discordConfig.dmTroubleshoot2')}</li>
                    <li>{t('discordConfig.dmTroubleshoot3')}</li>
                  </ol>
                </div>
              </div>
            )}
          </StepShell>

          {/* Step 4: Validate token */}
          <StepShell active={expandedSteps[4]}>
            <StepHeader
              step={4}
              title={t('discordConfig.step4Title')}
              icon={<KeyRound size={16} className="text-cyan" />}
              completed={isValid}
            />
            {expandedSteps[4] && (
              <div className="space-y-4 border-t border-border px-5 py-4">
                <p className="text-[13px] leading-[1.55] text-muted">{t('discordConfig.step4Description')}</p>
                <ol className="list-inside list-decimal space-y-1.5 pl-1 text-[12px] leading-[1.55] text-muted">
                  <li>{t('discordConfig.step4Item1')}</li>
                  <li>{t('discordConfig.step4Item2')}</li>
                  <li>{t('discordConfig.step4Item3')}</li>
                </ol>

                <div className="space-y-2 pt-1">
                  <label className="flex items-center gap-2 text-[12px] font-medium text-foreground">
                    <KeyRound size={14} className="text-cyan" /> {t('discordConfig.botToken')}
                  </label>
                  <input
                    type="password"
                    value={botToken}
                    onChange={(e) => setBotToken(e.target.value)}
                    placeholder={t('discordConfig.botTokenPlaceholder')}
                    className="w-full rounded-lg border border-border bg-background px-3 py-2.5 font-mono text-[12px] text-foreground outline-none transition placeholder:text-muted/55 focus:border-cyan focus:ring-1 focus:ring-cyan/40"
                  />
                  <p className="text-[11px] text-muted">{t('discordConfig.botTokenHint')}</p>
                </div>

                <div className="flex flex-wrap items-center gap-3">
                  <button
                    onClick={runAuthTest}
                    disabled={!botToken || checking}
                    className="inline-flex items-center gap-2 rounded-lg bg-mint px-4 py-2 text-[13px] font-bold text-[#080812] shadow-[0_0_24px_-4px_rgba(91,255,160,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {checking ? <RefreshCw size={14} className="animate-spin" /> : <Shield size={14} strokeWidth={2.25} />}
                    {t('discordConfig.validateToken')}
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
                        ? t('discordConfig.tokenValidated')
                        : `${t('discordConfig.authFailed')}: ${authResult.error}`}
                    </span>
                  )}
                </div>

                {authResult?.ok && (
                  <div className="space-y-2 pt-1">
                    <label className="flex items-center gap-2 text-[12px] font-medium text-foreground">
                      <Server size={14} className="text-cyan" /> {t('discordConfig.guild')}
                    </label>
                    {guilds.length > 0 && (
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="text-[11px] text-muted">
                          {t('discordConfig.selectedGuilds', { count: selectedGuilds.length })}
                        </span>
                        <div className="flex items-center gap-3">
                          <button
                            type="button"
                            onClick={selectAllGuilds}
                            className="text-[11px] font-medium text-cyan transition hover:text-cyan/80"
                          >
                            {t('discordConfig.selectAllGuilds')}
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              setGuildSelectionTouched(true);
                              setSelectedGuilds([]);
                            }}
                            className="text-[11px] font-medium text-muted transition hover:text-foreground"
                          >
                            {t('discordConfig.clearGuilds')}
                          </button>
                        </div>
                      </div>
                    )}
                    <div className="max-h-52 overflow-y-auto rounded-lg border border-border bg-background">
                      {guilds.length === 0 ? (
                        <div className="px-3 py-2.5 text-[12px] text-muted">{t('discordConfig.guildPlaceholder')}</div>
                      ) : (
                        guilds.map((g) => (
                          <label
                            key={g.id}
                            className="flex items-center gap-3 border-b border-border/70 px-3 py-2 last:border-b-0"
                          >
                            <input
                              type="checkbox"
                              checked={selectedGuilds.includes(g.id)}
                              onChange={(e) => toggleGuild(g.id, e.target.checked)}
                              className="size-4 accent-mint"
                            />
                            <span className="text-[12px] text-foreground">{g.name}</span>
                          </label>
                        ))
                      )}
                    </div>
                    <p className="text-[11px] text-muted">{t('discordConfig.guildHint')}</p>
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
              onNext({
                platform: 'discord',
                discord: {
                  ...(data.discord || {}),
                  bot_token: botToken,
                },
                discordGuildAllowlist: selectedGuilds,
                discordGuildAllowlistTouched: guildSelectionTouched,
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
