# Backend Provider Configuration Plan

> Companion to `backend-lifecycle-chip.md`. Per user direction on
> 2026-05-12, both efforts land together in PR #282 (`feature/backend-lifecycle`).

## Background

Today, Settings → Backends shows only `enabled` + `cli_path` + (now) the
lifecycle chip. The Provider/Auth surface that was always the *primary*
content of those pages (per the brief on 2026-05-11) is still missing.

The unified `design.pen` lays it out:

| Backend | Frame | Auth model in the design |
| --- | --- | --- |
| Claude   | `qUpin`  (`cdAuth2`)  | OAuth status banner + API Key / Base URL fallback panel |
| Codex    | `NrTO1`  (`cxAuth`)   | API Key + Base URL form (primary) + OAuth fallback |
| OpenCode | `x53H1P` (`ocAnthropicExp` + `ocGridWrap` + `ocLocalRow`) | Provider grid (14: 12 cloud + 2 local) with per-provider auth-mode toggle, API Key, Base URL, models list, plus a Default-provider selector |

The three backends do **not** share an auth model — each gets a tailored
page. The lifecycle chip from PR #282 sits in each page's header
(`cdHeadR` / `cxHeadR` / `ocHeadR`), unchanged.

## Goal

Let the user, from the UI, without dropping to the terminal:

1. switch **Claude / Codex** between OAuth and API-Key + Base-URL modes
   (for Anthropic API gateways, Azure OpenAI, openrouter proxies, etc.)
2. configure **per-provider API keys + Base URLs in OpenCode**
3. pick the **default OpenCode provider** for new sessions
4. see, at a glance, which providers are *configured* / *OAuth-available*
   / *local*

Out of scope (still terminal-only or punted):

- per-provider model whitelist editing (read-only list in the design)
- non-default Codex / Claude profiles (single `auth_mode` per backend)
- connectivity-test buttons (`cdTest` / `cxTest` in the design) — punted
  to a follow-up; per user direction 2026-05-12
- replacing the existing setup wizard `AgentDetection.tsx` — that stays
  as first-run; this page is for ongoing reconfiguration
- modifying `opencodeSetupPermission()` quick-setup flow — per user
  direction 2026-05-12, the wizard remains untouched; the new page is
  edit-only

## Sub-requirements (enumerated up front)

Per `feedback_multipart_requests.md` — listing every piece so nothing
gets silently dropped:

- **A. Per-backend Settings page scaffolding** — route, breadcrumb,
  header w/ lifecycle chip, sidebar selection — ~300 lines
- **B. Claude Provider page** — OAuth banner (Re-auth, Sign-out), API
  Key + Base URL fallback panel — ~400 lines
- **C. Codex Provider page** — API Key + Base URL form, OAuth fallback,
  `Where these values live` info hint, Reset Base URL — ~400 lines
- **D. OpenCode Provider page** — toolbar (search + filter chips +
  default selector), 14-card grid, expand-to-edit, models list — ~700
  lines (biggest piece)
- **E. V2Config schema + API + persistence** — config dataclass fields,
  HTTP endpoints, file writers (`~/.codex/config.toml`,
  `~/.claude/settings.json`, leverage existing OpenCode writer) — ~200
  lines Python
- **F. i18n + glue** — `settings.backend.{claude,codex,opencode}.*` keys
  in en + zh — ~200 lines

Estimated total: ~2,200 lines diff. PR #282 already has ~500 lines from
the lifecycle chip; the combined PR will be ~2,700 lines. Worth
flagging with the user before committing to bundling.

## Solution

### Process-model reminder (carried over from lifecycle plan)

- **Claude** — one-shot CLI per request. API-key mode is realized by
  injecting `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` env vars at
  invocation time (the SDK already honors these).
- **Codex** — persistent `codex app-server` per cwd. API-key mode is
  realized by writing `~/.codex/config.toml`; the daemon picks up
  changes via `restart_backend('codex')` (PR #282 wires this).
- **OpenCode** — singleton server. Per-provider API key writes go
  through the existing `vibe/opencode_config.py::upsert_opencode_provider_api_key`
  plus `set_api_key_auth(provider_id, key)` HTTP call; the server is
  hot-reloaded via the existing `_install_opencode_api_key()` flow.

### V2Config schema (Python)

```python
@dataclass
class ClaudeConfig:
    enabled: bool = True
    cli_path: str = "claude"
    default_model: str | None = None
    idle_timeout_seconds: int = 300
    # NEW
    auth_mode: Literal["oauth", "api_key"] = "oauth"
    api_key: str | None = None              # plaintext in config; v1
    base_url: str | None = None             # None → SDK default

@dataclass
class CodexConfig:
    # …existing fields…
    auth_mode: Literal["oauth", "api_key"] = "oauth"
    api_key: str | None = None
    base_url: str | None = None             # None → https://api.openai.com/v1

@dataclass
class OpenCodeConfig:
    # …existing fields…
    default_provider: str = "anthropic"     # NEW
    # Per-provider state lives in ~/.config/opencode/opencode.json (already
    # the source of truth); V2Config only stores default_provider here.
```

Secrets handling for v1: store plaintext, same model as Slack/Discord
tokens in `v2_config.json`. Note in the doc that file is `chmod 600`.

### HTTP API

| Method | Path | Body | Purpose |
| --- | --- | --- | --- |
| GET    | `/backend/<name>/auth` | — | Read current auth state (claude/codex) |
| POST   | `/backend/<name>/auth` | `{auth_mode, api_key?, base_url?}` | Save claude/codex auth |
| POST   | `/backend/<name>/auth/test` | — | Live connectivity probe (optional, behind `cdTest`/`cxTest` button in design) |
| GET    | `/backend/opencode/providers` | — | Return the provider catalog (dynamic — see "Provider catalog source" below) with each one's `configured` / `oauth_available` / `local` flag |
| POST   | `/backend/opencode/provider/<id>/auth` | `{api_key, base_url?}` | Write provider API key |
| DELETE | `/backend/opencode/provider/<id>/auth` | — | Remove provider API key |
| POST   | `/backend/opencode/default-provider` | `{provider_id}` | Set default |

### Frontend

- `ui/src/components/settings/SettingsBackendsPage.tsx` becomes the
  index (lists the three backends with chip + summary) and routes to:
- `ui/src/components/settings/backends/ClaudePage.tsx`
- `ui/src/components/settings/backends/CodexPage.tsx`
- `ui/src/components/settings/backends/OpenCodePage.tsx`

All three pages reuse `BackendLifecycleChip` in their header. All
buttons / inputs / badges go through `ui/src/components/ui/*` per
`AGENTS.md § Frontend (UI)` (the convention added in this PR).

OpenCode provider grid uses `Card` (`ui/components/ui/card.tsx`) as the
base, with each card showing `Badge variant="success"` for configured,
`Badge variant="info"` for OAuth-available, `Badge variant="secondary"`
for local. Expansion uses a controlled disclosure within the page
(not a modal) — design shows it inline (`ocAnthropicExp`).

### Order of work (vertical slices)

To keep the PR landable in chunks even while bundled:

1. **Phase A** — scaffolding + Codex page end-to-end (smallest single
   backend, exercises the full stack: V2Config + API + file writer + UI)
2. **Phase B** — Claude page (same shape as Codex, mostly a clone)
3. **Phase C** — OpenCode page (the largest piece, leans on existing
   `vibe/opencode_config.py` infrastructure)
4. **Phase D** — connectivity-test buttons (`cdTest` / `cxTest`) and
   polish

Each phase is one commit; the PR review watch picks them up as they
land.

## TODO

- [ ] Phase A · scaffolding: route + breadcrumb + sidebar + header
- [ ] Phase A · Codex API surface: V2Config fields, `GET/POST
  /backend/codex/auth`, `~/.codex/config.toml` writer
- [ ] Phase A · Codex page UI: API Key input (mask + copy), Base URL
  input (with Reset), OAuth fallback, info hint
- [ ] Phase B · Claude API surface: V2Config fields, `GET/POST
  /backend/claude/auth`, env-injection in CLI launch path
- [ ] Phase B · Claude page UI: signed-in banner, API Key fallback panel
- [ ] Phase C · OpenCode catalog endpoint
  (`/backend/opencode/providers`)
- [ ] Phase C · OpenCode page UI: toolbar + 14-card grid + expansion
- [ ] Phase C · OpenCode default-provider selector + persistence
- [ ] Phase D · `auth/test` endpoint + UI test buttons
- [ ] i18n keys (en + zh)
- [ ] Reviewer subagent + ruff + npm run build before push
- [ ] Update PR #282 title/description to reflect expanded scope

## Provider catalog source (resolved 2026-05-12)

OpenCode's running HTTP server exposes the relevant endpoints — confirmed
against `opencode.ai/docs/server` and our own `modules/agents/opencode/server.py`
which already calls one of them:

| Method | OpenCode path | Purpose |
| --- | --- | --- |
| GET | `/provider` | `{all, default, connected}` — full provider list **plus** which IDs have credentials |
| GET | `/provider/auth` | `{[providerID]: ProviderAuthMethod[]}` — which providers support OAuth and which auth methods are available |
| GET | `/config/providers` | `{providers: [...], default: {...}}` — providers with their models (already wired in `OpenCodeServer.get_available_models`) |
| PUT | `/auth/:id` | Set provider credentials (already wired in `set_api_key_auth`) |
| POST | `/provider/:id/oauth/authorize` | Kick off OAuth (future-proof for when we wire OAuth flows from the UI) |

The `GET /backend/opencode/providers` endpoint in our HTTP API will fan
these out: hit `/provider` + `/provider/auth` in parallel, merge into a
list of `{id, name, configured, oauth_available, models, default_model}`,
and infer `local: true` from the absence of network auth methods (Ollama
/ LM Studio surface as providers with empty auth-method lists).

This means the **catalog is fully dynamic** — no Python-side hard-coded
14-provider list. If OpenCode adds providers, our UI surfaces them on
the next refresh without a vibe-remote release.

## Open questions resolved 2026-05-12

1. ✅ **Provider list source of truth** — runtime introspection from
   OpenCode (above); no hard-coded catalog.
2. ✅ **Connectivity test** — punted to a follow-up PR. Each backend
   needs a distinct probe strategy; not blocking v1.
3. ✅ **OpenCode setup wizard** — left untouched. The new page is for
   ongoing edits only; `opencodeSetupPermission()` remains the first-run
   path.
4. ✅ **PR size** — single bundled PR #282 (~2,700 lines). User accepted
   the review-burden tradeoff.

## Evidence

- Unit: V2Config round-trip; `~/.codex/config.toml` writer; OpenCode
  provider-API-key writer (existing path, reuse).
- Contract: tests scenario for "configure API key in UI → cli sees it
  on next launch" for each backend.
- Manual: regression Docker — open Settings → Backends → {Claude,
  Codex, OpenCode}, configure each path, verify backend actually uses
  the saved values.
