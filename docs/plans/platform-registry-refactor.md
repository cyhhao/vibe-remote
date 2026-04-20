# Platform Registry Refactor Plan

## Background

Platform behavior is currently encoded in multiple places: V2 config validation,
IM client creation, formatter selection, setup readiness checks, and frontend
platform helpers. Adding Telegram exposed the risk: one missed frontend
credential branch caused setup to keep redirecting even though Telegram had a
valid token.

## Goal

Create a single platform registry that owns platform identity, config linkage,
client and formatter construction, credential readiness, and capability flags.
Both backend and frontend should consume registry-derived data for generic
platform decisions.

## Design

- Add `config.platform_registry` with `PlatformDescriptor` and
  `PlatformCapabilities`.
- Keep platform-specific setup forms and platform API endpoints separate, but
  remove duplicated generic platform lists and credential checks.
- Expose registry metadata through config payloads and a catalog endpoint so the
  UI can render platform choices and capability-gated navigation from backend
  data.
- Make setup readiness a backend-computed `setup_state` so the frontend guard no
  longer reimplements credential rules.

## Todo

- [x] Add registry and descriptor tests.
- [x] Wire V2 config validation and setup readiness to the registry.
- [x] Wire IM factory and controller formatter selection to the registry.
- [x] Expose `platform_catalog` and `setup_state` in Web UI API payloads.
- [x] Move frontend generic platform helpers to catalog/capability data.
- [x] Run focused Python tests, Ruff on changed Python, and UI build.
