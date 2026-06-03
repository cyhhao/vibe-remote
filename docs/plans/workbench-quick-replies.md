# Workbench quick-reply buttons

## Background

IM channels (Slack/Discord/…) already support agent quick-reply buttons: the
agent appends a `---\n[label] | [label]` block, `core/reply_enhancer.process_reply`
parses it into structured `EnhancedReply.buttons`, and each IM adapter renders
native buttons. The avibe **workbench Chat** does not support this yet — the avibe
result persist path (`core/message_dispatcher`) calls `process_reply(...).text`,
which **strips** the button block, and the parsed `enhanced.buttons` are only fed
to the IM delivery path, so for avibe they are discarded.

## Goal

Render the agent's quick-reply buttons at the end of a workbench chat message.
Interaction (per product):

1. Buttons appear at the end of the agent message.
2. Clicking one **sends that label as a user message** (normal turn flow).
3. The clicked button highlights (mint + ✓), the others grey out, and the whole
   group locks (no further clicks). **This locked/highlighted state persists
   across reload.**

## Solution (append-only, reuse the server parser)

**Backend** — surface the already-parsed buttons; no new parser, no schema change:

- `core/message_mirror.persist_agent_message(..., quick_replies=None)` — new
  optional arg; when present, store it in the row's `content.quick_replies`
  (same JSON `content` field that already carries `attachments`).
- `core/message_dispatcher` — in the avibe `result` persist branch, capture the
  `EnhancedReply.buttons` it already computes and pass
  `quick_replies=[b.text for b in enhanced.buttons]`.

**Choice persistence — recorded on the AGENT message (single source of truth).**

> Design note: an earlier iteration derived the answered state from the *user
> reply* (its metadata / text). That entity is separate, async, queue-merged,
> removable and race-prone, so reconstructing "which group is answered, with
> what" by correlating it back to the agent message was inherently fragile — a
> string of review findings (queue-merge metadata loss, text concatenation
> breaking the highlight, queued rows missing from the map, last-wins merges,
> draft clobbering, no idempotency) were all the *same* root cause: the answer
> didn't live where it belongs. The answer is a property of the agent message
> (the question), so we record it there, once.

- The agent message's `content` carries both `quick_replies` (the options) and,
  once answered, `quick_reply_chosen` (the picked label).
- Clicking sends the label as a normal user turn via `POST /messages` with
  `metadata = { quick_reply_for: <agentMessageId> }`. The send endpoint:
  - rejects early (`{already_answered}`, no turn) if that message already has a
    `quick_reply_chosen` (idempotent against a stale second tab / missed event);
  - skips `clear_draft` for a quick-reply send (a side action shouldn't wipe the
    user's unsent composer draft);
  - records the choice via `messages_service.set_quick_reply_chosen` **only after
    the turn is accepted** (started OR queued) — so a failed click stays
    retriable; `set_quick_reply_chosen` is set-once, so a rare double-dispatch
    still records one consistent answer.

**Frontend**

- `QuickReplies` (design-system `Button`s): active → clickable; answered → chosen
  highlighted (mint + ✓), the rest greyed + disabled. Optimistic local lock on
  click for instant feedback; unlocks if the send resolves `false`; hands control
  to the authoritative `chosen` once it loads.
- `MessageRow` reads its OWN message's `content.quick_replies` + `quick_reply_chosen`
  — no cross-message map, no `queue` scan, no user-reply metadata. The click
  handler sends the label tagged with `quick_reply_for`.

## Deferred (recorded, intentionally NOT built)

- **"Only the latest agent message's buttons are clickable" (auto-lock older
  groups when the conversation moves on).** Deferred per product: a user may
  legitimately want to click a quick-reply from an earlier message. So every
  unanswered group stays clickable regardless of age; a group locks ONLY once one
  of its own buttons has been chosen. Revisit if stale-context clicks become a
  problem.

- **Removing a queued quick-reply leaves the group locked (no auto-unlock).**
  If a quick-reply is clicked while a turn is running it queues, the choice is
  recorded, then the user can delete the queued send from the queue strip. The
  recorded choice is intentionally NOT cleared, so the group stays locked on a
  choice the agent never received. Accepted per product (owner call): the user
  deleted it deliberately, quick replies are a convenience layer, and they can
  always type manually — not worth a clear-on-queue-remove hook for this
  click-while-busy-then-undo corner.

- **Cross-tab one-shot atomicity.** Idempotency is an early `get_quick_reply_chosen`
  check + a set-once record after dispatch. Two tabs clicking the same button
  inside the dispatch window could each start a turn; the set-once keeps the
  recorded answer consistent. Accepted per product: negligible on a single-user
  product and the worst case is a duplicate (identical) turn — not worth a
  claim-before-dispatch + rollback path.

## Evidence layers

- Unit: `process_reply` button parsing already covered; add coverage that the
  avibe persist path carries `content.quick_replies`.
- Build: `npm run build`.
- Manual (regression): agent reply with a `---\n[a]|[b]` block shows buttons;
  click sends the label + locks/highlights; reload keeps it locked; an older
  unanswered group is still clickable.
