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

// Canonical effort ordering so a unioned set still reads low → max.
const EFFORT_ORDER = ['minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

// Backends whose model catalog actually ships per-model reasoning options.
// Today only Claude — Codex/OpenCode return a flat list, so a stale options map
// left over from a previous backend (e.g. ChatPage's claudeReasoning cache,
// which isn't cleared on backend switch) must NOT leak into their effort set.
const PER_MODEL_EFFORT_BACKENDS = new Set(['claude']);

function orderEfforts(values: string[]): string[] {
  const set = new Set(values);
  const ordered = EFFORT_ORDER.filter((v) => set.has(v));
  const extras = values.filter((v) => !EFFORT_ORDER.includes(v)); // future / unknown efforts
  return [...ordered, ...extras];
}

// Resolve the selectable effort values for a backend + model. Only backends that
// publish per-model reasoning options (Claude) consult ``reasoningOptions``;
// the rest always use their backend superset, so a stale map can't leak. With a
// specific model selected, use that model's set (Opus offers xhigh/max, Haiku
// doesn't). With NO model (the agent inherits the backend default) offer the
// UNION across all known models, so a valid effort like ``max`` isn't hidden
// just because the exact inherited model is unknown. ``reasoningOptions`` may be
// {} before the catalog loads — that yields the backend fallback.
export function resolveEffortOptions(
  backend: string,
  model: string | null | undefined,
  reasoningOptions: Record<string, { value: string; label: string }[]> | undefined,
): string[] {
  if (PER_MODEL_EFFORT_BACKENDS.has(backend) && reasoningOptions) {
    const clean = (opts?: { value: string; label: string }[]): string[] =>
      (opts ?? []).filter((o) => o.value !== '__default__').map((o) => o.value);
    if (model) {
      const perModel = clean(reasoningOptions[model]);
      if (perModel.length) return perModel;
    }
    const union = orderEfforts([...new Set(Object.values(reasoningOptions).flatMap(clean))]);
    if (union.length) return union;
  }
  return effortOptionsFor(backend);
}
