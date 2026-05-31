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

// Resolve the selectable effort values for a backend + model. When the model
// catalog ships per-model reasoning options (Claude via claude_models, keyed by
// model id with a "" default entry), use the selected model's set so e.g. Opus
// offers xhigh/max while Haiku doesn't. Backends without per-model data (Codex,
// OpenCode) fall back to the backend superset. ``reasoningOptions`` may be an
// empty object before the catalog loads — that simply yields the fallback.
export function resolveEffortOptions(
  backend: string,
  model: string | null | undefined,
  reasoningOptions: Record<string, { value: string; label: string }[]> | undefined,
): string[] {
  if (reasoningOptions) {
    const perModel = reasoningOptions[model ?? ''] ?? reasoningOptions[''];
    const values = perModel?.filter((o) => o.value !== '__default__').map((o) => o.value);
    if (values && values.length) return values;
  }
  return effortOptionsFor(backend);
}
