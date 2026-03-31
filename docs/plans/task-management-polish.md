# Task Management Polish

## Background

Recent task/hook work added session and delivery target separation, but task management is still awkward in three areas:

- stored tasks cannot be updated in place
- add/send validation only checks syntax, not obvious delivery reachability risks
- list/show output is still too close to raw storage payloads

## Goal

Improve the existing task CLI without breaking current task execution behavior.

## Solution

1. Add `vibe task update <id>` for partial in-place task edits.
2. Add non-blocking reachability warnings for obviously risky targets, starting with Lark DM users that lack `dm_chat_id` binding.
3. Enrich `task list` / `task show` with scheduling-oriented derived fields and add `task list --brief`.

## Notes

- Preserve existing task IDs during update.
- Keep `task add` / `hook send` successful when a risk is only a warning.
- Preserve existing raw task fields in default JSON output; only add derived fields on top.
- Keep prompt injection unchanged for this follow-up.
