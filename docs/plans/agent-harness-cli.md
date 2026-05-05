# Agent Harness CLI

## Background

Vibe Remote already exposes three harness-style primitives:

- `vibe task ...`: persist a scheduled turn definition and execute it later
- `vibe hook send ...`: queue one async turn into an existing Vibe session
- `vibe watch ...`: run a waiter process and enqueue hooks when events happen

These commands already form a usable local automation surface, but they are still
session-oriented. They do not provide a first-class way for one backend agent to
invoke another backend agent directly through Vibe Remote.

The new goal is to make `vibe` itself the agent-native harness surface, so an
agent can discover callable backends/subagents and invoke them directly through a
stable CLI instead of relying on injected ad-hoc shell scripts or a separate MCP
layer.

## Current Execution Model

### Existing commands

- `task` persists a definition in `scheduled_tasks.json`
- `task run` and `hook send` enqueue one request under `~/.vibe_remote/state/task_requests/`
- `watch` persists watcher definitions in `watches.json`
- `watch` completion and terminal failures enqueue `hook_send` requests
- `ScheduledTaskService` drains queued requests and routes them into
  `message_handler.handle_scheduled_message(...)`

### Important distinction

`hook` is not a generic agent invocation API.

Its real semantic meaning today is:

- continue an existing Vibe session
- inject one async prompt into that session
- preserve the same scheduled-turn/session alias behavior as `task`

This is different from direct agent invocation, whose semantic center is the
target backend/agent rather than the destination session key.

## Goals

- Add a first-class `vibe agent` CLI surface.
- Let any running backend agent discover callable agents across supported
  backends.
- Let any running backend agent invoke another backend agent or subagent through
  a stable non-interactive CLI.
- Keep `vibe` as the single harness interface for humans, scripts, and agents.
- Preserve the existing `task` / `watch` / `hook` semantics unless we have a
  strong migration reason.

## Non-Goals For V1

- Do not merge `hook` into `agent` at the command surface yet.
- Do not redesign the whole scheduled-turn pipeline.
- Do not introduce a separate MCP-only invocation layer.
- Do not solve multi-step orchestration, recursion control, or long-lived agent
  workflows in the first implementation.

## Proposed V1 Command Surface

### `vibe agent list`

Purpose:

- list callable agents visible from the current cwd

Expected scope:

- backend default agents
- backend subagents
- project-local definitions
- global definitions when relevant

Initial flags:

- `--cwd <path>`
- `--json`

### `vibe agent run`

Purpose:

- invoke one target backend/agent directly and return its result

Initial flags:

- `--backend <opencode|claude|codex>`
- `--agent <name>`
- `--prompt <text>` or `--prompt-file <path>`
- `--cwd <path>`
- `--timeout <seconds>`
- `--json`

V1 intentionally excludes:

- `--session-key`
- `--post-to`
- `--deliver-key`
- detached async execution
- stored invocation definitions
- recursive orchestration policy

## Why `hook` And `agent` Stay Separate In V1

### `hook`

Primary identity:

- session-oriented

Core question it answers:

- which existing Vibe conversation/session should receive one async turn?

Primary inputs:

- `session_key`
- optional delivery override
- prompt

### `agent`

Primary identity:

- agent-oriented

Core question it answers:

- which backend/agent should execute this work?

Primary inputs:

- backend
- agent name
- cwd
- prompt

If `agent run` also accepts `session_key` and delivery semantics in V1, the user
and the calling agent will no longer have a clean mental model of whether the
command means direct invocation or async session injection.

## Likely Internal Direction

Even if the CLI stays separate, the internal model should probably converge on a
shared invocation abstraction later.

Possible internal invocation kinds:

- `session_turn`
- `agent_run`

This would let Vibe Remote keep clean external commands while still reusing queue,
execution, receipts, policies, and observability internally.

## Open Questions To Resolve Before Implementation

### 1. Target identity

- Should `--agent default` mean the backend's default top-level agent?
- Or should the default top-level agent be represented with an empty agent name
  and only subagents appear in `list`?
- Should `agent list` show both `backend` and `kind` (`default` vs `subagent`)?

### 2. Execution model

- Should `agent run` always be one-shot and stateless in V1?
- Or should it optionally reuse backend-native sessions when possible?
- If session reuse is allowed later, is that a separate flag or a separate
  command family?

### 3. Output contract

- Should `agent run --json` return only the final textual result?
- Or should it also return structured fields like:
  - resolved backend
  - resolved agent
  - cwd
  - duration
  - exit status
  - files/artifacts
  - raw stderr / diagnostic summary
- Should non-JSON mode be human-readable only, with JSON mode treated as the
  stable machine contract?

### 4. Failure contract

- What should count as command failure vs agent-level failure?
- If the target agent ran successfully but returned an error message, should the
  CLI exit non-zero or still return `ok: true` with an agent error payload?

### 5. Backend coverage

- Must V1 support OpenCode, Claude, and Codex equally on day one?
- Or is it acceptable to ship one backend first if the command contract is
  already backend-neutral?

### 6. Discovery scope

- Should `agent list` include only agents that are currently enabled in Vibe
  config?
- Or should it include all discovered definitions and annotate whether each one
  is runnable right now?

### 7. Prompt layering

- When invoking a target subagent, what prompt layers apply?
- Expected candidates:
  - explicit `--prompt`
  - target subagent instructions
  - project `AGENTS.md` / `CLAUDE.md`
  - backend-native defaults
- Do we want `vibe agent run` to mimic the backend's normal behavior as closely
  as possible, or establish a Vibe-owned wrapper prompt?

### 8. Working directory semantics

- Should `--cwd` be required or optional?
- If omitted, should it default to the current shell cwd, Vibe runtime default
  cwd, or the caller's current conversation cwd when invoked from inside Vibe?

### 9. Recursion policy

- Can an agent invoked through `vibe agent run` call `vibe agent run` again?
- If yes, what depth limit or loop protection is required?
- If no, how will the command communicate that restriction?

### 10. Relationship to existing harness primitives

- Should `task` and `watch` remain session-turn producers only in V1?
- Or do we want a near-term follow-up where they can trigger `agent_run`
  invocations instead of only `hook`-style session turns?

## Recommended V1 Decisions

- Keep `hook` unchanged as the async session-turn primitive.
- Ship `agent list` and `agent run` as a separate command family.
- Keep V1 synchronous and one-shot.
- Make `--json` the stable machine interface.
- Avoid session/delivery flags on `agent run` until the direct invocation model
  is proven.

## Todo

1. Resolve the open questions above.
2. Finalize the V1 command contract and JSON schema.
3. Design the internal backend-neutral invocation interface.
4. Decide whether V1 executes inline in the CLI process or reuses a local Vibe
   runtime service path.
5. Implement `vibe agent list`.
6. Implement `vibe agent run`.
7. Add tests for discovery, invocation, JSON output, and failure behavior.
8. Update CLI docs and injected prompt guidance.
