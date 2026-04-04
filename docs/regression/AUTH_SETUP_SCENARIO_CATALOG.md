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

### `AUTH-SETUP-103 Wrong user cannot submit into another user's active flow`

- Type: negative path
- Layer: scenario
- Given:
  A setup flow is active in a shared channel for one user.
- When:
  Another user tries to submit the follow-up callback/code into that flow.
- Then:
  The reply is not consumed, the user gets an ownership error, and the original flow remains active.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_claude_wrong_user_cannot_submit_callback_into_active_flow`

### `AUTH-SETUP-102 Malformed Claude callback keeps the flow recoverable`

- Type: negative path
- Layer: scenario
- Given:
  Claude setup is waiting for the browser callback value.
- When:
  The user submits a malformed value that is not `authorizationCode#state`.
- Then:
  The callback is rejected, the flow stays active, and the user gets retry guidance.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_claude_malformed_callback_keeps_flow_active_and_instructs_retry`

### `AUTH-SETUP-204 Successful setup refreshes runtime before the next user turn`

- Type: recovery / post-success readiness
- Layer: scenario
- Given:
  A setup flow completes successfully.
- When:
  The next user turn happens immediately after the success message.
- Then:
  Runtime refresh and stale-session cleanup have already happened, so the next turn sees the refreshed backend state.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_successful_setup_refreshes_runtime_before_the_next_turn`

### `AUTH-SETUP-203 Setup timeout emits a recoverable terminal state`

- Type: recovery / timeout
- Layer: scenario
- Given:
  A setup flow starts but external completion never arrives.
- When:
  The flow hits its deadline.
- Then:
  The flow terminates into a recoverable failure, emits a retry/reset path, and cleans up the active flow.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_claude_timeout_emits_recoverable_terminal_state`

### `AUTH-SETUP-205 Concurrent backend flows do not steal each other's replies`

- Type: concurrency / routing
- Layer: scenario
- Given:
  Multiple backend setup flows are active in the same channel.
- When:
  The user sends backend-specific follow-up replies.
- Then:
  Each reply is consumed by the matching backend flow and the other flow remains intact until its own reply arrives.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_concurrent_setup_flows_route_replies_to_the_matching_backend`

### `AUTH-SETUP-104 Invalid OpenCode credential reply stays recoverable until a valid retry`

- Type: negative path
- Layer: scenario
- Given:
  OpenCode direct-key setup is waiting for a credential reply.
- When:
  The user first sends an invalid-looking value, then retries with a valid credential.
- Then:
  The invalid reply is ignored, the flow stays active, and the valid retry still completes the flow successfully.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_opencode_invalid_reply_keeps_flow_recoverable_until_valid_retry`

### `AUTH-SETUP-206 Terminal teardown leaves the next setup attempt clean`

- Type: recovery / teardown
- Layer: scenario
- Given:
  A setup flow reaches a terminal timeout/failure state.
- When:
  The user starts setup again immediately afterward.
- Then:
  The old flow no longer owns the channel state, a new flow starts cleanly, and fresh instructions are emitted for the new attempt.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_timed_out_flow_allows_clean_restart_without_stale_state`

### `AUTH-SETUP-105 Plain callback submission and command fallback do not double-consume the same flow`

- Type: negative path / input deduplication
- Layer: scenario
- Given:
  A setup flow accepts plain-text follow-up input in the chat.
- When:
  The user first submits the plain reply, then repeats the old `/setup code ...` fallback after the flow already completed.
- Then:
  The flow is consumed exactly once and the fallback sees no active flow instead of replaying the submission.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_callback_submission_and_fallback_command_do_not_double_consume_claude_flow`

### `AUTH-SETUP-901 Codex setup success refreshes runtime before the next turn`

- Type: historical regression / post-success readiness
- Layer: scenario
- Given:
  Codex setup completes successfully.
- When:
  The next user turn starts immediately afterward.
- Then:
  Codex runtime refresh has already completed, so the next turn does not reuse stale auth state.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_codex_successful_setup_refreshes_runtime_before_the_next_turn`

### `AUTH-SETUP-902 Claude assistant auth-failure event triggers recovery even without the deprecated API-error flag`

- Type: historical regression / auth recovery
- Layer: scenario-backed runtime regression
- Given:
  Claude runtime receives an assistant auth-failure event.
- When:
  The event carries `error=\"authentication_failed\"` but does not expose the older `isApiErrorMessage` flag.
- Then:
  OAuth recovery still triggers and the stale runtime session is cleaned up instead of leaking the failure as plain text.
- Current test:
  `tests/test_claude_agent_sessions.py::test_assistant_auth_error_without_is_api_error_flag_still_triggers_recovery`

### `AUTH-SETUP-207 Recoverable failure leaves the next attempt free of stale runtime state`

- Type: recovery / clean retry
- Layer: scenario
- Given:
  A setup attempt ends in a recoverable failure.
- When:
  The user starts setup again immediately afterward.
- Then:
  The failed flow no longer owns the channel state, runtime refresh does not leak from the failed attempt, and the next attempt can complete cleanly.
- Current test:
  `tests/test_agent_auth_setup_scenarios.py::test_failed_codex_setup_does_not_leave_stale_runtime_for_next_attempt`

## Coverage Matrix

This matrix tracks the current `AUTH-SETUP` capability by:

- backend
- user-visible phase
- dominant risk
- current evidence layer
- next action

Status legend:

- `covered`
  scenario coverage exists
- `partial`
  only unit/contract coverage exists, or only one backend branch is covered
- `manual`
  currently depends on manual regression
- `gap`
  not covered yet

| Scenario band | Backend | User-visible phase | Dominant risk | Status | Current evidence | Next action |
| --- | --- | --- | --- | --- | --- | --- |
| `AUTH-SETUP-001` | Codex | start -> device URL/code -> verify success | waiter missing, success never emitted | covered | scenario + unit | keep as happy-path baseline |
| `AUTH-SETUP-002` | Claude | start -> manual callback -> verify success | callback reply not consumed, completion never closes | covered | scenario + unit | keep as happy-path baseline |
| `AUTH-SETUP-003` | OpenCode | start -> direct key reply -> refresh success | credential reply not installed, stale session survives | covered | scenario + unit | keep as happy-path baseline |
| `AUTH-SETUP-101` | OpenCode | waiting for credential reply | normal chat stolen by setup flow | covered | scenario + unit | extend the same pattern to Claude/Codex waiting replies |
| `AUTH-SETUP-201` | Codex | re-enter setup while active flow exists | duplicate flows, stale process survives | covered | scenario + unit | add cross-backend re-entry coverage |
| `AUTH-SETUP-202` | Codex | process exits but verify fails | user gets no recovery path | covered | scenario + unit | add equivalent Claude/OpenCode recovery failures |
| `AUTH-SETUP-102` | Claude | callback reply accepted | malformed `authorizationCode#state` is consumed or flow dies | covered | scenario + unit | preserve as callback-shape guardrail |
| `AUTH-SETUP-103` | shared | waiting for user reply | wrong user can submit into another user's flow | covered | scenario + unit | keep as shared ownership guardrail |
| `AUTH-SETUP-104` | OpenCode | waiting for credential reply | invalid-looking credential consumed or separator-only reply swallowed | covered | scenario + unit + heuristic tests | preserve as invalid-retry guardrail |
| `AUTH-SETUP-105` | shared | fallback input handling | plain reply and fallback command both consume the same flow | covered | scenario | preserve as input-dedup guardrail |
| `AUTH-SETUP-203` | shared | long-running active flow | timeout leaves non-terminal or unrecoverable state | covered | scenario | preserve as timeout recovery baseline |
| `AUTH-SETUP-204` | shared | immediately after successful setup | success emitted before runtime is truly usable | covered | scenario + historical manual bug | preserve as post-success readiness guardrail |
| `AUTH-SETUP-205` | shared | concurrent replies in one channel | one backend flow steals another backend's reply | covered | scenario | preserve as concurrency routing guardrail |
| `AUTH-SETUP-206` | shared | success / failure teardown | stale sessions, pending requests, or old runtime survive | covered | scenario + unit fragments | preserve as clean-restart teardown baseline |
| `AUTH-SETUP-901` | Codex | post-OAuth usability | login completes but runtime is not refreshed | covered | scenario + historical bug fix | preserve as observed regression guardrail |
| `AUTH-SETUP-902` | Claude | auth failure detection | assistant auth failure is treated as plain text | covered | runtime regression test + historical bug fix | preserve as observed recovery guardrail |

## Priority Queue

The next scenario wave should focus on the gaps that most directly affect user trust:

1. `AUTH-SETUP-204`
   now covered; keep extending it beyond OpenCode when more backends need post-success readiness checks
2. `AUTH-SETUP-103`
   now covered; keep it as the shared ownership baseline
3. `AUTH-SETUP-205`
   now covered; keep it as the multi-flow routing baseline
4. `AUTH-SETUP-106`
   callback buttons should not allow stale button presses to restart the wrong backend flow after channel state changed
5. `AUTH-SETUP-208`
   cross-user concurrent setup attempts should not cross-contaminate runtime cleanup

## Coverage Notes

- `Codex`
  happy path, re-entry, failed verification, and post-success runtime usability are covered
- `Claude`
  happy path, malformed callback, wrong-user ownership, timeout recovery, and assistant auth-failure recovery are covered
- `OpenCode`
  happy path, plain-chat protection, invalid-retry recovery, and post-success readiness are covered
- `shared lifecycle`
  cross-user ownership, timeout, concurrency, input deduplication, post-success usability, and clean-restart teardown now have scenario baselines
