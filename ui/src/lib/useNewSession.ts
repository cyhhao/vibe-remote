import { useCallback, useEffect, useState } from 'react';

import { useApi } from '../context/ApiContext';
import type { WorkbenchProject } from '../context/ApiContext';

interface UseNewSessionOptions {
  /** Re-run load + reset on the rising edge — sheets pass their `open`. Default true (load once). */
  active?: boolean;
  /** Pre-translated copy: the hook stays i18n-free, callers pass t(...) strings. */
  loadErrorText: string;
  createFailedText: string;
}

export interface NewSessionState {
  projects: WorkbenchProject[];
  loaded: boolean;
  error: string | null;
  sending: boolean;
  selectedId: string | null;
  setSelected: (id: string) => void;
  target: WorkbenchProject | null;
  needsProject: boolean;
  /** Creates a session under `target` and returns the nav target; null if it couldn't start
   *  (empty / in-flight / not loaded / no project / error). The hook never navigates — the
   *  caller does, since one mount point (the workbench projects provider) sits outside the router. */
  send: (text: string) => Promise<{ sessionId: string; initialMessage: string } | null>;
  upsertSelectProject: (project: WorkbenchProject) => void;
}

const sortByRecent = (list: WorkbenchProject[]) =>
  list
    .slice()
    .sort((a, b) => (b.last_active_at || b.created_at).localeCompare(a.last_active_at || a.created_at));

// Shared new-session create flow: the desktop Workbench home (`Workbench.tsx`) and the mobile
// NewSessionSheet both use this so the project-load / target-resolution / createSession logic
// lives in one place. Draft handling + the sheet's open/close (Radix focus-trap) lifecycle stay
// in the consumer; navigation stays in the consumer too.
export function useNewSession({ active = true, loadErrorText, createFailedText }: UseNewSessionOptions): NewSessionState {
  const api = useApi();
  const [projects, setProjects] = useState<WorkbenchProject[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    setSending(false);
    setError(null);
    setLoaded(false);
    api
      .listProjects()
      .then((r) => {
        // A newer activation (sheet close→reopen) superseded this load.
        if (cancelled) return;
        const sorted = sortByRecent(r.projects);
        setProjects(sorted);
        // Keep the current pick if still visible (first-6 chips, e.g. a just-created project
        // that sorts to the top); otherwise fall back to the most-recent.
        setSelectedId((prev) => {
          const visible = sorted.slice(0, 6);
          return prev && visible.some((p) => p.id === prev) ? prev : sorted[0]?.id ?? null;
        });
        setLoaded(true);
      })
      .catch(() => {
        if (!cancelled) setError(loadErrorText);
      });
    return () => {
      cancelled = true;
    };
  }, [active, api, loadErrorText]);

  const target = projects.find((p) => p.id === selectedId) ?? projects[0] ?? null;
  const needsProject = loaded && !target;

  const send = useCallback(
    async (text: string): Promise<{ sessionId: string; initialMessage: string } | null> => {
      const trimmed = text.trim();
      // Never create from a stale/empty/in-flight state; no target → caller opens New Project.
      if (!trimmed || sending || !loaded || !target) return null;
      setSending(true);
      setError(null);
      try {
        // Omit agent_backend so the server routes through agents.default_backend.
        const session = await api.createSession({ project_id: target.id });
        setSending(false);
        return { sessionId: session.id, initialMessage: trimmed };
      } catch (err: any) {
        setSending(false);
        setError(err?.message ?? createFailedText);
        return null;
      }
    },
    [api, sending, loaded, target, createFailedText],
  );

  const upsertSelectProject = useCallback((project: WorkbenchProject) => {
    // create_project is find-or-create by path: dedup by id, hoist to top, select it.
    setProjects((prev) => [project, ...prev.filter((p) => p.id !== project.id)]);
    setSelectedId(project.id);
  }, []);

  return {
    projects,
    loaded,
    error,
    sending,
    selectedId,
    setSelected: setSelectedId,
    target,
    needsProject,
    send,
    upsertSelectProject,
  };
}
