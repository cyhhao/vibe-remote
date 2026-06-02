# Mobile / responsive Web UI

## Background
The avibe Web UI has two shells (see `AppShell.tsx`):
- **Workbench** (`/`): agent-centric — Inbox, Projects tree, Capabilities (Agents/Skills/Harness/Vaults), Chat. All of this lives in `WorkbenchSidebar`, which is inside the `md:flex hidden` desktop aside.
- **Control Panel / admin** (`/admin/*`): Dashboard, Channels, Users, Show Pages, Settings (multi-page), Logs.

**Core gap:** on mobile the desktop sidebar is hidden and the bottom nav only renders for `admin` (`mobileItems = []` in workbench). So the **Workbench is unnavigable on mobile**. Admin has a basic 5-item bottom nav but pages (esp. ChannelList master+detail) and dialogs aren't mobile-optimized.

Design model approved by Alex (2026-06-02). Reference: design.pen Workbench mobile frames + Show Page
`https://alex-app.avibe.bot/show/ses4xvk2m932f/`. design.pen IDs: TabBar `ONBGv`; screens Inbox `FnsGz`, Projects `FW7cI`, Chat `p6dhFi`, New-session sheet `KSXXB`, Capabilities·Agents `wdtCs`, More `Nxnja`.

## Approved interaction model
- **Two shells, each with its own bottom tab bar + a prominent center button** (mirrors desktop Workbench⇄Control-Panel switch):
  - Workbench tabs: `收件箱 Inbox · 项目 Projects · [＋ center] · 能力 Capabilities · 更多 More`. Center ＋ = new session.
  - Control Panel tabs: section nav + **center button = 工作台 (jump back to Workbench)** — Alex's explicit request (symmetry with the workbench ＋).
- **List → detail = full-page drill-down** (tap row → push screen + back), replacing desktop side-by-side master/detail.
- **Desktop popovers / dialogs → bottom sheets** on mobile (agent/model/effort picker, new-agent, run, add-skill, create-via-chat, new-project, context menus, visibility).
- Chat (`/chat/:id`) is a full-screen detail: **no bottom tab bar**, composer pinned at bottom.

### Alex's two tweaks (2026-06-02)
1. **More** screen: NO service start/stop control — read-only status only; service control stays in the Control Panel.
2. **Control Panel bottom Tab bar**: add a centered **工作台** entry button (back to Workbench).

## Build order (single branch `feature/mobile-responsive-webui`, atomic commits, one PR)
1. **Nav foundation** (this commit): `AppShell` mobile bottom tab bars for BOTH shells with center buttons; hide nav on `/chat` + `/setup`. New routes `/projects` + `/more`. `MorePage` (read-only status, control-panel link, theme/lang/account, host/version). `ProjectsPage` (projects → sessions drill-down, reuse `useApi` + `useWorkbenchInbox`). Capabilities tab → `/agents` (active across agents/skills/harness/vaults).
2. **Capability pages responsive + sub-tabs**: Agents/Skills/Harness mobile (single-column, drill-down detail, sub-tab strip Agents/Skills/Harness/Vaults). Dialogs → bottom sheets.
3. **Chat mobile**: transcript word-break/responsive, composer, agent/model/effort picker → bottom sheet, header back.
4. **New-session sheet** for the ＋ (project picker + compose + quick-starts).
5. **Admin pages responsive**: Dashboard, Channels (master+detail → drill-down), Users, Show Pages, Settings hub + sub-pages, Logs.
6. **Setup wizard mobile**.
7. **Dialog/Popover → Sheet** primitive (shared) + Light theme pass if requested.

## Conventions
- Reuse `ui/src/components/ui/*` primitives (Button/Badge/Card/...); extend variants, never re-roll (AGENTS.md §6).
- Match design.pen pixel values; map to tokens in `index.css`.
- Tailwind breakpoint `md` (768px) is the desktop/mobile split already in use.
- i18n: every string via `ui/src/i18n/{en,zh}.json` (`nav.*`, new `more.*`, `projects.*`).
- Verify each commit with `npm run build` in `ui/`.
