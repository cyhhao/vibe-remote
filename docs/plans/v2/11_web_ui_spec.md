# V2 Web UI Product Spec

Status: Final

This spec defines the local Web UI shipped in V2. It is launched by the `vibe` CLI, runs on `localhost`, and is the primary surface for:

- First-time setup (SaaS OAuth or self-host Socket Mode)
- Ongoing management (service controls + channel settings)
- Diagnostics (Doctor panel, aligned with `vibe doctor`)

It must align with:

- `docs/plans/v2/05_config_model.md`
- `docs/plans/v2/08_web_ui_flow.md`
- `docs/plans/v2/09_cli_and_install.md`
- `docs/plans/v2/10_slack_permissions.md`

---

## Goals

- Provide a guided, low-friction setup wizard that results in a valid local `~/.vibe_remote/` configuration.
- Support both deployment modes:
  - SaaS: official Slack app + OAuth + cloud relay pairing
  - Self-host: Slack Socket Mode + user-provided tokens
- Make service status obvious and controllable (start/stop/restart) from the dashboard.
- Make channel-level configuration intuitive: enable/disable per channel, set working directory, choose agent backend, and tune backend options.
- Provide a first-class Doctor panel that surfaces actionable diagnostics (the same checks as `vibe doctor`).
- Keep the UI safe-by-default:
  - Localhost-only
  - No message content persistence
  - Clear warnings before destructive actions (reset)

## Non-goals

- A hosted, multi-user, remotely accessible admin console.
- A full task/run history UI or “Vibe-native app” (future phases).
- Editing `sessions.json` from the UI.
- Multi-workspace support (V2 is single-workspace only).
- Supporting non-Slack IM platforms in V2.

## Users & key scenarios

- New user (SaaS): installs official Slack app via OAuth, pairs a local gateway, enables a few channels, starts service.
- New user (self-host): creates Slack app from manifest, pastes tokens, validates scopes/events, enables channels, starts service.
- Existing user: changes default agent backend or per-channel routing, updates `custom_cwd`, or changes which channels are enabled.
- Troubleshooting: bot not responding, gateway offline, missing Slack scopes, invalid tokens, executor CLI missing; user opens Doctor panel to diagnose and apply fixes.

## Permissions / roles

- Local operator (the person running `vibe`): full read/write access to local config and service controls.
- Slack workspace admin: required to install apps and create/configure Slack apps (self-host flow).
- Regular Slack member: can use the bot in channels after setup, but does not configure the gateway.

There is no in-product user account system in V2; access is implicitly the local machine user.

---

## Proposed solution

### Information architecture (routes)

The UI is a single-page app served by the local gateway.

- `/setup` (wizard; only shown when setup is incomplete)
- `/dashboard` (overview + service controls)
- `/channels` (channel list + quick toggles)
- `/channels/:channel_id` (channel detail/settings)
- `/doctor` (diagnostics)
- `/logs` (read-only log viewer + file path)
- `/advanced` (reset, file locations, ports)

Navigation rules:

- If config is missing or invalid: land on `/setup`.
- If config is valid: land on `/dashboard`.
- A persistent “Edit setup” action is available from Dashboard that re-enters the wizard with current values prefilled.

### Setup wizard (step-by-step)

Wizard is a linear stepper with back/next, autosave per step, and explicit validation before advancing.

Global wizard behavior:

- Autosave: when the user clicks “Continue”, the UI persists that step’s values via API; on refresh, the wizard resumes.
- Draft safety: values are stored in config files immediately, but service is not started until the final step.
- Inline errors: validation errors appear next to fields and in a summary banner at the top.
- Primary CTA is always right-aligned; secondary actions left-aligned.

#### Step 1: Welcome

Purpose: set expectations and confirm this is a local-only UI.

Content requirements:

- Headline: “Set up Vibe Remote on this machine”
- Bullets:
  - “Runs locally. Your code stays on your computer.”
  - “SaaS mode uses a cloud relay for Slack delivery, but does not store message content.”
  - “Self-host mode runs entirely locally via Slack Socket Mode.”
- CTA: “Get started”

#### Step 2: Choose mode

Collects: `config.mode`

UI:

- Two radio cards:
  - “SaaS (recommended)”
    - Subtext: “Fastest setup: OAuth install + cloud relay + local execution.”
  - “Self-host”
    - Subtext: “Use your own Slack app + Socket Mode tokens.”
- CTA: “Continue”

Rules:

- Switching modes resets Slack-specific fields for the other mode in-memory; on save it clears the unused fields from config (see Data & rules).

#### Step 3: Local executors

Collects: `config.agents.*` and `config.agents.default_backend`

UI requirements:

- Section: “Default agent backend”
  - Dropdown: OpenCode / ClaudeCode / Codex
  - Helper text: “Used when a channel does not override routing.”

- Cards for each backend:
  - Toggle: Enabled
  - Text input: CLI path
  - Button: “Detect” (runs local detection)
  - Inline status:
    - “Found: <version>”
    - or “Not found” with fix guidance

Backend-specific fields:

- OpenCode:
  - Optional text: Default agent (`config.agents.opencode.default_agent`)
  - Optional text: Default model (`config.agents.opencode.default_model`)
  - Dropdown: Default reasoning effort (`low|medium|high|xhigh`)
- ClaudeCode:
  - Optional text: Default model (`config.agents.claude.default_model`)
- Codex:
  - Optional text: Default model (`config.agents.codex.default_model`)

Defaults:

- OpenCode enabled by default.
- Default backend defaults to OpenCode when available; otherwise first detected enabled backend.

Validation:

- At least one backend must be enabled.
- For each enabled backend, `cli_path` must be present and executable.

Microcopy:

- If OpenCode missing: “OpenCode CLI not found. Install it first or switch the default backend.”

#### Step 4: Slack configuration

This step is mode-specific.

##### Step 4A: SaaS (OAuth + relay pairing)

Collects: `config.slack.team_id`, `config.slack.team_name` (read-only display), `config.gateway.relay_url`, `config.gateway.workspace_token`, `config.gateway.client_id`, `config.gateway.client_secret`.

UI:

- Card: “Connect your Slack workspace”
  - Button: “Connect Slack”
  - Opens system browser to cloud OAuth URL (provided by local API).

- After OAuth begins, show a progress state:
  - “Waiting for Slack authorization…”
  - Spinner + “Check status” is automatic polling.

- After OAuth completes:
  - Display workspace:
    - Team name and Team ID
    - Slack app ID (if provided)
  - Display relay pairing:
    - Relay URL
    - Workspace token (masked by default; reveal requires click)
    - Gateway client id

- CTA: “Continue”

Rules:

- Single workspace constraint: if `config.slack.team_id` is already set, this step shows “Connected to <team_name>” and disables “Connect Slack” unless the user resets workspace (Advanced).

Validation:

- OAuth completion must yield a workspace token and relay URL.
- Relay URL must be HTTPS.
- Workspace token must be present (non-empty) and stored locally.

##### Step 4B: Self-host (Socket Mode)

Collects: `config.slack.bot_token`, `config.slack.app_token`, optional `config.slack.signing_secret`, and optional `config.slack.app_id`.

UI sections:

1) “Create your Slack app”

- Show an App Manifest the user can paste into Slack (“From an app manifest”).
- Provide a copy-to-clipboard button.

Manifest (YAML):

```yaml
display_information:
  name: Vibe Remote (Self-host)
  description: Local-first agent runtime for Slack
  background_color: "#0B1B2B"
features:
  bot_user:
    display_name: Vibe Remote
    always_online: false
  slash_commands:
    - command: /start
      description: Open main menu
      should_escape: false
    - command: /stop
      description: Stop current session
      should_escape: false
oauth_config:
  scopes:
    bot:
      - channels:history
      - channels:read
      - chat:write
      - app_mentions:read
      - users:read
      - commands
      - groups:read
      - groups:history
      - groups:write
      - chat:write.public
      - files:read
      - files:write
      - reactions:read
      - reactions:write
      - users:read.email
      - team:read
settings:
  event_subscriptions:
    bot_events:
      - message.channels
      - message.groups
      - app_mention
      - member_joined_channel
      - member_left_channel
      - channel_created
      - channel_renamed
      - team_join
  socket_mode_enabled: true
  interactivity:
    is_enabled: true
``` 

2) “Add Socket Mode token”

- Text input: App token (`xapp-...`)
- Helper: “Create an app-level token with scope `connections:write`.”

3) “Add Bot token”

- Text input: Bot token (`xoxb-...`)

4) Optional: “Signing secret (optional)”

- Text input: signing secret
- Helper: “Socket Mode does not require a signing secret, but it can be used for additional verification.”

5) “Validate Slack connection”

- Button: “Run auth.test”
- Shows result:
  - team name/id
  - bot user id
  - missing scopes list (if any)

Validation:

- `bot_token` must start with `xoxb-`.
- `app_token` must start with `xapp-`.
- `auth.test` must succeed.

#### Step 5: Channel settings

Collects:

- `config.runtime.target_channels` (global allow-list, including `"*"`)
- Per-channel settings in `settings.json`:
  - `settings.channels[channel_id].custom_cwd`
  - `settings.channels[channel_id].routing.*`
  - `settings.channels[channel_id].hidden_message_types`

UI requirements:

- Fetch channel list:
  - SaaS: via cloud-backed endpoint using workspace token
  - Self-host: via Slack Web API using bot token

Channel list table:

- Columns:
  - Channel name (with privacy badge: Public/Private)
  - Enabled toggle
  - Working directory
  - Backend (effective)
  - Last activity (if available from `sessions.json`; otherwise omit)

Enable/disable behavior (maps to `config.runtime.target_channels`):

- If `target_channels == "*"`:
  - All channels show Enabled=true.
  - Turning a single channel off converts `target_channels` to an explicit list of all currently enabled channels except that one.
- If `target_channels` is a list:
  - Enabled=true if channel id is in the list.
  - Enabled=false otherwise.
  - Provide a top-level action: “Enable all channels” that sets `target_channels` to `"*"`.

Channel detail drawer/page (`/channels/:channel_id`):

1) Working directory

- Field: “Working directory for this channel”
  - Default: empty (uses `config.runtime.default_cwd`)
  - When set: writes to `settings.channels[channel_id].custom_cwd`
- Validation:
  - Must be an absolute path.
  - Must exist and be a directory.
  - Must be readable and writable.

2) Agent routing

- Field: “Agent backend”
  - Options: “Use workspace default”, “OpenCode”, “ClaudeCode”, “Codex”
  - Writes to `settings.channels[channel_id].routing.agent_backend` where “Use workspace default” stores `null`.

- Backend overrides (only shown when relevant):
  - OpenCode:
    - Agent (optional): `routing.opencode_agent`
    - Model (optional): `routing.opencode_model`
    - Reasoning effort (optional): `routing.opencode_reasoning_effort`
  - ClaudeCode:
    - Model override (optional): stored in `routing` only if supported in code; otherwise hidden in UI for V2.
  - Codex:
    - Model override (optional): stored in `routing` only if supported in code; otherwise hidden in UI for V2.

Notes for frontend engineering:

- The V2 config model explicitly defines OpenCode per-channel overrides in `settings.json`. For ClaudeCode/Codex, only global defaults are defined in `config.json`. The UI must not write undefined per-channel keys.

3) Message visibility

- Multi-select checkboxes: “Hide these message types in Slack”
  - System
  - Assistant
  - Tool calls
- Writes to `settings.channels[channel_id].hidden_message_types`.
- Default selection: `system`, `assistant`, `toolcall`.

4) Effective settings preview

- Read-only panel that shows resolved values:
  - Enabled (from `target_channels`)
  - Effective CWD (channel override or default)
  - Effective backend and model/agent (channel override or global default)

#### Step 6: Validation + finish

Purpose: run all checks before starting service.

UI:

- Button: “Run validation”
- Results grouped:
  - Executors
  - Slack
  - Relay (SaaS only)
  - Filesystem
  - Configuration

Each check shows:

- Status: Pass / Warn / Fail
- One-line explanation
- Action link (if fixable in UI) or “Copy command” (if fix requires CLI)

Rules:

- “Finish setup” CTA is disabled until all Fail items are resolved.
- Warn items do not block but are highlighted.

#### Step 7: Start service

UI:

- Primary CTA: “Start Vibe Remote”
- After successful start:
  - Show “Service running”
  - Button: “Go to dashboard”

Constraints:

- Start is idempotent. If already running, show “Already running” and navigate to Dashboard.

---

## Dashboard requirements

Dashboard is the default screen after setup.

### Overview card

- Service status pill: Running / Stopped / Error
- Mode: SaaS / Self-host
- Workspace: team name + team id
- Gateway connection (SaaS): Online / Offline, last connected time (`config.gateway.last_connected_at`)

### Controls

- Buttons:
  - Start (if stopped)
  - Stop (if running)
  - Restart

Rules:

- Actions are idempotent and show inline progress.
- Stop triggers a confirmation modal: “Stopping will pause Slack responses until restarted.”

### Channels snapshot

- Quick stats:
  - Enabled channels count
  - Total channels where bot is present (if available)
- Table of up to 5 recently active channels (if sessions data available) with “View all” link.

### Health snapshot

- Inline Doctor summary:
  - “All systems go” or “2 issues need attention”
  - Link to Doctor panel

### Footers / secondary

- Config file locations (read-only):
  - `~/.vibe_remote/config/config.json`
  - `~/.vibe_remote/state/settings.json`
  - `~/.vibe_remote/logs/vibe_remote.log`

---

## Doctor panel requirements

Doctor panel must mirror the checks in `docs/plans/v2/09_cli_and_install.md` and present them in a UI that is:

- Fast to scan (grouped, clear statuses)
- Actionable (links to fix in-place)
- Copy-friendly (export report)

### Doctor checks

The UI runs `POST /api/doctor/run` and displays the returned structured results.

Required check groups:

1) Config validation

- Validate schema presence and required fields based on mode.
- Validate file readability/writability.

2) Slack validation

- Self-host:
  - `auth.test` using `xoxb-` token
  - Validate required scopes from `docs/plans/v2/10_slack_permissions.md`.
- SaaS:
  - Validate workspace binding exists (team_id)
  - Validate workspace token is present
  - Cloud-backed `auth.test` equivalent via relay/control plane

3) Executors

- For each enabled backend:
  - CLI exists and is executable
  - Version detection succeeds

4) Relay connectivity (SaaS only)

- Relay URL reachable
- Successful hello/hello_ack handshake (see `docs/plans/v2/06_gateway_protocol.md`)
- Time since last connected under a clear threshold (e.g., warn if > 10 minutes)

5) Runtime

- Service process running (PID) and responding
- Ports in use / conflicts

### Doctor UI behaviors

- Button: “Run Doctor” (always visible)
- Auto-run on page load if last run older than 5 minutes.
- Expand/collapse per group; “Expand all” action.
- Export:
  - “Copy report” copies a redacted JSON summary to clipboard.
  - Redaction rules: mask tokens, secrets, and any values matching `xoxb-`, `xapp-`, `workspace_token`, `client_secret`.

---

## Layout / typography / color direction

The UI should feel like a developer tool: calm, precise, and readable for long sessions, without default “generic dashboard” styling.

### Typography

- Headings: "Space Grotesk" (600)
- Body: "IBM Plex Sans" (400/500)
- Code / tokens / paths: "IBM Plex Mono" (400)

Implementation notes:

- Self-host fonts (bundle in app assets) to avoid relying on Google Fonts and to work offline in self-host mode.

### Color system (CSS variables)

Use a warm neutral base with a teal accent (no purple bias).

```css
:root {
  --bg: #F6F2EA;          /* warm paper */
  --panel: #FFFFFF;
  --text: #1F2430;        /* ink */
  --muted: #5B6475;
  --border: #E6DFD3;

  --accent: #0B7285;      /* teal */
  --accent-2: #E29578;    /* clay */

  --success: #2F9E44;
  --warning: #F08C00;
  --danger: #D9480F;
}
```

Background direction:

- Subtle gradient from `--bg` to a slightly cooler tint at the top.
- Light grain/noise texture (very low opacity) is acceptable if it does not affect readability.

### Layout

- App shell uses a left sidebar (desktop) and bottom nav (mobile) with the same destinations.
- Max content width: 1100px, centered, with generous whitespace.
- Step wizard uses a sticky stepper header on desktop; collapses to “Step X of Y” on mobile.

### Motion

- Use one meaningful motion pattern:
  - Step transitions: 150–200ms fade + slide (reduced motion respected).
- Avoid constant micro-animations.

### Accessibility

- Keyboard navigable; visible focus rings.
- Color contrast meets WCAG AA.
- All form fields have labels and error text tied via `aria-describedby`.

---

## Component requirements

The UI must be built from reusable components with consistent states.

Required components:

- AppShell (sidebar/topbar, workspace chip, service status pill)
- Stepper (wizard steps with completion/validation state)
- Card / SectionHeader
- FormField (label, helper, validation error)
- ToggleSwitch
- Select / Combobox
- CodeBlock (monospace, copy button, masked mode)
- StatusPill (Running/Stopped/Error; Pass/Warn/Fail)
- InlineAlert (info/warn/error)
- Toast notifications (success/failure of save/start/stop)
- ConfirmationModal (stop, reset)
- ChannelTable (sortable by name, filter by enabled)
- EmptyState (no channels, no private channel access, etc.)
- Skeleton loaders for async data

---

## API endpoints (local UI backend)

All endpoints are served by the local gateway at:

- `http://127.0.0.1:<config.ui.setup_port>`

Conventions:

- JSON request/response.
- Errors use the same shape:

```json
{
  "error": {
    "code": "string",
    "message": "human readable",
    "details": {}
  }
}
```

### Status + runtime

- `GET /api/status`
  - Returns: mode, service_state, workspace summary, gateway connection summary.

Response:

```json
{
  "service": {"state": "running", "pid": 12345, "started_at": "2026-01-18T12:00:00Z"},
  "config": {"mode": "saas", "version": "2"},
  "workspace": {"team_id": "T123", "team_name": "Acme"},
  "gateway": {"relay_url": "https://relay.vibe.remote", "last_connected_at": "2026-01-18T12:00:00Z", "connected": true}
}
```

- `POST /api/service/start`
- `POST /api/service/stop`
- `POST /api/service/restart`

All three are idempotent and return the same payload as `/api/status`.

### Config + settings

- `GET /api/config`
  - Returns current `config.json` contents.
- `PUT /api/config`
  - Replaces allowed keys; server validates and persists.

- `GET /api/settings`
  - Returns current `settings.json` contents.
- `PUT /api/settings`
  - Replaces allowed keys; server validates and persists.

Write rules:

- Server rejects unknown top-level keys.
- Server applies defaults defined in `docs/plans/v2/05_config_model.md` when fields are missing.

### Executor detection

- `POST /api/executors/detect`

Request:

```json
{"backends": ["opencode", "claude", "codex"]}
```

Response:

```json
{
  "results": {
    "opencode": {"found": true, "cli_path": "/usr/local/bin/opencode", "version": "1.2.3"},
    "claude": {"found": false, "cli_path": null, "version": null},
    "codex": {"found": true, "cli_path": "/opt/homebrew/bin/codex", "version": "0.9.1"}
  }
}
```

### Slack + channels

- `POST /api/slack/validate`

Runs Slack validation appropriate to mode.

Response:

```json
{
  "ok": true,
  "team": {"id": "T123", "name": "Acme"},
  "bot_user_id": "U999",
  "missing_scopes": []
}
```

- `GET /api/slack/channels`

Returns channels where the bot is present and the user can configure.

Response:

```json
{
  "channels": [
    {"id": "C123", "name": "dev", "is_private": false, "is_member": true},
    {"id": "G456", "name": "incident-room", "is_private": true, "is_member": true}
  ]
}
```

### SaaS OAuth (mode == saas)

- `POST /api/saas/oauth/start`

Response:

```json
{"authorize_url": "https://vibe.remote/slack/oauth/start?state=..."}
```

- `GET /api/saas/oauth/status`

Response states:

```json
{"state": "pending"}
```

```json
{
  "state": "complete",
  "workspace": {"team_id": "T123", "team_name": "Acme", "app_id": "A111"},
  "gateway": {"relay_url": "https://relay.vibe.remote", "workspace_token": "***", "client_id": "gw-...", "client_secret": "***"}
}
```

```json
{"state": "error", "error": {"code": "oauth_failed", "message": "Slack authorization was cancelled."}}
```

### Doctor

- `POST /api/doctor/run`

Response:

```json
{
  "ran_at": "2026-01-18T12:00:00Z",
  "summary": {"pass": 12, "warn": 1, "fail": 0},
  "groups": [
    {
      "name": "Slack",
      "items": [
        {"id": "slack-auth-test", "status": "pass", "message": "auth.test succeeded", "action": null}
      ]
    }
  ]
}
```

---

## Data & rules (validation, persistence, permissions)

### Files written

UI writes only these V2 files:

- `~/.vibe_remote/config/config.json`
- `~/.vibe_remote/state/settings.json`

It must not write to:

- `~/.vibe_remote/state/sessions.json` (runtime-owned)

### Mode-dependent required fields

From `docs/plans/v2/05_config_model.md`:

- If `mode == "self_host"`:
  - `config.slack.bot_token` required
  - `config.slack.app_token` required
- If `mode == "saas"`:
  - `config.gateway.relay_url` required
  - `config.gateway.workspace_token` required
  - `config.slack.team_id` required

### Validation rules

- Tokens:
  - Bot token must match `^xoxb-[A-Za-z0-9-]+$`
  - App token must match `^xapp-[A-Za-z0-9-]+$`
- Relay URL:
  - Must be HTTPS
  - Must be a valid URL
- Paths:
  - `runtime.default_cwd` and any `custom_cwd` must be absolute
  - Must exist, be a directory, and be readable/writable
- Target channels:
  - Either `"*"` or a non-empty list of Slack channel IDs
  - When list: IDs must be unique

### Channel settings resolution (effective behavior)

- Enabled:
  - If `target_channels == "*"`: all channels are enabled.
  - Else: enabled iff channel id in list.

- CWD:
  - If `settings.channels[channel_id].custom_cwd` is set, use it.
  - Else use `config.runtime.default_cwd`.

- Routing:
  - If `settings.channels[channel_id].routing.agent_backend` is `null`, use `config.agents.default_backend`.
  - If backend is OpenCode:
    - use per-channel overrides if present, else OpenCode defaults.

### Security constraints

- Web server binds to `127.0.0.1` only.
- UI must never display raw tokens by default.
  - Tokens are masked and require explicit “Reveal” per field.
- “Copy” actions copy the full value, but only after user intent (button click).

---

## Edge cases & failure modes

- Slack OAuth cancelled (SaaS): show a clear error and allow retry.
- Missing Slack scopes (self-host): doctor and validation show exact missing scopes; link to manifest.
- Bot not in channel:
  - Channel list should still show channels (if API returns them) and mark “Bot not a member”; enable toggle disabled with instruction: “Invite the bot to this channel in Slack.”
- No channels found:
  - Show empty state with steps: “Create a channel” + “Invite the bot” + “Refresh”.
- Private channels not visible:
  - If `groups:*` scopes missing, show “Private channels unavailable” warning.
- Executor CLI missing:
  - Block wizard progression if enabled backend has missing/non-executable path.
  - Provide a “Disable this backend” action.
- Relay offline (SaaS):
  - Dashboard shows Offline with last_connected_at.
  - Doctor shows fail for relay connectivity with retry.
- Port conflict for setup UI:
  - Backend chooses a free port and returns it; UI displays actual URL in header.
- Service stop from dashboard:
  - UI navigates to a “Service stopped” page with a Start button; do not show broken state.

---

## Acceptance criteria

Setup wizard:

- On first run with no config, UI opens to `/setup` and completes all steps without manual file editing.
- SaaS flow:
  - “Connect Slack” starts OAuth, polling completes, and resulting workspace + relay values are persisted to `~/.vibe_remote/config/config.json`.
- Self-host flow:
  - User can copy manifest, paste `xapp-` and `xoxb-` tokens, run `auth.test`, and persist tokens to `~/.vibe_remote/config/config.json`.
- Channel settings step:
  - Channel list loads and user can enable/disable channels; resulting `runtime.target_channels` is persisted.
  - User can set a per-channel `custom_cwd` and routing values; persisted to `~/.vibe_remote/state/settings.json`.
- Validation step:
  - “Run validation” produces pass/warn/fail statuses and blocks finish when any fail exists.

Dashboard:

- Dashboard shows current service state, mode, workspace, and relay connection state.
- Start/Stop/Restart work and are idempotent (repeated clicks do not crash and return consistent state).
- Dashboard links to Channels and Doctor and reflects updated settings without page reload (or via refresh).

Doctor:

- Doctor panel runs and shows results for: config, Slack, executors, relay (SaaS), runtime.
- Doctor export is redacted and never includes raw tokens/secrets.

UX + accessibility:

- Works on mobile widths down to 360px.
- Keyboard navigation covers wizard, tables, and modals.
- All critical errors include a human-readable message and a next step.

---

## Metrics / telemetry

V2 focuses on local observability; do not log message contents.

Local (in `~/.vibe_remote/logs/vibe_remote.log` and/or UI backend):

- Setup completion event (mode, time-to-complete)
- Doctor run counts + failing check IDs
- Service start/stop/restart counts
- Relay connection duration + reconnect counts (SaaS)

Cloud (SaaS control plane; no message content):

- Workspace install success/failure
- Active gateway online/offline timestamps

---

## Rollout plan & risks

Rollout:

- Ship Web UI as part of V2 CLI (`vibe`) and make it the default entrypoint.
- Release behind a V2 version gate (`config.version`) to avoid interacting with V1 artifacts.

Key risks and mitigations:

- OAuth complexity (SaaS): mitigate with clear polling states and deterministic error codes.
- Token leakage: mitigate with masking-by-default, redaction in logs/exports, localhost-only binding.
- Slack scope drift: mitigate by embedding the exact scopes/events list in UI and validating missing scopes in Doctor.
- Confusing channel enable semantics: mitigate by making `target_channels` behavior explicit and showing an “Enabled in Slack” preview per channel.
