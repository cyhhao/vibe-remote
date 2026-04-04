# Scenario Catalogs

This directory is the project-level source of truth for capability scenario metadata.

If you are starting from zero context:

1. read `INDEX.yaml`
2. open the target capability's `catalog.yaml`
3. open the same capability's `observations.yaml`
4. read the listed scenario test files
5. only then change production code or the shared harness

Directory rules:

- `tests/scenarios/INDEX.yaml`
  Project capability index
- `tests/scenarios/<capability>/catalog.yaml`
  Stable scenario IDs and current coverage state
- `tests/scenarios/<capability>/observations.yaml`
  Historical reality-feedback notes and dependency observations
- `tests/scenarios/<capability>/test_*.py`
  The executable scenario evidence

`docs/regression/` is now a human-facing index for manual regression and explanation.
It is no longer the canonical location for scenario metadata.
