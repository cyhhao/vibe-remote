# Standard

## 1. Testing Model

The testing model is capability-first.

The development question is not:

- which function changed
- which module changed
- which backend changed

The development question is:

- which user-visible capability changed
- what scenarios define correctness for that capability
- which layers of evidence now need to be updated

## 2. Layers Of Evidence

Every capability should be validated through four distinct layers.

### Unit

Purpose:

- local logic
- parsing
- heuristics
- invariant enforcement

Typical traits:

- fast
- deterministic
- narrow
- implementation-proximate

### Contract

Purpose:

- validate the boundary between the system and a dependency or adapter

Typical examples:

- CLI wrapper request shape
- SDK control-channel payload shape
- HTTP auth endpoint contract
- platform adapter message schema

### Scenario

Purpose:

- prove that a capability closes as a user journey inside the service boundary

Typical scope:

- start flow
- accept follow-up input
- simulate external completion
- verify terminal state
- assert user-visible outputs

Scenario tests should be:

- capability-shaped
- deterministic
- built on reusable harness primitives

### Smoke / Manual

Purpose:

- real external systems
- real accounts
- real transports
- final integration confidence

These are still required when the project depends on OAuth pages, IM platforms, or vendor CLIs that cannot be fully simulated with high confidence.

## 3. Core Objects

### Capability

A user-visible behavior the system owns.

Examples:

- IM-driven backend auth recovery
- upload and attach a local file to an IM conversation
- schedule a delayed follow-up and deliver it into the correct thread

### Scenario

A stable, named flow that demonstrates one meaningful behavior of a capability.

Scenarios should have:

- stable ID
- short name
- actor
- trigger
- external conditions
- expected outputs

### Harness

The reusable test apparatus that drives scenarios.

Harness primitives should be shared, not rewritten per scenario.

Examples:

- fake transport
- fake process runner
- fake OAuth callback source
- fake persistence store
- probes for sent messages, state transitions, refresh hooks

## 4. Scenario Catalog Rules

Each capability must maintain a scenario catalog.

Scenario IDs should be stable and human-usable:

- `AUTH-SETUP-001`
- `AUTH-SETUP-101`
- `ATTACHMENT-002`

Recommended bands:

- `001-099` happy paths
- `100-199` negative or validation paths
- `200-299` recovery or retry paths
- `900+` known historical regressions

Each scenario entry should describe:

- `Given`
- `When`
- `Then`
- automation layer
- current test owner

## 5. Harness Design Rules

### Preferred Design

Use a lightweight scenario harness made of reusable ports and fakes.

Good harnesses:

- drive real public service APIs where possible
- simulate only the external boundary
- capture transcripts and terminal outputs
- are cheap to extend

### Avoid

- giant one-off mocks per test
- hardcoded assertions copied across scenarios
- harnesses that mirror the production implementation too closely
- full E2E infrastructure when a service-boundary scenario would do

## 6. Delivery Standard

Every feature or bug fix that changes a capability must update the relevant testing evidence.

Minimum rule:

1. update the capability/scenario definition if behavior changed
2. add or update unit tests for local logic changes
3. add or update scenario coverage when the capability flow changed
4. document remaining manual validation explicitly

This standard is only complete when PR review and CI both treat scenario coverage as a first-class requirement.
