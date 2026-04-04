# Scenario Testing Standard Repo Plan

## Background

`standards/scenario-testing/` is now acting as an incubating standard package inside Vibe Remote.

This is the right short-term shape for proving the model, but it is not the right long-term shape if we want:

- cross-project reuse
- a dedicated subagent focused on testing-system design
- stable templates and conventions that are not tied to Vibe Remote release cadence
- a shared standard that other repositories can adopt without inheriting Vibe Remote-specific assumptions

So the next step should be a standalone repository.

## Goal

Create a dedicated repository for capability-first, scenario-driven testing standards.

This repository should become:

1. the source of truth for the standard
2. the home of reusable templates and reference harness patterns
3. the future prompt/source package for a dedicated testing-owner agent
4. a project-agnostic system that Vibe Remote consumes as an adopter, not as the owner

## Non-Goals

- building a heavy generic testing framework before repeated reuse exists
- tightly coupling the repo to Vibe Remote terminology or directory layout
- trying to automate all real-world smoke flows
- replacing project-level engineering judgment with a rigid process machine

## Recommended Repository Identity

Working name candidates:

- `scenario-testing-standard`
- `capability-testing-standard`
- `closed-loop-testing-standard`

Recommended choice:

- `scenario-testing-standard`

Reason:

- short
- clear
- directly names the differentiator
- easy to reference from agents, docs, and prompts

## Repository Charter

This repo should define:

- the conceptual model
- the workflow standard
- the reusable authoring templates
- the harness design guidance
- project adoption playbooks
- curated examples from real projects

This repo should not initially own:

- a runtime-heavy test runner
- vendor-specific SDK shims
- project-specific test doubles
- large implementation libraries with hard dependencies

## Top-Level Structure

Recommended layout:

```text
scenario-testing-standard/
├── AGENTS.md
├── README.md
├── STANDARD.md
├── WORKFLOW.md
├── ADOPTION.md
├── ROADMAP.md
├── templates/
│   ├── capability-spec.md
│   ├── scenario-catalog.md
│   ├── harness-design.md
│   └── pr-checklist.md
├── examples/
│   ├── vibe-remote-auth-setup.md
│   ├── cli-oauth-flow.md
│   └── im-driven-user-flow.md
└── reference/
    ├── scenario-taxonomy.md
    ├── test-layer-model.md
    └── fake-vs-contract-boundaries.md
```

## Why This Structure

- `AGENTS.md` remains the primary machine-consumable entrypoint
- `README.md` stays human-first
- `STANDARD.md` and `WORKFLOW.md` keep model and process separate
- `templates/` are directly reusable in any project
- `examples/` prove adoption without hard-coding one implementation
- root `AGENTS.md` remains the future prompt entrypoint for a dedicated testing-owner agent

## Migration Strategy From Vibe Remote

Use a staged extraction instead of a big-bang move.

### Phase 1: Incubate in-repo

Already started:

- standard package exists in `standards/scenario-testing/`
- main `AGENTS.md` points at it
- Vibe Remote auth/setup acts as the first reference example

Exit criteria:

- the vocabulary feels stable
- templates are minimally useful
- at least one real project capability has used the standard successfully

### Phase 2: Extract to standalone repo

Copy the package into a new repository with only minimal restructuring:

- move `standards/scenario-testing/AGENTS.md` to repo root `AGENTS.md`
- lift the package docs to repo root
- keep examples under `examples/`
- add `ADOPTION.md` and `ROADMAP.md`

Vibe Remote then becomes an adopter and references the external standard.

### Phase 3: Add reference harness primitives

Only after at least two repositories reuse the approach should we consider shipping optional reference code like:

- scenario runner primitives
- transcript recorder helpers
- fake boundary ports
- expectation DSL examples

This should remain optional guidance first, library second.

## Minimal Deliverables For The Standalone Repo

Version 0.1 should include:

1. `AGENTS.md`
2. `README.md`
3. `STANDARD.md`
4. `WORKFLOW.md`
5. `ADOPTION.md`
6. templates
7. at least two examples

That is enough to be useful without overbuilding.

## Relationship To The Future Testing-Owner Agent

The agent should not be the standard.
The agent should consume the standard through the repo root `AGENTS.md`.

That separation matters because it keeps the standard usable by:

- humans
- coding agents
- reviewers
- PR automation

without introducing an extra packaging layer too early.

## Relationship To Vibe Remote

After extraction, Vibe Remote should keep:

- project-specific scenario catalogs
- project-specific harness implementations
- project-specific examples

It should not remain the place where the global standard evolves first.

Recommended Vibe Remote follow-up after extraction:

1. replace long-form standard text with a short pointer to the standalone repo
2. keep only Vibe Remote-specific adoption notes locally
3. continue evolving `tests/scenario_harness/` as project implementation, not as the global standard

## Governance Model

Use a lightweight governance model.

Suggested rules:

- changes to the standard require an explicit rationale
- examples should be derived from real project experience
- templates should stay generic
- new concepts must justify why existing vocabulary is insufficient

This helps prevent the repo from turning into abstract testing theory.

## Immediate Next Steps

### In Vibe Remote

1. keep refining the standard package locally while auth/setup becomes the first mature example
2. extract a lightweight reusable `tests/scenario_harness/` layer
3. confirm the standard is improving delivery quality, not just creating more docs

### For the standalone repo

1. create the repository
2. copy the standard package into root form
3. add `ADOPTION.md` and `ROADMAP.md`
4. make root `AGENTS.md` strong enough to act as the testing-owner agent prompt
5. point Vibe Remote back to the standalone repo as an adopter

## Decision Summary

The right long-term shape is:

- a standalone `scenario-testing-standard` repository
- docs-first, template-first
- subagent-ready but not subagent-only
- examples from Vibe Remote, but not owned by Vibe Remote
- optional reference harness primitives only after repeated reuse proves the need
