import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Bot, ChevronDown, Loader2, Plus } from 'lucide-react';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import type { VibeAgentBrief } from '../../context/ApiContext';
import { fetchBackendModels } from '../../lib/backendModels';
import { resolveEffortOptions } from '../../lib/effortOptions';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';
import { Button } from '../ui/button';

// The route fields the picker reads. A subset of WorkbenchSession (chat) and of
// WorkbenchSessionCreate (the create flow), so both can pass their object directly.
export interface AgentRouteValue {
  agent_backend?: string | null;
  agent_name?: string | null;
  agent_id?: string | null;
  model?: string | null;
  reasoning_effort?: string | null;
}

// The patch onChange emits. agent_variant tracks the backend so a session can
// resume its native thread (the native-session map is keyed by agent_variant).
export interface AgentRoutePatch extends AgentRouteValue {
  agent_variant?: string | null;
}

interface AgentRoutePickerProps {
  value: AgentRouteValue;
  agents: VibeAgentBrief[];
  onChange: (patch: AgentRoutePatch) => void | Promise<void>;
  /** Trigger label when no agent is selected — the create flow shows "默认 · …". */
  defaultLabel?: string;
  disabled?: boolean;
  align?: 'start' | 'end';
  /** Override the trigger width (chat caps at 62%; the create surfaces go full width). */
  triggerClassName?: string;
}

// design.pen Q5xIZa — one cyan-ringed trigger showing `[backend] agent · model ·
// effort` that opens a three-column cascading menu (Agent → Model → Effort).
// Picking an agent seeds model/effort from its defaults; the model column is
// fetched lazily per backend. CONTROLLED so it serves both the chat header
// (value=session, onChange patches the session) and the create surfaces
// (value=draft route, onChange updates the draft) — one source of truth. On
// phones the three columns stack into one scrollable list.
export const AgentRoutePicker: React.FC<AgentRoutePickerProps> = ({
  value,
  agents,
  onChange,
  defaultLabel,
  disabled,
  align = 'end',
  triggerClassName,
}) => {
  const { t } = useTranslation();
  const api = useApi();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [modelsByBackend, setModelsByBackend] = useState<Record<string, string[]>>({});
  // Claude reasoning efforts are MODEL-specific (newer Opus/Sonnet add xhigh/max),
  // so the backend returns them keyed by model. Cached so the effort column offers
  // exactly the efforts the selected model supports.
  const [claudeReasoning, setClaudeReasoning] = useState<Record<string, { value: string; label: string }[]>>({});
  const [loadingModels, setLoadingModels] = useState(false);
  const [patching, setPatching] = useState(false);

  // Serialize patches: an agent pick carries its default model/effort, so if it
  // resolves AFTER a later model/effort pick the later choice would be rolled
  // back. One patch at a time, items disabled while it's in flight.
  const applyPatch = useCallback(
    async (changes: AgentRoutePatch) => {
      if (patching) return;
      setPatching(true);
      try {
        await onChange(changes);
      } finally {
        setPatching(false);
      }
    },
    [patching, onChange],
  );

  const backend = value.agent_backend || '';
  const currentAgent = value.agent_name;
  const currentModel = value.model;
  const currentEffort = value.reasoning_effort;

  const grouped = useMemo(() => {
    const groups: Record<string, VibeAgentBrief[]> = {};
    for (const agent of agents) {
      (groups[agent.backend] ||= []).push(agent);
    }
    return groups;
  }, [agents]);

  // Fetch the active backend's model list the first time the menu opens for it;
  // cached per backend so toggling agents doesn't refetch.
  useEffect(() => {
    if (!open || !backend || modelsByBackend[backend]) return;
    let cancelled = false;
    setLoadingModels(true);
    (async () => {
      try {
        const { models, reasoningOptions } = await fetchBackendModels(api, backend);
        if (!cancelled) {
          if (reasoningOptions) setClaudeReasoning(reasoningOptions);
          setModelsByBackend((prev) => ({ ...prev, [backend]: models }));
        }
      } catch {
        if (!cancelled) setModelsByBackend((prev) => ({ ...prev, [backend]: [] }));
      } finally {
        if (!cancelled) setLoadingModels(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, backend, api, modelsByBackend]);

  const models = modelsByBackend[backend] ?? [];
  const effortOptions = useMemo(
    () => resolveEffortOptions(backend, currentModel, claudeReasoning),
    [backend, currentModel, claudeReasoning],
  );

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          type="button"
          variant="outline"
          disabled={disabled}
          className={clsx(
            'inline-flex h-auto items-center justify-start gap-1.5 rounded-lg border-cyan/40 bg-surface-2 px-2.5 py-1.5 text-[12px] font-normal hover:bg-cyan/[0.06]',
            triggerClassName ?? 'max-w-[62%]',
          )}
        >
          {backend && (
            <span className="inline-flex shrink-0 items-center gap-1 rounded border border-cyan/30 bg-cyan/[0.08] px-1.5 py-0.5 font-mono text-[9px] font-bold uppercase text-cyan">
              <Bot className="size-3" />
              {backend}
            </span>
          )}
          <span className="truncate font-semibold text-foreground">{currentAgent || defaultLabel || t('chat.pickAgent')}</span>
          {currentModel && (
            <>
              <span className="text-muted">·</span>
              <span className="truncate font-mono text-[10px] text-muted">{currentModel}</span>
            </>
          )}
          {currentEffort && (
            <>
              <span className="text-muted">·</span>
              {/* Localize via the same key the column uses so the closed trigger
                  doesn't show raw low/max in zh builds; unknown falls back to raw. */}
              <span className="shrink-0 text-[10px] capitalize text-muted">
                {t(`chat.picker.effortOptions.${currentEffort}`, { defaultValue: currentEffort })}
              </span>
            </>
          )}
          <ChevronDown className="ml-auto size-3 shrink-0 text-muted" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align={align} className="z-50 max-h-[70vh] w-[620px] max-w-[92vw] overflow-y-auto p-0">
        {/* On phones the three columns stack into one scrollable list; sm+ keeps
            the side-by-side cascading menu. */}
        <div className="grid grid-cols-1 divide-y divide-border sm:grid-cols-3 sm:divide-x sm:divide-y-0">
          {/* Column 1 — Agent */}
          <RouteColumn title={t('chat.picker.agent')}>
            {agents.length === 0 && (
              <div className="px-2 py-3 text-center text-[11px] text-muted">{t('chat.noAgents')}</div>
            )}
            {Object.entries(grouped).map(([be, list]) => (
              <div key={be} className="flex flex-col gap-0.5 pb-1">
                <div className="px-2 pt-1.5 font-mono text-[9px] font-bold uppercase tracking-[0.12em] text-muted">{be}</div>
                {list.map((agent) => (
                  <RouteItem
                    key={agent.id}
                    active={agent.name === currentAgent}
                    disabled={patching}
                    onClick={() =>
                      void applyPatch({
                        agent_name: agent.name,
                        agent_id: agent.id,
                        agent_backend: agent.backend,
                        agent_variant: agent.backend,
                        // Explicit null (not undefined) so switching to an agent with
                        // no default model/effort clears the previous override.
                        model: agent.model ?? null,
                        reasoning_effort: agent.reasoning_effort ?? null,
                      })
                    }
                  >
                    <span className="flex-1 truncate font-semibold">{agent.name}</span>
                    {agent.model && <span className="truncate font-mono text-[9px] text-muted">{agent.model}</span>}
                  </RouteItem>
                ))}
              </div>
            ))}
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setOpen(false);
                navigate('/agents');
              }}
              className="mt-1 h-auto w-full justify-start gap-1.5 rounded px-2 py-1.5 text-[11px] font-medium text-cyan hover:bg-cyan/[0.08] hover:text-cyan"
            >
              <Plus className="size-3.5" />
              {t('chat.picker.newAgent')}
            </Button>
          </RouteColumn>

          {/* Column 2 — Model (lazy-loaded for the active backend) */}
          <RouteColumn title={t('chat.picker.model')}>
            {loadingModels && models.length === 0 ? (
              <div className="flex items-center gap-1.5 px-2 py-3 text-[11px] text-muted">
                <Loader2 className="size-3 animate-spin" />
                {t('common.loading')}
              </div>
            ) : models.length === 0 ? (
              <div className="px-2 py-3 text-[11px] text-muted">{t('chat.picker.noModels')}</div>
            ) : (
              models.map((model) => (
                <RouteItem
                  key={model}
                  active={model === currentModel}
                  disabled={patching}
                  onClick={() => {
                    const patch: AgentRoutePatch = { model };
                    // Switching to a Claude model whose effort set no longer includes
                    // the current effort: clear it in the same patch so the displayed
                    // route matches what dispatches.
                    if (backend === 'claude' && currentEffort) {
                      const opts = claudeReasoning[model];
                      if (opts && !opts.some((o) => o.value === currentEffort)) patch.reasoning_effort = null;
                    }
                    void applyPatch(patch);
                  }}
                >
                  <span className="flex-1 truncate font-mono text-[11px]">{model}</span>
                </RouteItem>
              ))
            )}
          </RouteColumn>

          {/* Column 3 — Effort (Claude: model-specific; others: backend superset) */}
          <RouteColumn title={t('chat.picker.effort')}>
            {effortOptions.map((opt) => (
              <RouteItem
                key={opt}
                active={opt === currentEffort}
                disabled={patching}
                onClick={() => void applyPatch({ reasoning_effort: opt })}
              >
                <span className="flex-1 capitalize">{t(`chat.picker.effortOptions.${opt}`)}</span>
              </RouteItem>
            ))}
          </RouteColumn>
        </div>
      </PopoverContent>
    </Popover>
  );
};

const RouteColumn: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => (
  <div className="flex min-w-0 flex-col overflow-y-auto p-1.5 sm:max-h-[320px]">
    <div className="px-2 pb-1 pt-0.5 text-[10px] font-bold uppercase tracking-[0.1em] text-muted">{title}</div>
    {children}
  </div>
);

// A picker row on the shared Button primitive (variant + className overrides)
// so it inherits the design system's focus/disabled behavior.
const RouteItem: React.FC<{
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}> = ({ active, onClick, disabled, children }) => (
  <Button
    type="button"
    variant="ghost"
    size="sm"
    onClick={onClick}
    disabled={disabled}
    className={clsx(
      'h-auto w-full justify-start gap-2 rounded px-2 py-1.5 text-left text-[12px] font-normal',
      active ? 'bg-cyan/[0.10] text-cyan hover:bg-cyan/[0.10] hover:text-cyan' : 'text-foreground hover:bg-foreground/[0.04]',
    )}
  >
    {children}
  </Button>
);
