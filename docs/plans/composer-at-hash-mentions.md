# Composer @-agent / #-session mentions

## Background
The Web chat composer is a plain `<textarea>`. We want IM-style autocomplete:
`@` mentions an enabled **Agent**, `#` references any **Session** by title. On
send, selections become stable text markers plus a structured sidecar; the
current Agent reads the markers and acts via existing Harness commands.

## Locked design (from product dialogue)
- **Semantics = Model B.** Markers are *references* handed to the CURRENT agent;
  no core re-routing. The agent acts via `vibe agent run --agent <name>` /
  `--session-id <id>` / `vibe data query`.
- **Markers (angle brackets):** `@<Agent Display Name>` (the `--agent` handle),
  `#<session-id>` (the `--session-id` handle). Regex `/([@#])<([^>\n]+)>/g`.
- **Double-write:** the message `text` keeps the markers; a sidecar
  `content.references: [{kind:'agent',name,agent_id,backend} | {kind:'session',session_id,title}]`
  rides in the existing `content` JSON blob (same home as attachments /
  quick_replies → no schema migration).
- **`#` scope:** ALL sessions machine-wide, EXCLUDE the current session; recent-N
  by default, ≥2 chars → global title search.
- **Surface:** Web composer only; multiple `@`/`#` per message; only
  picker-selected items become markers (literal typed `@`/`#` stay plain text).
- **Inline chips required** → composer input becomes contenteditable.
- **Library:** Lexical + `lexical-beautiful-mentions` (MIT, multi-trigger,
  custom menu/chip, node `data`, hook). Fallback = TipTap (same data contract).

## Solution / architecture
The data contract (markers + `content.references`) is library-agnostic.

**Frontend**
- `ui/src/lib/mentions.ts` — shared contract: `MentionReference` type, marker
  regex, `referenceToMarker`, `linkifyMentions` (marker → markdown link with the
  `avibe-mention:` scheme), `parseMentionHref`.
- `ui/src/components/workbench/MentionEditor.tsx` — Lexical PlainText editor +
  `BeautifulMentionsPlugin` (triggers `@`/`#`, async `onSearch`). Serializes the
  editor to marker text + references via node traversal. cmdk-styled menu.
  Replicates the textarea contract: Enter-to-send (shift/IME/soft-keyboard
  aware), auto-grow, autofocus, placeholder, voice insert, clear, focus.
- `Composer.tsx` — when `onSearchAgents`/`onSearchSessions` props are present,
  render `<MentionEditor>` instead of `<textarea>` (Workbench home stays plain).
- `ui/src/components/ui/markdown.tsx` — `a` map renders a `Badge` chip for the
  `avibe-mention:` scheme; `urlTransform` passes that scheme through react-markdown's
  sanitizer; new optional `references` prop pre-runs `linkifyMentions`.
- `ChatPage.tsx` — supplies `onSearchAgents` (listVibeAgents enabled) +
  `onSearchSessions` (listSessions, exclude current, ≥2 chars → title search);
  threads `references` into `sendMessage` → `content.references`; `MessageRow`
  passes `content.references` to `<Markdown>`.
- `ApiContext.tsx` — `listSessions({ q })` title-search param.
- i18n keys (`en`/`zh`).

**Backend**
- `storage/workbench_sessions_service.list_sessions` — add `query` (title
  LIKE, case-insensitive) and `exclude_id`.
- UI route `GET /api/sessions` — accept `q` + `exclude_id`.
- `core/handlers/message_handler` — per-turn: expand `content.references` into a
  compact context block prepended to the agent message (resolve by id: agent
  enabled? desc; session exists? title + last_active + usable vibe commands).
  Re-resolve live (don't trust the client snapshot for actions).

## Todo
- [ ] FE: mentions.ts contract + helpers
- [ ] FE: MentionEditor (Lexical + beautiful-mentions)
- [ ] FE: Composer integration (gated on mention props)
- [ ] FE: markdown chip rendering + Badge + urlTransform
- [ ] FE: ChatPage wiring + MessageRow references
- [ ] FE: ApiContext title-search param + i18n + chip/editor CSS
- [ ] BE: list_sessions query + exclude_id (+ route)
- [ ] BE: per-turn reference context expansion (message_handler)
- [ ] Tests: pytest (search + reference expansion); npm run build green
- [ ] Codex self-review → non-draft PR → review watch
- [ ] Hand-off: real-device iOS Safari + Chinese IME validation (human)

## Evidence layers
- unit: pytest for list_sessions search/exclude + reference-block builder.
- contract: marker (de)serialization round-trip (vitest if present, else pure-fn check).
- scenario: none new.
- residual manual: iOS Safari + Chinese IME on the live composer (human).
