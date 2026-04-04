# Auth Setup Scenario Catalog

## Capability

`IM-driven backend auth recovery`

## Scenarios

### `AUTH-SETUP-001 Codex device auth happy path`

- Type: happy path
- Layer: scenario
- Given:
  An IM user starts Codex setup.
- When:
  Codex emits the device URL and code, the external login completes, and verification succeeds.
- Then:
  The user sees the login instructions, runtime refresh runs, and the user gets a success message.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_codex_device_auth_scenario_reaches_terminal_success`

### `AUTH-SETUP-002 Claude manual callback happy path`

- Type: happy path
- Layer: scenario
- Given:
  An IM user starts Claude setup in manual callback mode.
- When:
  The user replies with `authorizationCode#state` and Claude completion succeeds.
- Then:
  The user sees the callback instructions, runtime refresh runs, and the user gets a success message.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_claude_manual_callback_scenario_accepts_plain_reply_and_completes`

### `AUTH-SETUP-003 OpenCode direct key happy path`

- Type: happy path
- Layer: scenario
- Given:
  An IM user starts OpenCode direct-key setup.
- When:
  The user replies with a valid credential.
- Then:
  The key is installed, runtime refresh runs, stale sessions are cleared, and the user gets a success message.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_opencode_direct_key_scenario_installs_key_and_refreshes_runtime`

### `AUTH-SETUP-101 OpenCode plain chat is ignored while waiting for a credential`

- Type: negative path
- Layer: scenario
- Given:
  OpenCode setup is waiting for a credential reply.
- When:
  The user sends a normal chat message instead of a credential.
- Then:
  The message is not consumed as setup input and the flow keeps waiting.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_opencode_waiting_key_scenario_ignores_plain_chat`

### `AUTH-SETUP-201 Re-entering setup replaces the active flow`

- Type: recovery / retry
- Layer: scenario
- Given:
  A setup flow is already active for the same user and backend.
- When:
  The user starts setup again.
- Then:
  The old flow is canceled and terminated, and a fresh flow replaces it.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_codex_reentry_scenario_replaces_existing_flow`

### `AUTH-SETUP-202 Failed verification emits the reset path`

- Type: recovery / retry
- Layer: scenario
- Given:
  The setup process exits but login verification fails.
- When:
  The service evaluates the failed verification result.
- Then:
  The user gets a failure message with the reset/auth-setup action.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_codex_failure_scenario_emits_reset_path`

## Next High-Priority Gaps

- `AUTH-SETUP-102` wrong Claude callback format keeps the flow active
- `AUTH-SETUP-103` wrong user cannot submit code into someone else's flow
- `AUTH-SETUP-203` setup timeout emits a recoverable terminal state
- `AUTH-SETUP-204` success path refreshes runtime and clears stale sessions before next user turn
- `AUTH-SETUP-205` concurrent backend flows in one channel do not steal each other's replies
