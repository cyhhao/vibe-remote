import React from 'react';
import { AlertTriangle, CheckCircle, Cloud, Copy, ExternalLink, Globe2, KeyRound, ShieldCheck, Terminal } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useApi } from '../context/ApiContext';
import { useToast } from '../context/ToastContext';
import { copyTextToClipboard } from '../lib/utils';

type CloudflareAdminAccessConfig = {
  enabled: boolean;
  hostname: string;
  account_id: string;
  zone_id: string;
  tunnel_id: string;
  tunnel_token: string;
  access_app_id: string;
  access_app_aud: string;
  allowed_emails: string[];
  allowed_email_domains: string[];
  confirmed_access_policy: boolean;
  confirmed_tunnel_route: boolean;
};

const defaultCloudflareConfig = (): CloudflareAdminAccessConfig => ({
  enabled: false,
  hostname: '',
  account_id: '',
  zone_id: '',
  tunnel_id: '',
  tunnel_token: '',
  access_app_id: '',
  access_app_aud: '',
  allowed_emails: [],
  allowed_email_domains: [],
  confirmed_access_policy: false,
  confirmed_tunnel_route: false,
});

const splitList = (value: string): string[] => (
  value
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean)
);

const joinList = (value: string[] | undefined): string => (value || []).join('\n');

const TextField = ({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
}) => (
  <label className="block">
    <span className="block text-sm font-medium text-text mb-1">{label}</span>
    <input
      type={type}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      className="w-full bg-neutral-100 border border-border rounded-lg px-3 py-2 text-sm font-mono text-text focus:outline-none focus:ring-2 focus:ring-accent/30"
    />
  </label>
);

const StepCard = ({
  number,
  title,
  children,
}: {
  number: number;
  title: string;
  children: React.ReactNode;
}) => (
  <div className="bg-panel rounded-xl border border-border p-5 shadow-sm">
    <div className="flex items-center gap-3 mb-3">
      <div className="w-7 h-7 rounded-full bg-accent/10 text-accent flex items-center justify-center text-sm font-bold">
        {number}
      </div>
      <h3 className="font-semibold text-text">{title}</h3>
    </div>
    {children}
  </div>
);

export const AdminAccess: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [config, setConfig] = React.useState<any>({});
  const [cloudflare, setCloudflare] = React.useState<CloudflareAdminAccessConfig>(defaultCloudflareConfig());
  const [emailsText, setEmailsText] = React.useState('');
  const [domainsText, setDomainsText] = React.useState('');
  const [cloudflaredPath, setCloudflaredPath] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;

    const load = async () => {
      try {
        const loadedConfig = await api.getConfig();
        if (cancelled) return;
        const currentCloudflare = {
          ...defaultCloudflareConfig(),
          ...(loadedConfig.admin_access?.cloudflare || {}),
        };
        setConfig(loadedConfig);
        setCloudflare(currentCloudflare);
        setEmailsText(joinList(currentCloudflare.allowed_emails));
        setDomainsText(joinList(currentCloudflare.allowed_email_domains));

        const detected = await api.detectCli('cloudflared');
        if (!cancelled) {
          setCloudflaredPath(detected.found ? detected.path : null);
        }
      } catch {
        if (!cancelled) {
          showToast(t('adminAccess.loadFailed'), 'error');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const updateCloudflare = (patch: Partial<CloudflareAdminAccessConfig>) => {
    setCloudflare((current) => ({ ...current, ...patch }));
  };

  const targetUrl = `http://127.0.0.1:${config.ui?.setup_port || 5123}`;
  const publicUrl = cloudflare.hostname ? `https://${cloudflare.hostname}` : '';
  const runCommand = cloudflare.tunnel_token
    ? `cloudflared tunnel run --token ${cloudflare.tunnel_token}`
    : '';
  const accessReady = Boolean(
    cloudflare.hostname
    && cloudflare.tunnel_token
    && cloudflare.confirmed_access_policy
    && cloudflare.confirmed_tunnel_route
  );

  const copyRunCommand = async () => {
    if (!runCommand) return;
    const copied = await copyTextToClipboard(runCommand);
    showToast(copied ? t('adminAccess.copied') : t('common.copyFailed'), copied ? 'success' : 'error');
  };

  const save = async () => {
    setSaving(true);
    try {
      const nextCloudflare = {
        ...cloudflare,
        allowed_emails: splitList(emailsText),
        allowed_email_domains: splitList(domainsText),
      };
      const nextReady = Boolean(
        nextCloudflare.hostname
        && nextCloudflare.tunnel_token
        && nextCloudflare.confirmed_access_policy
        && nextCloudflare.confirmed_tunnel_route
      );
      if (nextCloudflare.enabled && !nextReady) {
        showToast(t('adminAccess.enableBlocked'), 'error');
        return;
      }
      const saved = await api.saveConfig({
        admin_access: {
          provider: 'cloudflare',
          cloudflare: nextCloudflare,
        },
      });
      const savedCloudflare = {
        ...defaultCloudflareConfig(),
        ...(saved.admin_access?.cloudflare || nextCloudflare),
      };
      setConfig(saved);
      setCloudflare(savedCloudflare);
      setEmailsText(joinList(savedCloudflare.allowed_emails));
      setDomainsText(joinList(savedCloudflare.allowed_email_domains));
      showToast(t('adminAccess.saved'));
    } catch {
      showToast(t('adminAccess.saveFailed'), 'error');
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="max-w-5xl mx-auto text-muted">{t('common.loading')}</div>;
  }

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <header className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
        <div>
          <div className="flex items-center gap-3 mb-2">
            <div className="w-11 h-11 rounded-2xl bg-accent/10 text-accent flex items-center justify-center">
              <ShieldCheck size={24} />
            </div>
            <div>
              <h2 className="text-3xl font-display font-bold text-text">{t('adminAccess.title')}</h2>
              <p className="text-muted">{t('adminAccess.subtitle')}</p>
            </div>
          </div>
        </div>
        <div className={`rounded-xl border px-4 py-3 text-sm ${
          accessReady && cloudflare.enabled
            ? 'bg-success/10 border-success/20 text-success'
            : 'bg-warning/10 border-warning/20 text-warning'
        }`}>
          <div className="flex items-center gap-2 font-semibold">
            {accessReady && cloudflare.enabled ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}
            {accessReady && cloudflare.enabled ? t('adminAccess.statusConfigured') : t('adminAccess.statusNeedsSetup')}
          </div>
        </div>
      </header>

      <div className="bg-panel rounded-2xl border border-border p-6 shadow-sm overflow-hidden relative">
        <div className="absolute right-0 top-0 h-full w-1/2 bg-gradient-to-l from-sky-50 to-transparent pointer-events-none" />
        <div className="relative flex flex-col gap-5 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-accent mb-2">
              <Cloud size={16} /> Cloudflare
            </div>
            <h3 className="text-xl font-semibold text-text">{t('adminAccess.cloudflareTitle')}</h3>
            <p className="text-sm text-muted max-w-2xl mt-2">{t('adminAccess.cloudflareDesc')}</p>
          </div>
          <button
            onClick={() => updateCloudflare({ enabled: !cloudflare.enabled })}
            className={`px-4 py-2 rounded-lg text-sm font-semibold border ${
              cloudflare.enabled
                ? 'bg-success/10 text-success border-success/20'
                : 'bg-neutral-100 text-muted border-border'
            }`}
          >
            {cloudflare.enabled ? t('common.enabled') : t('common.disabled')}
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_20rem] gap-6">
        <div className="space-y-4">
          <StepCard number={1} title={t('adminAccess.step1Title')}>
            <p className="text-sm text-muted mb-4">{t('adminAccess.step1Desc')}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <TextField
                label={t('adminAccess.hostname')}
                value={cloudflare.hostname}
                onChange={(hostname) => updateCloudflare({ hostname })}
                placeholder="admin.example.com"
              />
              <TextField
                label={t('adminAccess.accountId')}
                value={cloudflare.account_id}
                onChange={(account_id) => updateCloudflare({ account_id })}
              />
              <TextField
                label={t('adminAccess.zoneId')}
                value={cloudflare.zone_id}
                onChange={(zone_id) => updateCloudflare({ zone_id })}
              />
            </div>
          </StepCard>

          <StepCard number={2} title={t('adminAccess.step2Title')}>
            <p className="text-sm text-muted mb-4">{t('adminAccess.step2Desc')}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <TextField
                label={t('adminAccess.tunnelId')}
                value={cloudflare.tunnel_id}
                onChange={(tunnel_id) => updateCloudflare({ tunnel_id })}
              />
              <TextField
                label={t('adminAccess.tunnelToken')}
                value={cloudflare.tunnel_token}
                onChange={(tunnel_token) => updateCloudflare({ tunnel_token })}
                type="password"
              />
            </div>
            <div className="mt-4 rounded-lg border border-border bg-neutral-50 p-3">
              <div className="text-xs uppercase tracking-wide text-muted mb-1">{t('adminAccess.originTarget')}</div>
              <code className="text-sm font-mono text-text">{targetUrl}</code>
            </div>
          </StepCard>

          <StepCard number={3} title={t('adminAccess.step3Title')}>
            <p className="text-sm text-muted mb-4">{t('adminAccess.step3Desc')}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <TextField
                label={t('adminAccess.accessAppId')}
                value={cloudflare.access_app_id}
                onChange={(access_app_id) => updateCloudflare({ access_app_id })}
              />
              <TextField
                label={t('adminAccess.accessAud')}
                value={cloudflare.access_app_aud}
                onChange={(access_app_aud) => updateCloudflare({ access_app_aud })}
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
              <label className="block">
                <span className="block text-sm font-medium text-text mb-1">{t('adminAccess.allowedEmails')}</span>
                <textarea
                  value={emailsText}
                  onChange={(event) => setEmailsText(event.target.value)}
                  rows={4}
                  placeholder="alex@example.com"
                  className="w-full bg-neutral-100 border border-border rounded-lg px-3 py-2 text-sm font-mono text-text focus:outline-none focus:ring-2 focus:ring-accent/30"
                />
              </label>
              <label className="block">
                <span className="block text-sm font-medium text-text mb-1">{t('adminAccess.allowedDomains')}</span>
                <textarea
                  value={domainsText}
                  onChange={(event) => setDomainsText(event.target.value)}
                  rows={4}
                  placeholder="example.com"
                  className="w-full bg-neutral-100 border border-border rounded-lg px-3 py-2 text-sm font-mono text-text focus:outline-none focus:ring-2 focus:ring-accent/30"
                />
              </label>
            </div>
          </StepCard>

          <StepCard number={4} title={t('adminAccess.step4Title')}>
            <p className="text-sm text-muted mb-4">{t('adminAccess.step4Desc')}</p>
            <div className="space-y-3">
              <label className="flex items-start gap-3 rounded-lg border border-border bg-neutral-50 p-3">
                <input
                  type="checkbox"
                  checked={cloudflare.confirmed_tunnel_route}
                  onChange={(event) => updateCloudflare({ confirmed_tunnel_route: event.target.checked })}
                  className="mt-1"
                />
                <span>
                  <span className="block text-sm font-medium text-text">{t('adminAccess.confirmTunnelRoute')}</span>
                  <span className="block text-xs text-muted">{t('adminAccess.confirmTunnelRouteHint', { target: targetUrl })}</span>
                </span>
              </label>
              <label className="flex items-start gap-3 rounded-lg border border-border bg-neutral-50 p-3">
                <input
                  type="checkbox"
                  checked={cloudflare.confirmed_access_policy}
                  onChange={(event) => updateCloudflare({ confirmed_access_policy: event.target.checked })}
                  className="mt-1"
                />
                <span>
                  <span className="block text-sm font-medium text-text">{t('adminAccess.confirmAccessPolicy')}</span>
                  <span className="block text-xs text-muted">{t('adminAccess.confirmAccessPolicyHint')}</span>
                </span>
              </label>
            </div>
          </StepCard>
        </div>

        <aside className="space-y-4">
          <div className="bg-panel rounded-xl border border-border p-5 shadow-sm">
            <h3 className="font-semibold flex items-center gap-2 text-text mb-3">
              <Terminal size={18} /> {t('adminAccess.localConnector')}
            </h3>
            <div className="flex items-center gap-2 text-sm mb-4">
              <div className={`w-2.5 h-2.5 rounded-full ${cloudflaredPath ? 'bg-success' : 'bg-warning'}`} />
              <span className={cloudflaredPath ? 'text-success' : 'text-warning'}>
                {cloudflaredPath ? t('adminAccess.cloudflaredFound') : t('adminAccess.cloudflaredMissing')}
              </span>
            </div>
            {cloudflaredPath && (
              <code className="block text-xs font-mono bg-neutral-100 rounded p-2 text-text break-all mb-4">
                {cloudflaredPath}
              </code>
            )}
            <button
              onClick={() => void copyRunCommand()}
              disabled={!runCommand}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-accent text-white rounded-lg text-sm font-semibold disabled:opacity-50"
            >
              <Copy size={14} /> {t('adminAccess.copyRunCommand')}
            </button>
          </div>

          <div className="bg-panel rounded-xl border border-border p-5 shadow-sm">
            <h3 className="font-semibold flex items-center gap-2 text-text mb-3">
              <Globe2 size={18} /> {t('adminAccess.publicUrl')}
            </h3>
            {publicUrl ? (
              <a
                href={publicUrl}
                target="_blank"
                rel="noreferrer"
                className="text-sm text-accent hover:underline break-all"
              >
                {publicUrl}
              </a>
            ) : (
              <span className="text-sm text-muted">{t('common.notSet')}</span>
            )}
          </div>

          <div className="bg-panel rounded-xl border border-border p-5 shadow-sm">
            <h3 className="font-semibold flex items-center gap-2 text-text mb-3">
              <KeyRound size={18} /> {t('adminAccess.references')}
            </h3>
            <div className="space-y-2 text-sm">
              <a className="flex items-center gap-2 text-accent hover:underline" href="https://one.dash.cloudflare.com/" target="_blank" rel="noreferrer">
                {t('adminAccess.openZeroTrust')} <ExternalLink size={13} />
              </a>
              <a className="flex items-center gap-2 text-accent hover:underline" href="https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/" target="_blank" rel="noreferrer">
                {t('adminAccess.openAccessDocs')} <ExternalLink size={13} />
              </a>
            </div>
          </div>
        </aside>
      </div>

      <div className="sticky bottom-4 bg-panel/95 backdrop-blur rounded-xl border border-border p-4 shadow-lg flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="text-sm text-muted">
          {accessReady ? t('adminAccess.readyToSave') : t('adminAccess.incompleteWarning')}
        </div>
        <button
          onClick={() => void save()}
          disabled={saving}
          className="px-5 py-2 bg-accent text-white rounded-lg font-semibold disabled:opacity-50"
        >
          {saving ? t('common.saving') : t('common.save')}
        </button>
      </div>
    </div>
  );
};
