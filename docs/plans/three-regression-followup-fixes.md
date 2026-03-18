# Three Regression Follow-up Fixes

> Status: In Progress

## Background

The three-end regression workflow is running, but two UX issues remain:

1. The Discord container is configured for Codex, yet some Web UI surfaces still show OpenCode, creating inconsistent routing feedback.
2. The Web UI health/status polling appears overly chatty, and likely uses multiple endpoints more often than needed.

## Goal

Make backend display consistent across the Web UI and reduce polling overhead by simplifying the runtime status checks.

## Solution

1. Trace the backend shown in dashboard/pages to determine whether it is reading global defaults, stale local state, or per-channel routing.
2. Fix the UI/API contract so the effective backend shown for the current platform reflects the actual runtime routing the service will use.
3. Inspect the status polling implementation, collapse redundant endpoint polling where possible, and reduce the polling interval without breaking responsiveness.
4. Validate in the running three-end regression environment and cover the behavior with tests where practical.

## Todo

- [ ] Find the source of the incorrect OpenCode display in the Discord Web UI.
- [ ] Fix effective backend display so it matches the actual Discord runtime backend.
- [ ] Simplify and reduce status polling.
- [ ] Validate via tests/build and live container checks.
