# CI lint & pre-commit (ruff)

## Background
- CONTRIBUTING asks for lint before PR, but repo has no linter config or CI enforcement.
- We saw ruff available (`ruff 0.14.4`) and minimal safety rule set (`E9,F63,F7,F82`) already passes; broader E/F has many legacy hits, so we’ll start small to avoid churn.

## Goal
- Add lightweight CI + pre-commit using ruff to catch syntax/undefined-name issues, with shared config and line length at 120. Ignore E501 for now to avoid reflowing legacy lines.

## Plan
- Add `[tool.ruff]` config (line length 120, src `.`) and lint select `E9,F63,F7,F82`, extend-ignore `E501`.
- Add `.pre-commit-config.yaml` with ruff check hook using shared config.
- Add GitHub Actions workflow `lint.yml` running `pre-commit run --all-files` on PRs/push to master.
- Update CONTRIBUTING.md with quick-start (install pre-commit, run hooks) and note the limited rule scope.
- Run hooks locally to confirm green.

## Todo
- [x] Update pyproject with ruff config
- [x] Add pre-commit config
- [x] Add lint GitHub Action
- [x] Update CONTRIBUTING
- [ ] Run pre-commit --all-files
