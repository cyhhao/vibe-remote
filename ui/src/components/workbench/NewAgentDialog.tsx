import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ArrowRight, Bot, X } from 'lucide-react';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import type { VibeAgentFull } from '../../context/ApiContext';
import { Combobox } from '../ui/combobox';
import type { ComboboxOption } from '../ui/combobox';

type BackendKey = 'claude' | 'opencode' | 'codex';

interface BackendOption {
  key: BackendKey;
  label: string;
  publisher: string;
  color: 'mint' | 'cyan' | 'violet';
}

const BACKEND_OPTIONS: BackendOption[] = [
  { key: 'claude', label: 'Claude', publisher: 'Anthropic', color: 'mint' },
  // OpenCode is the upstream project at opencode.ai (not sst.dev — that
  // was an older publisher attribution that doesn't match the current
  // project page).
  { key: 'opencode', label: 'OpenCode', publisher: 'opencode.ai', color: 'cyan' },
  { key: 'codex', label: 'Codex', publisher: 'OpenAI', color: 'violet' },
];

const EFFORT_OPTIONS = ['low', 'medium', 'high', 'max'];

interface NewAgentDialogProps {
  /** When false the modal renders nothing — controlled by the parent. */
  open: boolean;
  onClose: () => void;
  /** Called after a successful POST /agents with the new agent. */
  onCreated: (agent: VibeAgentFull) => void;
}

// Mirrors design.pen ``gwn5C``. Single-step form: backend (immutable after
// create) → name → model + effort → optional system prompt.
export const NewAgentDialog: React.FC<NewAgentDialogProps> = ({ open, onClose, onCreated }) => {
  const { t } = useTranslation();
  const api = useApi();
  const [backend, setBackend] = useState<BackendKey>('claude');
  const [name, setName] = useState('');
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState<string>('medium');
  const [systemPrompt, setSystemPrompt] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modelOptions, setModelOptions] = useState<ComboboxOption[]>([]);

  useEffect(() => {
    if (!open) {
      setName('');
      setModel('');
      setEffort('medium');
      setSystemPrompt('');
      setBackend('claude');
      setError(null);
      setSubmitting(false);
    }
  }, [open]);

  // Reload the model catalog whenever the selected backend changes so
  // the Combobox suggests the right list. allowCustomValue stays on so
  // freshly-released model IDs can still be typed in.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    async function loadModels() {
      try {
        let models: string[] = [];
        if (backend === 'claude') {
          const result = await api.claudeModels();
          if (result.ok && result.models) models = result.models;
        } else if (backend === 'codex') {
          const result = await api.codexModels();
          if (result.ok && result.models) models = result.models;
        }
        if (!cancelled) {
          setModelOptions(models.map((m) => ({ value: m, label: m })));
          // Clear model when the backend changes if the previous choice
          // isn't in the new catalog — avoids silently mismatched pairs.
          if (model && !models.includes(model)) setModel('');
        }
      } catch {
        if (!cancelled) setModelOptions([]);
      }
    }
    loadModels();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backend, open, api]);

  const modelComboboxOptions = useMemo(() => modelOptions, [modelOptions]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const canSubmit = name.trim().length > 0 && !submitting;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setError(null);
    setSubmitting(true);
    try {
      const result = await api.createVibeAgent({
        name: name.trim(),
        backend,
        model: model.trim() || null,
        reasoning_effort: effort,
        system_prompt: systemPrompt.trim() || null,
        enabled: true,
      });
      if (result.ok) {
        onCreated(result.agent);
        onClose();
      }
    } catch (err: any) {
      setError(err?.message ?? String(err));
    } finally {
      setSubmitting(false);
    }
  };

  const colorClasses: Record<BackendOption['color'], { border: string; bg: string; text: string }> = {
    mint: { border: 'border-mint', bg: 'bg-mint/[0.08]', text: 'text-mint' },
    cyan: { border: 'border-cyan', bg: 'bg-cyan/[0.08]', text: 'text-cyan' },
    violet: { border: 'border-violet', bg: 'bg-violet/[0.08]', text: 'text-violet' },
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="flex w-full max-w-[520px] flex-col gap-5 rounded-2xl border border-border-strong bg-surface p-7 shadow-[0_24px_64px_-12px_rgba(0,0,0,0.6)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-[18px] font-bold text-foreground">{t('agents.create.title')}</h2>
            <p className="mt-1 max-w-[420px] text-[11.5px] leading-relaxed text-muted">
              {t('agents.create.body')}
            </p>
          </div>
          <button onClick={onClose} className="text-muted hover:text-foreground" aria-label={t('common.close')}>
            <X className="size-4" />
          </button>
        </div>

        {/* Backend picker */}
        <div className="flex flex-col gap-2">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted">
            {t('agents.create.backend')}
          </div>
          <div className="grid grid-cols-3 gap-2.5">
            {BACKEND_OPTIONS.map((opt) => {
              const active = backend === opt.key;
              const cc = colorClasses[opt.color];
              return (
                <button
                  key={opt.key}
                  type="button"
                  onClick={() => setBackend(opt.key)}
                  className={clsx(
                    'flex flex-col items-center justify-center gap-1.5 rounded-lg border-2 px-3 py-3.5 transition',
                    active ? `${cc.border} ${cc.bg}` : 'border-border-strong bg-surface-2 hover:bg-foreground/[0.04]',
                  )}
                >
                  <div className={clsx('flex size-8 items-center justify-center rounded-lg border', active ? cc.border + ' ' + cc.bg : 'border-border-strong')}>
                    <Bot className={clsx('size-4', active ? cc.text : 'text-muted')} />
                  </div>
                  <span className={clsx('text-[12px] font-bold', active ? cc.text : 'text-foreground')}>
                    {opt.label}
                  </span>
                  <span className="text-[10px] text-muted">{opt.publisher}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Name */}
        <div className="flex flex-col gap-1.5">
          <div className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted">
            {t('agents.create.name')}
          </div>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && canSubmit) handleSubmit();
            }}
            placeholder="reviewer"
            className="rounded-md border border-border-strong bg-surface-2 px-3 py-2 font-mono text-[13px] text-foreground outline-none focus:border-cyan"
          />
        </div>

        {/* Model + Effort — both rows share a 38px height so the Combobox
            on the left and the segmented control on the right align. The
            segments use rounded-md py-2 (was py-0.5 which collapsed to
            ~24px and looked stubby next to the model field). */}
        <div className="grid grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted">
              {t('agents.create.model')}
            </div>
            <Combobox
              options={modelComboboxOptions}
              value={model}
              onValueChange={setModel}
              placeholder={t('agents.detail.modelPlaceholder')}
              emptyText={t('agents.detail.modelEmpty')}
              allowCustomValue
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted">
              {t('agents.create.effort')}
            </div>
            <div className="grid h-[38px] grid-cols-4 rounded-md border border-border-strong bg-surface-2 p-0.5">
              {EFFORT_OPTIONS.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  onClick={() => setEffort(opt)}
                  className={clsx(
                    'rounded text-[11px] capitalize transition',
                    effort === opt ? 'bg-mint-soft font-bold text-mint' : 'font-medium text-muted hover:text-foreground',
                  )}
                >
                  {opt}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* System prompt */}
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <div className="font-mono text-[10px] font-bold uppercase tracking-[0.12em] text-muted">
              {t('agents.create.systemPrompt')}
            </div>
            <span className="text-[10px] text-muted">{t('agents.create.systemPromptHint')}</span>
          </div>
          <textarea
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={3}
            placeholder={t('agents.create.systemPromptPlaceholder')}
            className="rounded-md border border-border-strong bg-surface-3 px-3 py-2 text-[12px] text-foreground outline-none focus:border-cyan"
          />
        </div>

        {error && (
          <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
            {error}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border border-border-strong px-4 py-1.5 text-[12px] font-medium text-foreground hover:bg-foreground/[0.04]"
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSubmit}
            className={clsx(
              'inline-flex items-center gap-1.5 rounded-md px-4 py-1.5 text-[12px] font-bold transition',
              canSubmit
                ? 'bg-mint text-[#080812] shadow-[0_0_14px_-4px_rgba(91,255,160,0.6)] hover:brightness-110'
                : 'cursor-not-allowed bg-muted-soft text-muted',
            )}
          >
            {t('agents.create.submit')}
            <ArrowRight className="size-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
};
