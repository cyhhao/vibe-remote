# Distribution and Growth Plan

## Background

Vibe Remote already has a stronger product experience than a simple bridge: setup wizard, multi-platform routing, session resume, remote Web UI access, scheduled tasks, and async hooks. The public surface does not make those strengths obvious quickly enough, and the current distribution story still reads like a Python package rather than a developer CLI.

## Goal

Make Vibe Remote easier to understand, easier to install, and easier for AI coding agents to configure on behalf of users without overstating channels that are not shipped yet.

## Completed in This Pass

- Kept the README / README_ZH first-screen brand story intact instead of replacing it with a checklist-style positioning block.
- Added a concise "What Ships Today" checklist after installation, where it supports scanning without weakening the hero narrative.
- Added AI-agent installation guides in English and Chinese:
  - `docs/INSTALL_FOR_AI.md`
  - `docs/INSTALL_FOR_AI_ZH.md`
- Linked the AI-agent installation guides from the docs section without interrupting the landing-page narrative.
- Updated package metadata and installer banner copy so the project no longer presents as Slack-only.
- Added an npm entrypoint package under `npm/avibe`:
  - `npx avibe` and globally installed `avibe` bootstrap the existing Python `vibe-remote` package.
  - The npm package does not ship a second runtime; it installs or locates the real `vibe` command and delegates to it.
  - `avibe init` and `avibe start` map to the default `vibe` startup flow.
  - `avibe status`, `doctor`, `remote`, `upgrade`, `task`, `hook`, and other commands pass through to `vibe`.
  - CI now tests the wrapper and validates the packed npm contents.
  - A manual GitHub Actions workflow can publish `avibe` to npm with provenance after npm trusted publishing is configured.

## Next High-Leverage Work

1. Publish the `avibe` npm package, then switch public install copy to npm-first.
   - Preferred public command after publish: `npx avibe`.
   - Global install path: `npm install -g avibe && avibe`.
   - Keep the curl / PowerShell installers documented as fallback paths.

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
