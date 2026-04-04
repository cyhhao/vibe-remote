# Scenario Harness

This package holds reusable test-side primitives for capability-level scenario tests.

Rules:

- keep these helpers lightweight
- prefer service-boundary simulation over full E2E machinery
- only promote a fake or helper here after at least one real scenario proves the reuse
- keep provider-specific parsing logic out of this layer

Current contents:

- `core.py`
  Generic scenario-harness primitives: event probe, fake IM client, base controller, fake process, base harness
- `auth_setup.py`
  Auth/setup-specific harness built on top of the generic layer
