import React, { useCallback, useEffect, useRef, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { Check, RefreshCw, Smartphone, Wifi, AlertTriangle, Loader2, KeyRound } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { useApi } from '../../context/ApiContext';

interface WeChatConfigProps {
  data: Record<string, any>;
  onNext: (data: Record<string, any>) => void;
  onBack: () => void;
}

const QR_POLL_INTERVAL_MS = 5000;

export const WeChatConfig: React.FC<WeChatConfigProps> = ({ data, onNext, onBack }) => {
  const { t } = useTranslation();
  const api = useApi();

  const [loginState, setLoginState] = useState<'idle' | 'qr_ready' | 'scanning' | 'confirming' | 'connected' | 'error'>('idle');
  const [qrCodeUrl, setQrCodeUrl] = useState<string>('');
  const [message, setMessage] = useState<string>('');
  const [botToken, setBotToken] = useState<string>(data.wechat?.bot_token || '');
  const [baseUrl, setBaseUrl] = useState<string>(data.wechat?.base_url || '');
  const [starting, setStarting] = useState(false);

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoStartedRef = useRef(false);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  // Cleanup on unmount
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

      // Start polling
      if (result.session_key) {
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
    // Don't auto-start QR flow if we already have a token from previous config
    if (data.wechat?.bot_token) return;

    autoStartedRef.current = true;
    void startLogin();
  }, [loginState, startLogin, starting, data.wechat?.bot_token]);

  const startPolling = (key: string) => {
    stopPolling();
    const pollOnce = async () => {
      try {
        const result = await api.wechatPollLogin(key);
        if (!result) return;

        const status = result.status;
        if (status === 'scaned') {
          setLoginState('confirming');
          setMessage(result.message || t('wechatConfig.confirmOnPhone'));
        } else if (status === 'confirmed') {
          setLoginState('connected');
          setMessage(result.message || t('wechatConfig.connected'));
          setBotToken(result.bot_token || '');
          setBaseUrl(result.base_url || '');
          stopPolling();
        } else if (status === 'expired') {
          setLoginState('error');
          setMessage(result.message || t('wechatConfig.qrExpired'));
          stopPolling();
        } else if (status === 'error') {
          setLoginState('error');
          setMessage(result.message || t('wechatConfig.pollError'));
          stopPolling();
          return;
        }
        // status === 'wait' — schedule next poll after the server-side long poll window
        pollTimerRef.current = setTimeout(() => {
          void pollOnce();
        }, QR_POLL_INTERVAL_MS);
      } catch {
        pollTimerRef.current = setTimeout(() => {
          void pollOnce();
        }, QR_POLL_INTERVAL_MS);
      }
    };

    void pollOnce();
  };

  // Allow proceeding if we have a token (either freshly scanned or from existing config)
  const canProceed = !!botToken;
  // Whether we're showing the "already bound" idle state (existing token, QR not started)
  const isAlreadyBound = loginState === 'idle' && !!botToken;

  const stepIndicator = (stepNum: number, label: string, active: boolean, completed: boolean) => (
    <div className="flex items-center gap-2">
      <span
        className={clsx(
          'w-7 h-7 rounded-full text-sm font-bold flex items-center justify-center transition-colors',
          completed ? 'bg-success text-white' : active ? 'bg-accent text-white' : 'bg-neutral-200 text-muted'
        )}
      >
        {completed ? <Check size={14} /> : stepNum}
      </span>
      <span className={clsx('text-sm font-medium', active || completed ? 'text-text' : 'text-muted')}>{label}</span>
    </div>
  );

  const getStepState = () => {
    if (isAlreadyBound) return { step: 3, scanning: false, connected: true };
    if (loginState === 'idle') return { step: 1, scanning: false, connected: false };
    if (loginState === 'qr_ready') return { step: 2, scanning: false, connected: false };
    if (loginState === 'scanning' || loginState === 'confirming') return { step: 2, scanning: true, connected: false };
    if (loginState === 'connected') return { step: 3, scanning: false, connected: true };
    return { step: 1, scanning: false, connected: false }; // error
  };

  const { step, connected } = getStepState();

  return (
    <div className="flex flex-col h-full max-w-2xl mx-auto">
      <div className="mb-4">
        <h2 className="text-3xl font-display font-bold text-text">{t('wechatConfig.title')}</h2>
        <p className="text-muted mt-1">{t('wechatConfig.subtitle')}</p>
      </div>

      {/* Step indicators */}
      <div className="mb-6 bg-panel border border-border rounded-xl p-4">
        <div className="flex items-center justify-between">
          {stepIndicator(1, t('wechatConfig.stepStart'), step === 1 && loginState !== 'error', step > 1)}
          <div className="flex-1 h-px bg-border mx-3" />
          {stepIndicator(2, t('wechatConfig.stepScan'), step === 2, step > 2)}
          <div className="flex-1 h-px bg-border mx-3" />
          {stepIndicator(3, t('wechatConfig.stepDone'), false, connected)}
        </div>
      </div>

      <div className="flex-1 space-y-4 overflow-y-auto pr-1">
        {/* Already bound — existing token, no QR needed */}
        {isAlreadyBound && (
          <div className="bg-panel border border-border rounded-xl p-6 space-y-4">
            <div className="flex flex-col items-center space-y-4">
              <div className="w-20 h-20 bg-success/10 text-success rounded-full flex items-center justify-center">
                <Check size={40} />
              </div>
              <div className="text-center">
                <h3 className="text-lg font-semibold text-text">{t('wechatConfig.alreadyBound')}</h3>
                <p className="text-sm text-muted mt-1">{t('wechatConfig.alreadyBoundDesc')}</p>
              </div>
              <div className="w-full bg-bg border border-border rounded-lg p-3">
                <div className="text-xs text-muted mb-1 flex items-center gap-1"><KeyRound size={12} /> Token</div>
                <div className="text-sm font-mono text-text truncate">{botToken.slice(0, 12)}{'•'.repeat(16)}</div>
              </div>
              <button
                onClick={() => {
                  autoStartedRef.current = true;
                  void startLogin();
                }}
                disabled={starting}
                className="px-4 py-2 bg-panel border border-border text-text rounded-lg flex items-center gap-2 text-sm font-medium hover:bg-neutral-50 transition-colors disabled:opacity-50"
              >
                <RefreshCw size={14} className={starting ? 'animate-spin' : ''} />
                {t('wechatConfig.rebind')}
              </button>
            </div>
          </div>
        )}

        {/* Start Login — only when no existing token */}
        {loginState === 'idle' && !botToken && (
          <div className="bg-panel border border-border rounded-xl p-6 text-center space-y-4">
            <div className="w-16 h-16 bg-accent/10 text-accent rounded-full flex items-center justify-center mx-auto">
              <Loader2 size={32} className="animate-spin" />
            </div>
            <p className="text-sm text-muted">{starting ? t('wechatConfig.starting') : t('wechatConfig.startDescription')}</p>
          </div>
        )}

        {/* QR Code Display */}
        {(loginState === 'qr_ready' || loginState === 'scanning' || loginState === 'confirming') && (
          <div className="bg-panel border border-border rounded-xl p-6 space-y-4">
            <div className="flex flex-col items-center space-y-4">
              {/* QR Code */}
              <div className="bg-white p-4 rounded-xl shadow-sm border border-neutral-100">
                <QRCodeSVG
                  value={qrCodeUrl}
                  size={256}
                  level="M"
                  includeMargin={false}
                />
              </div>

              {/* Status indicator */}
              <div className={clsx(
                'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium border',
                loginState === 'qr_ready' && 'bg-blue-50 border-blue-200 text-blue-800',
                loginState === 'scanning' && 'bg-amber-50 border-amber-200 text-amber-800',
                loginState === 'confirming' && 'bg-amber-50 border-amber-200 text-amber-800',
              )}>
                {loginState === 'qr_ready' && (
                  <>
                    <Loader2 size={16} className="animate-spin" />
                    {t('wechatConfig.waitingForScan')}
                  </>
                )}
                {(loginState === 'scanning' || loginState === 'confirming') && (
                  <>
                    <Smartphone size={16} />
                    {t('wechatConfig.confirmOnPhone')}
                  </>
                )}
              </div>

              <p className="text-xs text-muted text-center">{t('wechatConfig.scanHint')}</p>
            </div>
          </div>
        )}

        {/* Connected */}
        {loginState === 'connected' && (
          <div className="bg-panel border border-border rounded-xl p-6 space-y-4">
            <div className="flex flex-col items-center space-y-4">
              <div className="w-20 h-20 bg-success/10 text-success rounded-full flex items-center justify-center animate-in fade-in zoom-in duration-300">
                <Check size={40} />
              </div>
              <div className="text-center">
                <h3 className="text-lg font-semibold text-text">{t('wechatConfig.connectedTitle')}</h3>
                <p className="text-sm text-muted mt-1">{message}</p>
              </div>
              <div className="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-success/10 border border-success/20 text-success">
                <Wifi size={16} />
                {t('wechatConfig.connectionEstablished')}
              </div>

              <div className="w-full rounded-xl border border-accent/20 bg-accent/5 px-4 py-3 text-left">
                <div className="text-sm font-semibold text-text">{t('wechatConfig.nextStepTitle')}</div>
                <p className="mt-1 text-sm text-muted">{t('wechatConfig.nextStepDesc')}</p>
              </div>
            </div>
          </div>
        )}

        {/* Error / Retry */}
        {loginState === 'error' && (
          <div className="bg-panel border border-border rounded-xl p-6 space-y-4">
            <div className="flex flex-col items-center space-y-4">
              <div className="w-16 h-16 bg-danger/10 text-danger rounded-full flex items-center justify-center">
                <AlertTriangle size={32} />
              </div>
              <div className="text-center">
                <h3 className="text-lg font-semibold text-text">{t('wechatConfig.errorTitle')}</h3>
                <p className="text-sm text-danger mt-1">{message}</p>
              </div>
              <button
                onClick={startLogin}
                disabled={starting}
                className="px-6 py-3 bg-accent text-white rounded-lg flex items-center gap-2 font-medium shadow-sm hover:bg-accent/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <RefreshCw size={16} />
                {t('wechatConfig.retry')}
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="mt-auto flex justify-between pt-6 border-t border-border">
        <button onClick={onBack} className="px-6 py-2 text-muted hover:text-text font-medium transition-colors">
          {t('common.back')}
        </button>
        <button
          onClick={() => {
            onNext({
              platform: 'wechat',
              wechat: {
                ...(data.wechat || {}),
                bot_token: botToken,
                base_url: baseUrl,
              },
            });
          }}
          disabled={!canProceed}
          className={clsx(
            'px-8 py-3 rounded-lg font-medium transition-colors shadow-sm',
            canProceed ? 'bg-accent hover:bg-accent/90 text-white' : 'bg-neutral-200 text-muted cursor-not-allowed'
          )}
        >
          {t('common.continue')}
        </button>
      </div>
    </div>
  );
};
