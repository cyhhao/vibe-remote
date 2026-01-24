# Resume Session UX (Slack)

## Background
- Backends (Claude, Codex, OpenCode) support `resume` using stored session IDs.
- Today Vibe Remote auto-resumes only when the user continues in the same Slack thread; there is no manual way to restore a past session, start in a new thread, or pick a session created outside Slack.
- Slack free retention (~90 days) can hide old threads, blocking auto-resume.

## Goal
Add a user-facing resume entry point in Slack (/start menu) to let users pick or input a stored session and bind it to the current thread, so long-lived or cross-thread work can continue.

## Constraints
- Follow existing Slack Block Kit style and handler patterns.
- Keep changes minimal and incremental; no new external deps.
- Persist mappings via existing SessionsStore/SettingsManager.
- Maintain backward-compatible behavior (auto-resume in-thread still works).

## Proposed UX
1) Add a "Resume Session" button to the /start quick actions.
2) Button opens a modal:
   - Dropdown to choose agent backend (claude | codex | opencode) with only those that have stored sessions.
   - Dropdown of known session IDs (label includes thread hint and timestamp if available).
   - Optional free-text input to paste a session ID manually (takes precedence when non-empty).
3) On submit:
   - Validate backend + session_id.
   - Store mapping for current thread to that session_id.
   - Reply in thread confirming resume and reminding how to start talking.

## Implementation Todos
- [x] Extend /start buttons to include "Resume Session".
- [x] Add modal schema + open handler for `cmd_resume` callback.
- [x] Implement modal submission handler: resolve session_id (manual input wins), persist via SettingsManager, mark thread active, send confirmation.
- [x] Add helper(s) in SettingsManager to list stored session mappings per agent with metadata for display.
- [x] Add tests / manual check notes (if no test harness, include sanity steps).

## Open Questions
- Do we surface stored sessions across all channels or per-user? (Plan: per-user across channels, consistent with SessionStore namespace.)
- Should we show working directory in the list? (Prefer yes if easily derivable; else skip.)
