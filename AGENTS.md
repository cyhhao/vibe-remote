# Agent Guidelines for Vibe Remote

This document is the operating manual for coding agents working in this repository.

## 1. Project Overview

Vibe Remote is a middleware layer that connects AI agent backends to IM platforms such as Slack, Discord, and Feishu/Lark.

Current product shape:

- V2 config-driven service with a Web UI setup wizard and settings pages
- multi-platform message transport with shared core orchestration
- multi-backend agent routing across OpenCode, Claude Code, and Codex
- Docker-based unified regression container for real cross-platform verification

Default mindset:

- treat the system as **multi-platform, multi-backend** first
- prefer root-cause fixes over narrow patches
- preserve user-visible behavior unless the task explicitly changes product behavior
- make the next agent/platform inherit correct behavior automatically

## 2. Design Philosophy and Architecture

### Core Rule: Fix at the Highest Appropriate Layer

- If a bug appears on one platform, check whether the same logic exists for the others before patching a platform adapter.
- If a behavior should be shared by multiple backends, prefer the shared core or backend abstraction over a single backend implementation.
- Keep transport/platform details out of core business logic whenever possible.

Decision checklist before writing code:

1. **Scope**: is this platform-specific/backend-specific, or common?
2. **Abstraction**: can the shared base or core layer own this behavior?
3. **Call path**: is the code called from controller/handlers/common flow?
4. **Future-proofing**: would a new platform/backend inherit the correct behavior automatically?

### Codebase Map

- `main.py` - entry point wiring `config.V2Config` into `core/controller.py`
- `core/controller.py` - orchestration and dependency wiring
- `core/handlers/` - platform/backend-agnostic business workflows
- `core/message_dispatcher.py` - outbound message routing and reply enhancement flow
- `core/reply_enhancer.py` - file-link and quick-reply prompt injection helpers
- `modules/im/` - IM platform adapters (`slack.py`, `discord.py`, `feishu.py`) plus shared base classes
- `modules/agents/` - agent backend adapters (`opencode/`, `codex/`, Claude-related modules) plus shared abstractions
- `modules/im/formatters/` - platform-specific formatting built on shared formatter concepts
- `config/` - V2 config, settings, sessions, paths, and compatibility conversion
- `ui/` - React + Vite + TypeScript Web UI
- `scripts/` - operational helpers, including regression testing workflows
- `tests/` - pytest-style unit/integration/regression coverage

### Runtime Data and Important Paths

- logs: `~/.vibe_remote/logs/vibe_remote.log`
- persisted state: `~/.vibe_remote/state/`
- default agent working directory: `_tmp/`
- generated regression data: `_tmp/three-regression/`

## 3. Runtime Environments

### Local `vibe` Service

Common commands:

- install: `uv tool install vibe`
- run: `vibe`
- inspect: `vibe status`
- stop: `vibe stop`

Use local `vibe` for:

- local packaging checks
- local CLI behavior checks
- editable-install UI preview when explicitly needed

Hard rule:

- **Never restart the local `vibe` service for routine verification.**
- The local `vibe` process may be the coding agent runtime itself; restarting it can interrupt the session.
- Unless the user explicitly asks otherwise, use the Docker regression environment for user-facing verification.

### Regression Testing (Docker)

When the user says `回归测试`, treat it as:

- update the latest code into the existing Docker-based regression environment
- let the user verify behavior on Slack, Discord, Feishu/Lark, and WeChat
- preserve previously accumulated regression config/state unless the user explicitly asks for a reset

The regression environment runs a single unified container with all four IM platforms enabled simultaneously.

Standard path:

- default command: `./scripts/run_three_regression.sh`

Rules:

- do **not** use `--reset-config` or `--reset-all` unless the user explicitly requests reset behavior
- do **not** use `--no-build` when code changes must take effect; it is only for restarting with the existing image
- after running the script, verify the service is healthy before handing back to the user
- prefer Docker regression over local `vibe` whenever validating cross-platform behavior, setup wizard behavior, or user-facing IM flows

## 4. Configuration and Routing Model

Persistent configuration is centered on `config/v2_config.py` and the Web UI.

High-level V2 config areas:

- platform config: Slack / Discord / Feishu credentials and switches
- runtime config: default cwd, log level, and related runtime behavior
- agent config: default backend plus per-backend enablement and CLI paths
- UI config: setup host/port and Web UI behavior

Agent routing model:

- global default: `agents.default_backend`
- backend availability and CLI path: `agents.<backend>.enabled` and `agents.<backend>.cli_path`
- per-channel overrides: configured via the Web UI Agent Settings / channel settings

Source-of-truth rule:

- when changing persistent product behavior, align with V2 config and current Web UI flows rather than legacy assumptions

## 5. Development Workflow

### Branching and Scope

- when starting a new feature or bug fix yourself, branch from the latest `master`
- if the user already put you on an existing branch/worktree, continue there unless asked to move
- keep commits small and focused; avoid mixing unrelated changes

### Planning for Non-Trivial Work

- if the task is complex or ambiguous, create a short plan before large changes
- capture background, goal, solution, and todo items in `docs/plans/`
- implementations should follow the plan and update it when scope changes materially
- if requirements are unclear, ask early before committing to a large direction

### Documentation Expectations

- update user documentation alongside user-visible features or changed workflows
- store project-specific plans, investigations, and summaries under `docs/`
- do not put ad-hoc project documentation in the repo root

### Worktrees

- use git worktree for long-running, parallel, or workspace-blocking efforts
- if detailed worktree workflow is needed, load the dedicated worktree skill

### Review Loop for PRs

- before opening a PR, run the reviewer subagent and fix significant issues first
- after opening a PR, use the `background-watch-hook` skill to keep a review-fix loop running until Codex review passes
- by default, create the review watch immediately after the PR is opened; do not wait for the user to remind you unless they explicitly say not to keep a watch

### Pre-Push Requirements

- run the smallest relevant validation first, then broader checks as needed
- before `git push`, run `ruff check` on changed Python files at minimum
- fix lint errors before pushing; CI runs `pre-commit run --all-files` with Ruff

## 6. Coding Standards

### Language and i18n

- default to English for comments, docs, logs, and user-facing copy
- use non-English text only when required for localization/i18n
- backend user-facing strings must go through `vibe/i18n/`
- frontend user-facing strings must go through `ui/src/i18n/en.json` and `ui/src/i18n/zh.json`
- never hardcode user-visible display text in handlers, platform adapters, or React components

### Python and Module Conventions

- follow PEP 8 and 4-space indentation
- use `snake_case` for functions and `PascalCase` for classes/dataclasses
- add type hints for public functions where practical
- keep modules cohesive
- add new business logic under `core/handlers/` when it is platform-agnostic
- add new IM integrations under `modules/im/` and new agent backends under `modules/agents/`
- no repo-wide formatter is enforced; keep diffs focused if you use Black/Ruff

### Frontend (UI)

- source lives in `ui/`
- build command: `npm run build` from `ui/`
- built assets land in `ui/dist/` and are served by `vibe/ui_server.py`

Important packaging caveat:

- the installed `vibe` command uses packaged UI assets, not raw `ui/dist/` from the repo by default
- for local preview of UI changes, use editable install (`uv tool install --force --editable .`) or reinstall the package after building
- do not restart local `vibe` just to verify UI changes unless the user explicitly requests a local-service workflow and the session impact is understood

## 7. Testing and Validation

- prefer the smallest relevant checks first: focused pytest, targeted scripts, or narrow manual validation
- add tests when an existing test pattern already exists
- do not introduce a brand-new test framework unless requested

Testing guidance:

- use pytest-style tests (`test_<feature>.py`) colocated or under `tests/`
- for IM integrations, stub/mock platform clients and validate outbound payload/schema behavior
- for multi-step auth/setup flows, add or update a closed-loop scenario harness case under `tests/test_agent_auth_setup_scenarios.py`; keep provider-specific parsing and heuristics in focused unit tests
- for UI changes, run `npm run build` in `ui/`
- for cross-platform or user-facing verification, use the Docker regression workflow
- until CI fully covers a flow, do a manual sanity check for the affected workflow when practical

## 8. Git, Security, and Operational Safety

### Git Hygiene

- commit messages must use `type(scope): summary`
- never commit secrets such as tokens or credentials files
- avoid destructive git operations unless the user explicitly requests them

### Operational Safety

- keep `AGENT_DEFAULT_CWD` scoped to `_tmp/` or another sanitized directory
- logs may contain sensitive context; scrub before sharing them back
- be careful with persisted state under `~/.vibe_remote/` and `_tmp/three-regression/`
- do not reset or wipe regression data unless the user explicitly asks for it

## 9. Release Notes

- tags follow the latest version number +1 (for example `v1.0.1` -> `v1.0.2`)
- GitHub-only pre-releases should use the `gh-vX.Y.ZrcN` format (for example `gh-v2.2.8rc2`) so they stay distinct from PyPI-triggering `v*` tags
- GitHub-only pre-releases must include installable artifacts (at minimum a wheel built with `ui/dist`) in the GitHub release assets
- releases are published automatically by workflow after tagging/push
