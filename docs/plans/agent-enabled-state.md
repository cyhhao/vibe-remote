# Agent Enabled State Plan

## Goal

Add first-class enablement to Vibe Agents before the Agent feature ships. The
final model should be simple and explicit: every Agent has `enabled`, including
built-in default Agents, and backend toggles synchronize the matching built-in
Agent state.

## Semantics

- `agents.enabled` is the persisted Agent-level switch.
- User-created, imported, and built-in Agents default to enabled.
- A disabled Agent is not eligible for default routing or Scope/session fallback.
- Built-in default Agents remain non-deletable, but can be disabled.
- Backend enablement is synchronized to its built-in default Agent on backend
  state changes:
  - backend enabled: ensure `<backend>` built-in Agent exists and enable it if
    the backend state just transitioned to enabled
  - backend disabled: if `<backend>` built-in Agent exists, set `enabled=false`
  - a normal Agent catalog refresh must not undo a user/manual disable of a
    built-in Agent while the backend remains enabled
- Because Agent features are not shipped yet, schema and tests can target the final shape without legacy data migration complexity beyond normal SQLite schema readiness.

## Implementation

- Add `enabled` to the `agents` table/model and VibeAgent dataclass.
- Teach `VibeAgentStore` create/update/import/default-agent APIs about `enabled`.
- Add store helpers for `set_enabled`, enabled list/default selection, and backend sync.
- Call backend sync from startup/API ensure paths and after config save.
- Expose CLI enable/disable and update/list payloads.
- Keep routing/default resolution on enabled Agents only.
- Add focused tests for store behavior, backend sync, CLI toggles, and routing fallback.

## Validation

- Ruff on changed Python files.
- Targeted pytest for Agent store/API/CLI/routing tests.
