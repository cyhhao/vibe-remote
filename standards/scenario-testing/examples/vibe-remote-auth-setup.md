# Vibe Remote Example: Auth Setup

This is a reference mapping of the standard onto one Vibe Remote capability.

It is intentionally an example, not the standard itself.

## Capability

`IM-driven backend auth recovery`

## Why This Capability Matters

This capability spans:

- IM command handling
- backend-specific auth setup flows
- external CLIs and SDKs
- runtime refresh after auth success
- user-visible success and failure messaging

It is exactly the kind of cross-cutting flow that can pass unit tests while still failing as a user journey.

## Reference Scenario IDs

- `AUTH-SETUP-001` Codex device auth happy path
- `AUTH-SETUP-002` Claude manual callback happy path
- `AUTH-SETUP-003` OpenCode direct key happy path
- `AUTH-SETUP-101` plain chat must not be consumed as setup input
- `AUTH-SETUP-202` failed verification must emit the reset path
- `AUTH-SETUP-901` Codex success must refresh runtime before the next turn

## Current Project Mapping

Reference implementation currently lives in:

- `tests/scenarios/auth_setup/catalog.yaml`
- `tests/scenarios/auth_setup/observations.yaml`
- `tests/scenarios/auth_setup/test_auth_setup_scenarios.py`
- `docs/regression/AUTH_SETUP_SCENARIOS.md`

These are project artifacts, not the reusable standard package itself.

## Example Harness Boundary

Inside the scenario harness:

- fake IM client
- fake process completion
- fake control-channel completion
- fake runtime refresh hooks

Outside the scenario harness:

- real provider OAuth pages
- real IM platform transports
- real external accounts and credentials
