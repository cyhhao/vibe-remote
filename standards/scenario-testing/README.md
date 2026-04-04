# Scenario Testing Standard

This package is intended to become a standalone, reusable standard for capability-first testing.

It is being incubated inside this repository first because Vibe Remote already has the right kind of system pressure:

- multi-step user flows
- external CLIs and SDKs
- real IM transport boundaries
- partial automation plus unavoidable manual verification

That makes it a good proving ground, but the standard itself is not Vibe Remote-specific.

## What This Standard Solves

Many teams already have:

- unit tests for local logic
- integration tests for selected adapters
- manual regression for full user journeys

The gap is the middle layer:

- a feature can pass unit tests but still fail as a complete flow
- a bug fix can address one branch but silently break the surrounding journey
- reviewers can validate local diffs but still miss flow closure regressions

This standard fills that gap with scenario-driven, closed-loop capability testing.

## Core Idea

The primary development object is a **capability**, not a file or helper function.

Each capability owns:

- a capability spec
- a scenario catalog
- a reusable scenario harness
- a delivery rule for PRs and CI

## Package Layout

- `AGENTS.md`
  Entry instructions for future agents or subagents working on this standard.
- `STANDARD.md`
  The testing model and terminology.
- `WORKFLOW.md`
  The recommended development and review process.
- `templates/`
  Reusable authoring templates.
- `examples/`
  Project-specific mappings and reference implementations.

## How To Adopt It In A Project

1. Define capabilities in user-visible terms.
2. Create a scenario catalog with stable IDs.
3. Build a lightweight harness layer with reusable fakes and probes.
4. Map the project's existing tests into:
   - unit
   - contract
   - scenario
   - smoke/manual
5. Make scenario coverage part of feature and bug-fix delivery.

## Why It Is Package-Shaped

This directory is structured so it can later be moved into its own repository with minimal changes.

The intended future is:

- a standalone repo for the standard
- a dedicated subagent that helps teams apply it
- one or more project adapters or examples layered on top
