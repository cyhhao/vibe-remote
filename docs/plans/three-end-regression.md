# Three-End Regression Plan

> Status: Completed

## Background

The repository already has automated Docker-based E2E coverage for API and platform-driver flows, but full product validation still requires a human to trigger real workflows from Slack, Discord, and Feishu. We need a repeatable way to bring up three isolated test environments in parallel without replacing the existing automated E2E suite.

## Goal

Add a named workflow called `三端回归测试` that:

- builds the latest branch code into Docker images,
- starts three independent containers for Slack, Discord, and Feishu,
- exposes three separate local UI ports,
- preconfigures each container with its platform, channel, and backend agent routing,
- keeps local secrets out of git,
- and preserves the current automated E2E entrypoints.

## Solution

1. Add a dedicated three-service Docker Compose file for Slack / Discord / Feishu regression environments.
2. Add a local-only env template for secrets and per-service mappings (ports, tokens, channels, backend routing).
3. Add a preparation script that materializes per-service `config.json` and `settings.json` into generated `_tmp/three-regression/` state directories before the containers start.
4. Add a one-command runner script that loads the local env file, rebuilds the services, recreates the containers, waits for health, and prints the three local URLs plus platform/channel/backend mapping.
5. Document the difference between `E2E 测试` and `三端回归测试` in a dedicated docs folder.

## Todo

- [x] Add local-secret-safe env template and ignore rules.
- [x] Add generated-config bootstrap for the three regression services.
- [x] Add three-service Docker Compose orchestration with separate ports and volumes.
- [x] Add a runner script for build/up/down/status/log workflows.
- [x] Add docs that explain setup, usage, and the difference from automated E2E.
- [x] Validate the new workflow with targeted checks.
