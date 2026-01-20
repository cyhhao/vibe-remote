# Pending Question State Lost on Restart

## Status
Open

## Summary
When vibe-remote restarts while OpenCode has a pending question, the `_pending_questions` in-memory state is lost. This causes subsequent user messages to be sent as new prompts instead of question answers, leaving the OpenCode session stuck.

## Root Cause
`_pending_questions` is an in-memory dict in `OpenCodeAgent` that tracks pending questions per session. When vibe-remote restarts:
1. This state is lost
2. User messages are routed to `_process_message()` instead of `_process_question_answer()`
3. New prompt is sent to OpenCode via `prompt_async()`
4. OpenCode session remains blocked waiting for the question answer

## Reproduction Steps
1. Send a prompt that triggers OpenCode to ask a question
2. While question is pending, restart vibe-remote
3. Send any message in the same Slack thread
4. Observe: message sent as new prompt, session stuck

## Verified Behavior (API Test)
```
# With pending question, send new prompt:
- Question remains pending
- New user message added to session
- Assistant does NOT respond (session blocked)

# After answering question:
- Question cleared
- Assistant completes original flow
- New prompt treated as context, not separate request
```

## Impact
- User's Slack conversation gets stuck
- No response from OpenCode until question is manually answered (which user cannot do after restart)
- Session effectively becomes unusable

## Proposed Fix
In `restore_active_polls()` or `_restored_poll_loop()`:
1. Check OpenCode `/question` API for pending questions in the session
2. If found, rebuild `_pending_questions` state with question details
3. Re-emit question UI to Slack (or notify user to re-trigger)

## Complexity
Medium-High
- Need to fetch question details from OpenCode
- Need to reconstruct pending question payload (session_id, directory, question_id, options, etc.)
- Need Slack context to re-emit question modal

## Related
- PR #24 review comment by Codex
- `modules/agents/opencode_agent.py`: `_pending_questions`, `restore_active_polls()`, `_restored_poll_loop()`

## Workaround
User can start a new thread to continue working with OpenCode.
