import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bot, ChevronDown, Funnel, Loader2, Plus, RefreshCw, Search, Trash2, Upload } from 'lucide-react';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import type { VibeAgentBrief, VibeAgentFull } from '../../context/ApiContext';
import { useToast } from '../../context/ToastContext';
import { NewAgentDialog } from './NewAgentDialog';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Switch } from '../ui/switch';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';

const BACKEND_ORDER = ['claude', 'opencode', 'codex'] as const;
type Backend = (typeof BACKEND_ORDER)[number];

const BACKEND_LABEL: Record<Backend, string> = {
  claude: 'Claude',
  opencode: 'OpenCode',
  codex: 'Codex',
};

const BACKEND_ICON_CLASS: Record<Backend, string> = {
  claude: 'text-mint',
  opencode: 'text-cyan',
  codex: 'text-violet',
};

const EFFORT_OPTIONS = ['low', 'medium', 'high', 'max'];

function isSystemAgent(agent: { source: string }): boolean {
  return agent.source === 'builtin' || agent.source === 'system';
}

export const AgentsPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const { showToast } = useToast();
  const [agents, setAgents] = useState<VibeAgentBrief[]>([]);
  const [defaultName, setDefaultName] = useState<string | null>(null);
  const [selected, setSelected] = useState<VibeAgentFull | null>(null);
  const [loading, setLoading] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [backendFilter, setBackendFilter] = useState<Backend | 'all'>('all');
  const [importing, setImporting] = useState<Backend | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.listVibeAgents({ includeDisabled: true });
      setAgents(result.agents);
      setDefaultName(result.default_agent_name);
      // Keep the currently-selected agent fresh after edits / refreshes.
      if (selected) {
        const fresh = result.agents.find((a) => a.name === selected.name);
        if (!fresh) setSelected(null);
      }
    } catch (err: any) {
      setError(err?.message ?? String(err));
    } finally {
      setLoading(false);
    }
  }, [api, selected]);

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Auto-select the default agent on first load so the detail panel has
  // something to show — eliminates the empty "select an agent" state
  // that confused users on first visit.
  useEffect(() => {
    if (selected || agents.length === 0) return;
    const target = (defaultName && agents.find((a) => a.name === defaultName)) || agents[0];
    if (target) selectAgent(target.name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultName, agents]);

  const selectAgent = useCallback(
    async (name: string) => {
      try {
        const result = await api.getVibeAgent(name);
        if (result.ok) setSelected(result.agent);
      } catch (err: any) {
        setError(err?.message ?? String(err));
      }
    },
    [api],
  );

  // Apply text search + backend filter; backend grouping is a layout
  // concern that operates on the filtered set.
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return agents.filter((agent) => {
      if (backendFilter !== 'all' && agent.backend !== backendFilter) return false;
      if (!q) return true;
      return (
        agent.name.toLowerCase().includes(q) ||
        (agent.description ?? '').toLowerCase().includes(q) ||
        (agent.model ?? '').toLowerCase().includes(q)
      );
    });
  }, [agents, search, backendFilter]);

  const grouped = useMemo(() => {
    const groups: Record<Backend, VibeAgentBrief[]> = { claude: [], opencode: [], codex: [] };
    for (const agent of filtered) {
      const key = (agent.backend as Backend) in groups ? (agent.backend as Backend) : null;
      if (key) groups[key].push(agent);
    }
    return groups;
  }, [filtered]);

  const onCreated = (agent: VibeAgentFull) => {
    refresh().then(() => setSelected(agent));
  };

  const updateField = async (patch: Partial<VibeAgentFull>) => {
    if (!selected) return;
    try {
      const result = await api.updateVibeAgent(selected.name, patch as any);
      if (result.ok) {
        setSelected(result.agent);
        refresh();
      }
    } catch (err: any) {
      setError(err?.message ?? String(err));
    }
  };

  const onDelete = async () => {
    if (!selected || isSystemAgent(selected)) return;
    const confirmed = window.confirm(t('agents.deleteConfirm', { name: selected.name }));
    if (!confirmed) return;
    try {
      const result = await api.removeVibeAgent(selected.name);
      if (result.ok) {
        setSelected(null);
        refresh();
      } else if (result.code === 'agent_in_use') {
        setError(t('agents.deleteInUse', { name: selected.name }));
      } else if (result.message) {
        setError(result.message);
      }
    } catch (err: any) {
      setError(err?.message ?? String(err));
    }
  };

  const onImport = async (from: Backend) => {
    setImporting(from);
    try {
      const result = await api.importVibeAgents({ from, all: true });
      if (result.ok) {
        const created = result.created?.length ?? 0;
        const skipped = result.skipped?.length ?? 0;
        showToast(t('agents.importSuccess', { created, skipped }), 'success');
        refresh();
      } else {
        showToast(
          t('agents.importFailed', { error: result.message || result.error || result.code || 'unknown' }),
          'error',
        );
      }
    } catch (err: any) {
      showToast(t('agents.importFailed', { error: err?.message ?? String(err) }), 'error');
    } finally {
      setImporting(null);
    }
  };

  const totalShown = filtered.length;
  const noMatches = totalShown === 0 && agents.length > 0;

  return (
    <div className="mx-auto flex w-full max-w-[1200px] flex-col gap-5 py-2">
      {/* Header — design.pen l5V2m: 40x40 mint-soft icon + title + subtitle */}
      <div className="flex items-center gap-4">
        <div className="flex size-10 shrink-0 items-center justify-center rounded-[10px] border border-mint/40 bg-mint-soft text-mint shadow-[0_0_18px_-6px_rgba(91,255,160,0.5)]">
          <Bot className="size-5" />
        </div>
        <div className="flex flex-1 flex-col">
          <h1 className="text-[24px] font-bold text-foreground">{t('agents.title')}</h1>
          <p className="text-[12px] text-muted">{t('agents.subtitle', { count: agents.length })}</p>
        </div>
        <Button type="button" variant="outline" size="xs" onClick={() => refresh()} disabled={loading}>
          <RefreshCw className={clsx('size-3.5', loading && 'animate-spin')} />
          {t('common.refresh')}
        </Button>
      </div>

      {/* Toolbar — design.pen Imduv: search + backend filter + spacer + Import + 新建 Agent */}
      <div className="flex flex-wrap items-center gap-2.5">
        <div className="flex w-[320px] items-center gap-2 rounded-md border border-border-strong bg-surface px-3 py-2">
          <Search className="size-3.5 shrink-0 text-muted" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('agents.searchPlaceholder')}
            className="flex-1 bg-transparent text-[12px] text-foreground outline-none placeholder:text-muted"
          />
        </div>
        <BackendFilter value={backendFilter} onChange={setBackendFilter} />
        <div className="flex-1" />
        <ImportMenu onImport={onImport} importing={importing} />
        <Button type="button" variant="brand" size="xs" onClick={() => setShowNew(true)}>
          <Plus />
          {t('agents.newAgent')}
        </Button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
          {error}
        </div>
      )}

      {/* Body — list + detail. The detail column only renders when a row
          is selected; the empty "select an agent" placeholder used to
          dominate the right side of a fresh page. With auto-select on
          mount it's rarely needed; when it is empty we just collapse
          back to a single column. */}
      <div
        className={clsx(
          'grid gap-5',
          selected ? 'grid-cols-1 lg:grid-cols-[1fr_420px]' : 'grid-cols-1',
        )}
      >
        <div className="flex flex-col gap-4">
          {BACKEND_ORDER.map((backend) => {
            const items = grouped[backend];
            if (!items || items.length === 0) return null;
            return (
              <div key={backend} className="flex flex-col gap-2">
                <div className="flex items-center gap-2 px-1">
                  <Bot className={clsx('size-3.5', BACKEND_ICON_CLASS[backend])} />
                  <span className={clsx('text-[13px] font-bold', BACKEND_ICON_CLASS[backend])}>
                    {BACKEND_LABEL[backend]}
                  </span>
                  <span className="font-mono text-[10px] text-muted">
                    {items.length} agents
                  </span>
                </div>
                <div className="flex flex-col gap-2">
                  {items.map((agent) => (
                    <AgentRow
                      key={agent.id}
                      agent={agent}
                      isSelected={selected?.name === agent.name}
                      isDefault={defaultName === agent.name}
                      onSelect={() => selectAgent(agent.name)}
                    />
                  ))}
                </div>
              </div>
            );
          })}

          {agents.length === 0 && !loading && (
            <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border bg-surface px-6 py-16 text-center">
              <Bot className="size-8 text-muted" />
              <div className="text-[14px] font-semibold text-foreground">{t('agents.empty')}</div>
              <Button type="button" variant="brand" size="sm" onClick={() => setShowNew(true)}>
                <Plus />
                {t('agents.newAgent')}
              </Button>
            </div>
          )}

          {noMatches && (
            <div className="rounded-xl border border-dashed border-border bg-surface px-6 py-10 text-center text-[12px] text-muted">
              {t('agents.noSearchMatch')}
            </div>
          )}
        </div>

        {selected && (
          <div className="self-start rounded-2xl border border-border-strong bg-surface p-5">
            <AgentDetailPanel
              agent={selected}
              isDefault={defaultName === selected.name}
              onChange={updateField}
              onSetDefault={async () => {
                try {
                  const result = await api.setDefaultVibeAgent(selected.name);
                  if (result.ok) setDefaultName(result.default_agent_name);
                } catch (err: any) {
                  setError(err?.message ?? String(err));
                }
              }}
              onDelete={onDelete}
            />
          </div>
        )}
      </div>

      <NewAgentDialog open={showNew} onClose={() => setShowNew(false)} onCreated={onCreated} />
    </div>
  );
};

// One row in the backend-grouped list. Hover state + click selects.
interface AgentRowProps {
  agent: VibeAgentBrief;
  isSelected: boolean;
  isDefault: boolean;
  onSelect: () => void;
}

const AgentRow: React.FC<AgentRowProps> = ({ agent, isSelected, isDefault, onSelect }) => {
  const description = [agent.model, agent.reasoning_effort, agent.description].filter(Boolean).join(' · ');
  return (
    <button
      type="button"
      onClick={onSelect}
      className={clsx(
        'flex items-center gap-3 rounded-xl border px-4 py-3 text-left transition',
        isSelected
          ? 'border-mint/40 bg-mint-soft shadow-[0_0_18px_-10px_rgba(91,255,160,0.6)]'
          : 'border-border bg-surface hover:border-border-strong hover:bg-surface-2',
      )}
    >
      <div className="flex flex-1 flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="text-[14px] font-semibold text-foreground">{agent.name}</span>
          {isDefault && <Badge variant="success" className="px-1.5 py-0 text-[9px] font-mono uppercase">DEFAULT</Badge>}
          {isSystemAgent(agent) && (
            <Badge variant="secondary" className="px-1.5 py-0 text-[9px] font-mono uppercase">SYSTEM</Badge>
          )}
        </div>
        {description && <div className="text-[11px] text-muted">{description}</div>}
      </div>
      <Badge variant={agent.enabled ? 'success' : 'secondary'} className="font-mono uppercase">
        <span className={clsx('size-1.5 rounded-full', agent.enabled ? 'bg-mint' : 'bg-muted')} />
        {agent.enabled ? 'enabled' : 'disabled'}
      </Badge>
    </button>
  );
};

interface BackendFilterProps {
  value: Backend | 'all';
  onChange: (next: Backend | 'all') => void;
}

// Compact Popover trigger that mirrors design.pen dMFRl — funnel icon +
// "Backend: All" label + chevron. Replaces the old hand-rolled checkbox.
const BackendFilter: React.FC<BackendFilterProps> = ({ value, onChange }) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const label = value === 'all' ? t('agents.backendAll') : BACKEND_LABEL[value];
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="flex items-center gap-1.5 rounded-md border border-border-strong bg-surface px-3 py-2 text-[12px] font-medium text-foreground transition hover:bg-foreground/[0.04]"
        >
          <Funnel className="size-3 text-muted" />
          <span className="text-muted">{t('agents.backendFilter')}:</span>
          <span>{label}</span>
          <ChevronDown className="size-3 text-muted" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[180px] p-1">
        {(['all', ...BACKEND_ORDER] as const).map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => {
              onChange(key);
              setOpen(false);
            }}
            className={clsx(
              'flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] transition',
              value === key ? 'bg-cyan-soft text-cyan' : 'text-foreground hover:bg-foreground/[0.04]',
            )}
          >
            {key !== 'all' && <Bot className={clsx('size-3.5', BACKEND_ICON_CLASS[key])} />}
            <span>{key === 'all' ? t('agents.backendAll') : BACKEND_LABEL[key]}</span>
          </button>
        ))}
      </PopoverContent>
    </Popover>
  );
};

interface ImportMenuProps {
  onImport: (from: Backend) => void;
  importing: Backend | null;
}

// Outline Button that opens a popover with one entry per backend. The
// backend supports bulk import via `from=<backend>&all=true`, which
// surfaces every installed agent definition the user already has on
// disk for that backend.
const ImportMenu: React.FC<ImportMenuProps> = ({ onImport, importing }) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button type="button" variant="outline" size="xs" disabled={importing !== null}>
          {importing ? <Loader2 className="size-3.5 animate-spin" /> : <Upload className="size-3.5" />}
          {t('agents.import')}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[200px] p-1">
        {BACKEND_ORDER.map((backend) => (
          <button
            key={backend}
            type="button"
            disabled={importing !== null}
            onClick={() => {
              onImport(backend);
              setOpen(false);
            }}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] text-foreground transition hover:bg-foreground/[0.04] disabled:opacity-50"
          >
            <Bot className={clsx('size-3.5', BACKEND_ICON_CLASS[backend])} />
            <span>{t(`agents.importFrom${BACKEND_LABEL[backend]}` as const)}</span>
          </button>
        ))}
      </PopoverContent>
    </Popover>
  );
};

interface DetailProps {
  agent: VibeAgentFull;
  isDefault: boolean;
  onChange: (patch: Partial<VibeAgentFull>) => void;
  onSetDefault: () => void;
  onDelete: () => void;
}

const AgentDetailPanel: React.FC<DetailProps> = ({ agent, isDefault, onChange, onSetDefault, onDelete }) => {
  const { t } = useTranslation();
  const system = isSystemAgent(agent);
  const [model, setModel] = useState(agent.model ?? '');
  const [effort, setEffort] = useState(agent.reasoning_effort ?? 'medium');
  const [systemPrompt, setSystemPrompt] = useState(agent.system_prompt ?? '');

  useEffect(() => {
    setModel(agent.model ?? '');
    setEffort(agent.reasoning_effort ?? 'medium');
    setSystemPrompt(agent.system_prompt ?? '');
  }, [agent.id]);

  return (
    <div className="flex flex-col gap-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        {system && <Badge variant="secondary" className="px-1.5 py-0 text-[9px] font-mono uppercase">SYSTEM</Badge>}
        <div className="flex-1 truncate text-[15px] font-bold text-foreground">{agent.name}</div>
        {isDefault ? (
          <Badge variant="success" className="px-1.5 py-0 text-[9px] font-mono uppercase">DEFAULT</Badge>
        ) : (
          <Button type="button" variant="outline" size="xs" onClick={onSetDefault}>
            {t('agents.makeDefault')}
          </Button>
        )}
      </div>

      {/* Enable toggle */}
      <div
        className={clsx(
          'flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5',
          agent.enabled ? 'border-mint/30 bg-mint-soft' : 'border-border-strong bg-surface-2',
        )}
      >
        <div className="flex flex-col">
          <span className="text-[12px] font-bold text-foreground">{t('agents.detail.enabled')}</span>
          <span className="text-[10px] text-muted">{t('agents.detail.enabledHint')}</span>
        </div>
        <Switch
          checked={agent.enabled}
          onCheckedChange={(next) => onChange({ enabled: next })}
          label={t('agents.detail.enabled')}
        />
      </div>

      {/* Backend (read-only) */}
      <Field label={t('agents.detail.backend')}>
        <div className="rounded-md border border-border bg-surface-3 px-3 py-2 text-[12px] text-muted">
          <span className="font-mono font-semibold text-foreground">{agent.backend}</span>
          <span className="ml-2 text-[10px]">{t('agents.detail.backendLocked')}</span>
        </div>
      </Field>

      {/* Model */}
      <Field label={t('agents.detail.model')}>
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          onBlur={() => {
            if (model !== (agent.model ?? '')) onChange({ model: model.trim() || null });
          }}
          placeholder={t('agents.create.modelPlaceholder')}
          className="rounded-md border border-border-strong bg-surface-2 px-3 py-2 font-mono text-[12px] text-foreground outline-none focus:border-cyan"
        />
      </Field>

      {/* Effort */}
      <Field label={t('agents.detail.effort')}>
        <div className="flex rounded-md border border-border-strong bg-surface-2 p-0.5">
          {EFFORT_OPTIONS.map((opt) => (
            <button
              key={opt}
              type="button"
              onClick={() => {
                setEffort(opt);
                onChange({ reasoning_effort: opt });
              }}
              className={clsx(
                'flex-1 rounded px-2 py-1 text-[11px] font-semibold capitalize transition',
                effort === opt ? 'bg-mint-soft text-mint' : 'text-muted hover:text-foreground',
              )}
            >
              {opt}
            </button>
          ))}
        </div>
      </Field>

      {/* System prompt */}
      <Field label={t('agents.detail.systemPrompt')}>
        <textarea
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          onBlur={() => {
            if (systemPrompt !== (agent.system_prompt ?? '')) {
              onChange({ system_prompt: systemPrompt.trim() || null });
            }
          }}
          rows={5}
          placeholder={t('agents.create.systemPromptPlaceholder')}
          className="rounded-md border border-border-strong bg-surface-3 px-3 py-2 text-[12px] text-foreground outline-none focus:border-cyan"
        />
        <span className="text-[10px] text-muted">{t('agents.detail.systemPromptHint')}</span>
      </Field>

      {/* Footer actions */}
      <div className="flex items-center gap-2 pt-1">
        {!system ? (
          <Button type="button" variant="outline" size="xs" onClick={onDelete} className="border-pink/40 bg-pink/[0.08] text-pink hover:bg-pink/[0.14]">
            <Trash2 />
            {t('common.delete')}
          </Button>
        ) : (
          <span className="text-[10px] text-muted">{t('agents.detail.systemLocked')}</span>
        )}
      </div>
    </div>
  );
};

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="flex flex-col gap-1.5">
    <div className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted">{label}</div>
    {children}
  </div>
);
