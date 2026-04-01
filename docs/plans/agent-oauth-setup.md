# Agent OAuth Setup

## Background

Vibe Remote currently assumes Claude Code and Codex are already authenticated on the host machine. When the login state expires, recovery requires opening a shell on the machine and re-running the backend CLI login flow manually.

The requested product behavior is:

- `/setup` starts OAuth setup for the backend currently bound to the channel/user scope
- auth-related backend failures expose a `reset oauth` action button
- the browser link, device code, and any follow-up code submission happen through IM messages instead of SSHing into the machine

## Scope

First implementation focuses on:

- slash command entry: `/setup`, `/setup <backend>`, `/setup code <value>`
- callback entry: `reset oauth` button on recoverable Claude/Codex auth failures
- Codex setup via `codex login --device-auth`
- Claude setup via `claude auth login --claudeai`
- in-memory auth flow tracking for the running bot process

Out of scope for this pass:

- persistent auth flow resume after bot restart
- automatic DM handoff for secret-bearing setup messages
- OpenCode auth setup

## Design

Add a controller-owned `AgentAuthService` responsible for:

1. Resolving the effective backend for the current context
2. Starting a backend-specific auth subprocess
3. Parsing stdout/PTY output into user-facing IM prompts
4. Accepting follow-up code submission for active Claude flows
5. Verifying login status on completion
6. Sending success/failure notifications and cleanup

### Command semantics

- `/setup`
  - resolves the backend from current routing
  - force-resets existing cached OAuth session before starting a fresh flow
- `/setup claude|codex`
  - explicit backend override
- `/setup code <value>`
  - submits follow-up code into the currently active flow for this session/backend

### Error recovery button

Recoverable auth failures emit a button with callback data:

- `auth_setup:auto`
- `auth_setup:claude`
- `auth_setup:codex`

The callback re-runs the same setup flow as `/setup`.

### Backend behavior

#### Codex

- run `codex logout` best-effort
- run `codex login --device-auth`
- parse:
  - verification URL
  - one-time device code
- wait for process exit
- verify with `codex login status`

#### Claude

- run `claude auth logout` best-effort
- run `claude auth login --claudeai` inside a PTY
- parse:
  - OAuth URL
  - any prompt requesting a code paste
- when code is requested, prompt the user to send `/setup code <value>`
- wait for process exit
- verify with `claude auth status`

## Safety

- treat `/setup` and `auth_setup:*` as admin-protected actions
- never echo submitted codes back to chat
- never print credential file contents
- run auth flows in the same OS user context as the bot process

## Todo

- add `setup` command and callback routing
- implement `AgentAuthService`
- add auth error classification helpers for Claude/Codex
- emit recoverable auth errors with `reset oauth` buttons
- add focused tests for command parsing, callback routing, flow messaging, and auth error classification
