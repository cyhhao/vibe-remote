# Auth Setup Scenario Catalog

This file is now a human-facing index.

The canonical scenario metadata for this capability lives in:

- `tests/scenarios/auth_setup/catalog.yaml`
- `tests/scenarios/auth_setup/observations.yaml`
- `tests/scenarios/auth_setup/test_auth_setup_scenarios.py`

Use the canonical files when you need deterministic answers to:

- which `AUTH-SETUP-*` IDs exist
- which ones are covered or still gaps
- which historical regressions were observed in reality
- which scenario tests are the current executable evidence

Use this document only as a shortcut summary.

## Capability

`IM-driven backend auth recovery`

## Covered Scenario Bands

- Happy paths
  - `AUTH-SETUP-001` Codex device auth
  - `AUTH-SETUP-002` Claude manual callback
  - `AUTH-SETUP-003` OpenCode direct key
- Negative / validation paths
  - `AUTH-SETUP-101/102/103/104/105`
- Recovery / retry / teardown
  - `AUTH-SETUP-201/202/203/204/205/206/207`
- Historical regressions
  - `AUTH-SETUP-901/902`

## Fast Navigation

1. Read `tests/scenarios/INDEX.yaml`
2. Open `tests/scenarios/auth_setup/catalog.yaml`
3. Open `tests/scenarios/auth_setup/observations.yaml`
4. Read `tests/scenarios/auth_setup/test_auth_setup_scenarios.py`
5. Read `tests/test_claude_agent_sessions.py` for the runtime-specific `AUTH-SETUP-902` regression
