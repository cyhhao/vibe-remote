# Architecture Cleanup Plan (First-Principles, Non-Regression First)

> **Branch**: `refactor/architecture-cleanup`  
> **Prerequisite**: PR #69 merged  
> **Mode**: Aggressive cleanup in one branch, with strict compatibility gates  
> **Status**: Completed

## 1. Why This Refactor Exists

This repository currently implements one product behavior through several duplicated decision paths.  
The real risk is not file size. The real risk is **decision drift** (same user action, different behavior by platform/code path).

This plan does a large cleanup, but enforces one hard priority:

**Product behavior stays intact. Refactor may change ownership and structure, not user-visible logic.**

## Progress Update

- Completed: `BaseHandler` introduced and adopted by all 4 handlers.
- Completed: callback query path now reuses controller-owned handler instances (no per-event re-instantiation).
- Completed: modal submission/update callbacks now routed directly to handler-owned methods.
- Completed: controller-heavy submission/update methods replaced with compatibility wrappers.
- Completed: `SessionsFacade` introduced; session/thread/dedup/poll usage migrated from direct `SettingsManager` methods to session facade paths.
- Completed: IM auth checks now go through `BaseIMClient.check_authorization` (shared command-action extraction + centralized auth call).
- Completed: routing modal data collection unified in a single handler helper (`_gather_routing_modal_data`) and reused by Slack/Discord/Feishu routing entrypoints.
- Completed: routing model duplication reduced in `SettingsManager` by converging runtime routing conversion onto `RoutingSettings`-based helpers.
- Completed: `emit_agent_message` state machine extracted into `core/message_dispatcher.py` and controller now delegates dispatching.
- Completed: regression guard tests added for auth invariants and IM auth action extraction.
- Completed: introduced platform-neutral `RoutingModalData` (`core/modals.py`) and switched routing modal data flow to typed model instead of ad-hoc dicts.
- Completed: introduced shared command parsing/dispatch helpers in `BaseIMClient` and migrated Slack/Discord/Feishu text command handling to shared path.
- Completed: extracted routing modal selection parsing into Slack adapter helper (`modules/im/slack_modal.py::parse_routing_modal_selection`) to keep core handler logic platform-agnostic.
- Completed: removed controller compatibility wrappers for migrated handlers (controller now delegates via direct callback wiring).
- Completed: moved Slack routing selection parsing out of core into Slack adapter helper (`modules/im/slack_modal.py`).
- Completed: removed temporary `SettingsManager` session-compat shim methods after all call sites migrated to `SessionsFacade`.

## 2. First-Principles Invariants (Must Always Hold)

The refactor is complete only if these invariants are true:

1. **Authorize before side effects**  
   No state write or agent call before auth decision.
2. **Idempotent inbound handling**  
   Same message event cannot be processed twice.
3. **Stable session identity**  
   Session key generation is single-source and deterministic.
4. **Core/transport separation**  
   Core does not parse platform UI details (`block_id`, card schema internals, etc.).
5. **State ownership clarity**  
   IM layer cannot bypass state facades via direct store mutation.

## 3. Current Evidence (Code Audit Snapshot)

| Area | Current State |
|---|---|
| `core/controller.py` | 1,270 LOC, mixed orchestration + business logic |
| IM layer | 7,565 LOC total, substantial duplicated flow |
| `modules/settings_manager.py` | 690 LOC, mixed settings + sessions + pass-through methods |
| Handlers | duplicated helper methods across 4 classes |
| Largest hot spot | `SlackBot._build_routing_modal_view` ~523 LOC |

## 4. Target Architecture (Single Behavior Graph)

### 4.1 Layers

- **Policy Core**: auth, dedup, routing/session decision, command normalization.
- **Application Orchestrator**: controller wires dependencies and delegates.
- **Platform Adapters**: Slack/Discord/Feishu extract events + render UI only.
- **State Facades**: clear separation of configuration state and session/runtime state.

### 4.2 Ownership Rules

- `controller` orchestrates; it does not own modal parsing details.
- `handlers` own business workflows for settings/routing/sessions/commands.
- `BaseIMClient` owns shared inbound pipeline steps.
- platform clients own transport specifics only.

## 5. Execution Plan (Aggressive, Guarded)

## Phase 0 - Baseline and Safety Net (mandatory before structural moves)

**Goal**: lock current behavior before moving code.

Deliverables:
- Add focused regression tests for:
  - auth gate outcomes (`unbound_dm`, `unauthorized_channel`, `not_admin`)
  - `/bind` flow and first-admin invariant
  - resume-session selection + manual session submit
  - routing save/update flow
  - inbound dedup behavior
- Keep a single runtime path (no long-lived feature switch) to avoid dual-path drift.

Implementation note:
- To keep complexity low and avoid long-lived dual paths, migration shipped directly with behavior guards (tests + phased delegation) instead of adding a runtime feature toggle.

No-regression constraints:
- Existing command names, callback IDs, and i18n keys remain unchanged.
- Existing settings/session JSON formats remain backward compatible.

## Phase 1 - Controller Decomposition + Handler Foundation

**Goal**: remove business logic from `controller`, keep behavior identical.

Deliverables:
- Create `core/handlers/base.py` and migrate shared helper methods there.
- Move these methods out of `controller` into handlers:
  - `handle_settings_update`
  - `handle_change_cwd_submission`
  - `handle_resume_session_submission`
  - `handle_routing_modal_update`
  - `handle_routing_update`
- Update callback wiring to handler-owned methods.
- Stop per-call handler re-instantiation in `message_handler`.
- Extract `emit_agent_message` state machine into dedicated dispatcher class.

No-regression constraints:
- Callback payload contracts unchanged.
- Threading, chunking, and reaction behavior in outbound replies unchanged.

## Phase 2 - State Layer Split (Settings vs Sessions)

**Goal**: enforce clear state ownership and remove mixed responsibilities.

Deliverables:
- Introduce `SessionsFacade` and migrate all session/thread/dedup/poll APIs to it.
- Keep temporary compatibility wrappers in `SettingsManager` for one migration window.
- Replace direct `settings_manager.store` usage in IM/handlers with explicit facade methods.
- Normalize duplicated data models where semantically safe.

No-regression constraints:
- Existing persisted files can be read without migration scripts.
- Admin and bind invariants preserved exactly.

## Phase 3 - Shared Inbound Pipeline Across IM Platforms

**Goal**: unify decisions, not transport.

Deliverables:
- Implement shared inbound pipeline in `BaseIMClient` for:
  - hot reload trigger
  - auth check
  - dedup check
  - command action normalization
  - unified denial routing
- Keep platform hooks for extraction and platform-specific mention/thread semantics.

No-regression constraints:
- Slack `app_mention`, Discord guild/thread behavior, and Feishu shared-content extraction remain platform-correct.

## Phase 4 - Modal Schema Unification + Renderers

**Goal**: one modal definition, three renderers.

Deliverables:
- Build platform-neutral modal schema objects for settings/routing/resume/cwd.
- Add per-platform renderers:
  - Slack Block Kit renderer
  - Discord View renderer
  - Feishu Card renderer
- Keep existing callback IDs and submission payload semantics stable.

No-regression constraints:
- User can complete the same flows with identical outputs and permissions.

## Phase 5 - Compatibility Cleanup and Dead Code Removal

**Goal**: remove temporary shims once parity is proven.

Deliverables:
- Remove deprecated wrappers and duplicate conversion helpers.
- Remove temporary compatibility paths once parity is proven.
- Finalize docs and architecture map.

No-regression constraints:
- All Phase 0 tests remain green with compatibility code removed.

## 6. Behavior-Compatibility Matrix (Release Gate)

All rows must pass on Slack + Discord + Feishu unless marked platform-specific.

| Capability | Expected Compatibility |
|---|---|
| DM message handling | identical accept/reject behavior |
| Channel auth enforcement | identical denial outcomes |
| Admin-protected actions | identical permission checks |
| `/bind` | identical success/failure semantics |
| settings update | identical persisted values |
| routing update | identical backend/model/reasoning persistence |
| resume session | identical mapping and resume behavior |
| message dedup | no duplicate downstream processing |
| i18n text keys | no key regressions |

## 7. Validation Strategy

Automated checks (minimum per major step):
- targeted unit tests for changed module
- existing `tests/test_resume_session.py`
- existing `tests/test_v2_sessions.py`
- existing `tests/test_ui_api.py` (for API-facing setting behavior)

Manual sanity checks (required before enabling new path by default):
- start bot and send regular message in channel
- run `/settings`, `/routing`, `/set_cwd`, `/resume`
- verify DM bind and admin restrictions
- verify thread continuation and dedup

## 8. Rollout and Fallback

- Roll out by incremental commits with behavior-preserving tests at each step.
- Avoid dual runtime paths to reduce maintenance and drift risk.
- Keep rollback via git history and branch-level revert points.

## 9. Scope and Risk Posture

This is intentionally a large cleanup. To control risk without reducing scope:

- Use small, reviewable commits inside one branch.
- Keep each commit behavior-preserving where possible.
- Add tests before moving logic whenever practical.
- Prefer root-cause ownership fixes over wrappers, except temporary compatibility shims.

## 10. Definition of Done

The refactor is done only when all are true:

- Controller returns to orchestration role.
- Platform adapters no longer contain duplicated policy decisions.
- State boundaries are explicit and enforced.
- Modal logic is single-source with platform renderers.
- Compatibility matrix is fully green.
- Temporary migration shims are removed.

## Final Check

- Controller orchestration-only shape achieved; handler wrappers removed.
- Shared policy decisions (auth + command normalization + session facade usage) are centralized.
- Core no longer parses Slack routing modal block/action identifiers.
- Modal data flow is typed (`RoutingModalData`, `RoutingModalSelection`) and adapter-facing.
- Focused compatibility tests pass (`auth`, `routing parsing`, `sessions`, `paths`, `ui api`).
