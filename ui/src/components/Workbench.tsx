import { useCallback, useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import { Activity, Bot, FolderPlus, Sparkles } from 'lucide-react';

import { useApi } from '../context/ApiContext';
import type { WorkbenchProject } from '../context/ApiContext';
import { NewProjectDialog } from './workbench/NewProjectDialog';
import { Composer } from './workbench/Composer';

// Mirrors design.pen DnkGJ "Workbench" canvas: a centered hero panel +
// suggestion chips with the shared chat Composer below it. The Composer is
// wired to create a new session under the most-recently-active project using
// the default backend, then routes to /chat/<id> with the typed message
// pre-seeded. No project? The send surfaces the NewProjectDialog so the user
// gets unstuck without bouncing pages.
export const Workbench: React.FC = () => {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const api = useApi();
  const [projects, setProjects] = useState<WorkbenchProject[] | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newProjectOpen, setNewProjectOpen] = useState(false);

  // Load projects so we know whether the Composer can fire directly or needs
  // to nudge the user to create one first.
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

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || sending) return;
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
        // Hand the typed message to ChatPage as router state; it replays it
        // through the fire-and-forget compose path so the agent turn starts.
        navigate(`/chat/${encodeURIComponent(session.id)}`, {
          state: { initialMessage: trimmed },
        });
      } catch (err: any) {
        setError(err?.message ?? String(err));
        setSending(false);
      }
    },
    [api, navigate, sending, targetProject],
  );

  // Quick chips under the hero — three of the most common first moves.
  const suggestions = [
    { key: 'newProject', icon: FolderPlus, onClick: () => setNewProjectOpen(true) },
    { key: 'openAgents', icon: Bot, onClick: () => navigate('/agents') },
    { key: 'openHarness', icon: Activity, onClick: () => navigate('/harness') },
  ] as const;

  return (
    // Center the hero + Composer as a group, leaving the AppShell's top/bottom
    // padding as margin (no negative-margin full-bleed). min-h fills the visible
    // area so justify-center actually centers; the responsive offset accounts
    // for the mobile header + bottom nav vs. the desktop sidebar layout.
    <div className="flex min-h-[calc(100dvh-13rem)] flex-col items-center justify-center gap-5 md:min-h-[calc(100dvh-7rem)]">
      {/* Hero panel — a centered card, not a full-bleed fill. */}
      <div className="flex w-full max-w-[640px] flex-col items-center gap-6 rounded-2xl border border-border bg-surface-2 px-6 py-10">
        <div className="flex size-14 items-center justify-center rounded-2xl border border-mint/40 bg-mint-soft text-mint shadow-[0_0_24px_-6px_rgba(91,255,160,0.6)]">
          <Sparkles className="size-6" />
        </div>
        <div className="flex max-w-[520px] flex-col items-center gap-3 text-center">
          <h1 className="text-[22px] font-semibold text-foreground">{t('workbench.canvas.heroTitle')}</h1>
          <p className="text-[13px] leading-[1.55] text-muted">{t('workbench.canvas.heroBody')}</p>
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

      {/* Input — the shared chat Composer, width-matched to the hero. */}
      <div className="flex w-full max-w-[640px] flex-col gap-1">
        <Composer
          onSend={send}
          placeholder={t('workbench.canvas.inputPlaceholder')}
          disabled={sending}
          className="max-w-[640px]"
        />
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
            // create_project is find-or-create by path: this may return an
            // already-tracked project, refreshed. Drop any stale copy and hoist
            // the fresh one to the top so the "most recent" target reflects the
            // folder just opened.
            setProjects((prev) => {
              if (!prev) return [project];
              return [project, ...prev.filter((p) => p.id !== project.id)];
            });
          }}
        />
      )}
    </div>
  );
};
