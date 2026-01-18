# Client/Server Split and Repo Layout

This document clarifies what lives in the local gateway (client) vs cloud SaaS (server), and proposes a short-term repo layout.

## Boundary Summary

- Client (local gateway): anything that needs local resources or local execution.
- Server (cloud): Slack OAuth, Events API ingress, tenant routing, and relay.

## Client (Local Gateway) Scope

Keep in client:
- `core/` (controller, handlers, routing)
- `modules/agents/` (OpenCode, ClaudeCode, Codex)
- `modules/session_manager.py`
- `modules/settings_manager.py`
- `modules/im/formatters/`
- `modules/im/base.py` (interface)
- `modules/im/slack.py` (Socket Mode only, self-host path)
- `config/` (client runtime config loader)
- `main.py` (client entrypoint, CLI hooks)

Client responsibilities:
- Connect to relay (SaaS mode) or Slack Socket Mode (self-host mode).
- Execute agent logic locally and return results.
- Maintain local session/settings state under `~/.vibe_remote/`.

## Server (Cloud SaaS) Scope

Move to server:
- OAuth install flow
- Slack Events API receiver
- Workspace binding and connection state
- Relay routing (WebSocket/gRPC)
- Slack Web API for message responses

Server responsibilities:
- No local execution, no filesystem access.
- No message persistence (V2 requirement).

## Minimal File Migration (Conceptual)

From current code, split logically as follows:

- Extract Slack Events API handler from `modules/im/slack.py` into `cloud/ingress/slack_events.py`.
- Create `cloud/relay/` service to manage gateway connections and routing.
- Create `cloud/api/oauth.py` for Slack OAuth callbacks and workspace token issuance.
- Keep Socket Mode handling inside client `modules/im/slack.py` for self-host mode only.

## Repo Layout (Short-term)

Keep one repo for fast iteration. Add a `cloud/` module:

```
/ (repo root)
  core/
  modules/
  config/
  cloud/
    ingress/
    relay/
    api/
    storage/
  scripts/
  docs/
```

## Repo Layout (Long-term)

Split into two repos after V2 stabilizes:

- `vibe-remote-gateway` (client)
- `vibe-remote-cloud` (server)

## Notes

- The client remains the open-source Apache component.
- The server (cloud) can be proprietary or open-core as needed.
