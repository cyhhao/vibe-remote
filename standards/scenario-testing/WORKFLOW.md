# Workflow

## Project Adoption Workflow

When bringing this standard into a new project, do not start with individual tests.

Start with `ADOPTION.md`, then establish:

1. a product and capability summary
2. a capability map
3. at least one scenario catalog with stable IDs
4. a harness boundary inventory
5. a dependency observation log

Only after that baseline exists should feature and bug-fix workflows rely on scenario coverage as a delivery rule.

## Feature Workflow

When adding a feature:

1. Identify the capability being introduced or changed.
2. Update or create the capability spec.
3. Update or create the scenario catalog.
4. Implement the smallest relevant unit and contract tests.
5. Add or update at least one closed-loop scenario.
6. Record any residual manual checks.
7. Update the dependency observation log if the change was driven by real-world behavior not previously modeled.

## Bug Fix Workflow

When fixing a bug:

1. Identify the capability that failed.
2. Map the bug to an existing scenario ID, or create a new regression scenario ID.
3. Add the narrow unit/contract test if the root cause is local.
4. Add or update the scenario test that proves the user journey now closes.
5. Record whether the fix required updating an observed fake, contract fixture, or dependency assumption.
6. Document why the previous test layers did not catch it.

## Review Workflow

Reviewers should ask:

1. What capability changed?
2. Which scenario IDs were affected?
3. Does the PR update the right layer of evidence?
4. If the bug was flow-level, where is the scenario coverage?
5. What remains intentionally manual?
6. Did the change reveal a dependency behavior the harness did not previously model?
7. If so, where was that knowledge recorded?

PR authors should include in the PR body:

- changed capability
- affected scenario IDs
- evidence layers updated
- residual manual checks

## CI Workflow

Projects adopting this standard should evolve toward:

- fast unit and contract checks on every change
- targeted scenario suites for affected capabilities
- smaller smoke/manual matrices for external systems

CI does not need to run all scenario suites at once on day one.
It does need a path toward capability-aware regression gates.

## Reality Feedback Workflow

When manual regression, smoke validation, or production behavior disagrees with the current harness:

1. capture the observation in the dependency observation log
2. identify affected capability and scenario IDs
3. decide whether the change belongs in:
   - a fake
   - a contract fixture
   - the scenario catalog
   - the manual runbook
4. update the relevant automated evidence
5. keep the fake aligned with observed behavior for high-risk paths

## Change Management

Avoid introducing the standard as a single giant migration.

Preferred rollout:

1. choose one high-pain capability
2. create the first scenario catalog
3. build the smallest reusable harness layer
4. prove value through one or two regressions caught earlier
5. generalize the harness only after repeated reuse

## Documentation Rule

Do not hide scenario knowledge only in PR comments or issue threads.

By the time a change is merged, the durable artifacts should live in:

- the capability spec
- the scenario catalog
- the scenario tests
- the PR/testing checklist
