# `vibe agent models` — query available models + reasoning efforts

## Background

`vibe agent create / update` accept `--model` and `--reasoning-effort` as free-form
strings with no `choices`, no validation, and no discovery path. An agent (or user)
calling the CLI to configure another Agent has no way to learn which models or
reasoning-effort levels a backend actually supports — it can only guess. The
knowledge already exists in the codebase (Claude catalog, Codex built-in list +
caches, OpenCode live providers incl. PR #461 custom providers / user models) but
nothing surfaces it through the CLI.

## Goal

Add `vibe agent models` so an agent can discover, for a given Agent (or backend),
the available models and the reasoning-effort levels valid for each — including
OpenCode custom providers and user-added models — and feed that back into
`create` / `update`.

## Design (approved)

Command, Agent-centric to match `vibe agent show / update / remove`:

```
vibe agent models <name>          # an Agent: resolve its backend + current model
  --backend {claude,codex,opencode}   # query a backend directly (pre-creation)
  --provider <id>                     # OpenCode-only filter (error on other backends)
  --model <id>                        # narrow to one model's reasoning efforts
  --json                              # no-op; JSON is the default envelope
```

Exactly one of `<name>` or `--backend`. Output `kind: "agent_models"`:

```
{ ok, kind: "agent_models", agent, backend,
  current: { model, reasoning_effort, model_known, reasoning_effort_valid, valid } | null,
  default_model,
  providers: [ { id, name, custom } ],          # OpenCode only
  models:    [ { value, default?, provider?, source?, reasoning_efforts: [...] } ],
  source, live, notes }
```

Reasoning efforts are nested **per model** because they are a property of the model
(Claude `xhigh`/`max` depend on the model; OpenCode varies by provider variant).

## Architecture — reuse, don't rebuild

- New backend-agnostic `api.agent_model_options(backend, *, model, provider, cwd)`
  wraps the three existing producers into one shape:
  - `claude_models()` → models + per-model reasoning options (already returns both)
  - `codex_models()` → **extend** to also return `reasoning_options`
    (`build_codex_reasoning_options()` already exists; just surface it)
  - `opencode_options(cwd)` → already overlays custom providers + user models
    (PR #461: `_read_opencode_custom_provider_ids` + `_merge_opencode_user_models`),
    so reusing it covers them for free. Annotate `source: user|catalog`
    (`opencode_config._is_vibe_user_model`) and `custom` providers
    (`read_opencode_custom_providers`). `--provider` filters via the existing
    `allowed_providers` concept. cwd resolved internally (`runtime.default_cwd`),
    never a CLI flag.
- The same resolver can later back the existing `/api/{claude,codex}/models`
  endpoints so the Claude reasoning rules stop being duplicated in TS
  (`RoutingConfigPanel.tsx`). Out of scope for this PR (follow-up).

## Validation policy — warn, don't reject (decision #1)

`create` / `update` keep accepting free-form values (catalogs are best-effort and
can lag a new model). When a set value is not in the known set, attach a non-fatal
`warnings` + `hint` pointing at `vibe agent models <name>`. Uses only the cheap
file-based sources (claude/codex); OpenCode is skipped (its list is live) to keep
`create`/`update` fast.

## Scope (decision #2)

In: the query command, the resolver, `codex_models()` reasoning, the create/update
warning. Out (follow-up PR): consolidating the duplicated TS reasoning rules onto
the Python source.

## Todo

- [ ] `api.agent_model_options` + `codex_models()` reasoning
- [ ] `vibe agent models` parser + `cmd_agent_models`
- [ ] create/update warn-not-reject hint
- [ ] tests (resolver, CLI, codex reasoning, warning)
- [ ] docs + ruff + targeted pytest
- [ ] reviewer subagent → PR (non-draft) → Codex review watch

## Evidence layers

- unit: codex reasoning, resolver per backend, warning helper
- contract: `cmd_agent_models` name / `--backend` / `--provider` error envelope
- scenario: n/a (no catalog entry for CLI agent mgmt yet)
- manual: `vibe agent models` against claude/codex/opencode in an isolated home
