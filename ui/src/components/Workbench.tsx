import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Activity, ArrowUp, Bot, FolderPlus, Loader2, Plus, Sparkles } from 'lucide-react';

import { useApi } from '../context/ApiContext';
import type { WorkbenchProject } from '../context/ApiContext';
import { Badge } from './ui/badge';
import { Button } from './ui/button';
import { NewProjectDialog } from './workbench/NewProjectDialog';

// Mirrors design.pen DnkGJ "Workbench" canvas. Hero card on a surface-2
// rounded backdrop + suggestion chips + bottom InputBar. The InputBar
// is wired to create a new session under the most-recently-active
// project using the default backend, then routes to /chat/<id> with the
// typed message pre-seeded. No project? The button surfaces the
// NewProjectDialog so the user gets unstuck without bouncing pages.
export const Workbench: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const api = useApi();
  const [projects, setProjects] = useState<WorkbenchProject[] | null>(null);
  const [draft, setDraft] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newProjectOpen, setNewProjectOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Load projects so we know whether the InputBar can fire directly or
  // needs to nudge the user to create one first.
  useEffect(() => {
    let cancelled = false;
    api
      .listProjects()
      .then((result) => {
        if (!cancelled) setProjects(result.projects);
      })
      .catch(() => {
        if (!cancelled) setProjects([]);
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

  const sortedProjects = (projects ?? []).slice().sort((a, b) => {
    const aTs = a.last_active_at || a.created_at;
    const bTs = b.last_active_at || b.created_at;
    return bTs.localeCompare(aTs);
  });
  const targetProject = sortedProjects[0] || null;
  const hasProjects = !!targetProject;

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || sending) return;
    if (!targetProject) {
      setNewProjectOpen(true);
      return;
    }
    setSending(true);
    setError(null);
    try {
      // Omit agent_backend so the server routes the new chat through the
      // configured agents.default_backend rather than a hard-coded one.
      const session = await api.createSession({ project_id: targetProject.id });
      // Hand the typed message to ChatPage as router state. ChatPage replays it
      // through its fire-and-forget compose path (plain POST → dispatch_async),
      // so the agent turn actually starts and the reply arrives over the
      // session stream — instead of persisting a user message here that no
      // dispatch would ever pick up.
      navigate(`/chat/${encodeURIComponent(session.id)}`, {
        state: { initialMessage: text },
      });
    } catch (err: any) {
      setError(err?.message ?? String(err));
      setSending(false);
    }
  }, [api, draft, navigate, sending, targetProject]);

  // Quick chips at the bottom of the canvas — three of the most common
  // first moves. Click routes to the existing surface for that flow.
  const suggestions = [
    {
      key: 'newProject',
      icon: FolderPlus,
      onClick: () => setNewProjectOpen(true),
    },
    {
      key: 'openAgents',
      icon: Bot,
      onClick: () => navigate('/agents'),
    },
    {
      key: 'openHarness',
      icon: Activity,
      onClick: () => navigate('/harness'),
    },
  ] as const;

  return (
    <div className="-my-5 flex h-[calc(100dvh-2.5rem)] flex-col gap-4 md:-my-8 md:h-[calc(100dvh-4rem)]">
      {/* Canvas card — flex-1 so it eats all the space between the
          (absent for now) breadcrumb and the InputBar. */}
      <div className="flex flex-1 flex-col items-center justify-center gap-6 overflow-hidden rounded-2xl border border-border bg-surface-2 px-6 py-10">
        <div className="flex size-14 items-center justify-center rounded-2xl border border-mint/40 bg-mint-soft text-mint shadow-[0_0_24px_-6px_rgba(91,255,160,0.6)]">
          <Sparkles className="size-6" />
        </div>
        <div className="flex max-w-[560px] flex-col items-center gap-3 text-center">
          <h1 className="text-[22px] font-semibold text-foreground">
            {t('workbench.canvas.heroTitle')}
          </h1>
          <p className="text-[13px] leading-[1.55] text-muted">
            {t('workbench.canvas.heroBody')}
          </p>
          <Badge variant="warning" className="font-mono text-[10px]">
            {t('workbench.canvas.v1Hint')}
          </Badge>
        </div>
        <div className="flex flex-wrap items-center justify-center gap-2">
          {suggestions.map(({ key, icon: Icon, onClick }) => (
            <button
              key={key}
              type="button"
              onClick={onClick}
              className="group flex items-center gap-2 rounded-full border border-border-strong bg-surface px-3 py-2 text-[12px] text-foreground transition hover:border-mint/40 hover:bg-mint-soft hover:text-mint"
            >
              <Icon className="size-3.5 text-muted group-hover:text-mint" />
              <span>{t(`workbench.canvas.suggestions.${key}`)}</span>
            </button>
          ))}
        </div>
      </div>

      {/* InputBar — design.pen s6tin3. Plus icon (+ new project shortcut),
          textarea-sized input, mint Send button. Enter sends; Shift+Enter
          inserts a newline. */}
      <div className="flex flex-col gap-1">
        <div className="flex items-end gap-2 rounded-2xl border border-border-strong bg-surface-2 p-3">
          <button
            type="button"
            onClick={() => setNewProjectOpen(true)}
            aria-label={t('workbench.canvas.suggestions.newProject')}
            className="flex size-9 shrink-0 items-center justify-center rounded-md text-muted transition hover:bg-foreground/[0.04] hover:text-foreground"
          >
            <Plus className="size-4" />
          </button>
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            placeholder={t('workbench.canvas.inputPlaceholder')}
            rows={1}
            className="flex-1 resize-none bg-transparent py-2 text-[13px] text-foreground outline-none placeholder:text-muted"
          />
          <Button
            type="button"
            variant="brand"
            size="xs"
            onClick={send}
            disabled={!draft.trim() || sending}
            className="shrink-0"
          >
            {sending ? <Loader2 className="size-3.5 animate-spin" /> : <ArrowUp className="size-3.5" />}
            {t('workbench.canvas.send')}
          </Button>
        </div>
        <div className="flex items-center justify-between px-2 text-[10.5px] text-muted">
          <span>{t('workbench.canvas.inputHint')}</span>
          {projects !== null && !hasProjects && (
            <span className="text-gold">{t('workbench.canvas.noProjectForChat')}</span>
          )}
        </div>
        {error && (
          <div className="mt-1 rounded-md border border-destructive/40 bg-destructive/[0.06] px-3 py-2 text-[12px] text-destructive">
            {error}
          </div>
        )}
      </div>

      {newProjectOpen && (
        <NewProjectDialog
          onClose={() => setNewProjectOpen(false)}
          onCreated={(project) => {
            setNewProjectOpen(false);
            setProjects((prev) => (prev ? [project, ...prev] : [project]));
          }}
        />
      )}
    </div>
  );
};
