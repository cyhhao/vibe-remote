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

## Current Scenario Matrix

- Codex device auth:
  - start setup
  - emit device URL and code
  - complete process
  - verify login
  - refresh runtime
  - emit success
- Claude manual callback:
  - start control-channel auth
  - emit manual URL
  - accept plain `authorizationCode#state` reply
  - wait for completion
  - verify login
  - refresh runtime
  - emit success
- OpenCode direct key:
  - start setup
  - emit auth URL
  - accept plain credential reply
  - install key
  - refresh runtime
  - clear stale sessions
  - emit success

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
