# Agent / Scope Model

## Goal

Scope remains the workspace and routing context: it connects an Agent, active
sessions, working directory, and IM output. Scope should no longer expose a
Backend choice as a first-class setting.

Agent is the robot. It owns the Backend plus default model, reasoning effort,
and system prompt. Each enabled Backend gets one built-in, non-deletable
default Agent named after that Backend.

## Implementation Plan

1. Ensure built-in default Agents exist for enabled Backends.
2. Normalize legacy Scope settings:
   - `agent_backend` maps to the matching built-in Agent when `agent_name` is
     absent.
   - backend-specific model and reasoning fields are migrated into the
     canonical Scope-level `model` and `reasoning_effort` overrides.
3. Resolve runtime settings from Scope first:
   - Scope chooses Agent.
   - Agent supplies Backend and defaults.
   - Scope may override model and reasoning effort.
   - Scope does not override system prompt.
4. Simplify Scope UI to one Agent selector plus model / reasoning overrides.
5. Keep migration-period input/read-back compatibility for old fields without
   storing them as independent state.
