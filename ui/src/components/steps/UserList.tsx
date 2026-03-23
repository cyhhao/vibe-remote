import React, { useEffect, useState } from 'react';
import { User, Shield, ShieldOff, Copy, Check, Plus, Trash2, FolderOpen, HelpCircle, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useApi } from '../../context/ApiContext';
import { useToast } from '../../context/ToastContext';
import { getEnabledPlatforms } from '../../lib/platforms';
import { Combobox } from '../ui/combobox';
import { DirectoryBrowser } from '../ui/directory-browser';
import clsx from 'clsx';
import { copyTextToClipboard } from '../../lib/utils';

/** Input that only commits value on blur */
function BlurInput({
  value,
  onCommit,
  ...props
}: { value: string; onCommit: (v: string) => void } & Omit<React.InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'onBlur'>) {
  const [local, setLocal] = useState(value);
  useEffect(() => setLocal(value), [value]);
  return (
    <input
      {...props}
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={() => { if (local !== value) onCommit(local); }}
    />
  );
}

interface UserConfig {
  display_name: string;
  is_admin: boolean;
  bound_at: string;
  enabled: boolean;
  show_message_types: string[];
  custom_cwd: string;
  routing: {
    agent_backend: string | null;
    opencode_agent?: string | null;
    opencode_model?: string | null;
    opencode_reasoning_effort?: string | null;
    claude_agent?: string | null;
    claude_model?: string | null;
    claude_reasoning_effort?: string | null;
    codex_model?: string | null;
    codex_reasoning_effort?: string | null;
  };
}

interface BindCodeItem {
  code: string;
  type: string;
  created_at: string;
  expires_at: string | null;
  is_active: boolean;
  used_by: string[];
}

// ─── Bind Code Section ───────────────────────────────────────────────────

const BindCodeSection: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [codes, setCodes] = useState<BindCodeItem[]>([]);
  const [showForm, setShowForm] = useState(false);
  const [newType, setNewType] = useState<'one_time' | 'expiring'>('one_time');
  const [newExpiry, setNewExpiry] = useState('');
  const [copiedCode, setCopiedCode] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showUnavailableModal, setShowUnavailableModal] = useState(false);

  const loadCodes = async () => {
    try {
      const result = await api.getBindCodes();
      if (result.ok) setCodes(result.bind_codes || []);
    } catch (e) {
      console.error('Failed to load bind codes:', e);
    }
  };

  useEffect(() => { loadCodes(); }, []);

  const handleCreate = async () => {
    setLoading(true);
    try {
      const result = await api.createBindCode(newType, newType === 'expiring' ? newExpiry : undefined);
      if (result.ok) {
        showToast(t('bindCode.created'));
        setShowForm(false);
        setNewType('one_time');
        setNewExpiry('');
        loadCodes();
      }
    } catch (e) {
      console.error('Failed to create bind code:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (code: string) => {
    try {
      const result = await api.deleteBindCode(code);
      if (result.ok) {
        showToast(t('bindCode.deleted'));
        loadCodes();
      }
    } catch (e) {
      console.error('Failed to delete bind code:', e);
    }
  };

  const handleCopy = async (code: string) => {
    const copied = await copyTextToClipboard(code);
    if (!copied) {
      showToast(t('common.copyFailed'), 'error');
      return;
    }

    setCopiedCode(code);
    setTimeout(() => setCopiedCode(null), 2000);
  };

  const getCodeStatus = (bc: BindCodeItem) => {
    if (!bc.is_active) {
      return bc.type === 'one_time' && bc.used_by.length > 0 ? 'used' : 'inactive';
    }
    if (bc.type === 'expiring' && bc.expires_at) {
      if (new Date(bc.expires_at) < new Date()) return 'expired';
    }
    return 'active';
  };

  const statusColors: Record<string, string> = {
    active: 'bg-success/10 text-success border-success/20',
    used: 'bg-neutral-100 text-muted border-border',
    expired: 'bg-warning/10 text-warning border-warning/20',
    inactive: 'bg-neutral-100 text-muted border-border',
  };

  const activeCodes = codes.filter((bc) => getCodeStatus(bc) === 'active');
  const unavailableCodes = codes.filter((bc) => getCodeStatus(bc) !== 'active');

  const renderCodeRow = (bc: BindCodeItem) => {
    const status = getCodeStatus(bc);
    return (
      <div key={bc.code} className="flex items-center justify-between py-2.5">
        <div className="flex items-center gap-3 flex-wrap">
          <code className="font-mono text-sm bg-bg px-2 py-0.5 rounded border border-border">{bc.code}</code>
          <span className={clsx('text-xs px-2 py-0.5 rounded-full border', statusColors[status])}>
            {t(`bindCode.${status}`)}
          </span>
          <span className="text-xs text-muted">
            {bc.type === 'one_time' ? t('bindCode.oneTime') : t('bindCode.expiring')}
          </span>
          {bc.used_by.length > 0 && (
            <span className="text-xs text-muted">{t('bindCode.usedBy', { count: bc.used_by.length })}</span>
          )}
          {bc.type === 'expiring' && bc.expires_at && (
            <span className="text-xs text-muted">
              {t('bindCode.expiresAt', { date: new Date(bc.expires_at).toLocaleDateString() })}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => handleCopy(bc.code)}
            className="p-1.5 text-muted hover:text-text transition-colors"
            title={t('bindCode.copy')}
          >
            {copiedCode === bc.code ? <Check size={14} className="text-success" /> : <Copy size={14} />}
          </button>
          {bc.is_active && (
            <button
              onClick={() => handleDelete(bc.code)}
              className="p-1.5 text-muted hover:text-danger transition-colors"
              title={t('bindCode.delete')}
            >
              <Trash2 size={14} />
            </button>
          )}
        </div>
      </div>
    );
  };

  return (
    <>
      <div className="mb-6 bg-panel border border-border rounded-xl p-4 shadow-sm">
        <div className="flex items-center justify-between mb-3 gap-2">
          <h3 className="text-lg font-semibold font-display">{t('bindCode.title')}</h3>
          <div className="flex items-center gap-2">
            {unavailableCodes.length > 0 && (
              <button
                onClick={() => setShowUnavailableModal(true)}
                className="px-3 py-1.5 bg-neutral-100 hover:bg-neutral-200 text-text rounded text-sm font-medium transition-colors"
              >
                {t('bindCode.viewUnavailable', { count: unavailableCodes.length })}
              </button>
            )}
            <button
              onClick={() => setShowForm(!showForm)}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-accent hover:bg-accent/90 text-white rounded text-sm font-medium transition-colors"
            >
              <Plus size={14} /> {t('bindCode.newCode')}
            </button>
          </div>
        </div>

        <div className="mb-4 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900">
          <div className="flex items-center gap-2 font-medium text-blue-800">
            <HelpCircle size={14} /> {t('bindCode.usageTitle')}
          </div>
          <p className="mt-1 text-blue-900">{t('bindCode.usageHint')}</p>
        </div>

        {showForm && (
          <div className="mb-4 p-3 bg-bg border border-border rounded-lg space-y-3">
            <div className="flex items-center gap-4">
              <label className="text-sm text-muted">{t('bindCode.codeType')}</label>
              <label className="flex items-center gap-1.5 text-sm">
                <input type="radio" checked={newType === 'one_time'} onChange={() => setNewType('one_time')} className="text-accent" />
                {t('bindCode.oneTime')}
              </label>
              <label className="flex items-center gap-1.5 text-sm">
                <input type="radio" checked={newType === 'expiring'} onChange={() => setNewType('expiring')} className="text-accent" />
                {t('bindCode.expiring')}
              </label>
            </div>
            {newType === 'expiring' && (
              <div className="flex items-center gap-3">
                <label className="text-sm text-muted">{t('bindCode.expirationDate')}</label>
                <input
                  type="date"
                  value={newExpiry}
                  onChange={(e) => setNewExpiry(e.target.value)}
                  min={new Date().toISOString().split('T')[0]}
                  className="bg-bg border border-border rounded px-3 py-1.5 text-sm focus:outline-none focus:border-accent text-text"
                />
              </div>
            )}
            <button
              onClick={handleCreate}
              disabled={loading || (newType === 'expiring' && !newExpiry)}
              className="px-4 py-1.5 bg-accent hover:bg-accent/90 text-white rounded text-sm font-medium disabled:opacity-50 transition-colors"
            >
              {t('bindCode.generate')}
            </button>
          </div>
        )}

        {codes.length === 0 ? (
          <p className="text-sm text-muted">{t('bindCode.noCodes')}</p>
        ) : activeCodes.length === 0 ? (
          <p className="text-sm text-muted">{t('bindCode.noAvailableCodes')}</p>
        ) : (
          <div className="divide-y divide-border">
            {activeCodes.map(renderCodeRow)}
          </div>
        )}
      </div>

      {showUnavailableModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
          role="dialog"
          aria-modal="true"
          aria-label={t('bindCode.unavailableTitle')}
          onClick={() => setShowUnavailableModal(false)}
        >
          <div
            className="bg-panel border border-border rounded-xl shadow-xl w-full max-w-2xl max-h-[75vh] flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <h4 className="text-base font-semibold text-text">{t('bindCode.unavailableTitle')}</h4>
              <button
                onClick={() => setShowUnavailableModal(false)}
                className="text-muted hover:text-text transition-colors"
                title={t('bindCode.close')}
              >
                <X size={16} />
              </button>
            </div>
            <div className="px-4 py-2 overflow-y-auto">
              {unavailableCodes.length === 0 ? (
                <p className="text-sm text-muted py-4">{t('bindCode.noUnavailableCodes')}</p>
              ) : (
                <div className="divide-y divide-border">{unavailableCodes.map(renderCodeRow)}</div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  );
};

// ─── User List Page ──────────────────────────────────────────────────────

export const UserList: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [, setLoading] = useState(false);
  const [users, setUsers] = useState<Record<string, UserConfig>>({});
  const [config, setConfig] = useState<any>({});
  const [opencodeOptionsByCwd, setOpencodeOptionsByCwd] = useState<Record<string, any>>({});
  const [claudeAgentsByCwd, setClaudeAgentsByCwd] = useState<Record<string, any[]>>({});
  const [claudeModels, setClaudeModels] = useState<string[]>([]);
  const [claudeReasoningOptions, setClaudeReasoningOptions] = useState<Record<string, { value: string; label: string }[]>>({});
  const [codexModels, setCodexModels] = useState<string[]>([]);
  const [browsingCwdFor, setBrowsingCwdFor] = useState<string | null>(null);
  const [selectedPlatform, setSelectedPlatform] = useState<string>('slack');

  useEffect(() => {
    api.getConfig().then((loadedConfig) => {
      setConfig(loadedConfig);
      const enabled = getEnabledPlatforms(loadedConfig);
      setSelectedPlatform(enabled[0] || 'slack');
    });
  }, []);

  useEffect(() => {
    if (!selectedPlatform) return;
    loadUsers(selectedPlatform);
  }, [selectedPlatform]);

  const loadUsers = async (platform: string) => {
    try {
      const result = await api.getUsers(platform);
      if (result.ok) setUsers(result.users || {});
    } catch (e) {
      console.error('Failed to load users:', e);
    }
  };

  const loadOpenCodeOptions = async (cwd: string) => {
    try {
      const result = await api.opencodeOptions(cwd);
      if (result.ok) setOpencodeOptionsByCwd((prev) => ({ ...prev, [cwd]: result.data }));
    } catch (e) { console.error('Failed to load OpenCode options:', e); }
  };

  const loadClaudeAgents = async (cwd: string) => {
    try {
      const result = await api.claudeAgents(cwd);
      if (result.ok) setClaudeAgentsByCwd((prev) => ({ ...prev, [cwd]: result.agents || [] }));
    } catch (e) { console.error('Failed to load Claude agents:', e); }
  };

  useEffect(() => {
    if (config.agents?.claude?.enabled) {
      api.claudeModels().then((r) => {
        if (r.ok) {
          setClaudeModels(r.models || []);
          setClaudeReasoningOptions(r.reasoning_options || {});
        }
      });
    }
  }, [config.agents?.claude?.enabled]);

  useEffect(() => {
    if (config.agents?.codex?.enabled) api.codexModels().then(r => r.ok && setCodexModels(r.models || []));
  }, [config.agents?.codex?.enabled]);

  // Load agent options for enabled users
  useEffect(() => {
    const defaultCwd = config.runtime?.default_cwd || '~/work';
    const defaultBackend = config.agents?.default_backend || 'opencode';
    Object.values(users).forEach((u) => {
      if (!u.enabled) return;
      const cwd = u.custom_cwd || defaultCwd;
      const backend = u.routing?.agent_backend || defaultBackend;
      if (backend === 'opencode' && config.agents?.opencode?.enabled && !opencodeOptionsByCwd[cwd]) loadOpenCodeOptions(cwd);
      if (backend === 'claude' && config.agents?.claude?.enabled && !claudeAgentsByCwd[cwd]) loadClaudeAgents(cwd);
    });
  }, [users, config]);

  const persistUsers = async (next: Record<string, UserConfig>) => {
    setLoading(true);
    try {
      await api.saveUsers({ users: next }, selectedPlatform);
      showToast(t('userList.settingsSaved'));
    } catch {
      showToast(t('userList.settingsSaveFailed'), 'error');
    } finally {
      setLoading(false);
    }
  };

  const updateUser = (userId: string, patch: Partial<UserConfig>) => {
    const base = users[userId] || defaultUserConfig();
    const next = { ...base, ...patch };
    if (!next.routing || typeof next.routing !== 'object') {
      next.routing = { agent_backend: config.agents?.default_backend || 'opencode' };
    }
    const nextUsers = { ...users, [userId]: next };
    setUsers(nextUsers);
    void persistUsers(nextUsers);
  };

  const handleToggleAdmin = async (userId: string, isAdmin: boolean) => {
    const current = users[userId];
    const currentAdminCount = Object.values(users).filter((u) => u.is_admin).length;
    if (current?.is_admin && !isAdmin && currentAdminCount <= 1) {
      if (!confirm(t('userList.lastAdminDemoteWarning'))) return;
    }
    try {
      const result = await api.toggleAdmin(userId, isAdmin, selectedPlatform);
      if (result.ok) {
        showToast(t('userList.adminToggled'));
        loadUsers(selectedPlatform);
      } else {
        showToast(result.error || t('userList.cannotRemoveLastAdmin'), 'error');
      }
    } catch (e) {
      console.error('Failed to toggle admin:', e);
    }
  };

  const handleRemoveUser = async (userId: string) => {
    const current = users[userId];
    const currentAdminCount = Object.values(users).filter((u) => u.is_admin).length;
    const warningKey = current?.is_admin && currentAdminCount <= 1 ? 'userList.lastAdminRemoveWarning' : 'userList.removeConfirm';
    if (!confirm(t(warningKey))) return;
    try {
      const result = await api.removeUser(userId, selectedPlatform);
      if (result.ok) {
        showToast(t('userList.userRemoved'));
        loadUsers(selectedPlatform);
      } else {
        showToast(result.error || '', 'error');
      }
    } catch (e) {
      console.error('Failed to remove user:', e);
    }
  };

  const defaultUserConfig = (): UserConfig => ({
    display_name: '',
    is_admin: false,
    bound_at: '',
    enabled: true,
    show_message_types: [],
    custom_cwd: '',
    routing: {
      agent_backend: null,
      claude_reasoning_effort: null,
    },
  });

  const getReasoningOptions = (cwd: string, modelKey: string) => {
    const lookup = opencodeOptionsByCwd[cwd]?.reasoning_options || {};
    return (lookup as Record<string, { value: string; label: string }[]>)[modelKey] || [];
  };

  const getClaudeReasoningOptions = (model: string) => {
    const modelKey = model || '';
    const cached = claudeReasoningOptions[modelKey];
    if (cached?.length) return cached;

    const fallback = claudeReasoningOptions[''] || [];
    if (modelKey.toLowerCase().includes('claude-opus-4-6')) {
      return fallback.some((option) => option.value === 'max')
        ? fallback
        : [...fallback, { value: 'max', label: 'Max' }];
    }

    return fallback;
  };

  const getReasoningLabel = (value: string, fallback: string) => {
    switch (value) {
      case 'low':
        return t('channelList.reasoningLow');
      case 'medium':
        return t('channelList.reasoningMedium');
      case 'high':
        return t('channelList.reasoningHigh');
      case 'max':
        return t('channelList.reasoningMax');
      default:
        return fallback;
    }
  };

  const userEntries = Object.entries(users).sort(
    (a, b) => Number(b[1].is_admin) - Number(a[1].is_admin) || a[1].display_name.localeCompare(b[1].display_name)
  );
  const enabledPlatforms = getEnabledPlatforms(config);

  return (
    <>
    <div className="max-w-5xl mx-auto flex flex-col h-full">
      <div className="flex justify-between items-center mb-6">
        <div>
          <h2 className="text-3xl font-display font-bold">{t('userList.title')}</h2>
          <p className="text-muted">{t('userList.subtitle')}</p>
        </div>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {enabledPlatforms.map((platform) => (
          <button
            key={platform}
            onClick={() => setSelectedPlatform(platform)}
            className={clsx(
              'px-3 py-1.5 rounded-full text-sm border transition-colors',
              selectedPlatform === platform ? 'bg-accent text-white border-accent' : 'bg-panel text-text border-border hover:border-accent/60'
            )}
          >
            {t(`platform.${platform}.title`)}
          </button>
        ))}
      </div>

      {selectedPlatform === 'wechat' ? (
        <div className="mb-6 bg-panel border border-border rounded-xl p-4 shadow-sm">
          <p className="text-sm text-muted">{t('wechat.userBound')}</p>
        </div>
      ) : (
        <BindCodeSection />
      )}

      <div className="flex-1 overflow-y-auto border border-border rounded-xl divide-y divide-border bg-panel shadow-sm">
        {userEntries.length === 0 ? (
          <div className="p-8 text-center text-muted">{t('userList.noUsers')}</div>
        ) : (
          userEntries.map(([userId, userConfig]) => {
            const effectiveCwd = userConfig.custom_cwd || config.runtime?.default_cwd || '~/work';
            const effectiveBackend = userConfig.routing?.agent_backend || config.agents?.default_backend || 'opencode';
            const opencodeOptions = opencodeOptionsByCwd[effectiveCwd];
            const claudeAgents = claudeAgentsByCwd[effectiveCwd] || [];
            return (
              <div key={userId} className="p-4 hover:bg-neutral-50/50 transition-colors">
                {/* User header row */}
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <User size={20} className="text-muted" />
                    <div>
                      <div className="font-medium flex items-center gap-2 text-text">
                        {userConfig.display_name || userId}
                        {userConfig.is_admin && (
                          <span className="text-xs px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20 font-medium">{t('userList.admin')}</span>
                        )}
                      </div>
                      <div className="text-xs text-muted font-mono">ID: {userId}</div>
                      {userConfig.bound_at && (
                        <div className="text-xs text-muted">
                          {t('userList.boundAt')}: {new Date(userConfig.bound_at).toLocaleDateString()}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {/* Admin toggle */}
                    <button
                      onClick={() => handleToggleAdmin(userId, !userConfig.is_admin)}
                      className={clsx(
                        'flex items-center gap-1.5 px-2.5 py-1 rounded text-xs font-medium transition-colors border',
                        userConfig.is_admin
                          ? 'bg-accent/10 text-accent border-accent/20 hover:bg-accent/20'
                          : 'bg-neutral-100 text-muted border-border hover:bg-neutral-200'
                      )}
                      title={userConfig.is_admin ? 'Remove admin' : 'Make admin'}
                    >
                      {userConfig.is_admin ? <Shield size={12} /> : <ShieldOff size={12} />}
                      {userConfig.is_admin ? t('userList.admin') : t('userList.user')}
                    </button>
                    {/* Remove */}
                    <button
                      onClick={() => handleRemoveUser(userId)}
                      className="p-1.5 text-muted hover:text-danger transition-colors"
                      title={t('userList.removeUser')}
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>

                {/* User settings (always shown for enabled users) */}
                {userConfig.enabled && (
                  <div className="mt-4 pl-8 space-y-4">
                    {/* Basic Settings */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-muted uppercase">{t('channelList.workingDirectory')}</label>
                        <div className="flex gap-1.5">
                          <BlurInput
                            type="text"
                            placeholder={config.runtime?.default_cwd || t('channelList.useGlobalDefault')}
                            value={userConfig.custom_cwd}
                            onCommit={(v) => updateUser(userId, { custom_cwd: v })}
                            className="flex-1 bg-bg border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-accent text-text placeholder:text-muted/50 font-mono"
                          />
                          <button
                            type="button"
                            onClick={() => setBrowsingCwdFor(userId)}
                            title={t('directoryBrowser.title')}
                            className="px-2 py-2 bg-neutral-100 hover:bg-neutral-200 border border-border rounded text-muted hover:text-text transition-colors shrink-0"
                          >
                            <FolderOpen size={14} />
                          </button>
                        </div>
                      </div>
                      <div className="space-y-1">
                        <label className="text-xs font-medium text-muted uppercase">{t('channelList.backend')}</label>
                        <select
                          value={effectiveBackend}
                          onChange={(e) => updateUser(userId, { routing: { ...userConfig.routing, agent_backend: e.target.value } })}
                          className="w-full bg-bg border border-border rounded px-3 py-2 text-sm focus:outline-none focus:border-accent text-text"
                        >
                          <option value="opencode">OpenCode</option>
                          <option value="claude">ClaudeCode</option>
                          <option value="codex">Codex</option>
                        </select>
                      </div>
                    </div>

                    {/* Show Message Types */}
                    <div className="space-y-2">
                      <div className="text-xs font-medium text-muted uppercase flex items-center gap-1">
                        {t('channelList.showMessageTypes')}
                        <span className="relative group">
                          <HelpCircle size={12} className="text-muted/50 cursor-help" />
                          <span className="absolute bottom-full left-0 mb-2 px-3 py-2 bg-text text-bg text-xs rounded shadow-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10 w-64 whitespace-normal font-normal normal-case">
                            {t('channelList.showMessageTypesHint')}
                          </span>
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-3 text-sm">
                        {['system', 'assistant', 'toolcall'].map((msgType) => {
                          const checked = (userConfig.show_message_types || []).includes(msgType);
                          return (
                            <label key={msgType} className="flex items-center gap-2 text-text">
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => {
                                  const next = checked
                                    ? userConfig.show_message_types.filter((v) => v !== msgType)
                                    : [...(userConfig.show_message_types || []), msgType];
                                  updateUser(userId, { show_message_types: next });
                                }}
                                className="h-4 w-4 rounded border-border text-accent focus:ring-accent"
                              />
                              <span className="capitalize">{msgType === 'toolcall' ? 'Toolcall' : msgType}</span>
                            </label>
                          );
                        })}
                      </div>
                    </div>

                    {/* OpenCode Settings */}
                    {effectiveBackend === 'opencode' && (
                      <div className="space-y-3">
                        <div className="text-xs font-medium text-muted uppercase">{t('channelList.opencodeSettings')}</div>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 bg-bg/50 p-3 rounded border border-border">
                          <div className="space-y-1">
                            <label className="text-xs text-muted">{t('channelList.agent')}</label>
                            <select
                              value={userConfig.routing.opencode_agent || ''}
                              onChange={(e) => updateUser(userId, { routing: { ...userConfig.routing, opencode_agent: e.target.value || null } })}
                              className="w-full bg-panel border border-border rounded px-3 py-2 text-sm"
                            >
                              <option value="">{t('common.default')}</option>
                              {(opencodeOptions?.agents || []).map((a: any) => (
                                <option key={a.name} value={a.name}>{a.name}</option>
                              ))}
                            </select>
                          </div>
                          <div className="space-y-1">
                            <label className="text-xs text-muted">{t('channelList.model')}</label>
                            <select
                              value={userConfig.routing.opencode_model || ''}
                              onChange={(e) => updateUser(userId, { routing: { ...userConfig.routing, opencode_model: e.target.value || null, opencode_reasoning_effort: null } })}
                              className="w-full bg-panel border border-border rounded px-3 py-2 text-sm"
                            >
                              <option value="">{t('common.default')}</option>
                              {(opencodeOptions?.models?.providers || []).flatMap((provider: any) => {
                                const pid = provider.id || provider.provider_id || provider.name;
                                const pLabel = provider.name || pid;
                                const models = provider.models || {};
                                if (Array.isArray(models)) {
                                  return models.map((m: any) => {
                                    const mid = typeof m === 'string' ? m : m.id;
                                    return <option key={`${pid}:${mid}`} value={`${pid}/${mid}`}>{pLabel}/{mid}</option>;
                                  });
                                }
                                return Object.keys(models).map((mid) => (
                                  <option key={`${pid}:${mid}`} value={`${pid}/${mid}`}>{pLabel}/{mid}</option>
                                ));
                              })}
                            </select>
                          </div>
                          <div className="space-y-1">
                            <label className="text-xs text-muted">{t('channelList.reasoningEffort')}</label>
                            <select
                              value={userConfig.routing.opencode_reasoning_effort || ''}
                              onChange={(e) => updateUser(userId, { routing: { ...userConfig.routing, opencode_reasoning_effort: e.target.value || null } })}
                              disabled={!getReasoningOptions(effectiveCwd, userConfig.routing.opencode_model || '').length}
                              className="w-full bg-panel border border-border rounded px-3 py-2 text-sm disabled:opacity-50"
                            >
                              <option value="">{t('common.default')}</option>
                              {getReasoningOptions(effectiveCwd, userConfig.routing.opencode_model || '').map((opt) => (
                                <option key={opt.value} value={opt.value}>{opt.label}</option>
                              ))}
                            </select>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Claude Settings */}
                    {effectiveBackend === 'claude' && (
                      <div className="space-y-3">
                        <div className="text-xs font-medium text-muted uppercase">{t('channelList.claudeSettings')}</div>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 bg-bg/50 p-3 rounded border border-border">
                          <div className="space-y-1">
                            <label className="text-xs text-muted">{t('channelList.agent')}</label>
                            <select
                              value={userConfig.routing.claude_agent || ''}
                              onChange={(e) => updateUser(userId, { routing: { ...userConfig.routing, claude_agent: e.target.value || null } })}
                              className="w-full bg-panel border border-border rounded px-3 py-2 text-sm"
                            >
                              <option value="">{t('common.default')}</option>
                              {claudeAgents.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}
                            </select>
                          </div>
                          <div className="space-y-1">
                            <label className="text-xs text-muted">{t('channelList.model')}</label>
                            <Combobox
                              options={[{ value: '', label: t('common.default') }, ...claudeModels.map(m => ({ value: m, label: m }))]}
                              value={userConfig.routing.claude_model || ''}
                              onValueChange={(v) => updateUser(userId, { routing: { ...userConfig.routing, claude_model: v || null, claude_reasoning_effort: null } })}
                              placeholder={t('channelList.claudeModelPlaceholder')}
                              searchPlaceholder={t('channelList.searchModel')}
                              allowCustomValue={true}
                            />
                          </div>
                          <div className="space-y-1">
                            <label className="text-xs text-muted">{t('channelList.reasoningEffort')}</label>
                            <select
                              value={userConfig.routing.claude_reasoning_effort || ''}
                              onChange={(e) => updateUser(userId, { routing: { ...userConfig.routing, claude_reasoning_effort: e.target.value || null } })}
                              className="w-full bg-panel border border-border rounded px-3 py-2 text-sm"
                            >
                              <option value="">{t('common.default')}</option>
                              {getClaudeReasoningOptions(userConfig.routing.claude_model || '')
                                .filter((option) => option.value !== '__default__')
                                .map((option) => (
                                  <option key={option.value} value={option.value}>
                                    {getReasoningLabel(option.value, option.label)}
                                  </option>
                                ))}
                            </select>
                          </div>
                        </div>
                      </div>
                    )}

                    {/* Codex Settings */}
                    {effectiveBackend === 'codex' && (
                      <div className="space-y-3">
                        <div className="text-xs font-medium text-muted uppercase">{t('channelList.codexSettings')}</div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-3 bg-bg/50 p-3 rounded border border-border">
                          <div className="space-y-1">
                            <label className="text-xs text-muted">{t('channelList.model')}</label>
                            <Combobox
                              options={[{ value: '', label: t('common.default') }, ...codexModels.map(m => ({ value: m, label: m }))]}
                              value={userConfig.routing.codex_model || ''}
                              onValueChange={(v) => updateUser(userId, { routing: { ...userConfig.routing, codex_model: v || null } })}
                              placeholder={t('channelList.codexModelPlaceholder')}
                              searchPlaceholder={t('channelList.searchModel')}
                              allowCustomValue={true}
                            />
                          </div>
                          <div className="space-y-1">
                            <label className="text-xs text-muted">{t('channelList.reasoningEffort')}</label>
                            <select
                              value={userConfig.routing.codex_reasoning_effort || ''}
                              onChange={(e) => updateUser(userId, { routing: { ...userConfig.routing, codex_reasoning_effort: e.target.value || null } })}
                              className="w-full bg-panel border border-border rounded px-3 py-2 text-sm"
                            >
                              <option value="">{t('common.default')}</option>
                              <option value="low">{t('channelList.reasoningLow')}</option>
                              <option value="medium">{t('channelList.reasoningMedium')}</option>
                              <option value="high">{t('channelList.reasoningHigh')}</option>
                              <option value="xhigh">{t('channelList.reasoningXHigh')}</option>
                            </select>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>

    {/* Directory browser modal */}
    {browsingCwdFor && (
      <DirectoryBrowser
        initialPath={users[browsingCwdFor]?.custom_cwd || config.runtime?.default_cwd || '~/work'}
        onSelect={(path) => {
          updateUser(browsingCwdFor, { custom_cwd: path });
          setBrowsingCwdFor(null);
        }}
        onClose={() => setBrowsingCwdFor(null)}
      />
    )}
    </>
  );
};
