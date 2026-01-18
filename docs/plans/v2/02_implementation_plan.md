# Implementation Plan

## Phase 0: Foundation Alignment

- Define the transport abstraction for inbound events (Slack Socket Mode vs Cloud Relay).
- Define a unified agent run model (request, response, trace, metadata).
- Make sure the self-host path remains fully functional with Socket Mode.

## Phase 1: SaaS MVP (No Data Persistence)

- Official Slack app with OAuth install.
- Cloud ingress for Slack Events API.
- Relay service that forwards events to local gateways over WebSocket/gRPC.
- Local gateway that connects out to the relay and forwards events to the existing agent runtime.
- Minimal control plane: workspace status, gateway online/offline, basic error logs.

## Phase 2: Commercial Value Expansion

- Observability: structured run logs, performance metrics, error diagnostics.
- Higher-level Vibe agent orchestration (task planning + sub-agent execution).
- Tiered limits and billing (per user or per workspace).

## Phase 3: Vibe App

- Web console evolves into a real UI for tasks, runs, and approvals.
- Slack becomes a trigger/notification channel.
- Integrations extend beyond Slack (GitHub/CI, issue trackers).
