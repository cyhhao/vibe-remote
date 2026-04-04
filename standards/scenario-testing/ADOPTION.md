# Adoption Workflow

This document describes how a testing-owner agent should adopt this standard in a new project.

The goal is not to start by writing tests.
The goal is to build a reusable capability map, scenario catalog, harness boundary, and reality-feedback loop that the project can keep extending.

## Mission

The testing-owner agent is responsible for turning product behavior into durable testing assets.

Its job is to:

- understand the product before proposing tests
- map user-visible capabilities before mapping files
- turn high-value user journeys into scenario catalogs
- define reusable harness boundaries and fake/contract responsibilities
- absorb real-world feedback back into the test system

## Required Outputs

An adoption pass should normally leave behind:

- a deterministic scenario asset layout rooted at `tests/scenarios/`
- a capability map
- at least one capability spec
- at least one scenario catalog with stable IDs
- a harness boundary inventory
- reusable fake/probe primitives where needed
- a dependency observation log for reality feedback
- explicit manual gaps for what cannot yet be automated

## Phase 1: Understand The Product

Start from the product, not the code.

The testing-owner agent should answer:

- what is the product trying to help the user accomplish
- what design philosophy or quality bar is visible in the product
- which user journeys are core to product trust
- which failures would be most damaging even if local unit tests pass

Recommended evidence sources:

- product docs
- setup guides
- top-level architecture docs
- onboarding flows
- recent bug reports and manual regression habits

Primary output:

- a short product and capability summary that uses user-visible language

## Phase 2: Build The Capability Map

List the product in terms of capabilities, not modules.

Examples of good capability language:

- IM-driven backend auth recovery
- scheduled result delivery
- attachment upload and delivery
- session resumption across retries

Examples of poor starting points:

- auth service
- controller refactor
- slack adapter

Primary output:

- a capability map with rough priority and risk
- a project-level `tests/scenarios/INDEX.yaml`

## Phase 3: Build The Scenario Catalog

For each target capability, enumerate the meaningful user journeys.

The initial catalog should include:

- happy paths
- validation and negative paths
- recovery and retry paths
- concurrency or re-entry paths
- known historical regressions

Scenario definitions should use:

- `Given`
- `When`
- `Then`
- stable scenario ID
- automation layer target

Primary output:

- a scenario catalog with stable IDs, stored in a fixed capability directory such as `tests/scenarios/<capability>/catalog.yaml`

## Phase 4: Define The Boundary Model

Before building tests, define what belongs inside the service boundary and what should be faked or contract-tested.

Typical boundary questions:

- what user-visible flow should the scenario drive for real
- which external systems should be replaced with fakes
- which boundaries need contract tests instead of idealized mocks
- which behaviors are too risky or expensive to automate and must stay smoke/manual

Primary outputs:

- harness boundary inventory
- fake vs contract vs smoke decision list

## Phase 5: Build Reusable Harness Primitives

Do not start with one-off mocks per scenario.

Start by extracting reusable primitives such as:

- event probes
- fake transports or fake clients
- fake process handles
- state stores
- expectation helpers
- capability-specific harness wrappers

Rule:

- generic primitives belong in shared harness core
- capability-specific logic belongs in a thin harness layer
- scenario tests should compose these pieces, not redefine them

Primary outputs:

- reusable harness primitives
- first closed-loop scenario coverage for the capability

## Phase 6: Maintain A Reality Feedback Loop

Do not let the harness stay frozen at idealized behavior.

When real-world verification reveals that a dependency behaves differently from the current fake or assumption:

1. capture the observed behavior
2. identify which capability and scenario IDs are affected
3. decide whether this is:
   - a product rule
   - a dependency contract
   - a code bug
   - a fake/harness gap
4. update the fake, contract fixture, or scenario definition
5. add or update regression coverage

Examples:

- a CLI requires runtime restart after OAuth before it becomes usable
- a vendor SDK emits auth failures through metadata instead of exceptions
- a callback flow needs an additional verification step before user-visible success

Primary output:

- an updated dependency observation log plus changed tests and scenarios
- with the observation stored beside the capability, for example `tests/scenarios/<capability>/observations.yaml`

## Fake Maturity Model

Treat fakes as assets with maturity levels.

### Ideal Fake

Built from the expected product behavior.
Useful for bootstrapping.
Not sufficient long-term on its own.

### Contract Fake

Built from official docs, protocol guarantees, or wire-level evidence.
Useful when the dependency has a stable documented contract.

### Observed Fake

Updated based on verified runtime behavior from manual regression, production incidents, or targeted reproductions.
This is the preferred long-term shape for high-risk dependencies.

Rule:

High-value capabilities should evolve away from purely ideal fakes.

## Ongoing Responsibilities

After initial adoption, the testing-owner agent should keep doing four things:

1. keep the capability map current
2. keep scenario catalogs ahead of feature work and bug fixes
3. push repeated manual regressions into reusable scenario coverage
4. keep dependency behavior models aligned with reality

## Definition Of Done For Adoption

Adoption is not complete when a project merely has one scenario test.

Adoption is complete enough to be useful when:

- the project has a capability map
- at least one high-risk capability has a scenario catalog
- the project has a deterministic `tests/scenarios/` entry structure
- at least one reusable harness layer exists
- reality feedback can be recorded and replayed into tests
- PR authors can point to scenario IDs and evidence layers
