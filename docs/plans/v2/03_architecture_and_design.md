# Architecture and Design

## Deployment Modes

### SaaS (Commercial)

Slack Events API -> Cloud Ingress -> Relay -> Local Gateway -> Agent Runtime -> Sub-Agents

- Cloud does not persist message content.
- Gateway performs all local execution and tool access.
- Cloud maintains workspace bindings and connection routing.

### Self-host (Open Source)

Slack Socket Mode -> Local Gateway -> Agent Runtime -> Sub-Agents

- No cloud dependency.
- User provides Slack app tokens and runs the gateway locally.

## Core Components

1. Ingress Adapter
   - Slack Events API (SaaS)
   - Slack Socket Mode (Self-host)

2. Relay Transport
   - Outbound WebSocket or gRPC from gateway to cloud
   - Authentication via workspace-scoped token
   - Heartbeats + retry

3. Agent Runtime
   - Unified request/response envelope
   - Routing to Vibe Agent or sub-agents
   - Tool execution is local

4. Control Plane (SaaS)
   - Workspace management
   - Gateway status
   - Minimal observability

## Key Design Decisions

- Gateway-first execution for privacy and local tooling.
- Cloud as ingress and reliability layer, not a data store.
- Open-source core under Apache license; cloud features deliver commercial value.
