// Single source of truth for reasoning-effort options, shared by ChatPage, the
// Agents detail panel, and the New Agent dialog. Mirrors the backend lists in
// modules/agents/opencode/utils.py: Codex is minimal..xhigh, Claude is
// low/medium/high (+ xhigh/max on models that support it), OpenCode uses the
// broad superset.
export const EFFORT_BY_BACKEND: Record<string, string[]> = {
  claude: ['low', 'medium', 'high'],
  codex: ['minimal', 'low', 'medium', 'high', 'xhigh'],
  opencode: ['minimal', 'low', 'medium', 'high', 'xhigh', 'max'],
};

const DEFAULT_EFFORTS = ['low', 'medium', 'high'];

export const effortOptionsFor = (backend: string): string[] => EFFORT_BY_BACKEND[backend] ?? DEFAULT_EFFORTS;

// Backends whose model catalog actually ships per-model reasoning options.
// Today only Claude — Codex/OpenCode return a flat list, so a stale options map
// left over from a previous backend (e.g. ChatPage's claudeReasoning cache,
// which isn't cleared on backend switch) must NOT leak into their effort set.
const PER_MODEL_EFFORT_BACKENDS = new Set(['claude']);

// Resolve the selectable effort values for a backend + model. Only backends that
// publish per-model reasoning options (Claude) consult ``reasoningOptions``; the
// rest always use their backend superset, so a stale map can't leak. A known
// model uses its own set (Opus exposes xhigh/max, Haiku doesn't); an inherited
// (empty) or unknown/custom model uses the catalog's "" default set. We do NOT
// union across all models: the backend re-derives the effective model and runs
// normalize_claude_reasoning_effort, so a broader UI list would just surface
// efforts the backend silently drops (a false affordance). ``reasoningOptions``
// may be {} before the catalog loads — that yields the backend fallback.
export function resolveEffortOptions(
  backend: string,
  model: string | null | undefined,
  reasoningOptions: Record<string, { value: string; label: string }[]> | undefined,
): string[] {
  if (PER_MODEL_EFFORT_BACKENDS.has(backend) && reasoningOptions) {
    const perModel = reasoningOptions[model ?? ''] ?? reasoningOptions[''];
    const values = perModel?.filter((o) => o.value !== '__default__').map((o) => o.value);
    if (values && values.length) return values;
  }
  return effortOptionsFor(backend);
}
