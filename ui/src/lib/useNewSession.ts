import { useCallback, useEffect, useMemo, useState } from 'react';

import { useWorkbenchProjectsTree } from '../context/WorkbenchProjectsContext';
import type { WorkbenchProject } from '../context/ApiContext';

interface UseNewSessionOptions {
  /** Re-run the per-open reset on the rising edge — sheets pass their `open`. Default true. */
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
   *  caller does, since the projects provider is mounted outside the router. */
  send: (text: string) => Promise<{ sessionId: string; initialMessage: string } | null>;
  upsertSelectProject: (project: WorkbenchProject) => void;
}

const sortByRecent = (list: WorkbenchProject[]) =>
  list
    .slice()
    .sort((a, b) => (b.last_active_at || b.created_at).localeCompare(a.last_active_at || a.created_at));

// Shared new-session create flow for the desktop Workbench home (`Workbench.tsx`)
// and the mobile NewSessionSheet. It's a thin layer over the shared projects
// provider — the project LIST and the create itself come from there, so a
// project/session created here shows up in the sidebar + Projects tree without a
// separate fetch (one source of truth). The hook only adds the picker selection,
// the transient sending/error state, and the most-recent target resolution.
// Navigation + draft handling + the sheet's open/close lifecycle stay in the consumer.
export function useNewSession({ active = true, loadErrorText, createFailedText }: UseNewSessionOptions): NewSessionState {
  const { projects: rawProjects, projectsError, createSessionForProject, upsertProjectToTop } = useWorkbenchProjectsTree();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const projects = useMemo(() => (rawProjects ? sortByRecent(rawProjects) : []), [rawProjects]);
  const loaded = rawProjects !== null;

  // Clear transient state when the sheet (re)opens so a prior submit / error
  // doesn't leak into the next open. The home passes active=true (runs once).
  useEffect(() => {
    if (!active) return;
    setSending(false);
    setError(null);
  }, [active]);

  // selectedId is the explicit pick; fall back to the most-recent project so a
  // null or now-hidden selection still resolves a sane target.
  const target = projects.find((p) => p.id === selectedId) ?? projects[0] ?? null;
  const needsProject = loaded && !target;

  const send = useCallback(
    async (text: string): Promise<{ sessionId: string; initialMessage: string } | null> => {
      const trimmed = text.trim();
      // Never create from a stale/empty/in-flight state; no target → caller opens New Project.
      if (!trimmed || sending || !loaded || !target) return null;
      setSending(true);
      setError(null);
      const session = await createSessionForProject(target.id);
      setSending(false);
      if (!session) {
        setError(createFailedText);
        return null;
      }
      return { sessionId: session.id, initialMessage: trimmed };
    },
    [sending, loaded, target, createSessionForProject, createFailedText],
  );

  const upsertSelectProject = useCallback(
    (project: WorkbenchProject) => {
      upsertProjectToTop(project); // updates the shared tree (sidebar + Projects page) too
      setSelectedId(project.id);
    },
    [upsertProjectToTop],
  );

  // Surface a project-load failure (provider-level) when we have no list, plus any
  // create error raised here.
  const visibleError = error ?? (!loaded && projectsError != null ? loadErrorText : null);

  return {
    projects,
    loaded,
    error: visibleError,
    sending,
    selectedId,
    setSelected: setSelectedId,
    target,
    needsProject,
    send,
    upsertSelectProject,
  };
}
