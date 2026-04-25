import React, { useEffect, useState } from 'react';
import { ExternalLink, KeyRound, Link2, Power, RefreshCcw, ShieldCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useApi } from '../context/ApiContext';
import { useToast } from '../context/ToastContext';

export const RemoteAccess: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [loading, setLoading] = useState(true);
  const [pairing, setPairing] = useState(false);
  const [status, setStatus] = useState<any>(null);
  const [backendUrl, setBackendUrl] = useState('https://vibe.io');
  const [pairingKey, setPairingKey] = useState('');

  const refresh = async () => {
    setLoading(true);
    try {
      const [config, remoteStatus] = await Promise.all([api.getConfig(), api.remoteAccessStatus()]);
      setStatus(remoteStatus);
      setBackendUrl(config?.remote_access?.vibe_cloud?.backend_url || 'https://vibe.io');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh().catch(() => setLoading(false));
  }, []);

  const pair = async () => {
    setPairing(true);
    try {
      const result = await api.pairVibeCloudRemoteAccess({
        backend_url: backendUrl.trim(),
        pairing_key: pairingKey.trim(),
        device_name: 'Vibe Remote',
      });
      setStatus(result);
      setPairingKey('');
      showToast(t('remoteAccess.pairSuccess'), 'success');
      await refresh();
    } finally {
      setPairing(false);
    }
  };

  const stop = async () => {
    const result = await api.stopRemoteAccess();
    setStatus(result);
    showToast(t('remoteAccess.stopSuccess'), 'success');
  };

  const start = async () => {
    const result = await api.startRemoteAccess();
    setStatus(result);
    showToast(t('remoteAccess.startSuccess'), 'success');
  };

  const publicUrl = status?.public_url;
  const paired = Boolean(status?.paired);
  const running = Boolean(status?.running);

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
              {t('remoteAccess.connectTitle')}
            </h2>
            <p className="text-sm text-muted mt-1">{t('remoteAccess.connectDesc')}</p>
          </div>

          <label className="block space-y-2">
            <span className="text-sm font-medium">{t('remoteAccess.backendUrl')}</span>
            <input
              className="w-full rounded-lg border border-border bg-bg px-3 py-2"
              value={backendUrl}
              onChange={(event) => setBackendUrl(event.target.value)}
              placeholder="https://vibe.io"
            />
          </label>

          <label className="block space-y-2">
            <span className="text-sm font-medium">{t('remoteAccess.pairingKey')}</span>
            <input
              className="w-full rounded-lg border border-border bg-bg px-3 py-2 font-mono"
              value={pairingKey}
              onChange={(event) => setPairingKey(event.target.value)}
              placeholder="vrp_xxxxxxxxxxxxxxxxx"
            />
          </label>

          <button
            type="button"
            className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-white font-medium disabled:opacity-50"
            disabled={pairing || !pairingKey.trim() || !backendUrl.trim()}
            onClick={pair}
          >
            <Link2 className="w-4 h-4" />
            {pairing ? t('remoteAccess.pairing') : t('remoteAccess.pair')}
          </button>
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
              <div className="font-semibold mt-1">{running ? t('common.running') : t('common.stopped')}</div>
            </div>
          </div>

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
