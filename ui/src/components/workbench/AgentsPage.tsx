import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Bot, Plus, RefreshCw, Trash2 } from 'lucide-react';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import type { VibeAgentBrief, VibeAgentFull } from '../../context/ApiContext';
import { NewAgentDialog } from './NewAgentDialog';

const BACKEND_ORDER = ['claude', 'opencode', 'codex'] as const;
type Backend = (typeof BACKEND_ORDER)[number];

const BACKEND_LABEL: Record<Backend, string> = {
  claude: 'Claude',
  opencode: 'OpenCode',
  codex: 'Codex',
};

const BACKEND_COLOR: Record<Backend, { ic: string; text: string; soft: string }> = {
  claude: { ic: 'text-mint', text: 'text-mint', soft: 'border-mint/30 bg-mint/[0.06]' },
  opencode: { ic: 'text-cyan', text: 'text-cyan', soft: 'border-cyan/30 bg-cyan/[0.06]' },
  codex: { ic: 'text-violet', text: 'text-violet', soft: 'border-violet/30 bg-violet/[0.06]' },
};

const EFFORT_OPTIONS = ['low', 'medium', 'high', 'max'];

function isSystemAgent(agent: { source: string }): boolean {
  return agent.source === 'builtin' || agent.source === 'system';
}

export const AgentsPage: React.FC = () => {
  const { t } = useTranslation();
  const api = useApi();
  const [agents, setAgents] = useState<VibeAgentBrief[]>([]);
  const [defaultName, setDefaultName] = useState<string | null>(null);
  const [selected, setSelected] = useState<VibeAgentFull | null>(null);
  const [loading, setLoading] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showDisabled, setShowDisabled] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.listVibeAgents({ includeDisabled: showDisabled });
      setAgents(result.agents);
      setDefaultName(result.default_agent_name);
      // Keep the currently-selected agent fresh after edits / refreshes.
      if (selected) {
        const fresh = result.agents.find((a) => a.name === selected.name);
        if (!fresh) {
          setSelected(null);
        }
      }
    } catch (err: any) {
      setError(err?.message ?? String(err));
    } finally {
      setLoading(false);
    }
  }, [api, showDisabled, selected]);

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showDisabled]);

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

  const grouped = useMemo(() => {
    const groups: Record<Backend, VibeAgentBrief[]> = { claude: [], opencode: [], codex: [] };
    for (const agent of agents) {
      const key = (agent.backend as Backend) in groups ? (agent.backend as Backend) : null;
      if (key) groups[key].push(agent);
    }
    return groups;
  }, [agents]);

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

  return (
    <div className="mx-auto flex w-full max-w-[1080px] flex-col gap-5 py-2">
      {/* Header */}
      <div className="flex items-center gap-4">
        <div className="flex size-12 shrink-0 items-center justify-center rounded-2xl border border-mint/30 bg-mint/[0.08] text-mint shadow-[0_0_24px_-6px_rgba(91,255,160,0.5)]">
          <Bot className="size-5" />
        </div>
        <div className="flex flex-1 flex-col">
          <h1 className="text-2xl font-bold text-foreground">{t('agents.title')}</h1>
          <p className="text-[13px] text-muted">
            {t('agents.subtitle', { count: agents.length })}
          </p>
        </div>
        <button
          type="button"
          onClick={() => refresh()}
          disabled={loading}
          className={clsx(
            'flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-[12px] font-medium transition',
            loading
              ? 'cursor-wait border-border bg-foreground/[0.02] text-muted'
              : 'border-border-strong text-foreground hover:bg-foreground/[0.04]',
          )}
        >
          <RefreshCw className={clsx('size-3.5', loading && 'animate-spin')} />
          {t('common.refresh')}
        </button>
      </div>

      {/* Toolbar */}
      <div className="flex items-center gap-2">
        <label className="inline-flex cursor-pointer items-center gap-2 text-[12px] text-muted">
          <input
            type="checkbox"
            checked={showDisabled}
            onChange={(e) => setShowDisabled(e.target.checked)}
            className="accent-mint"
          />
          {t('agents.showDisabled')}
        </label>
        <div className="flex-1" />
        <button
          type="button"
          onClick={() => setShowNew(true)}
          className="inline-flex items-center gap-1.5 rounded-md bg-mint px-3 py-1.5 text-[12px] font-bold text-[#080812] shadow-[0_0_14px_-4px_rgba(91,255,160,0.6)] hover:brightness-110"
        >
          <Plus className="size-3.5" />
          {t('agents.newAgent')}
        </button>
      </div>

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
          {error}
        </div>
      )}

      {/* Body: list + detail */}
      <div className="grid grid-cols-[1fr_380px] gap-5">
        <div className="flex flex-col gap-5">
          {BACKEND_ORDER.map((backend) => {
            const items = grouped[backend];
            if (!items || items.length === 0) return null;
            const color = BACKEND_COLOR[backend];
            return (
              <div key={backend} className="flex flex-col gap-2">
                <div className="flex items-center gap-2 px-1">
                  <Bot className={clsx('size-3.5', color.ic)} />
                  <span className={clsx('text-[13px] font-bold', color.text)}>
                    {BACKEND_LABEL[backend]}
                  </span>
                  <span className="font-mono text-[10px] text-muted">{items.length} agents</span>
                </div>
                <div className="flex flex-col gap-2">
                  {items.map((agent) => {
                    const isSelected = selected?.name === agent.name;
                    const isDefault = defaultName === agent.name;
                    return (
                      <button
                        key={agent.id}
                        type="button"
                        onClick={() => selectAgent(agent.name)}
                        className={clsx(
                          'flex items-center gap-3 rounded-lg border px-4 py-3 text-left transition',
                          isSelected
                            ? 'border-mint/40 bg-mint/[0.05]'
                            : 'border-border bg-surface hover:bg-foreground/[0.03]',
                        )}
                      >
                        <div className="flex flex-1 flex-col gap-0.5">
                          <div className="flex items-center gap-2">
                            <span className="text-[14px] font-semibold text-foreground">{agent.name}</span>
                            {isDefault && (
                              <span className="rounded border border-mint/40 bg-mint/[0.10] px-1.5 py-0 font-mono text-[9px] font-bold text-mint">
                                DEFAULT
                              </span>
                            )}
                            {isSystemAgent(agent) && (
                              <span className="rounded border border-border-strong bg-surface-2 px-1.5 py-0 font-mono text-[9px] text-muted">
                                SYSTEM
                              </span>
                            )}
                          </div>
                          <div className="text-[11px] text-muted">
                            {[agent.model, agent.reasoning_effort, agent.description]
                              .filter(Boolean)
                              .join(' · ') || t('agents.noConfig')}
                          </div>
                        </div>
                        <span
                          className={clsx(
                            'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[10px] font-mono font-bold',
                            agent.enabled
                              ? 'border-mint/30 bg-mint/[0.08] text-mint'
                              : 'border-border-strong bg-foreground/[0.04] text-muted',
                          )}
                        >
                          <span className={clsx('size-1.5 rounded-full', agent.enabled ? 'bg-mint' : 'bg-muted')} />
                          {agent.enabled ? 'enabled' : 'disabled'}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}

          {agents.length === 0 && !loading && (
            <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border bg-surface px-6 py-16 text-center">
              <Bot className="size-8 text-muted" />
              <div className="text-[14px] font-semibold text-foreground">{t('agents.empty')}</div>
              <button
                type="button"
                onClick={() => setShowNew(true)}
                className="rounded-md bg-mint px-3 py-1.5 text-[12px] font-bold text-[#080812]"
              >
                {t('agents.newAgent')}
              </button>
            </div>
          )}
        </div>

        {/* Detail panel */}
        <div className="flex flex-col gap-3 self-start rounded-xl border border-border-strong bg-surface p-5">
          {selected ? (
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
          ) : (
            <div className="flex flex-col items-center justify-center gap-3 py-12 text-center text-[12px] text-muted">
              <Bot className="size-6 text-muted" />
              {t('agents.selectPrompt')}
            </div>
          )}
        </div>
      </div>

      <NewAgentDialog open={showNew} onClose={() => setShowNew(false)} onCreated={onCreated} />
    </div>
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
        {system && (
          <span className="rounded border border-border-strong bg-surface-2 px-1.5 py-0 font-mono text-[9px] text-muted">
            SYSTEM
          </span>
        )}
        <div className="flex-1 truncate text-[15px] font-bold text-foreground">{agent.name}</div>
        {isDefault ? (
          <span className="rounded border border-mint/40 bg-mint/[0.10] px-1.5 py-0 font-mono text-[9px] font-bold text-mint">
            DEFAULT
          </span>
        ) : (
          <button
            type="button"
            onClick={onSetDefault}
            className="rounded border border-border-strong px-2 py-0.5 font-mono text-[9px] text-muted hover:text-foreground"
          >
            {t('agents.makeDefault')}
          </button>
        )}
      </div>

      {/* Enable toggle */}
      <div
        className={clsx(
          'flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5',
          agent.enabled
            ? 'border-mint/30 bg-mint/[0.06]'
            : 'border-border-strong bg-foreground/[0.02]',
        )}
      >
        <div className="flex flex-col">
          <span className="text-[12px] font-bold text-foreground">{t('agents.detail.enabled')}</span>
          <span className="text-[10px] text-muted">{t('agents.detail.enabledHint')}</span>
        </div>
        <button
          type="button"
          onClick={() => onChange({ enabled: !agent.enabled })}
          className={clsx(
            'flex h-6 w-11 items-center rounded-full px-0.5 transition',
            agent.enabled ? 'justify-end bg-mint' : 'justify-start bg-muted-soft',
          )}
        >
          <span className="size-5 rounded-full bg-white shadow" />
        </button>
      </div>

      {/* Backend (read-only) */}
      <div className="flex flex-col gap-1.5">
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted">
          {t('agents.detail.backend')}
        </div>
        <div className="rounded-md border border-border bg-surface-3 px-3 py-2 text-[12px] text-muted">
          <span className="font-mono font-semibold text-foreground">{agent.backend}</span>
          <span className="ml-2 text-[10px]">{t('agents.detail.backendLocked')}</span>
        </div>
      </div>

      {/* Model */}
      <div className="flex flex-col gap-1.5">
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted">
          {t('agents.detail.model')}
        </div>
        <input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          onBlur={() => {
            if (model !== (agent.model ?? '')) {
              onChange({ model: model.trim() || null });
            }
          }}
          placeholder={t('agents.create.modelPlaceholder')}
          className="rounded-md border border-border-strong bg-surface-2 px-3 py-2 font-mono text-[12px] text-foreground outline-none focus:border-cyan"
        />
      </div>

      {/* Effort */}
      <div className="flex flex-col gap-1.5">
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted">
          {t('agents.detail.effort')}
        </div>
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
                'flex-1 rounded text-[11px] font-semibold capitalize transition',
                effort === opt ? 'bg-mint/[0.10] text-mint' : 'text-muted hover:text-foreground',
              )}
            >
              {opt}
            </button>
          ))}
        </div>
      </div>

      {/* System prompt */}
      <div className="flex flex-col gap-1.5">
        <div className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted">
          {t('agents.detail.systemPrompt')}
        </div>
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
      </div>

      {/* Footer actions */}
      <div className="flex items-center gap-2 pt-1">
        {!system && (
          <button
            type="button"
            onClick={onDelete}
            className="inline-flex items-center gap-1.5 rounded-md border border-pink/40 bg-pink/[0.08] px-3 py-1.5 text-[11px] font-bold text-pink hover:bg-pink/[0.14]"
          >
            <Trash2 className="size-3.5" />
            {t('common.delete')}
          </button>
        )}
        {system && (
          <span className="text-[10px] text-muted">{t('agents.detail.systemLocked')}</span>
        )}
      </div>
    </div>
  );
};
