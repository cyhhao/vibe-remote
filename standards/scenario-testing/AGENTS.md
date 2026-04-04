# Scenario Testing Standard

This directory is a portable standard package for capability-first, scenario-driven testing.

Treat this folder as the entrypoint when you are doing any of the following:

- designing or refactoring a project's testing architecture
- introducing scenario-harness or closed-loop flow testing
- defining reusable testing standards that should survive beyond one repository
- translating recurring manual regressions into stable automated coverage

## Mission

Build a testing system where feature work and bug fixes are validated as user-visible capability flows, not only as isolated unit behaviors.

The goal is not to replace unit tests or E2E tests.
The goal is to add a reusable middle layer that answers:

- what capability is being changed
- what scenarios define its success and failure boundaries
- what parts can be simulated deterministically
- what still requires contract, smoke, or manual verification

## Operating Rules

1. Start from capabilities, not files, modules, or bugs.
2. Require stable scenario IDs and reusable scenario catalogs.
3. Prefer shared harness primitives over one-off mocks.
4. Keep the harness light enough for everyday development use.
5. Turn the standard into delivery rules, not just reference docs.

## Folder Map

- `README.md`
  Overview and intended reuse model.
- `STANDARD.md`
  The core methodology and testing pyramid.
- `WORKFLOW.md`
  The day-to-day workflow for feature work, bug fixes, reviews, and CI.
- `templates/`
  Reusable templates for capability specs, scenario catalogs, and PR checklists.
- `examples/`
  Concrete project mappings. These explain how one repo applies the standard without redefining the standard itself.

## Expected Outputs

For a project adopting this standard, the normal outputs are:

- a capability spec
- a scenario catalog with stable IDs
- a reusable scenario harness layer
- project-specific reference scenarios
- PR/CI rules that enforce the standard

## Non-Goals

- inventing a heavy generic workflow engine up front
- forcing all real-world verification into automation
- replacing focused unit tests with giant scenario tests
- coupling the standard to Vibe Remote specifics

## Adoption Rule

When you add or modify a testing standard in this repository, update the project-specific guidance to point back to this folder rather than duplicating the standard elsewhere.
