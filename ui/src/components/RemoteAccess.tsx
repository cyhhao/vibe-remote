import React, { useEffect, useState } from 'react';
import { CheckCircle2, ExternalLink, Link2, RefreshCcw } from 'lucide-react';
import { Trans, useTranslation } from 'react-i18next';
import { useApi } from '../context/ApiContext';
import { useToast } from '../context/ToastContext';

const VIBE_CLOUD_URL = 'https://avibe.bot';

export const RemoteAccess: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [loading, setLoading] = useState(true);
  const [pairing, setPairing] = useState(false);
  const [status, setStatus] = useState<any>(null);
  const [pairingKey, setPairingKey] = useState('');
  const [reconfiguring, setReconfiguring] = useState(false);
  const [actionMessage, setActionMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const describeError = (payload: any) => {
    const code = typeof payload?.error === 'string' ? payload.error : '';
    if (!code) {
      return t('errors.remote_access_unknown');
    }
    return t(`errors.${code}`, { defaultValue: t('errors.remote_access_unknown') });
  };

  const refresh = async () => {
    setLoading(true);
    try {
      const remoteStatus = await api.remoteAccessStatus();
      setStatus(remoteStatus);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh().catch(() => setLoading(false));
  }, []);

  const pair = async () => {
    setPairing(true);
    setActionMessage(null);
    try {
      const result = await api.pairVibeCloudRemoteAccess({
        backend_url: VIBE_CLOUD_URL,
        pairing_key: pairingKey.trim(),
        device_name: 'Vibe Remote',
      });
      setStatus(result);
      setPairingKey('');
      if (result?.start?.ok === false) {
        const message = describeError(result.start);
        setActionMessage({ type: 'error', text: message });
        showToast(message, 'error');
      } else {
        const message = t('remoteAccess.pairSuccess');
        setReconfiguring(false);
        setActionMessage({ type: 'success', text: message });
        showToast(message, 'success');
      }
      await refresh();
    } catch (error) {
      const message = error instanceof Error ? error.message : t('errors.remote_access_unknown');
      setActionMessage({ type: 'error', text: message });
    } finally {
      setPairing(false);
    }
  };

  const stop = async () => {
    setActionMessage(null);
    try {
      const result = await api.stopRemoteAccess();
      setStatus(result);
      if (result?.ok === false) {
        const message = describeError(result);
        setActionMessage({ type: 'error', text: message });
        showToast(message, 'error');
        return;
      }
      const message = t('remoteAccess.stopSuccess');
      setActionMessage({ type: 'success', text: message });
      showToast(message, 'success');
    } catch (error) {
      const message = error instanceof Error ? error.message : t('errors.remote_access_unknown');
      setActionMessage({ type: 'error', text: message });
    }
  };

  const start = async () => {
    setActionMessage(null);
    try {
      const result = await api.startRemoteAccess();
      setStatus(result);
      if (result?.ok === false) {
        const message = describeError(result);
        setActionMessage({ type: 'error', text: message });
        showToast(message, 'error');
        return;
      }
      const message = t('remoteAccess.startSuccess');
      setActionMessage({ type: 'success', text: message });
      showToast(message, 'success');
    } catch (error) {
      const message = error instanceof Error ? error.message : t('errors.remote_access_unknown');
      setActionMessage({ type: 'error', text: message });
    }
  };

  const publicUrl = status?.public_url;
  const paired = Boolean(status?.paired);
  const running = Boolean(status?.running);
  const showPairingForm = !paired || reconfiguring;
  const connectorState = status?.pid_state === 'unknown'
    ? t('remoteAccess.stateNeedsAttention')
    : running
      ? t('common.running')
      : t('common.stopped');

  return (
    <section className="overflow-hidden rounded-md border border-border bg-surface-2/45">
      <div className="flex items-start justify-between gap-4 border-b border-border px-4 py-3">
        <div className="min-w-0 space-y-2">
          <h2 className="text-[13px] font-semibold text-foreground">{t('remoteAccess.title')}</h2>
          <p className="max-w-2xl text-[11px] leading-relaxed text-muted">
            <Trans
              i18nKey="remoteAccess.subtitleWithLink"
              components={{
                cloud: (
                  <a
                    href={VIBE_CLOUD_URL}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-0.5 font-medium text-cyan hover:underline"
                  />
                ),
              }}
            />
          </p>
          <ol className="ml-4 list-decimal space-y-1 text-[11px] leading-relaxed text-muted">
            <li>{t('remoteAccess.flowStep1')}</li>
            <li>{t('remoteAccess.flowStep2')}</li>
            <li>{t('remoteAccess.flowStep3')}</li>
          </ol>
        </div>
        <button
          className="inline-flex h-8 shrink-0 items-center gap-2 whitespace-nowrap rounded-md border border-border bg-surface-3 px-3 text-[12px] text-foreground transition hover:border-border-strong"
          onClick={refresh}
          type="button"
        >
          <RefreshCcw className="size-3.5" />
          {t('common.refresh')}
        </button>
      </div>

      <div className="grid border-b border-border md:grid-cols-3">
        <div className="border-b border-border px-4 py-3 md:border-b-0 md:border-r">
          <div className="text-[11px] text-muted">{t('remoteAccess.paired')}</div>
          <div className="mt-1 text-[13px] font-medium text-foreground">{paired ? t('common.enabled') : t('common.disabled')}</div>
        </div>
        <div className="border-b border-border px-4 py-3 md:border-b-0 md:border-r">
          <div className="text-[11px] text-muted">{t('remoteAccess.connector')}</div>
          <div className="mt-1 text-[13px] font-medium text-foreground">{loading ? t('common.loading') : connectorState}</div>
        </div>
        <div className="px-4 py-3">
          <div className="text-[11px] text-muted">{t('remoteAccess.vibeCloudService')}</div>
          <a className="mt-1 inline-flex text-[13px] font-medium text-cyan" href={VIBE_CLOUD_URL} target="_blank" rel="noreferrer">
            avibe.bot
            <ExternalLink className="ml-1 size-3.5" />
          </a>
        </div>
      </div>

      {showPairingForm ? (
        <div className="grid gap-3 px-4 py-3 md:grid-cols-[1fr_auto] md:items-end">
          <label className="space-y-1.5">
            <span className="text-[12px] font-medium text-foreground">{t('remoteAccess.pairingKey')}</span>
            <input
              className="h-8 w-full rounded-md border border-input bg-surface-3 px-3 font-mono text-[12px] text-foreground outline-none focus:border-ring focus:ring-2 focus:ring-ring/30"
              value={pairingKey}
              onChange={(event) => setPairingKey(event.target.value)}
              placeholder="vrp_xxxxxxxxxxxxxxxxx"
            />
            <span className="block text-[10px] text-muted">{t('remoteAccess.pairingKeyHelp')}</span>
          </label>
          <div className="flex gap-2">
            <button
              type="button"
              className="inline-flex h-8 items-center gap-2 rounded-md bg-primary px-3 text-[12px] font-semibold text-primary-foreground disabled:opacity-50"
              disabled={pairing || !pairingKey.trim()}
              onClick={pair}
            >
              <Link2 className="size-3.5" />
              {pairing ? t('remoteAccess.pairing') : t('remoteAccess.pair')}
            </button>
            {paired && (
              <button
                type="button"
                className="h-8 rounded-md border border-border px-3 text-[12px] text-foreground"
                onClick={() => {
                  setReconfiguring(false);
                  setPairingKey('');
                }}
              >
                {t('common.cancel')}
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-[13px] font-medium text-mint">
              <CheckCircle2 className="size-3.5" />
              {t('remoteAccess.configuredBadge')}
            </div>
            {publicUrl && (
              <a
                href={publicUrl}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-flex max-w-full items-center gap-1 truncate font-mono text-[11px] text-cyan hover:underline"
                title={publicUrl}
              >
                <span className="truncate">{publicUrl}</span>
                <ExternalLink className="size-3 shrink-0" />
              </a>
            )}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              className="h-8 rounded-md border border-border px-3 text-[12px] text-foreground"
              onClick={() => setReconfiguring(true)}
            >
              {t('remoteAccess.repair')}
            </button>
            <button className="h-8 rounded-md border border-border px-3 text-[12px] text-foreground disabled:opacity-50" disabled={!paired || running} onClick={start} type="button">
              {t('common.start')}
            </button>
            <button className="h-8 rounded-md border border-border px-3 text-[12px] text-foreground disabled:opacity-50" disabled={!paired || !running} onClick={stop} type="button">
              {t('common.stop')}
            </button>
          </div>
        </div>
      )}

      {actionMessage && (
        <div className={`border-t border-border px-4 py-3 text-[12px] ${
          actionMessage.type === 'error' ? 'text-gold' : 'text-mint'
        }`}>
          {actionMessage.text}
        </div>
      )}
    </section>
  );
};
