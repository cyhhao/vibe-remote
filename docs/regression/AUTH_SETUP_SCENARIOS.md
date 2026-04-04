# Auth Setup Scenario Harness

This document is now a human-facing guide for the auth/setup capability.

The canonical scenario assets are no longer stored in `docs/regression/`.
They live next to the executable tests:

- `tests/scenarios/INDEX.yaml`
- `tests/scenarios/auth_setup/catalog.yaml`
- `tests/scenarios/auth_setup/observations.yaml`
- `tests/scenarios/auth_setup/test_auth_setup_scenarios.py`
- `tests/scenario_harness/auth_setup.py`

## Why This Capability Uses Scenario Tests

`AUTH-SETUP` is a multi-step user journey:

1. setup starts
2. the user sees login instructions
3. the user or external system sends a follow-up
4. verification runs
5. runtime refresh happens
6. the user sees a terminal success or failure message

This is exactly the class of flow that can pass unit tests while still breaking as a user-visible journey.

## Where To Look

If you are an agent starting from zero context:

1. start with `tests/scenarios/INDEX.yaml`
2. open `tests/scenarios/auth_setup/catalog.yaml`
3. open `tests/scenarios/auth_setup/observations.yaml`
4. read `tests/scenarios/auth_setup/test_auth_setup_scenarios.py`
5. only then inspect `tests/scenario_harness/auth_setup.py` or production code

## Boundary Rule

- `tests/scenarios/auth_setup/test_auth_setup_scenarios.py`
  owns the closed-loop service-boundary scenarios
- `tests/test_agent_auth_service.py`
  owns focused auth/setup unit coverage
- `tests/test_claude_agent_sessions.py`
  owns runtime-specific Claude regression evidence such as `AUTH-SETUP-902`
- manual Docker regression
  still owns real IM transports, real OAuth pages, and real provider accounts
