# Auth Setup Scenario Harness

This document defines the lightweight closed-loop regression pattern for IM-driven backend auth/setup flows.

It complements existing unit tests and manual Docker regression:

- unit tests still own parsing, heuristics, and narrow edge cases
- scenario harness tests own the multi-step setup flow inside the service boundary
- manual regression still owns real IM adapters, real OAuth pages, and real provider accounts

## Goal

Catch regressions where each individual helper still passes, but the full setup flow no longer closes correctly.

Typical failures in this category:

- setup starts, but no completion waiter is attached
- the user reply is no longer consumed by the active setup flow
- runtime refresh is skipped after successful verification
- the final success/failure message is never emitted
- one backend-specific branch accidentally steals another backend's follow-up input

## Keep It Lightweight

Do not build a generic workflow engine first.

The preferred pattern is:

1. keep production code changes small
2. add a focused scenario test under `tests/test_agent_auth_setup_scenarios.py`
3. reuse shared test primitives from `tests/scenario_harness/` before creating new one-off fakes
4. use fake process / fake control client / fake IM client transcripts
5. drive the real public service methods (`start_setup`, `maybe_consume_setup_reply`, `submit_code`) whenever practical

If a new helper is only needed by one scenario, keep it local to the test file.

## Required Coverage

When changing auth/setup behavior, add or update at least one closed-loop scenario that covers:

1. setup starts
2. the user-facing login instruction is emitted
3. the external follow-up is simulated
   - CLI text transcript
   - plain IM reply
   - control-channel completion
4. login verification runs
5. backend runtime refresh happens
6. a terminal user-visible message is emitted

For any backend-specific auth flow, the default target is one happy-path scenario plus unit tests for branchy edge cases.

## Current Project Mapping

Reference implementation currently lives in:

- `tests/scenario_harness/`
- `tests/test_agent_auth_setup_scenarios.py`
- `docs/regression/AUTH_SETUP_SCENARIOS.md`
- `docs/regression/AUTH_SETUP_SCENARIO_CATALOG.md`

## Current Scenario Matrix

- Codex device auth:
  - start setup
  - emit device URL and code
  - complete process
  - verify login
  - refresh runtime
  - emit success
  - prove the next user turn sees refreshed runtime instead of stale auth state
- Claude manual callback:
  - start control-channel auth
  - emit manual URL
  - accept plain `authorizationCode#state` reply
  - treat plain reply and `/setup code ...` fallback as one logical submission, not two
  - reject malformed callback values while keeping the flow recoverable
  - wait for completion
  - time out into a recoverable terminal state if completion never arrives
  - verify login
  - refresh runtime
  - emit success
  - reject wrong-user callback submission without stealing the flow
  - treat assistant auth-failure runtime events as OAuth recovery, even without deprecated metadata flags
- OpenCode direct key:
  - start setup
  - emit auth URL
  - accept plain credential reply
  - reject invalid-looking replies without killing the flow
  - allow a later valid retry to complete the flow
  - install key
  - refresh runtime
  - clear stale sessions
  - emit success
  - prove the next user turn sees refreshed runtime and cleared sessions
- Shared multi-flow routing:
  - keep Claude and OpenCode flows active in one channel
  - route callback-shaped input to Claude
  - route credential-shaped input to OpenCode
  - avoid cross-flow stealing
- Shared teardown and restart:
  - let a flow timeout into a recoverable terminal state
  - start setup again immediately
  - prove the new attempt gets a fresh flow and fresh instructions
  - prove a failed attempt does not leak stale runtime state into the next retry

The detailed capability matrix now lives in:

- `docs/regression/AUTH_SETUP_SCENARIO_CATALOG.md`

Use the catalog when deciding:

- which backend/path needs the next scenario
- whether a new bug is already covered by an existing scenario ID
- whether the gap is scenario-worthy or should stay unit/contract/manual

## What Stays in Unit Tests

Keep these as small focused tests instead of scenario tests:

- callback/code parsing
- credential-shape heuristics
- auth-error classification
- flow lookup priority
- single cleanup invariants

Scenario tests should assert the user journey, not every internal branch.

## What Still Needs Manual Regression

Keep manual regression for:

- real IM transport behavior
- real OAuth/account/provider interactions
- CLI or SDK behavior that cannot be deterministically simulated at the service boundary

When a bug is first discovered manually, the follow-up rule is:

1. fix the root cause
2. add the narrow unit test if needed
3. add or update the nearest closed-loop scenario so the same journey cannot silently regress again
