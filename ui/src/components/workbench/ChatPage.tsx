import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, Bot, ChevronDown, Loader2, MessageSquare, Pencil } from 'lucide-react';
import clsx from 'clsx';

import { useApi } from '../../context/ApiContext';
import type { VibeAgentBrief, WorkbenchMessage, WorkbenchSession } from '../../context/ApiContext';
import { Popover, PopoverContent, PopoverTrigger } from '../ui/popover';

const EFFORT_OPTIONS = ['low', 'medium', 'high', 'max'];

// Mirrors design.pen kxEkn — the inline header replaces the old "Session
// settings" dialog. Title is click-to-edit; the cyan-bordered pill on the
// right opens a single popover that drives agent / model / effort all at
// once so the user doesn't have to navigate three different menus.
export const ChatPage: React.FC = () => {
  const { sessionId } = useParams<{ sessionId: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const api = useApi();
  const [session, setSession] = useState<WorkbenchSession | null>(null);
  const [agents, setAgents] = useState<VibeAgentBrief[]>([]);
  const [messages, setMessages] = useState<WorkbenchMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!sessionId) return;
    setLoading(true);
    setError(null);
    try {
      const [fetched, agentList, msgs] = await Promise.all([
        api.getSession(sessionId),
        api.listVibeAgents({ includeDisabled: false }),
        api.listSessionMessages(sessionId, { limit: 50 }),
      ]);
      setSession(fetched);
      setAgents(agentList.agents);
      setMessages(msgs.messages);
    } catch (err: any) {
      setError(err?.message ?? String(err));
    } finally {
      setLoading(false);
    }
  }, [api, sessionId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const patch = useCallback(
    async (changes: Partial<WorkbenchSession>) => {
      if (!session) return;
      try {
        const updated = await api.updateSession(session.id, changes as any);
        setSession(updated);
      } catch (err: any) {
        setError(err?.message ?? String(err));
      }
    },
    [api, session],
  );

  if (!sessionId) {
    return <ChatMissing onBack={() => navigate('/inbox')} />;
  }

  if (loading && !session) {
    return (
      <div className="flex h-[60vh] flex-col items-center justify-center gap-2 text-muted">
        <Loader2 className="size-5 animate-spin" />
        <span className="text-[12px]">{t('common.loading')}</span>
      </div>
    );
  }

  if (!session) {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 py-8">
        <button
          type="button"
          onClick={() => navigate('/inbox')}
          className="inline-flex items-center gap-1.5 text-[12px] text-cyan hover:underline"
        >
          <ArrowLeft className="size-3.5" />
          {t('chat.backToInbox')}
        </button>
        <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
          {error ?? t('chat.notFound')}
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-[1080px] flex-col gap-4 py-2">
      <button
        type="button"
        onClick={() => navigate('/inbox')}
        className="inline-flex items-center gap-1.5 self-start text-[12px] text-muted hover:text-foreground"
      >
        <ArrowLeft className="size-3.5" />
        {t('chat.backToInbox')}
      </button>

      <ChatHeaderBar session={session} agents={agents} onPatch={patch} />

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
          {error}
        </div>
      )}

      <Transcript messages={messages} session={session} />
    </div>
  );
};

interface ChatHeaderBarProps {
  session: WorkbenchSession;
  agents: VibeAgentBrief[];
  onPatch: (changes: Partial<WorkbenchSession>) => Promise<void>;
}

const ChatHeaderBar: React.FC<ChatHeaderBarProps> = ({ session, agents, onPatch }) => {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-3.5 rounded-2xl border border-border bg-surface px-5 py-3.5">
      <div className="flex items-center gap-3">
        <ProjectPill projectId={session.project_id} />
        <TitleField key={session.id} title={session.title} onCommit={(title) => onPatch({ title })} />
      </div>
      <div className="flex items-center gap-2">
        <AgentPicker session={session} agents={agents} onPatch={onPatch} />
        <ModelField key={`model-${session.id}`} model={session.model} onCommit={(model) => onPatch({ model })} />
        <EffortPicker effort={session.reasoning_effort} onPick={(value) => onPatch({ reasoning_effort: value })} />
        <span className="ml-auto font-mono text-[10px] text-muted">{t('chat.changesPersist')}</span>
      </div>
    </div>
  );
};

const ProjectPill: React.FC<{ projectId: string | null }> = ({ projectId }) => (
  <span className="inline-flex items-center gap-1.5 rounded-md border border-cyan/40 bg-cyan/[0.08] px-2 py-0.5 font-mono text-[10px] font-semibold text-cyan">
    <span className="size-1.5 rounded-full bg-cyan" />
    {projectId || 'workbench'}
  </span>
);

interface TitleFieldProps {
  title: string | null;
  onCommit: (next: string | null) => void;
}

const TitleField: React.FC<TitleFieldProps> = ({ title, onCommit }) => {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(title ?? '');
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    setValue(title ?? '');
  }, [title]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  if (!editing) {
    return (
      <button
        type="button"
        onClick={() => setEditing(true)}
        className="group inline-flex flex-1 items-center gap-2 truncate text-left text-[16px] font-bold text-foreground hover:text-foreground"
      >
        <span className="truncate">{title || t('chat.untitled')}</span>
        <Pencil className="size-3.5 shrink-0 text-muted opacity-0 transition-opacity group-hover:opacity-100" />
      </button>
    );
  }

  const commit = (next: string) => {
    const trimmed = next.trim();
    if (trimmed === (title ?? '')) {
      setEditing(false);
      return;
    }
    onCommit(trimmed || null);
    setEditing(false);
  };

  return (
    <input
      ref={inputRef}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => commit(value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') commit(value);
        if (e.key === 'Escape') {
          setValue(title ?? '');
          setEditing(false);
        }
      }}
      placeholder={t('chat.titlePlaceholder')}
      className="flex-1 rounded-md border border-cyan/40 bg-surface-2 px-2 py-1 text-[15px] font-bold text-foreground outline-none focus:border-cyan"
    />
  );
};

interface AgentPickerProps {
  session: WorkbenchSession;
  agents: VibeAgentBrief[];
  onPatch: (changes: Partial<WorkbenchSession>) => Promise<void>;
}

const AgentPicker: React.FC<AgentPickerProps> = ({ session, agents, onPatch }) => {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const current = session.agent_name;

  const grouped = useMemo(() => {
    const groups: Record<string, VibeAgentBrief[]> = {};
    for (const agent of agents) {
      groups[agent.backend] = groups[agent.backend] || [];
      groups[agent.backend].push(agent);
    }
    return groups;
  }, [agents]);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="inline-flex items-center gap-1.5 rounded-md border border-cyan/30 bg-cyan/[0.06] px-2 py-1 text-[12px] font-semibold text-foreground hover:bg-cyan/[0.10]"
        >
          <Bot className="size-3.5 text-cyan" />
          <span>{current || t('chat.pickAgent')}</span>
          <ChevronDown className="size-3 text-muted" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[260px] p-2">
        {Object.keys(grouped).length === 0 && (
          <div className="px-3 py-4 text-center text-[12px] text-muted">{t('chat.noAgents')}</div>
        )}
        {Object.entries(grouped).map(([backend, list]) => (
          <div key={backend} className="flex flex-col gap-1 py-1">
            <div className="px-2 font-mono text-[9px] font-bold uppercase tracking-[0.12em] text-muted">{backend}</div>
            <div className="flex flex-col">
              {list.map((agent) => {
                const active = agent.name === current;
                return (
                  <button
                    key={agent.id}
                    type="button"
                    onClick={async () => {
                      setOpen(false);
                      if (active) return;
                      await onPatch({
                        agent_name: agent.name,
                        agent_id: agent.id,
                        agent_backend: agent.backend,
                        model: agent.model,
                        reasoning_effort: agent.reasoning_effort,
                      });
                    }}
                    className={clsx(
                      'flex items-center gap-2 rounded px-2 py-1.5 text-left text-[12px] transition',
                      active ? 'bg-cyan/[0.10] text-cyan' : 'text-foreground hover:bg-foreground/[0.04]',
                    )}
                  >
                    <span className="flex-1 truncate font-semibold">{agent.name}</span>
                    {agent.model && <span className="font-mono text-[10px] text-muted">{agent.model}</span>}
                  </button>
                );
              })}
            </div>
          </div>
        ))}
      </PopoverContent>
    </Popover>
  );
};

interface ModelFieldProps {
  model: string | null;
  onCommit: (next: string | null) => void;
}

const ModelField: React.FC<ModelFieldProps> = ({ model, onCommit }) => {
  const { t } = useTranslation();
  const [value, setValue] = useState(model ?? '');

  useEffect(() => {
    setValue(model ?? '');
  }, [model]);

  return (
    <input
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => {
        if (value !== (model ?? '')) onCommit(value.trim() || null);
      }}
      placeholder={t('chat.modelPlaceholder')}
      className="w-[200px] rounded-md border border-border-strong bg-surface-2 px-2 py-1 font-mono text-[11px] text-foreground outline-none focus:border-cyan"
    />
  );
};

const EffortPicker: React.FC<{ effort: string | null; onPick: (value: string) => void }> = ({ effort, onPick }) => (
  <div className="flex rounded-md border border-border-strong bg-surface-2 p-0.5">
    {EFFORT_OPTIONS.map((opt) => (
      <button
        key={opt}
        type="button"
        onClick={() => onPick(opt)}
        className={clsx(
          'rounded px-2 py-0.5 text-[11px] font-semibold capitalize transition',
          effort === opt ? 'bg-mint/[0.10] text-mint' : 'text-muted hover:text-foreground',
        )}
      >
        {opt}
      </button>
    ))}
  </div>
);

interface TranscriptProps {
  messages: WorkbenchMessage[];
  session: WorkbenchSession;
}

const Transcript: React.FC<TranscriptProps> = ({ messages, session }) => {
  const { t } = useTranslation();
  if (messages.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border bg-surface px-6 py-16 text-center">
        <MessageSquare className="size-8 text-muted" />
        <div className="text-[14px] font-semibold text-foreground">{t('chat.transcriptEmpty')}</div>
        <div className="max-w-md text-[12px] text-muted">{t('chat.transcriptEmptyHint')}</div>
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {messages.map((message) => (
        <MessageRow key={message.id} message={message} session={session} />
      ))}
    </div>
  );
};

const MessageRow: React.FC<{ message: WorkbenchMessage; session: WorkbenchSession }> = ({ message, session }) => {
  const isAgent = message.author === 'agent';
  const isSystem = message.author === 'system';
  return (
    <div
      className={clsx(
        'flex flex-col gap-1 rounded-xl border px-4 py-3',
        isAgent
          ? 'border-mint/20 bg-mint/[0.04]'
          : isSystem
          ? 'border-border bg-foreground/[0.02]'
          : 'border-border bg-surface',
      )}
    >
      <div className="flex items-center gap-2 text-[10px]">
        <span
          className={clsx(
            'rounded border px-1.5 py-0 font-mono font-bold uppercase',
            isAgent ? 'border-mint/40 bg-mint/[0.10] text-mint' : 'border-border-strong bg-foreground/[0.04] text-muted',
          )}
        >
          {message.author}
        </span>
        {message.author_name && <span className="font-semibold text-foreground">{message.author_name}</span>}
        {isAgent && session.agent_name && <span className="font-mono text-muted">{session.agent_name}</span>}
        <span className="ml-auto font-mono text-muted">{message.created_at}</span>
      </div>
      <div className="whitespace-pre-wrap text-[13px] text-foreground">{message.text || '—'}</div>
    </div>
  );
};

const ChatMissing: React.FC<{ onBack: () => void }> = ({ onBack }) => {
  const { t } = useTranslation();
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 py-8">
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center gap-1.5 text-[12px] text-cyan hover:underline"
      >
        <ArrowLeft className="size-3.5" />
        {t('chat.backToInbox')}
      </button>
      <div className="rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
        {t('chat.missingSessionId')}
      </div>
    </div>
  );
};
