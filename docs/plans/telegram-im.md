# Telegram IM Integration Plan

## Background

Vibe Remote already has the right product direction for Telegram: AI should live in the messaging tools people already use, behave like a colleague, and share one core orchestration layer across multiple transports.

The current codebase is also no longer Slack-shaped:

- `config/v2_config.py` already models multiple enabled platforms and per-platform config blocks.
- `modules/im/base.py` and `modules/im/factory.py` already provide the IM abstraction seam.
- `core/controller.py` already boots multiple IM runtimes and routes by `context.platform`.
- `core/handlers/` already holds most business logic at a platform-agnostic layer.
- `ui/` and `vibe/api.py` already support platform selection and per-platform setup flows.

That means Telegram should be added as a first-class platform, not as a fork of Slack logic.

## Vision Fit

Telegram is a strong fit for the product vision in `VISION.md`:

- it is mobile-first and message-first
- it supports DMs, groups, supergroups, and forum topics
- it supports inline buttons, replies, files, and bot commands
- it is common in communities where users want to trigger coding agents away from a laptop

The product goal should remain: one shared colleague-like core, multiple messaging transports.

## Architecture Findings

### What is already in good shape

1. Multi-platform runtime already exists.
   - `Controller` owns `self.im_clients`, `self.primary_platform`, and a `MultiIMClient`.
   - `IMFactory.create_clients()` already branches by platform.

2. Session routing is already platform-aware.
   - `SessionHandler.get_base_session_id()` prefixes session ids with the platform.
   - DM and non-DM session semantics are already separated.

3. Settings and routing are already platform-scoped.
   - settings keys are resolved via platform-specific managers
   - session keys are globally unique via `platform::scope`

4. The UI already assumes multiple platform cards and platform-specific setup steps.
   - `ui/src/lib/platforms.ts`
   - `ui/src/components/steps/PlatformSelection.tsx`
   - `ui/src/components/steps/ChannelList.tsx`

### What is not solved yet

1. Platform capability differences are still uneven.
   - Slack and Discord have rich interactive flows.
   - WeChat proves the repo can support weaker interaction models, but some flows are still biased toward modal-capable platforms.

2. Channel discovery is not universally abstracted.
   - Slack/Discord/Lark rely on server-side list APIs.
   - Telegram Bot API does not provide a generic "list all chats the bot belongs to" endpoint.

3. Platform-specific onboarding is still credential-flow centric.
   - Telegram will need both token validation and different operator guidance for privacy mode, group usage, and topic behavior.

## Telegram Facts And Constraints

Based on the official Telegram Bot API:

- inbound delivery can use `getUpdates` or `setWebhook`; they are mutually exclusive
- bots receive `callback_query`, can edit messages, and can send files/documents/media
- Telegram supports `message_thread_id` for forum topics
- bots can expose slash commands via `setMyCommands`
- group visibility is constrained by privacy mode unless the bot is explicitly addressed

Implications for Vibe Remote:

1. Phase 1 should use long polling, not webhooks.
   - It matches the current single-process local-first runtime.
   - It avoids introducing inbound public HTTP requirements just to get the first version shipped.

2. Telegram should be treated as a channel-capable platform, but not as a directory-listing platform.
   - We can manage chats after they are discovered.
   - We should not pretend we can browse all groups/channels from the setup wizard the way Slack and Discord do.

3. Telegram is interactive enough for reply enhancements.
   - inline keyboard maps well to quick replies and action buttons
   - callback queries map well to resume/routing/settings actions

4. Telegram does not have Slack/Discord-style modals.
   - for free-form input we should prefer conversational follow-up plus `ForceReply`, not platform-specific modal emulation

## Recommended Scope

### Phase 1 scope

Support:

- private chats
- group chats and supergroups
- forum topics in supergroups
- inline buttons / callback queries
- file upload and file download
- slash commands as fallback entrypoints
- mention-gated group interactions

Defer:

- webhook mode
- Telegram channel broadcast workflows as a first-class use case
- payments, inline queries, business account features, stickers, polls
- full parity for every Slack modal flow

### Product stance

Telegram channels are not the best phase-1 target for Vibe Remote. The core product is conversational collaboration, so the first version should optimize for DMs, groups, and forum topics. Pure broadcast-channel support can wait unless there is a concrete user need.

## Proposed Architecture

### 1. Config model

Add `TelegramConfig` to `config/v2_config.py` and extend supported platforms:

- `platform`: allow `"telegram"`
- `platforms.enabled`: allow `"telegram"`
- `V2Config.telegram: Optional[TelegramConfig]`

Suggested config shape:

```python
@dataclass
class TelegramConfig(BaseIMConfig):
    bot_token: str = ""
    require_mention: bool = True
    use_webhook: bool = False
    webhook_url: Optional[str] = None
    webhook_secret_token: Optional[str] = None
    allowed_chat_ids: Optional[list[str]] = None
    allowed_user_ids: Optional[list[str]] = None

    def validate(self) -> None:
        # Allow empty during setup wizard, validate when enabled in runtime.
        pass
```

Notes:

- `require_mention` should default to `True` for Telegram.
- webhook fields should exist early in the schema, but webhook runtime should remain disabled in phase 1.
- allowlists are optional but worth baking in now because Telegram group surfaces can be noisy.

### 2. New IM adapter

Add:

- `modules/im/telegram.py`
- `modules/im/telegram_api.py`
- `modules/im/formatters/telegram_formatter.py`

Recommended design:

- use direct Bot API calls over `aiohttp`
- do not introduce `python-telegram-bot` in phase 1

Reasoning:

- the repo already has a clean IM abstraction, so a second bot framework is unnecessary
- the Bot API surface Vibe Remote needs is plain HTTP + JSON + multipart upload
- avoiding `python-telegram-bot` sidesteps its single-threaded/non-thread-safe caveats and avoids adding a new framework-level lifecycle inside our own runtime
- staying on `aiohttp` keeps the adapter consistent with existing code patterns

### 3. IM factory and controller wiring

Update:

- `modules/im/factory.py`
- `core/controller.py`
- `modules/im/formatters/__init__.py`

Needed behavior:

- create a `TelegramBot` when Telegram is enabled
- refresh `require_mention` on config hot reload
- create a Telegram formatter in `_create_formatter`
- ensure `get_im_client_for_context()` works unchanged with Telegram contexts

### 4. Telegram message model mapping

Map Telegram updates into `MessageContext` like this:

- `platform`: `"telegram"`
- `user_id`: sender user id
- `channel_id`: chat id
- `thread_id`: `message_thread_id` when present
- `message_id`: Telegram message id
- `platform_specific`:
  - `is_dm`
  - raw update/message handles when needed
  - mention/reply metadata
  - callback query id when relevant

Session rules:

- DM: stable session per `chat_id`
- Plain group/supergroup without forum topics: stable session per `chat_id`; only explicit `New Session` creates a new backend session
- Forum topic: stable session per `message_thread_id`
- Forum supergroup `General` topic: a new top-level user message should auto-create a new forum topic and route the session into that topic

That fits the existing `SessionHandler` model without needing a Telegram-specific session rewrite.

### 5. Shared discovered-chat registry

This is the most important design choice.

Telegram cannot rely on the existing "load all channels from platform API" pattern. The right fix is not to hardcode a Telegram exception deep in the UI. The right fix is to add a shared discovered-chat registry for platforms that learn available conversations from inbound events.

Proposed shared concept:

- new state-backed registry under `~/.vibe_remote/state/`
- stores discovered chat metadata per platform
- written by IM adapters when they receive inbound events or membership updates
- readable by `vibe/api.py` and the Channel UI

Suggested metadata:

- `platform`
- `chat_id`
- `chat_type`
- `title`
- `username`
- `is_dm`
- `is_forum`
- `last_seen_at`
- `last_message_thread_id` if useful

This abstraction is reusable for future platforms that also lack a listing API.

### 6. Interaction model

Support these primitives in `TelegramBot`:

- `send_message`
- `send_message_with_buttons`
- `edit_message`
- `answer_callback`
- `send_dm`
- `download_file`
- `download_file_to_path`
- `upload_image_from_path`
- `upload_file_from_path`
- `send_typing_indicator`
- `clear_typing_indicator`

Recommended Telegram-specific UI behavior:

- use inline keyboard for quick actions
- use callback query handlers for buttons
- use conversational prompts or `ForceReply` for text input flows such as `set_cwd`
- keep commands as fallback, not as the primary menu model
- avoid trying to fake Slack-style modal parity

Recommended session UX behavior:

- DM: one chat defaults to one session until the user explicitly clicks `New Session`
- Plain group/supergroup: one chat defaults to one session until the user explicitly clicks `New Session`
- Forum supergroup `General`: a new top-level user message should automatically create a new topic that becomes the new session container
- Inside an existing topic: keep reusing that topic's session

### 7. Group mention and privacy behavior

Telegram requires a cleaner policy than Slack:

- DMs always work
- in groups, default to mention/reply-triggered behavior
- expose `require_mention` as an explicit Telegram platform setting
- document that reading all group messages depends on Telegram privacy mode configuration

This should be reflected in onboarding copy and not buried in logs.

### 8. Buttons and commands

Telegram should behave like Discord, not like a command-first bot.

Primary interaction model:

- use buttons for menu entry and common actions
- expose `New Session`, `Settings`, `Resume`, and other high-frequency actions as buttons
- keep the conversation UI centered on reply buttons and follow-up actions

Fallback command set:

- `/start`
- `/new`
- `/cwd`
- `/set_cwd`
- `/settings`
- `/stop`
- `/bind`

Implementation note:

- inbound slash commands should still flow through the existing shared command handlers
- optionally call `setMyCommands` during startup or setup save to improve UX
- command help copy should explicitly point users back to button-first usage

## UI And Setup Plan

### 1. Setup wizard

Update:

- `ui/src/lib/platforms.ts`
- `ui/src/components/steps/PlatformSelection.tsx`
- `ui/src/components/Wizard.tsx`
- add `ui/src/components/steps/TelegramConfig.tsx`

Telegram setup step should include:

- bot token input
- token validation via `getMe`
- clear explanation of DM/group/topic support
- privacy mode guidance
- forum topic guidance for supergroups
- note that chats appear after the bot receives messages or is added to groups

### 2. Dashboard and settings

Update:

- dashboard platform cards
- platform labels and descriptions
- channel settings routing to Telegram-specific channel management

Telegram settings should show:

- token presence / validation state
- require-mention toggle
- discovered chats count
- whether forum auto-topic mode is available for a discovered supergroup
- webhook fields hidden or marked future-facing in phase 1

### 3. Channel management

Do not copy Slack/Discord discovery UX.

Recommended Telegram channel page:

- list discovered chats from the shared registry
- allow enabling/disabling each discovered chat
- allow manual add by `chat_id` as an escape hatch
- clearly label DM / group / supergroup / forum-topic-capable chats
- for forum-capable supergroups, show that new top-level messages in `General` auto-create a new topic/session

This is the highest-leverage product adjustment needed for Telegram.

## Backend API Plan

Update `vibe/api.py` and `vibe/ui_server.py` with Telegram-specific endpoints:

- `telegram_auth_test(bot_token)` via `getMe`
- `telegram_get_me(bot_token)` if separate payload is useful
- `telegram_list_discovered_chats()` backed by the shared registry
- optional `telegram_add_chat(chat_id)` for manual entry

Important:

- do not claim we can enumerate all Telegram chats from the Bot API
- if chat enrichment is needed, use `getChat(chat_id)` only after the chat is already known

## Formatter Plan

Add `TelegramFormatter`.

Prefer a conservative formatting policy:

- start with plain text plus fenced code blocks where safe
- add Telegram MarkdownV2 or HTML formatting only with correct escaping
- avoid aggressive markdown conversion until tests cover escaping well

Reasoning:

- Telegram formatting rules are strict enough that partial escaping causes user-visible failures
- a reliable plain-text-first formatter is better than a flaky rich formatter

## Testing Plan

### Unit tests

Add or extend:

- `tests/test_telegram_bot.py`
- `tests/test_multi_platform_runtime.py`
- `tests/test_session_handler_base_id.py`
- `tests/test_v2_compat_platforms.py`
- `tests/test_ui_api.py`

Minimum cases:

- config load/save with Telegram enabled
- IM factory creates Telegram client
- inbound DM, group, and topic message mapping
- callback query routing
- session id behavior for DM vs plain group vs topic
- forum `General` top-level message triggers auto-topic creation flow
- file download/upload plumbing
- discovered chat registry updates on inbound events
- API/auth test behavior

### Manual validation

1. DM the bot and verify button-first entry works; `/start` remains only as fallback.
2. Add the bot to a plain group and verify the chat stays on one session until `New Session`.
3. Use a forum-enabled supergroup and verify a new top-level message in `General` auto-creates a topic for a new session.
4. Inside an existing topic, verify follow-up messages stay on the same session.
5. Trigger buttons for settings/resume/routing.
6. Send an attachment and verify download path handling.
7. Ask the agent to send a file/image and verify outbound upload.

### Regression environment

Do not try to fold Telegram into the existing Docker three-regression flow immediately.

Recommended rollout:

1. ship adapter + unit coverage first
2. add manual Telegram smoke validation
3. then design whether the unified regression container should evolve into a four-platform container

## Implementation Phases

### Phase 0: skeleton

- add config schema and platform enum support
- add Telegram formatter stub
- add Telegram adapter skeleton
- wire factory/controller/UI labels

Exit condition:

- app boots with Telegram enabled
- token can be saved and validated

### Phase 1: core messaging

- implement long-poll update loop
- map inbound messages to `MessageContext`
- support `send_message`, typing, inline buttons, callback queries, and fallback slash commands
- support DM and group basics

Exit condition:

- DM and plain-group conversations work end-to-end with button-first interaction

### Phase 2: session and topic correctness

- handle `message_thread_id`
- implement forum `General` to auto-topic session creation flow
- verify topic session behavior
- refine mention/reply behavior in groups
- support message editing for long-running updates where appropriate

Exit condition:

- forum topic conversations behave predictably

### Phase 3: file and richer actions

- implement file download via `getFile`
- implement outbound document/image upload
- polish reply enhancements and resume/settings flows

Exit condition:

- Telegram reaches practical parity with existing core flows

### Phase 4: UI and operator experience

- finish Telegram setup step
- build discovered chat management UI
- document privacy mode and group behavior
- add setup docs in English and Chinese

Exit condition:

- new users can self-serve setup without reading code

## Risks

1. Channel discovery mismatch
   - Biggest product risk.
   - Mitigation: build a shared discovered-chat registry instead of forcing Telegram into Slack-like channel listing.

2. Formatting fragility
   - Telegram rich text escaping is easy to break.
   - Mitigation: start conservative; add rich formatting only when tests prove it.

3. Group privacy confusion
   - Users may expect the bot to read all group traffic.
   - Mitigation: default `require_mention=true` and explain privacy mode clearly in UI/docs.

4. Auto-topic creation permissions and noise
   - Forum auto-topic mode requires the bot to be able to create topics, and can create noise in active groups.
   - Mitigation: only enable it for forum-capable supergroups, document the behavior clearly, and gate it behind detected capability.

5. Scope creep toward "Telegram everything"
   - The Bot API surface is large.
   - Mitigation: stay focused on conversational Vibe Remote workflows, not the full Telegram platform.

## Open Questions

1. Should phase 1 allow manual `chat_id` entry in the UI, or is discovered-chat-only enough?
2. Do we want `/settings` and `/set_cwd` free-form input flows to use `ForceReply`, or just button-triggered conversational prompts first?
3. Should forum auto-topic creation be configurable per supergroup, or always on when the chat supports it?
4. Should broadcast channels be explicit non-goals in the first PR, or included as limited send-only surfaces?
5. Should we introduce a generic discovered-chat registry now, or a Telegram-only version that we generalize later?

My recommendation is to introduce the shared registry now. It is the highest-layer fix and matches the repository's architecture principles.

## References

- Vision: `VISION.md`
- Existing plans:
  - `docs/plans/multi-platform-im.md`
  - `docs/plans/discord-im.md`
  - `docs/plans/wechat-ilink-integration.md`
- Official Telegram Bot API: https://core.telegram.org/bots/api
- python-telegram-bot docs: https://docs.python-telegram-bot.org/en/stable/
