# Rebrand Plan: Vibe Remote → avibe ("the Agent OS")

Status: draft · branch `rebrand-avibe` (synced to origin/master #481) · 2026-06-04

## 1. Background

Vibe Remote is repositioning from "middleware that bridges AI agents to IM
platforms" to an **Agent OS**: one install command turns a machine into an
environment an agent lives in, and the user operates the whole system by
talking to that agent (Web or IM). The Web app is already feature-complete
(chat, configure agent / model / Skills / Harness / Webhook). `pyproject.toml`
already describes the product as a "Local-first agent runtime".

Brand: **avibe** (domain `avibe.bot`). GitHub org: **avibe-bot** (already live —
`avibe-bot/vibe-show-runtime` is referenced by `core/show_runtime.py`).

## 2. Decisions

Locked:
- **CLI command stays `vibe`.** No rename, no alias.
- **Repo name = `avibe`** under the `avibe-bot` org.
- **Backward compatibility for existing users is required** (config/state, install entry, update check).

Layered naming (resolves "avibe vs avibe-os" — different layers, so we keep both
the OS flag and the escape hatch):

| Layer | Value | Notes |
|---|---|---|
| Brand / domain | `avibe` / `avibe.bot` | trend-neutral, permanent escape hatch |
| GitHub repo | `avibe` | under `avibe-bot` |
| PyPI distribution | `avibe-os` | `avibe` is taken on PyPI; dist name ≠ import name ≠ command |
| CLI command | `vibe` | unchanged |
| Python import packages | `vibe`, `config`, `core`, `modules`, `storage` | unchanged (renaming = pure churn) |
| Category / tagline | "the Agent OS" | revisable copy, not identity |

Runtime home dir (Alex's call): **physically rename `~/.vibe_remote` → `~/.avibe`
on upgrade, then create a back-symlink `~/.vibe_remote` → `~/.avibe`.** Refined
robustness rules in §5a.

## 3. Verified current-state inventory (source of truth for the sweep)

From `pyproject.toml`, `AGENTS.md`, live CLI, and a repo-wide grep:
- PyPI dist name: **`vibe-remote`** (`[project].name`).
- Command: **`vibe`** → `vibe.cli:main` (`[project.scripts]`).
- Wheel packages: **`vibe`, `config`, `core`, `modules`, `storage`** — import surface is multi-package, NOT a single `vibe_remote`.
- Runtime home: `~/.vibe_remote/` (`state/`, `logs/`), env `VIBE_REMOTE_HOME`, log file `vibe_remote.log`; pytest autouse `VIBE_REMOTE_HOME` isolation + marker `uses_real_paths`.
- i18n: backend `vibe/i18n/`; frontend `ui/src/i18n/{en,zh}.json`. No hardcoded user-facing strings (AGENTS.md §6).
- Other repos: `avibe-bot-backend` (keep), `avibe-docs` (keep; domain on-brand, body copy not).

### Machine-critical endpoint inventory (the parts that can strand old users)
| Endpoint | Location | After-transfer risk |
|---|---|---|
| **Self update-check** | `core/update_checker.py` → `api.github.com/repos/cyhhao/vibe-remote/releases/tags`, `github.com/.../releases/tag` | **HIGH** — old clients are hardcoded here; depends on GitHub API rename-redirect surviving. Must test + never recreate old name. |
| Install one-liner | `install.sh` / `install.ps1` (`REPO="cyhhao/vibe-remote"`), short URL → 307 → `raw.githubusercontent.com/cyhhao/vibe-remote/master/install.sh` | MED — `raw.githubusercontent.com` does NOT reliably redirect after rename. Fix: repoint the owned short URL's 307 target at transfer. |
| npm entry | `npm/avibe/bin/avibe.js` hardcodes the two raw install URLs | MED — update raw URLs. (We already hold the `avibe` npm name via `npm/avibe`.) |
| Agent system-prompt link | `core/system_prompt_injection.py` → `github.com/cyhhao/vibe-remote/raw/master/skills/use-vibe-remote/SKILL.md` | LOW-MED — `github.com/.../raw/` web path redirects better than raw.githubusercontent; update anyway. |
| Package URLs | `pyproject.toml` `[project.urls]` ×4 | LOW — update on transfer. |
| Show Runtime archive | `core/show_runtime.py` → `avibe-bot/vibe-show-runtime` | NONE — already on-brand. |
| Docs / README / VISION / skill examples / tests | many `cyhhao/vibe-remote` strings | LOW — bulk sweep. |

### Key insight (de-risks the whole project)
The command and Python import packages are already stable (`vibe`, `config`,
...), NOT `vibe_remote`. This rebrand is **not a code-identifier churn**. The
real surface is: (a) PyPI distribution name, (b) GitHub repo + the endpoint
table above, (c) brand/display strings, (d) the runtime-home-dir migration.

## 4. Workstreams (commits on `rebrand-avibe`, one PR at the checkpoint)

- **W1 — Repoint machine-critical endpoints, THEN transfer GitHub** (sequencing matters; see §5c).
- **W2 — Runtime home + env compat** (§5a).
- **W3 — Distribution: `avibe-os` + `vibe-remote` shim** (§5b).
- **W4 — Brand/display copy** via `vibe/i18n/` + `ui/src/i18n/{en,zh}.json`, README, package description. Never hardcode. EN/ZH lockstep.
- **W5 — Docs** (`avibe-docs`), EN/ZH 1:1; commit + push to `main` (no PR) per that repo's convention.
- **W6 — Skill + integrations**: `use-vibe-remote` skill name + SKILL.md raw URL.
- **W7 — IM bot re-registration** (external; Slack/Discord/Telegram/Lark display names, OAuth redirects, app-directory review — start early).

## 5. Backward-compat designs (the careful part)

### 5a. Home dir + env (rename + back-symlink, hardened)
Adopt Alex's model: on upgrade, `rename(~/.vibe_remote → ~/.avibe)`, then create
symlink `~/.vibe_remote → ~/.avibe`. Hardening so it never strands anyone:
- **The in-code resolver is the real guarantee, not the symlink.** Resolution
  order in `config/paths`: `AVIBE_HOME` → `VIBE_REMOTE_HOME` (deprecated, warn)
  → `~/.avibe` if exists → `~/.vibe_remote` if exists (adopt) → default `~/.avibe`.
  The symlink is a convenience for stale absolute references, not the mechanism.
- **Atomic + idempotent + run-before-live.** Do the migration at CLI startup
  BEFORE the service binds or caches any path (the live `vibe` process may be the
  agent runtime — never migrate under a running service). If rename succeeds but
  symlink creation fails, a later startup re-creates the missing symlink.
- **Conflict rule.** If both `~/.avibe` (real) and `~/.vibe_remote` (real, not a
  symlink) exist: prefer `~/.avibe`, do NOT clobber, emit a one-time warning.
- **Windows fallback.** Symlinks need admin/Dev Mode; make the symlink
  best-effort and rely on the resolver (or a directory junction) there.
- Covers: `state/`, `logs/`, sessions, scheduled tasks, watches, Show Page
  workspaces, `remote_access` pairing, agent CLI homes. Container/regression
  homes (e.g. `/data/vibe_remote`) are separate paths — handle independently.
- Tests: every resolver branch; old-user simulation (only `~/.vibe_remote/`) →
  no data loss, notice shown once; both env vars honored.

### 5b. PyPI (you cannot rename a PyPI project)
`avibe-os` is a NEW project; PyPI has no rename. Migration without dual-publishing forever:
1. Going forward, **`avibe-os` is the real package** — single publish per release (update the release workflow, AGENTS.md §9).
2. **One-time `vibe-remote` shim release**: a thin dist that declares
   `[project.scripts] vibe = "vibe.cli:main"` and `dependencies = ["avibe-os>=<floor>"]`.
   Then `pip/uv install -U vibe-remote` keeps pulling the latest real code (via
   the dep) and still exposes `vibe`. Published once, not every release.
3. **Seamless onto the new name** = via the app's own updater / install script:
   detect an old `vibe-remote` tool install and re-install as `avibe-os`
   (`uv tool uninstall vibe-remote && uv tool install avibe-os`, or pip
   equivalent). This is the only lever that actually moves users to the new
   name; PyPI can't do it.
4. Users who never re-install stay on the shim and keep working indefinitely.

### 5c. GitHub transfer safety (keep the redirect alive)
- **Sequencing: repoint first, transfer second.** Ship a release that updates the
  endpoint table (§3) — especially `update_checker.py` — to the new repo (or a
  stable `avibe.bot`-owned endpoint), let it roll out, THEN transfer. New clients
  stop depending on the redirect; only the tail of old clients relies on it.
- **The redirect dies only if the old name is recreated.** Hard rule: never
  create a repo at `cyhhao/vibe-remote` (or `avibe-bot/vibe-remote`) again. This
  is pure discipline — there is no "occupy + redirect" both-ways option.
- **Decouple machine-critical endpoints from the repo path.** The install short
  URL (307, owned) and the Show Runtime base (separate repo) are already
  decoupled; the self-updater is the one that is NOT — that is the priority fix.
- **Test before committing**: confirm the updater's HTTP client follows the
  GitHub API 301 from a renamed repo; repoint the install short URL's 307 target
  to the new raw path; update `npm/avibe/bin` raw URLs; update SKILL.md link +
  `pyproject.toml` URLs.

## 6. Codex collaboration (division of labor)
- **Claude (lead execution)**: edits across repos, build the resolver/shims/migration, run ruff + focused pytest + Docker regression + `npm run build`, open the PR, verify (incl. old-user simulation + the API-redirect test).
- **Codex (thoroughness / adversarial)**:
  - C1 — independent exhaustive reference sweep across all three repos; reconcile against §3.
  - C2 — adversarial review of §5a resolver/migration (state paths, first-run races, run-before-live, Windows, regression shared-state) and §5c sequencing.
  - C3 — mandated pre-merge Codex review (AGENTS.md §5).
- **Invocation** (both verified):
  - Native/dogfood: `vibe agent run --agent codex --message "<task>" [--async] [--session-id <id>]` (an enabled `codex` Vibe Agent already exists: backend codex, gpt-5.5, effort low; `--async` posts back).
  - Direct CLI: `codex exec --json --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --cd <dir> "<prompt>"` (codex-cli 0.130.0). Synchronous JSON, full control of reasoning effort — preferred for tight-loop sub-tasks and meticulous audits (the existing agent runs effort "low").

## 7. Testing / validation
- ruff on changed Python before push (AGENTS.md §5).
- focused pytest on the home/env resolver (`test_upgrade_flow.py` already exists — extend it).
- Docker regression for user-facing flows; do NOT reset state; confirm old-home users load cleanly.
- `npm run build` for UI copy changes.
- live test: updater follows GitHub API 301 after a simulated rename.

## 8. Sequencing
P0 decisions (this doc) → **P1 repoint endpoints (esp. update_checker) + roll out**
→ P2 GitHub transfer (never recreate old name) → P3 home/env migration (early,
hard testing) → P4 distribution (`avibe-os` + shim) → P5 brand/UI/docs → P6 IM
re-registration (parallel, external). One branch, many commits, one PR at the checkpoint.

## 9. Execution checkpoint: latest master sync

Before implementation, the task worktree was fast-forwarded from
`origin/master` to `f8cc2453` (`ci: run npm wrapper on avibe runner (#481)`).
The only remaining local change at this point is this untracked plan document.

## 10. Reference sweep classification rubric

The initial sweep must classify each `Vibe Remote` / `vibe-remote` /
`vibe_remote` / `VIBE_REMOTE` occurrence by product layer, not by blind string
replacement.

### Keep

Keep references that are part of a stable compatibility surface or historical
record:
- CLI command and user shell command examples using `vibe`.
- Python import packages and code identifiers: `vibe`, `config`, `core`,
  `modules`, `storage`.
- Deprecated compatibility inputs that must remain accepted, especially
  `VIBE_REMOTE_HOME` and old `~/.vibe_remote` path handling.
- Tests and fixtures that intentionally simulate old users, old package names,
  old paths, or old GitHub URLs.
- Migration IDs, historical release notes, and compatibility prose where the old
  name is necessary to explain what is being migrated.

### Change

Change references that define the current/future product identity or future
machine-readable endpoints:
- User-facing brand copy: UI strings, backend i18n messages, README, package
  description, docs, skill prose, install pages.
- Future canonical repo/package metadata: GitHub URLs, `[project.urls]`,
  release workflow references, install docs, package manager examples.
- Machine-critical endpoints in new releases: update checker repo path, raw
  install script URLs, npm installer URLs, skill raw URL.
- Runtime defaults: introduce `AVIBE_HOME`, make `~/.avibe` the default, and
  present old paths only as deprecated compatibility.
- Docker image/package names that represent current public distribution rather
  than legacy fixture state.

### External

Track references that cannot be fully changed by a repo commit:
- Owned short install URL target and any CDN/redirect rule behind it.
- GitHub transfer from `cyhhao/vibe-remote` to `avibe-bot/avibe`.
- PyPI publishing of `avibe-os` and the one-time `vibe-remote` shim.
- npm package publication and validation after installer URL changes.
- Slack/Discord/Telegram/Lark/Feishu/WeChat bot display names, OAuth redirect
  URLs, app-directory review, and any platform-side branding.
- DNS or hosted endpoints if machine-readable update/install endpoints move to
  `avibe.bot`.

### Needs Alex decision

Do not guess these during implementation:
- Exact target for the install short URL after transfer, and whether
  machine-readable endpoints should move to `avibe.bot` instead of the new
  GitHub repo path.
- PyPI floor version for the `vibe-remote` shim dependency on `avibe-os`.
- Timing of the GitHub transfer relative to the endpoint-repoint release.
- Whether the physical home-dir move should happen in the first rebrand PR or
  be deferred after endpoint and distribution migration.

### Review carefully, do not bulk-replace

Some references are likely mixed-purpose and need per-occurrence judgment:
- `vibe_remote.log` and other filenames: decide whether they are user-visible
  current defaults, legacy compatibility, or migration targets.
- `/data/vibe_remote` and regression paths: preserve existing state unless an
  isolated migration path is explicitly tested.
- `use-vibe-remote` skill naming: update public/raw links and prose, but avoid
  breaking existing skill lookup until a compatibility alias is defined.
- `vibe-remote` in dependency metadata: current real package should become
  `avibe-os`, while old-package references remain only for shim/migration.
