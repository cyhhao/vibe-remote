import React from 'react';
import { AlertTriangle, CheckCircle, ChevronDown, Cloud, Download, ExternalLink, Globe2, KeyRound, Play, ShieldCheck, Square } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useApi } from '../context/ApiContext';
import { useToast } from '../context/ToastContext';

type CloudflareRemoteAccessConfig = {
  enabled: boolean;
  hostname: string;
  account_id: string;
  zone_id: string;
  tunnel_id: string;
  tunnel_token: string;
  cloudflared_path: string;
  access_app_id: string;
  access_app_aud: string;
  allowed_emails: string[];
  allowed_email_domains: string[];
  confirmed_access_policy: boolean;
  confirmed_tunnel_route: boolean;
};

const defaultCloudflareConfig = (): CloudflareRemoteAccessConfig => ({
  enabled: false,
  hostname: '',
  account_id: '',
  zone_id: '',
  tunnel_id: '',
  tunnel_token: '',
  cloudflared_path: '',
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

const normalizeCloudflare = (
  value: CloudflareRemoteAccessConfig,
  emailsText: string,
  domainsText: string,
): CloudflareRemoteAccessConfig => ({
  ...value,
  allowed_emails: splitList(emailsText),
  allowed_email_domains: splitList(domainsText),
});

const hasRequiredAccessPolicyScope = (value: CloudflareRemoteAccessConfig): boolean => (
  (value.allowed_emails || []).length > 0 || (value.allowed_email_domains || []).length > 0
);

const isCloudflareChecklistReady = (value: CloudflareRemoteAccessConfig): boolean => Boolean(
  value.hostname
  && value.tunnel_token
  && value.confirmed_access_policy
  && value.confirmed_tunnel_route
  && hasRequiredAccessPolicyScope(value)
);

const sameCloudflareConfig = (left: CloudflareRemoteAccessConfig, right: CloudflareRemoteAccessConfig): boolean => (
  JSON.stringify(left) === JSON.stringify(right)
);

const FieldBadge = ({ required, label }: { required?: boolean; label?: string }) => {
  const { t } = useTranslation();
  return (
    <span className={`ml-2 rounded-full px-2 py-0.5 text-[11px] font-semibold ${
      required ? 'bg-danger/10 text-danger' : 'bg-neutral-100 text-muted'
    }`}>
      {label || (required ? t('remoteAccess.required') : t('remoteAccess.optional'))}
    </span>
  );
};

const TextField = ({
  label,
  value,
  onChange,
  placeholder,
  type = 'text',
  required,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  type?: string;
  required?: boolean;
}) => (
  <label className="block">
    <span className="block text-sm font-medium text-text mb-1">
      {label}
      <FieldBadge required={required} />
    </span>
    <input
      type={type}
      value={value}
      onChange={(event) => onChange(event.target.value)}
      placeholder={placeholder}
      className="w-full bg-neutral-100 border border-border rounded-lg px-3 py-2 text-sm font-mono text-text focus:outline-none focus:ring-2 focus:ring-accent/30"
    />
  </label>
);

const DetailBlock = ({ children }: { children: React.ReactNode }) => {
  const { t } = useTranslation();
  return (
    <details className="mt-4 rounded-lg border border-border bg-neutral-50 p-3 group">
      <summary className="cursor-pointer list-none flex items-center justify-between text-sm font-semibold text-text">
        {t('remoteAccess.showDetailedGuide')}
        <ChevronDown size={16} className="transition-transform group-open:rotate-180" />
      </summary>
      <div className="mt-3 text-sm text-muted space-y-2">
        {children}
      </div>
    </details>
  );
};

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

export const RemoteAccess: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [loading, setLoading] = React.useState(true);
  const [saving, setSaving] = React.useState(false);
  const [installing, setInstalling] = React.useState(false);
  const [applying, setApplying] = React.useState(false);
  const [config, setConfig] = React.useState<any>({});
  const [cloudflare, setCloudflare] = React.useState<CloudflareRemoteAccessConfig>(defaultCloudflareConfig());
  const [emailsText, setEmailsText] = React.useState('');
  const [domainsText, setDomainsText] = React.useState('');
  const [status, setStatus] = React.useState<any>(null);

  const load = React.useCallback(async () => {
    const loadedConfig = await api.getConfig();
    const currentCloudflare = {
      ...defaultCloudflareConfig(),
      ...(loadedConfig.remote_access?.cloudflare || loadedConfig.admin_access?.cloudflare || {}),
    };
    setConfig(loadedConfig);
    setCloudflare(currentCloudflare);
    setEmailsText(joinList(currentCloudflare.allowed_emails));
    setDomainsText(joinList(currentCloudflare.allowed_email_domains));
    setStatus(await api.remoteAccessStatus());
  }, [api]);

  React.useEffect(() => {
    let cancelled = false;

    const run = async () => {
      try {
        await load();
      } catch {
        if (!cancelled) {
          showToast(t('remoteAccess.loadFailed'), 'error');
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    run();
    return () => {
      cancelled = true;
    };
  }, [load]);

  const updateCloudflare = (patch: Partial<CloudflareRemoteAccessConfig>) => {
    setCloudflare((current) => ({ ...current, ...patch }));
  };

  const targetUrl = `http://127.0.0.1:${config.ui?.setup_port || 5123}`;
  const publicUrl = cloudflare.hostname ? `https://${cloudflare.hostname}` : '';
  const currentCloudflare = normalizeCloudflare(cloudflare, emailsText, domainsText);
  const savedCloudflare = {
    ...defaultCloudflareConfig(),
    ...(config.remote_access?.cloudflare || config.admin_access?.cloudflare || {}),
  };
  const accessReady = isCloudflareChecklistReady(currentCloudflare);
  const savedAccessReady = isCloudflareChecklistReady(savedCloudflare);
  const hasUnsavedChanges = !sameCloudflareConfig(currentCloudflare, savedCloudflare);
  const canStartConnector = savedAccessReady && !hasUnsavedChanges && Boolean(status?.binary_found);

  const formatConnectorError = (result: any) => {
    const error = result?.error || 'unknown';
    return t(`remoteAccess.errors.${error}`, { defaultValue: error });
  };

  const installCloudflared = async () => {
    setInstalling(true);
    try {
      const result = await api.remoteAccessInstallCloudflared();
      setStatus(await api.remoteAccessStatus());
      if (result.path) {
        updateCloudflare({ cloudflared_path: result.path });
      }
      if (result.ok === false) {
        showToast(formatConnectorError(result), 'error');
        return;
      }
      showToast(t('remoteAccess.installSucceeded'));
    } catch (error: any) {
      showToast(error?.message || t('remoteAccess.installFailed'), 'error');
    } finally {
      setInstalling(false);
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      const nextCloudflare = {
        ...cloudflare,
        allowed_emails: splitList(emailsText),
        allowed_email_domains: splitList(domainsText),
      };
      const nextReady = isCloudflareChecklistReady(nextCloudflare);
      const saved = await api.saveConfig({
        remote_access: {
          provider: 'cloudflare',
          cloudflare: { ...nextCloudflare, enabled: nextReady ? nextCloudflare.enabled : false },
        },
      });
      const savedCloudflare = {
        ...defaultCloudflareConfig(),
        ...(saved.remote_access?.cloudflare || nextCloudflare),
      };
      setConfig(saved);
      setCloudflare(savedCloudflare);
      setEmailsText(joinList(savedCloudflare.allowed_emails));
      setDomainsText(joinList(savedCloudflare.allowed_email_domains));
      setStatus(await api.remoteAccessStatus());
      showToast(t('remoteAccess.saved'));
    } catch (error: any) {
      showToast(error?.message || t('remoteAccess.saveFailed'), 'error');
    } finally {
      setSaving(false);
    }
  };

  const startConnector = async () => {
    if (!canStartConnector) {
      showToast(
        hasUnsavedChanges
          ? t('remoteAccess.startBlockedUnsaved')
          : !status?.binary_found
            ? t('remoteAccess.installBlocked')
            : t('remoteAccess.startBlockedIncomplete'),
        'error',
      );
      return;
    }
    setApplying(true);
    try {
      await api.saveConfig({
        remote_access: {
          provider: 'cloudflare',
          cloudflare: { ...savedCloudflare, enabled: true },
        },
      });
      const result = await api.remoteAccessApplyCloudflare();
      setStatus(result);
      await load();
      if (result.ok === false || !result.running) {
        showToast(formatConnectorError(result), 'error');
        return;
      }
      showToast(t('remoteAccess.connectorStarted'));
    } catch (error: any) {
      showToast(error?.message || t('remoteAccess.applyFailed'), 'error');
    } finally {
      setApplying(false);
    }
  };

  const stopConnector = async () => {
    setApplying(true);
    try {
      await api.saveConfig({
        remote_access: {
          provider: 'cloudflare',
          cloudflare: { ...savedCloudflare, enabled: false },
        },
      });
      const result = await api.remoteAccessApplyCloudflare();
      setStatus(result);
      await load();
      if (result.ok === false) {
        showToast(formatConnectorError(result), 'error');
        return;
      }
      showToast(t('remoteAccess.connectorStopped'));
    } catch (error: any) {
      showToast(error?.message || t('remoteAccess.applyFailed'), 'error');
    } finally {
      setApplying(false);
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
              <h2 className="text-3xl font-display font-bold text-text">{t('remoteAccess.title')}</h2>
              <p className="text-muted">{t('remoteAccess.subtitle')}</p>
            </div>
          </div>
        </div>
        <div className={`rounded-xl border px-4 py-3 text-sm ${
          status?.running
            ? 'bg-success/10 border-success/20 text-success'
            : savedAccessReady
              ? 'bg-warning/10 border-warning/20 text-warning'
              : 'bg-neutral-100 border-border text-muted'
        }`}>
          <div className="flex items-center gap-2 font-semibold">
            {status?.running ? <CheckCircle size={16} /> : <AlertTriangle size={16} />}
            {status?.running
              ? t('remoteAccess.statusRunning')
              : savedAccessReady
                ? t('remoteAccess.statusReady')
                : t('remoteAccess.statusNeedsSetup')}
          </div>
        </div>
      </header>

      <div className="bg-panel rounded-2xl border border-border p-6 shadow-sm overflow-hidden relative">
        <div className="absolute right-0 top-0 h-full w-1/2 bg-gradient-to-l from-sky-50 to-transparent pointer-events-none" />
        <div className="relative flex flex-col gap-5 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-accent mb-2">
              <Cloud size={16} /> {t('remoteAccess.cloudflareProvider')}
            </div>
            <h3 className="text-xl font-semibold text-text">{t('remoteAccess.cloudflareTitle')}</h3>
            <p className="text-sm text-muted max-w-2xl mt-2">{t('remoteAccess.cloudflareDesc')}</p>
          </div>
          <div className={`rounded-lg border px-3 py-2 text-sm font-semibold ${
            savedCloudflare.enabled
              ? 'bg-success/10 text-success border-success/20'
              : 'bg-neutral-100 text-muted border-border'
          }`}>
            {savedCloudflare.enabled ? t('remoteAccess.enabledAfterSave') : t('remoteAccess.disabledUntilStart')}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[1fr_20rem] gap-6">
        <div className="space-y-4">
          <StepCard number={1} title={t('remoteAccess.stepInstallTitle')}>
            <p className="text-sm text-muted mb-4">{t('remoteAccess.stepInstallDesc')}</p>
            <div className="rounded-lg border border-border bg-neutral-50 p-4 space-y-3">
              <div className="flex items-center gap-2 text-sm">
                <div className={`w-2.5 h-2.5 rounded-full ${status?.binary_found ? 'bg-success' : 'bg-warning'}`} />
                <span className={status?.binary_found ? 'text-success' : 'text-warning'}>
                  {status?.binary_found ? t('remoteAccess.cloudflaredFound') : t('remoteAccess.cloudflaredMissing')}
                </span>
              </div>
              {status?.binary_path && (
                <code className="block text-xs font-mono bg-white rounded p-2 text-text break-all">
                  {status.binary_path}
                </code>
              )}
              {status?.binary_version && <div className="text-xs text-muted">{status.binary_version}</div>}
              <button
                onClick={() => void installCloudflared()}
                disabled={installing}
                className="inline-flex items-center justify-center gap-2 px-3 py-2 bg-accent text-white rounded-lg text-sm font-semibold disabled:opacity-50"
              >
                <Download size={14} /> {installing ? t('remoteAccess.installing') : t('remoteAccess.installCloudflared')}
              </button>
            </div>
            <DetailBlock>
              <ol className="list-decimal ml-5 space-y-1">
                <li>{t('remoteAccess.stepInstallGuide1')}</li>
                <li>{t('remoteAccess.stepInstallGuide2')}</li>
              </ol>
            </DetailBlock>
          </StepCard>

          <StepCard number={2} title={t('remoteAccess.step1Title')}>
            <p className="text-sm text-muted mb-4">{t('remoteAccess.step1Desc')}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <TextField
                label={t('remoteAccess.hostname')}
                value={cloudflare.hostname}
                onChange={(hostname) => updateCloudflare({ hostname })}
                placeholder={t('remoteAccess.hostnamePlaceholder')}
                required
              />
              <TextField
                label={t('remoteAccess.accountId')}
                value={cloudflare.account_id}
                onChange={(account_id) => updateCloudflare({ account_id })}
                placeholder={t('remoteAccess.accountIdPlaceholder')}
              />
              <TextField
                label={t('remoteAccess.zoneId')}
                value={cloudflare.zone_id}
                onChange={(zone_id) => updateCloudflare({ zone_id })}
                placeholder={t('remoteAccess.zoneIdPlaceholder')}
              />
            </div>
            <DetailBlock>
              <ol className="list-decimal ml-5 space-y-1">
                <li>{t('remoteAccess.step1Guide1')}</li>
                <li>{t('remoteAccess.step1Guide2')}</li>
                <li>{t('remoteAccess.step1Guide3')}</li>
              </ol>
              <a className="inline-flex items-center gap-2 text-accent hover:underline" href="https://dash.cloudflare.com/" target="_blank" rel="noreferrer">
                {t('remoteAccess.openCloudflareDashboard')} <ExternalLink size={13} />
              </a>
            </DetailBlock>
          </StepCard>

          <StepCard number={3} title={t('remoteAccess.step3Title')}>
            <p className="text-sm text-muted mb-4">{t('remoteAccess.step3Desc')}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <TextField
                label={t('remoteAccess.accessAppId')}
                value={cloudflare.access_app_id}
                onChange={(access_app_id) => updateCloudflare({ access_app_id })}
                placeholder={t('remoteAccess.accessAppIdPlaceholder')}
              />
              <TextField
                label={t('remoteAccess.accessAud')}
                value={cloudflare.access_app_aud}
                onChange={(access_app_aud) => updateCloudflare({ access_app_aud })}
                placeholder={t('remoteAccess.accessAudPlaceholder')}
              />
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
              <label className="block">
                <span className="block text-sm font-medium text-text mb-1">
                  {t('remoteAccess.allowedEmails')}
                  <FieldBadge required label={t('remoteAccess.oneRequired')} />
                </span>
                <textarea
                  value={emailsText}
                  onChange={(event) => setEmailsText(event.target.value)}
                  rows={4}
                  placeholder={t('remoteAccess.allowedEmailsPlaceholder')}
                  className="w-full bg-neutral-100 border border-border rounded-lg px-3 py-2 text-sm font-mono text-text focus:outline-none focus:ring-2 focus:ring-accent/30"
                />
              </label>
              <label className="block">
                <span className="block text-sm font-medium text-text mb-1">
                  {t('remoteAccess.allowedDomains')}
                  <FieldBadge required label={t('remoteAccess.oneRequired')} />
                </span>
                <textarea
                  value={domainsText}
                  onChange={(event) => setDomainsText(event.target.value)}
                  rows={4}
                  placeholder={t('remoteAccess.allowedDomainsPlaceholder')}
                  className="w-full bg-neutral-100 border border-border rounded-lg px-3 py-2 text-sm font-mono text-text focus:outline-none focus:ring-2 focus:ring-accent/30"
                />
              </label>
            </div>
            <DetailBlock>
              <ol className="list-decimal ml-5 space-y-1">
                <li>{t('remoteAccess.step3Guide1')}</li>
                <li>{t('remoteAccess.step3Guide2', { hostname: cloudflare.hostname || t('remoteAccess.hostnamePlaceholder') })}</li>
                <li>{t('remoteAccess.step3Guide3')}</li>
                <li>{t('remoteAccess.step3Guide4')}</li>
              </ol>
              <a className="inline-flex items-center gap-2 text-accent hover:underline" href="https://one.dash.cloudflare.com/" target="_blank" rel="noreferrer">
                {t('remoteAccess.openZeroTrustAccess')} <ExternalLink size={13} />
              </a>
            </DetailBlock>
          </StepCard>

          <StepCard number={4} title={t('remoteAccess.step2Title')}>
            <p className="text-sm text-muted mb-4">{t('remoteAccess.step2Desc')}</p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <TextField
                label={t('remoteAccess.tunnelId')}
                value={cloudflare.tunnel_id}
                onChange={(tunnel_id) => updateCloudflare({ tunnel_id })}
                placeholder={t('remoteAccess.tunnelIdPlaceholder')}
              />
              <TextField
                label={t('remoteAccess.tunnelToken')}
                value={cloudflare.tunnel_token}
                onChange={(tunnel_token) => updateCloudflare({ tunnel_token })}
                placeholder={t('remoteAccess.tunnelTokenPlaceholder')}
                type="password"
                required
              />
            </div>
            <div className="mt-4 rounded-lg border border-border bg-neutral-50 p-3">
              <div className="text-xs uppercase tracking-wide text-muted mb-1">{t('remoteAccess.originTarget')}</div>
              <code className="text-sm font-mono text-text">{targetUrl}</code>
            </div>
            <DetailBlock>
              <ol className="list-decimal ml-5 space-y-1">
                <li>{t('remoteAccess.step2Guide1')}</li>
                <li>{t('remoteAccess.step2Guide2')}</li>
                <li>{t('remoteAccess.step2Guide3', { hostname: cloudflare.hostname || t('remoteAccess.hostnamePlaceholder'), target: targetUrl })}</li>
                <li>{t('remoteAccess.step2Guide4')}</li>
              </ol>
              <a className="inline-flex items-center gap-2 text-accent hover:underline" href="https://one.dash.cloudflare.com/" target="_blank" rel="noreferrer">
                {t('remoteAccess.openZeroTrustTunnels')} <ExternalLink size={13} />
              </a>
            </DetailBlock>
          </StepCard>

          <StepCard number={5} title={t('remoteAccess.step4Title')}>
            <p className="text-sm text-muted mb-4">{t('remoteAccess.step4Desc')}</p>
            <div className="space-y-3">
              <label className="flex items-start gap-3 rounded-lg border border-border bg-neutral-50 p-3">
                <input
                  type="checkbox"
                  checked={cloudflare.confirmed_tunnel_route}
                  onChange={(event) => updateCloudflare({ confirmed_tunnel_route: event.target.checked })}
                  className="mt-1"
                />
                <span>
                  <span className="block text-sm font-medium text-text">{t('remoteAccess.confirmTunnelRoute')} <FieldBadge required /></span>
                  <span className="block text-xs text-muted">{t('remoteAccess.confirmTunnelRouteHint', { target: targetUrl })}</span>
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
                  <span className="block text-sm font-medium text-text">{t('remoteAccess.confirmAccessPolicy')} <FieldBadge required /></span>
                  <span className="block text-xs text-muted">{t('remoteAccess.confirmAccessPolicyHint')}</span>
                </span>
              </label>
            </div>
          </StepCard>

          {savedAccessReady && (
            <StepCard number={6} title={t('remoteAccess.stepStartTitle')}>
              <p className="text-sm text-muted mb-4">
                {hasUnsavedChanges ? t('remoteAccess.startNeedsResave') : t('remoteAccess.stepStartDesc')}
              </p>
              <button
                onClick={() => void (status?.running ? stopConnector() : startConnector())}
                disabled={applying || (!status?.running && !canStartConnector)}
                className="inline-flex items-center justify-center gap-2 px-4 py-2 border border-border bg-white rounded-lg text-sm font-semibold text-text disabled:opacity-50"
              >
                {status?.running ? <Square size={14} /> : <Play size={14} />}
                {applying
                  ? t('remoteAccess.applying')
                  : status?.running ? t('remoteAccess.stopConnector') : t('remoteAccess.startConnector')}
              </button>
              {!status?.running && !canStartConnector && (
                <p className="mt-2 text-xs text-warning">
                  {hasUnsavedChanges ? t('remoteAccess.startBlockedUnsaved') : t('remoteAccess.installBlocked')}
                </p>
              )}
            </StepCard>
          )}
        </div>

        <aside className="space-y-4">
          <div className="bg-panel rounded-xl border border-border p-5 shadow-sm">
            <h3 className="font-semibold flex items-center gap-2 text-text mb-3">
              <Globe2 size={18} /> {t('remoteAccess.publicUrl')}
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
              <KeyRound size={18} /> {t('remoteAccess.lifecycle')}
            </h3>
            <p className="text-sm text-muted">{t('remoteAccess.lifecycleDesc')}</p>
          </div>
        </aside>
      </div>

      <div className="sticky bottom-4 bg-panel/95 backdrop-blur rounded-xl border border-border p-4 shadow-lg flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="text-sm text-muted">
          {accessReady ? t('remoteAccess.readyToSave') : t('remoteAccess.incompleteWarning')}
          {savedAccessReady && !hasUnsavedChanges && !status?.running ? ` ${t('remoteAccess.startAppearsAfterSave')}` : ''}
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
