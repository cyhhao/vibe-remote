import React, { useCallback, useEffect, useRef, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Check,
  KeyRound,
  Loader2,
  RefreshCw,
  Smartphone,
  Wifi,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';
import { EyebrowBadge, WizardCard } from '../visual';

interface WeChatConfigProps {
  data: Record<string, any>;
  onNext: (data: Record<string, any>) => void;
  onBack: () => void;
}

const QR_POLL_INTERVAL_MS = 5000;

// Mirrors design.pen XCWAT visual treatment for the QR-driven WeChat onboarding.
// Three-stop horizontal stepper, mint-bordered QR card, mint primary actions.
export const WeChatConfig: React.FC<WeChatConfigProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const api = useApi();

  const [loginState, setLoginState] = useState<
    'idle' | 'qr_ready' | 'scanning' | 'confirming' | 'connected' | 'error'
  >('idle');
  const [qrCodeUrl, setQrCodeUrl] = useState<string>('');
  const [message, setMessage] = useState<string>('');
  const [botToken, setBotToken] = useState<string>(data.wechat?.bot_token || '');
  const [baseUrl, setBaseUrl] = useState<string>(data.wechat?.base_url || '');
  const [starting, setStarting] = useState(false);

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoStartedRef = useRef(false);
  const activeSessionKeyRef = useRef<string | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, [stopPolling]);

  const startLogin = useCallback(async () => {
    setStarting(true);
    setLoginState('idle');
    setMessage('');
    setQrCodeUrl('');
    setBotToken('');
    setBaseUrl('');
    activeSessionKeyRef.current = null;
    stopPolling();

    try {
      let result = await api.wechatStartLogin();
      if (result?.error) {
        await new Promise((resolve) => setTimeout(resolve, 800));
        result = await api.wechatStartLogin();
      }
      if (result.error) {
        setLoginState('error');
        setMessage(result.error);
        return;
      }
      setQrCodeUrl(result.qrcode_url || '');
      setMessage(result.message || '');
      setLoginState('qr_ready');

      if (result.session_key) {
        activeSessionKeyRef.current = result.session_key;
        startPolling(result.session_key);
      }
    } catch (err: any) {
      try {
        await new Promise((resolve) => setTimeout(resolve, 800));
        const retryResult = await api.wechatStartLogin();
        if (retryResult?.error) {
          setLoginState('error');
          setMessage(retryResult.error);
          return;
        }
        setQrCodeUrl(retryResult.qrcode_url || '');
        setMessage(retryResult.message || '');
        setLoginState('qr_ready');
        if (retryResult.session_key) {
          activeSessionKeyRef.current = retryResult.session_key;
          startPolling(retryResult.session_key);
        }
      } catch (retryErr: any) {
        setLoginState('error');
        setMessage(retryErr?.message || err?.message || t('wechatConfig.startFailed'));
      }
    } finally {
      setStarting(false);
    }
  }, [api, stopPolling, t]);

  useEffect(() => {
    if (autoStartedRef.current) return;
    if (starting) return;
    if (loginState !== 'idle') return;
    if (data.wechat?.bot_token) return;

    autoStartedRef.current = true;
    void startLogin();
  }, [loginState, startLogin, starting, data.wechat?.bot_token]);

  const startPolling = (key: string) => {
    stopPolling();
    const pollOnce = async () => {
      try {
        const result = await api.wechatPollLogin(key);
        if (!result || activeSessionKeyRef.current !== key) return;

        const status = result.status;
        if (status === 'scaned') {
          setLoginState('confirming');
          setMessage(result.message || t('wechatConfig.confirmOnPhone'));
        } else if (status === 'confirmed') {
          setLoginState('connected');
          setMessage(result.message || t('wechatConfig.connected'));
          setBotToken(result.bot_token || '');
          setBaseUrl(result.base_url || '');
          activeSessionKeyRef.current = null;
          stopPolling();
          return;
        } else if (status === 'refreshed') {
          setLoginState('qr_ready');
          setQrCodeUrl(result.qrcode_url || '');
          setMessage(result.message || t('wechatConfig.qrExpired'));
        } else if (status === 'expired') {
          setLoginState('error');
          setMessage(result.message || t('wechatConfig.qrExpired'));
          activeSessionKeyRef.current = null;
          stopPolling();
          return;
        } else if (status === 'error') {
          setLoginState('error');
          setMessage(result.message || t('wechatConfig.pollError'));
          activeSessionKeyRef.current = null;
          stopPolling();
          return;
        }
        pollTimerRef.current = setTimeout(() => {
          void pollOnce();
        }, QR_POLL_INTERVAL_MS);
      } catch {
        if (activeSessionKeyRef.current !== key) return;
        pollTimerRef.current = setTimeout(() => {
          void pollOnce();
        }, QR_POLL_INTERVAL_MS);
      }
    };

    void pollOnce();
  };

  const canProceed = !!botToken;
  const isAlreadyBound = loginState === 'idle' && !!botToken;

  const getStepState = () => {
    if (isAlreadyBound) return { step: 3, scanning: false, connected: true };
    if (loginState === 'idle') return { step: 1, scanning: false, connected: false };
    if (loginState === 'qr_ready') return { step: 2, scanning: false, connected: false };
    if (loginState === 'scanning' || loginState === 'confirming') return { step: 2, scanning: true, connected: false };
    if (loginState === 'connected') return { step: 3, scanning: false, connected: true };
    return { step: 1, scanning: false, connected: false };
  };

  const { step, connected } = getStepState();

  const completedDots = connected ? 3 : step;

  const stepLabels = [t('wechatConfig.stepStart'), t('wechatConfig.stepScan'), t('wechatConfig.stepDone')];

  return (
    <div className="flex w-full justify-center">
      <WizardCard className="gap-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="space-y-2">
            <EyebrowBadge tone="mint">WeChat</EyebrowBadge>
            <h2 className="text-[28px] font-bold leading-tight tracking-[-0.4px] text-foreground">
              {t('wechatConfig.title')}
            </h2>
            <p className="max-w-[560px] text-[14px] leading-[1.55] text-muted">
              {t('wechatConfig.subtitle')}
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-full border border-border bg-white/[0.04] px-3 py-1.5">
            <span className="font-mono text-[11px] font-bold uppercase tracking-[0.16em] text-mint">
              {completedDots} / 3
            </span>
            <div className="flex gap-1">
              {[0, 1, 2].map((i) => (
                <span
                  key={i}
                  className={clsx(
                    'h-1 w-6 rounded-full',
                    i < completedDots ? 'bg-mint shadow-[0_0_8px_rgba(91,255,160,0.6)]' : 'bg-white/[0.08]'
                  )}
                />
              ))}
            </div>
          </div>
        </div>

        {/* Horizontal stepper */}
        <div className="rounded-xl border border-border bg-background px-5 py-4">
          <div className="flex items-center justify-between gap-3">
            {stepLabels.map((label, idx) => {
              const num = idx + 1;
              const isCompleted = num < step || (connected && num <= 3);
              const isActive = !connected && num === step && loginState !== 'error';
              return (
                <React.Fragment key={label}>
                  <div className="flex items-center gap-2">
                    <span
                      className={clsx(
                        'flex size-7 items-center justify-center rounded-full text-[12px] font-bold transition-colors',
                        isCompleted
                          ? 'bg-mint text-[#080812]'
                          : isActive
                            ? 'bg-cyan/15 text-cyan'
                            : 'bg-white/[0.06] text-muted'
                      )}
                    >
                      {isCompleted ? <Check size={14} /> : num}
                    </span>
                    <span
                      className={clsx(
                        'text-[12px] font-semibold',
                        isCompleted || isActive ? 'text-foreground' : 'text-muted'
                      )}
                    >
                      {label}
                    </span>
                  </div>
                  {idx < stepLabels.length - 1 && <span className="mx-2 h-px flex-1 bg-border" />}
                </React.Fragment>
              );
            })}
          </div>
        </div>

        <div className="space-y-4">
          {/* Already bound */}
          {isAlreadyBound && (
            <div className="rounded-xl border border-border bg-background px-6 py-6">
              <div className="flex flex-col items-center gap-4 text-center">
                <div className="flex size-16 items-center justify-center rounded-full border border-mint/30 bg-mint/[0.08] text-mint shadow-[0_0_32px_-6px_rgba(91,255,160,0.5)]">
                  <Check size={32} />
                </div>
                <div>
                  <h3 className="text-[16px] font-semibold text-foreground">{t('wechatConfig.alreadyBound')}</h3>
                  <p className="mt-1 text-[12px] text-muted">{t('wechatConfig.alreadyBoundDesc')}</p>
                </div>
                <div className="w-full max-w-md rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-left">
                  <div className="mb-1 flex items-center gap-1 text-[11px] text-muted">
                    <KeyRound size={12} /> Token
                  </div>
                  <div className="truncate font-mono text-[12px] text-foreground">
                    {botToken.slice(0, 12)}
                    {'•'.repeat(16)}
                  </div>
                </div>
                <button
                  onClick={() => {
                    autoStartedRef.current = true;
                    void startLogin();
                  }}
                  disabled={starting}
                  className="inline-flex items-center gap-2 rounded-lg border border-border bg-white/[0.04] px-4 py-2 text-[12px] font-medium text-foreground transition hover:border-border-strong disabled:opacity-50"
                >
                  <RefreshCw size={14} className={starting ? 'animate-spin' : ''} />
                  {t('wechatConfig.rebind')}
                </button>
              </div>
            </div>
          )}

          {/* Starting */}
          {loginState === 'idle' && !botToken && (
            <div className="rounded-xl border border-border bg-background px-6 py-8 text-center">
              <div className="mx-auto flex size-14 items-center justify-center rounded-full border border-cyan/30 bg-cyan/[0.06] text-cyan">
                <Loader2 size={26} className="animate-spin" />
              </div>
              <p className="mt-3 text-[13px] text-muted">
                {starting ? t('wechatConfig.starting') : t('wechatConfig.startDescription')}
              </p>
            </div>
          )}

          {/* QR */}
          {(loginState === 'qr_ready' || loginState === 'scanning' || loginState === 'confirming') && (
            <div className="rounded-xl border border-mint/35 bg-surface-2 px-6 py-6 shadow-[0_8px_32px_-8px_rgba(91,255,160,0.078)]">
              <div className="flex flex-col items-center gap-4">
                <div className="rounded-xl border border-border bg-white p-4 shadow-[0_0_24px_-4px_rgba(91,255,160,0.4)]">
                  <QRCodeSVG value={qrCodeUrl} size={224} level="M" includeMargin={false} />
                </div>
                <div
                  className={clsx(
                    'inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-[12px] font-medium',
                    loginState === 'qr_ready' && 'border-cyan/30 bg-cyan/[0.06] text-cyan',
                    (loginState === 'scanning' || loginState === 'confirming') &&
                      'border-gold/30 bg-gold/10 text-gold'
                  )}
                >
                  {loginState === 'qr_ready' && (
                    <>
                      <Loader2 size={14} className="animate-spin" />
                      {t('wechatConfig.waitingForScan')}
                    </>
                  )}
                  {(loginState === 'scanning' || loginState === 'confirming') && (
                    <>
                      <Smartphone size={14} />
                      {t('wechatConfig.confirmOnPhone')}
                    </>
                  )}
                </div>
                <p className="text-center text-[11px] text-muted">{t('wechatConfig.scanHint')}</p>
              </div>
            </div>
          )}

          {/* Connected */}
          {loginState === 'connected' && (
            <div className="rounded-xl border border-mint/35 bg-surface-2 px-6 py-6 shadow-[0_8px_32px_-8px_rgba(91,255,160,0.078)]">
              <div className="flex flex-col items-center gap-4 text-center">
                <div className="flex size-16 items-center justify-center rounded-full border border-mint/30 bg-mint/[0.08] text-mint shadow-[0_0_32px_-6px_rgba(91,255,160,0.5)]">
                  <Check size={32} />
                </div>
                <div>
                  <h3 className="text-[16px] font-semibold text-foreground">{t('wechatConfig.connectedTitle')}</h3>
                  <p className="mt-1 text-[12px] text-muted">{message}</p>
                </div>
                <div className="inline-flex items-center gap-2 rounded-lg border border-mint/30 bg-mint/[0.08] px-3 py-1.5 text-[12px] font-medium text-mint">
                  <Wifi size={14} />
                  {t('wechatConfig.connectionEstablished')}
                </div>
                <div className="w-full max-w-md rounded-lg border border-cyan/30 bg-cyan/[0.06] px-3 py-2.5 text-left">
                  <div className="text-[12px] font-semibold text-foreground">{t('wechatConfig.nextStepTitle')}</div>
                  <p className="mt-0.5 text-[11px] text-muted">{t('wechatConfig.nextStepDesc')}</p>
                </div>
              </div>
            </div>
          )}

          {/* Error */}
          {loginState === 'error' && (
            <div className="rounded-xl border border-danger/30 bg-danger/10 px-6 py-6">
              <div className="flex flex-col items-center gap-4 text-center">
                <div className="flex size-14 items-center justify-center rounded-full border border-danger/30 bg-danger/15 text-danger">
                  <AlertTriangle size={28} />
                </div>
                <div>
                  <h3 className="text-[14px] font-semibold text-foreground">{t('wechatConfig.errorTitle')}</h3>
                  <p className="mt-1 text-[12px] text-danger">{message}</p>
                </div>
                <button
                  onClick={startLogin}
                  disabled={starting}
                  className="inline-flex items-center gap-2 rounded-lg bg-mint px-4 py-2 text-[13px] font-bold text-[#080812] shadow-[0_0_24px_-4px_rgba(91,255,160,0.6)] transition hover:brightness-105 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <RefreshCw size={14} strokeWidth={2.25} />
                  {t('wechatConfig.retry')}
                </button>
              </div>
            </div>
          )}
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
                platform: 'wechat',
                wechat: {
                  ...(data.wechat || {}),
                  bot_token: botToken,
                  base_url: baseUrl,
                },
              })
            }
            disabled={!canProceed}
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
