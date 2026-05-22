# Distribution and Growth Plan

## Background

Vibe Remote already has a stronger product experience than a simple bridge: setup wizard, multi-platform routing, session resume, remote Web UI access, and the Agent Harness for scheduled tasks, watches, and async Agent Runs. The public surface does not make those strengths obvious quickly enough, and the current distribution story still reads like a Python package rather than a developer CLI.

## Goal

Make Vibe Remote easier to understand, easier to install, and easier for AI coding agents to configure on behalf of users without overstating channels that are not shipped yet.

The canonical install path should remain the current one-line curl / PowerShell installer because it is explicit, cross-runtime, and does not require users to already think in the Node ecosystem. npm should be an additional familiar entrypoint for Node-heavy developers, not the primary public installation story.

## Completed in This Pass

- Kept the README / README_ZH first-screen brand story intact instead of replacing it with a checklist-style positioning block.
- Added a concise "What Ships Today" checklist after installation, where it supports scanning without weakening the hero narrative.
- Added AI-agent installation guides in English and Chinese:
  - `docs/INSTALL_FOR_AI.md`
  - `docs/INSTALL_FOR_AI_ZH.md`
- Linked the AI-agent installation guides from the docs section without interrupting the landing-page narrative.
- Updated package metadata and installer banner copy so the project no longer presents as Slack-only.
- Added an npm entrypoint package under `npm/avibe`:
  - `npx @avibe/cli` and globally installed `avibe` / `vibe` bootstrap the existing Python `vibe-remote` package.
  - The npm package does not ship a second runtime; it installs or locates the real `vibe` command and delegates to it.
  - The npm package skips its own `vibe` shim when resolving the real Python runtime to avoid recursion.
  - `avibe init` and `avibe start` map to the default `vibe` startup flow.
  - `avibe status`, `doctor`, `remote`, `upgrade`, `task`, `hook`, and other commands pass through to `vibe`.
  - CI now tests the wrapper and validates the packed npm contents.
  - A manual GitHub Actions workflow can publish `@avibe/cli` to npm with provenance after npm trusted publishing is configured.

## Next High-Leverage Work

1. Publish the `@avibe/cli` npm package as a supplemental install entrypoint.
   - Keep the main README / docs install path as the existing one-line curl / PowerShell installer.
   - Document `npx @avibe/cli` and `npm install -g @avibe/cli && vibe` as optional Node-friendly alternatives after publish.
   - Do not make npm the default public install copy unless the product distribution strategy changes explicitly.

2. Add Homebrew distribution.
   - Short path: maintain a tap first.
   - Long path: submit to `homebrew-core` after install stability and release artifacts are boring.

3. Produce signed standalone binaries or app bundles.
   - Candidate tools: PyInstaller, Briefcase, or a small Go/Rust bootstrapper that installs and manages the Python package.
   - Decision criterion: startup reliability and update behavior matter more than removing every dependency.

4. Expand China-first platform coverage only where it fits the architecture.
   - Evaluate DingTalk, WeCom, QQ, and personal WeChat improvements through the shared IM abstraction.
   - Do not fork product behavior per platform unless platform constraints force it.

5. Add a comparison page that is fair and specific.
   - Compare with cc-connect and OpenClaw on setup, UX depth, session continuity, remote UI, security model, and automation.
   - Avoid attacking projects; explain which workflow each tool optimizes for.

## Non-Goals

- Do not claim npm, Homebrew, or standalone binary support until the release path is real.
- Do not turn Vibe Remote into a broad agent framework just to match bridge-project feature checklists.
- Do not add platform adapters by copying large platform-specific stacks into core.
