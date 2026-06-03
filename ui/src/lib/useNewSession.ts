import { useCallback, useEffect, useMemo, useState } from 'react';

import { useApi } from '../context/ApiContext';
import { useWorkbenchProjectsTree } from '../context/WorkbenchProjectsContext';
import type { VibeAgentBrief, WorkbenchProject } from '../context/ApiContext';

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
  // Agent (backend) selection — null = the server default (agents.default_backend).
  agents: VibeAgentBrief[];
  defaultAgentName: string | null;
  selectedAgent: VibeAgentBrief | null;
  setSelectedAgent: (id: string | null) => void;
  /** Creates a session under `target` (with the picked agent, if any) and returns the nav
   *  target; null if it couldn't start (empty / in-flight / not loaded / no project / error).
   *  The hook never navigates — the caller does, since the provider is mounted outside the router. */
  send: (text: string) => Promise<{ sessionId: string; initialMessage: string } | null>;
  upsertSelectProject: (project: WorkbenchProject) => void;
}

const sortByRecent = (list: WorkbenchProject[]) =>
  list
    .slice()
    .sort((a, b) => (b.last_active_at || b.created_at).localeCompare(a.last_active_at || a.created_at));

// Shared new-session create flow for the desktop Workbench home (`Workbench.tsx`)
// and the mobile NewSessionSheet. A thin layer over the shared projects provider
// (the project LIST + the create itself come from there, so a project/session
// created here shows up in the sidebar + Projects tree). It adds the picker
// selections (project + agent), the transient sending/error state, and target
// resolution. Navigation + draft + the sheet's open/close lifecycle stay in the consumer.
export function useNewSession({ active = true, loadErrorText, createFailedText }: UseNewSessionOptions): NewSessionState {
  const api = useApi();
  const { projects: rawProjects, projectsError, createSessionForProject, upsertProjectToTop } = useWorkbenchProjectsTree();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [agents, setAgents] = useState<VibeAgentBrief[]>([]);
  const [defaultAgentName, setDefaultAgentName] = useState<string | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);

  const projects = useMemo(() => (rawProjects ? sortByRecent(rawProjects) : []), [rawProjects]);
  const loaded = rawProjects !== null;

  // Agents rarely change → fetch once per mount (not per sheet-open). Lets the
  // user pick which Vibe Agent (backend) runs the session instead of always
  // falling back to the server default.
  useEffect(() => {
    let cancelled = false;
    api
      .listVibeAgents({ includeDisabled: false })
      .then((res) => {
        if (cancelled) return;
        setAgents(res.agents);
        setDefaultAgentName(res.default_agent_name);
      })
      .catch(() => {
        if (!cancelled) {
          setAgents([]);
          setDefaultAgentName(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [api]);

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
  const selectedAgent = useMemo(() => agents.find((a) => a.id === selectedAgentId) ?? null, [agents, selectedAgentId]);

  const send = useCallback(
    async (text: string): Promise<{ sessionId: string; initialMessage: string } | null> => {
      const trimmed = text.trim();
      // Never create from a stale/empty/in-flight state; no target → caller opens New Project.
      if (!trimmed || sending || !loaded || !target) return null;
      setSending(true);
      setError(null);
      // null selectedAgent → omit agent fields so the server uses its default.
      const overrides = selectedAgent
        ? {
            agent_id: selectedAgent.id,
            agent_name: selectedAgent.name,
            agent_backend: selectedAgent.backend,
            // Match agent_variant to the backend so the session can resume its
            // native thread (mirrors the chat AgentRoutePicker).
            agent_variant: selectedAgent.backend,
            model: selectedAgent.model ?? undefined,
            reasoning_effort: selectedAgent.reasoning_effort ?? undefined,
          }
        : undefined;
      const session = await createSessionForProject(target.id, overrides);
      setSending(false);
      if (!session) {
        setError(createFailedText);
        return null;
      }
      return { sessionId: session.id, initialMessage: trimmed };
    },
    [sending, loaded, target, selectedAgent, createSessionForProject, createFailedText],
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
    agents,
    defaultAgentName,
    selectedAgent,
    setSelectedAgent: setSelectedAgentId,
    send,
    upsertSelectProject,
  };
}
