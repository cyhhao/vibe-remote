# Show Runtime Dependencies — Radical Redesign (vendor externalization + per-session deps)

Status: **locked — implementing** · Date: 2026-06-09
Repos: `vibe-show-runtime` (Node runtime) + `avibe` (Python serving)
Branches: `vibe-show-runtime@feat/vendor-prebuild` (from `origin/main`, post-#19) · `avibe@feat/show-vendor-deps` (from `origin/master`)

## Decisions (locked by Alex)
1. **Radical, no historical compatibility.** Retire the existing `/_show-runtime/deps/` registry + import-rewriting + `no-store` band-aid entirely. **No fallback code paths.**
2. **Shared deps are externalized**, served from ONE unified public vendor URL and referenced by every Show Page via a browser **import map**. Shared deps are **never** copied/linked under a session dir.
3. **v1: a single, runtime-provided React** (and provided toolkit). No per-session React-version override in v1 (architecture leaves room to add later). Per-session **extra** deps are allowed, **no gating / no limits**.
4. Build on the runtime's current `main` (which already includes #19's shared cache + source scanner + `dependencySignature`); the old quick-fix branch `fix/show-immutable-cache-clobber` is abandoned (never pushed).

## Why the old approach was awkward (root cause, verified)
- Deployed runtime (**#15**) keyed Vite's `cacheDir` by **sessionId** → every session re-optimized the *same* shared `node_modules` into its own dir → per-session chunk hashes for identical bytes.
- The Python layer then built an indirection (`/_show-runtime/deps/r9-<v>/<name>` + in-memory registry + import rewriting) purely to *re-share* those per-session URLs, and could only mark them `no-store` (a single public URL could map to different sessions' bytes → the cross-session poisoning [P1]).
- **#19 (already in `main`, not yet deployed)** removed sessionId from the cache key (shared, signature-keyed cache), pinned `optimizeDeps.include`, and added a source-import scanner + `dependencySignature`. This already fixes the per-session divergence for the common case — but still relies on per-session Vite optimization of the vendor set, and the Python band-aid still exists.

## Target architecture (3 tiers)
| Tier | What | URL | Cache | Shared? |
| --- | --- | --- | --- | --- |
| **Vendor** | react, react-dom/client, react/jsx-runtime, `@avibe/show-ui/*`, lucide, framer-motion, … | `/_show-runtime/vendor/<runtimeVer>/<file>` | `immutable` 1y | ✅ all sessions/pages |
| **Per-session extras** | a page's own installed npm deps (optional) | `/show/<id>/…` (that session's Vite) | `immutable`/ETag, per dep-set signature | ✅ only same extras |
| **App code** | App.tsx + local files | `/show/<id>/app/…` | `no-store`/short (dev, HMR) | ❌ per session |

- **Browser import map** (injected into each Show Page HTML server-side) maps bare specifiers (`react`, `@avibe/show-ui/`, …) → the vendor URLs.
- Each session's Vite **externalizes** the vendor specifiers (a resolve plugin leaves them bare for the import map) → it only optimizes/serves the app's own code + that session's extras. React stays a singleton (everyone resolves to the one vendor React; `resolve.dedupe`).
- Vendor is **built once per runtime version** at install/prepare time; the runtime install dir is already content-addressed (`versions/<sha>/…`) → a stable, version-keyed vendor URL. Runtime upgrade → new vendor URL → import map points to it on next load. **No per-session maintenance, nothing goes stale.**

## Implementation stages
### Runtime (`vibe-show-runtime`) — PR #1
- **R-A. Vendor pre-build** (CLI, e.g. `vibe-show-runtime build-vendor`): bundle `defaultOptimizeDepsInclude(...)` (the provided set) into content-hashed ESM under `<runtime>/vendor/`, emit a manifest `{specifier → /…/<file>}`. One react instance. Runs at install/prepare.
- **R-B. Dev externalization plugin**: resolve plugin in `warmSession`'s `viteConfig` that marks vendor specifiers external (leave bare) so the per-session Vite no longer optimizes them; drop them from `optimizeDeps.include` (keep only the app's `extraBareImports` that are NOT provided). `resolve.dedupe` for react/react-dom.
- **R-C. Per-session install**: optional per-session `package.json`; install extras under the session dir (or a session-scoped store); widen `server.fs.allow`; the existing `dependencySignature` already keys their cache. Extras' `react` import → vendor (externalized).
- Expose the vendor dir + manifest path so the Python side can serve/inject.

### avibe (`avibe`) — PR #2 (depends on a runtime release with the above)
- **A-A. Serve vendor**: `/_show-runtime/vendor/<runtimeVer>/<file>` → static, `immutable`. Serve the manifest.
- **A-B. Inject import map**: into each Show Page document (extend `_inject_show_runtime_config` / `_show_page_runtime_response`), built from the vendor manifest for the active runtime version.
- **A-C. Retire the band-aid (no fallback)**: delete `_should_redirect_to_public_show_runtime_dep`, `_register_public_show_runtime_dep`, the `/_show-runtime/deps/<v>/<name>` route, the sibling/private/dep rewriters, the `_SHOW_RUNTIME_PUBLIC_DEP_REGISTRY`, and the `no-store`/`_strip_mutated_*` cache dance for these. Keep only what the new model needs.
- `core/show_runtime.py`: trigger vendor pre-build at `prepare()`; per-session install path.

### Validation & ship
- Unit/contract tests both repos (vendor manifest, externalization leaves bare imports, import-map injection, per-session extras isolation, vendor immutable headers).
- **Local Incus regression**: real Show Page load — verify vendor served once + `immutable` + shared across sessions; verify a per-session extra dep works; verify HMR on app code still works. **Validate before cutover** (this is sequencing safety, not a code fallback).
- Codex review each diff. **PR order: runtime first → release → avibe.** Review watches.

## Key risks
- **React singleton** — enforce dedupe + import map everywhere (incl. extras). #1 correctness risk.
- **Dev externalization** is a small custom resolve plugin (not a config flag) — verify HMR + error overlay still behave.
- **Per-session `npm install`** of agent-authored deps — no gating per decision; keep `server.fs.allow` scoped; watch resource use.
- **Cross-repo sequencing** — runtime must ship before avibe can consume the vendor/manifest; regression-validate before cutover.

## Open (non-blocking) follow-ups
- Per-session React version (deferred from v1).
- Whether to prune old per-session vite-cache dirs left by #15/#19 on upgrade.
