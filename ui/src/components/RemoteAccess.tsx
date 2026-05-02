import React, { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, ExternalLink, KeyRound, Link2, Power, RefreshCcw, ShieldCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';
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
  const legacyNestedHostname = typeof publicUrl === 'string' && publicUrl.includes('.remote.avibe.bot');
  const connectorState = status?.pid_state === 'unknown'
    ? t('remoteAccess.stateNeedsAttention')
    : running
      ? t('common.running')
      : t('common.stopped');

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="rounded-3xl border border-slate-200 bg-gradient-to-br from-slate-950 via-slate-900 to-cyan-950 text-white p-8 shadow-xl overflow-hidden relative">
        <div className="absolute -right-20 -top-24 h-64 w-64 rounded-full bg-cyan-400/20 blur-3xl" />
        <div className="relative space-y-3">
          <div className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1 text-sm text-cyan-100">
            <ShieldCheck className="w-4 h-4" />
            {t('remoteAccess.badge')}
          </div>
          <h1 className="text-3xl md:text-4xl font-display font-bold tracking-tight">{t('remoteAccess.title')}</h1>
          <p className="text-cyan-50/80 max-w-2xl">{t('remoteAccess.subtitle')}</p>
        </div>
      </div>

      <div className="grid lg:grid-cols-[1.1fr_0.9fr] gap-6">
        <section className="rounded-2xl border border-border bg-panel p-6 space-y-5">
          <div>
            <h2 className="text-xl font-semibold flex items-center gap-2">
              <KeyRound className="w-5 h-5 text-accent" />
              {paired && !reconfiguring ? t('remoteAccess.configuredTitle') : t('remoteAccess.connectTitle')}
            </h2>
            <p className="text-sm text-muted mt-1">
              {paired && !reconfiguring ? t('remoteAccess.configuredDesc') : t('remoteAccess.connectDesc')}
            </p>
          </div>

          <div className="rounded-xl border border-border bg-neutral-50 p-4 text-sm">
            <div className="text-muted">{t('remoteAccess.vibeCloudService')}</div>
            <a className="mt-1 inline-flex items-center gap-2 font-medium text-accent" href={VIBE_CLOUD_URL} target="_blank" rel="noreferrer">
              {VIBE_CLOUD_URL}
              <ExternalLink className="h-4 w-4" />
            </a>
          </div>

          {showPairingForm && (
            <div className="rounded-xl border border-cyan-200 bg-cyan-50 p-4 text-sm text-cyan-950">
              <div className="font-semibold">{t('remoteAccess.flowTitle')}</div>
              <ol className="mt-2 list-decimal space-y-1 pl-5">
                <li>{t('remoteAccess.flowStep1')}</li>
                <li>{t('remoteAccess.flowStep2')}</li>
                <li>{t('remoteAccess.flowStep3')}</li>
              </ol>
            </div>
          )}

          {showPairingForm ? (
            <>
              <label className="block space-y-2">
                <span className="text-sm font-medium">{t('remoteAccess.pairingKey')}</span>
                <input
                  className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono"
                  value={pairingKey}
                  onChange={(event) => setPairingKey(event.target.value)}
                  placeholder="vrp_xxxxxxxxxxxxxxxxx"
                />
                <span className="block text-xs text-muted">{t('remoteAccess.pairingKeyHelp')}</span>
              </label>

              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-white font-medium disabled:opacity-50"
                  disabled={pairing || !pairingKey.trim()}
                  onClick={pair}
                >
                  <Link2 className="w-4 h-4" />
                  {pairing ? t('remoteAccess.pairing') : t('remoteAccess.pair')}
                </button>
                {paired && (
                  <button
                    type="button"
                    className="rounded-lg border border-border px-4 py-2"
                    onClick={() => {
                      setReconfiguring(false);
                      setPairingKey('');
                    }}
                  >
                    {t('common.cancel')}
                  </button>
                )}
              </div>
            </>
          ) : (
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4 text-sm text-emerald-950">
              <div className="flex items-start gap-3">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="min-w-0 flex-1">
                  <div className="font-semibold">{t('remoteAccess.configuredBadge')}</div>
                  <div className="mt-1">{t('remoteAccess.configuredHelp')}</div>
                  {publicUrl && <div className="mt-2 truncate font-mono text-xs">{publicUrl}</div>}
                </div>
              </div>
              <button
                type="button"
                className="mt-4 rounded-lg border border-emerald-300 px-3 py-2 font-medium"
                onClick={() => setReconfiguring(true)}
              >
                {t('remoteAccess.repair')}
              </button>
            </div>
          )}

          {legacyNestedHostname && (
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950">
              <div className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <div>
                  <div className="font-semibold">{t('remoteAccess.legacyHostnameTitle')}</div>
                  <div className="mt-1">{t('remoteAccess.legacyHostnameDesc')}</div>
                </div>
              </div>
            </div>
          )}
        </section>

        <section className="rounded-2xl border border-border bg-panel p-6 space-y-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-xl font-semibold">{t('remoteAccess.statusTitle')}</h2>
              <p className="text-sm text-muted mt-1">{loading ? t('common.loading') : t('remoteAccess.statusDesc')}</p>
            </div>
            <button className="rounded-lg border border-border p-2 hover:bg-neutral-50" onClick={refresh} type="button">
              <RefreshCcw className="w-4 h-4" />
            </button>
          </div>

          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="rounded-xl bg-neutral-50 p-4 border border-border">
              <div className="text-muted">{t('remoteAccess.paired')}</div>
              <div className="font-semibold mt-1">{paired ? t('common.enabled') : t('common.disabled')}</div>
            </div>
            <div className="rounded-xl bg-neutral-50 p-4 border border-border">
              <div className="text-muted">{t('remoteAccess.connector')}</div>
              <div className="font-semibold mt-1">{connectorState}</div>
            </div>
          </div>

          {actionMessage && (
            <div className={`rounded-xl border p-4 text-sm ${
              actionMessage.type === 'error'
                ? 'border-amber-200 bg-amber-50 text-amber-950'
                : 'border-emerald-200 bg-emerald-50 text-emerald-950'
            }`}>
              <div className="flex items-start gap-3">
                {actionMessage.type === 'error' ? <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /> : <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />}
                <div>
                  <div className="font-semibold">
                    {actionMessage.type === 'error' ? t('remoteAccess.actionNeeded') : t('remoteAccess.ready')}
                  </div>
                  <div className="mt-1">{actionMessage.text}</div>
                </div>
              </div>
            </div>
          )}

          {publicUrl && (
            <a className="flex items-center justify-between rounded-xl border border-border p-4 hover:bg-neutral-50" href={publicUrl} target="_blank" rel="noreferrer">
              <span className="font-mono text-sm truncate">{publicUrl}</span>
              <ExternalLink className="w-4 h-4 shrink-0" />
            </a>
          )}

          <div className="flex gap-3">
            <button className="rounded-lg border border-border px-4 py-2 disabled:opacity-50" disabled={!paired || running} onClick={start} type="button">
              <Power className="w-4 h-4 inline mr-2" />
              {t('common.start')}
            </button>
            <button className="rounded-lg border border-border px-4 py-2 disabled:opacity-50" disabled={!paired || !running} onClick={stop} type="button">
              {t('common.stop')}
            </button>
          </div>
        </section>
      </div>
    </div>
  );
};
