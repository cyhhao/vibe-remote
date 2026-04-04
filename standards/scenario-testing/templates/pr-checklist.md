# Scenario Testing PR Checklist

## Summary

- Capability identified in user-visible terms
- User-visible change described briefly

## Scenario Coverage

- Scenario IDs listed in PR description
- Unit tests added or updated for local logic changes
- Contract tests added or updated for boundary changes
- Scenario coverage added or updated for flow-level changes
- Residual smoke/manual checks explicitly listed
- Historical regression scenario added when fixing a flow-level bug

## Validation

- Commands run are listed
- Validation result is stated clearly

## Review Readiness

- The PR makes it obvious which layer caught or should catch the change
- The PR makes it obvious what is still intentionally manual
