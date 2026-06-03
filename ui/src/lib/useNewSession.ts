import { useCallback, useEffect, useMemo, useState } from 'react';

import { useApi } from '../context/ApiContext';
import { useWorkbenchProjectsTree } from '../context/WorkbenchProjectsContext';
import type { VibeAgentBrief, WorkbenchProject, WorkbenchSessionCreate } from '../context/ApiContext';

interface UseNewSessionOptions {
  /** Re-run the per-open reset on the rising edge — sheets pass their `open`. Default true. */
  active?: boolean;
  /** Pre-translated copy: the hook stays i18n-free, callers pass t(...) strings. */
  loadErrorText: string;
  createFailedText: string;
}

// The agent/model/effort selection (agent route). Empty = the server default
// (agents.default_backend). Fields allow null because the AgentRoutePicker emits
// null to clear model/effort when switching agents; send() drops nulls before
// creating so the create payload only carries real values.
export interface AgentRouteSelection {
  agent_backend?: string | null;
  agent_name?: string | null;
  agent_id?: string | null;
  agent_variant?: string | null;
  model?: string | null;
  reasoning_effort?: string | null;
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
  // Agent route (agent + model + effort). Empty = the server default.
  agents: VibeAgentBrief[];
  defaultAgentName: string | null;
  agentRoute: AgentRouteSelection;
  setAgentRoute: (patch: AgentRouteSelection) => void;
  /** Creates a session under `target` (with the picked agent route, if any) and returns the
   *  nav target; null if it couldn't start. The hook never navigates — the caller does. */
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
// selections (project + agent route), the transient sending/error state, and
// target resolution. Navigation + draft + the sheet's open/close lifecycle stay
// in the consumer.
export function useNewSession({ active = true, loadErrorText, createFailedText }: UseNewSessionOptions): NewSessionState {
  const api = useApi();
  const { projects: rawProjects, projectsError, createSessionForProject, upsertProjectToTop } = useWorkbenchProjectsTree();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [agents, setAgents] = useState<VibeAgentBrief[]>([]);
  const [defaultAgentName, setDefaultAgentName] = useState<string | null>(null);
  // The chosen agent route (agent/model/effort) as create fields; {} = default.
  const [agentRoute, setAgentRoute] = useState<AgentRouteSelection>({});

  const projects = useMemo(() => (rawProjects ? sortByRecent(rawProjects) : []), [rawProjects]);
  const loaded = rawProjects !== null;

  // Agents rarely change → fetch once per mount (not per sheet-open). Feeds the
  // shared AgentRoutePicker so the user can pick agent + model + effort instead
  // of always falling back to the server default.
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

  const applyAgentRoute = useCallback(
    (patch: AgentRouteSelection) => setAgentRoute((prev) => ({ ...prev, ...patch })),
    [],
  );

  // Seed the route from the selected project's default Agent so the picker shows
  // what the new session will actually use: create_session inherits the same
  // project default on the backend, so an un-seeded picker would display the
  // GLOBAL default while a different (project) Agent is what runs. This is the
  // React "adjust state while rendering when a prop changes" pattern (guarded by
  // the previous project id) — it re-seeds only when the selected project
  // changes, so a list reorder or the user's own pick within the project isn't
  // clobbered, and avoids the extra render an effect would add.
  const [seededProjectId, setSeededProjectId] = useState<string | null>(null);
  if (target && seededProjectId !== target.id) {
    setSeededProjectId(target.id);
    const def = target.default_agent;
    setAgentRoute(
      def
        ? {
            agent_backend: def.agent_backend,
            agent_name: def.agent_name,
            agent_variant: def.agent_variant,
            model: def.model,
            reasoning_effort: def.reasoning_effort,
          }
        : {},
    );
  }

  const send = useCallback(
    async (text: string): Promise<{ sessionId: string; initialMessage: string } | null> => {
      const trimmed = text.trim();
      // Never create from a stale/empty/in-flight state; no target → caller opens New Project.
      if (!trimmed || sending || !loaded || !target) return null;
      setSending(true);
      setError(null);
      // Empty agentRoute → no agent fields → the server uses its default backend.
      // Drop null/undefined (the picker stores null to clear) so the create
      // payload only carries the fields the user actually set.
      const overrides: Partial<WorkbenchSessionCreate> = {};
      if (agentRoute.agent_backend) overrides.agent_backend = agentRoute.agent_backend;
      if (agentRoute.agent_name) overrides.agent_name = agentRoute.agent_name;
      if (agentRoute.agent_id) overrides.agent_id = agentRoute.agent_id;
      if (agentRoute.agent_variant) overrides.agent_variant = agentRoute.agent_variant;
      if (agentRoute.model) overrides.model = agentRoute.model;
      if (agentRoute.reasoning_effort) overrides.reasoning_effort = agentRoute.reasoning_effort;
      const session = await createSessionForProject(target.id, overrides);
      setSending(false);
      if (!session) {
        setError(createFailedText);
        return null;
      }
      return { sessionId: session.id, initialMessage: trimmed };
    },
    [sending, loaded, target, agentRoute, createSessionForProject, createFailedText],
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
    agentRoute,
    setAgentRoute: applyAgentRoute,
    send,
    upsertSelectProject,
  };
}
