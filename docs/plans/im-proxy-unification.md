# Plan: Unify IM Proxy Configuration

## Background

Per-platform proxy support is fragmented:

| Platform | Current state |
| --- | --- |
| Telegram | `TelegramConfig.proxy_url` (PR #251), threaded through aiohttp |
| WeChat | `WeChatConfig.proxy_url`, threaded through aiohttp |
| Discord | system SOCKS auto-detect via `vibe.proxy.get_system_socks_proxy()` |
| Slack | none (slack_sdk supports `proxy=` but unused) |
| Lark/Feishu | none (lark-oapi has no proxy hook) |

Two configs already have `proxy_url`, but in different places; UI has it only on the
Telegram wizard. Users in restricted regions have to figure this out per platform.

## Goal

Single canonical `proxy_url` field at the base config level. Every adapter that
*can* honor it does so; the one that can't (Lark) surfaces a clear warning.
UI exposes the same field on every platform's setup wizard.

## Solution shape

1. **Base config** — lift `proxy_url: Optional[str] = None` to
   `modules/im/base.py:BaseIMConfig`. Drop the duplicate field on
   `TelegramConfig` / `WeChatConfig`.
2. **Resolution helper** — add `vibe.proxy.resolve_proxy(config_proxy)` returning
   explicit config first, then `get_system_socks_proxy()`, else `None`. Keeps
   Discord's existing system-detect behavior, extends it to all platforms.
3. **Adapter wiring**:
   - Telegram, WeChat — already wired; switch to `resolve_proxy()` for fallback.
   - Discord — replace `get_system_socks_proxy()` call with `resolve_proxy()`.
   - Slack — pass `proxy=resolve_proxy(config.proxy_url)` to `AsyncWebClient`.
   - Lark — log a clear warning if `proxy_url` is set; accept the gap until
     `lark-oapi` exposes a proxy hook.
4. **Backend API** — add `proxy_url` parameter to `slack_auth_test`,
   `discord_auth_test`, `lark_auth_test` (the Telegram one already has it).
5. **UI**:
   - Add a "Proxy URL (optional)" input to `SlackConfig.tsx`, `DiscordConfig.tsx`,
     `LarkConfig.tsx`, `WeChatConfig.tsx`. Reuse Telegram's pattern.
   - Lark wizard shows an extra hint that proxy is not yet wired through.
   - Thread the value through corresponding `*AuthTest` calls.
6. **Tests** — add unit tests for the lifted base field and `resolve_proxy()`.

## Out of scope

- Refactoring the wizard into a shared `<ProxyUrlField>` component (follow-up).
- A separate global "system-proxy mode" toggle.
- Lark proxy implementation (blocked on upstream `lark-oapi`).

## Todo

- [ ] Lift `proxy_url` to `BaseIMConfig`; remove duplicates
- [ ] Add `resolve_proxy()` to `vibe/proxy.py`
- [ ] Wire Discord, Slack, Telegram, WeChat through `resolve_proxy()`
- [ ] Lark adapter: warn when `proxy_url` set
- [ ] Backend `*_auth_test` functions accept `proxy_url`
- [ ] UI: add proxy field to Slack/Discord/Lark/WeChat wizards
- [ ] Tests: base config field + `resolve_proxy()`
- [ ] Open PR
