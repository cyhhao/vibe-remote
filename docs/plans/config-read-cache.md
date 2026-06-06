# Config Read Cache Plan

## Goal

Reduce repeated Workbench UI round-trips to `GET /api/config` over remote links.
The config payload is read by the app shell and several pages, but changes only
through explicit mutations in the same API context.

## Scope

- Keep the existing shared read-cache mechanism.
- Give `/api/config` a longer client-side TTL than generic read endpoints.
- Keep successful mutating calls invalidating the read cache, so `saveConfig`
  and related writes still make the next `getConfig()` fresh.
- Do not change the `/api/config` response shape or add a new endpoint.

## Validation

- Frontend production build.
- Diff review for cache invalidation paths.
