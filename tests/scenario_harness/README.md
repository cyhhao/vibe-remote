# Scenario Harness

This package holds reusable test-side primitives for capability-level scenario tests.

Rules:

- keep these helpers lightweight
- prefer service-boundary simulation over full E2E machinery
- only promote a fake or helper here after at least one real scenario proves the reuse
- keep provider-specific parsing logic out of this layer

Current contents:

- `auth_setup.py`
  Shared harness primitives for IM-driven auth/setup scenarios
