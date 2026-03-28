# Vibe Remote Sentry Context

This repository already initializes the Python Sentry SDK in two entry points:

- `main.py` initializes Sentry for the main service
- `vibe/ui_server.py` initializes Sentry for the Flask UI

Current runtime metadata attached during initialization:

- `release = vibe-remote@<package-version>`
- tag `component = service|ui`
- tag `mode = <config.mode>`
- tag `primary_platform = <config.platforms.primary>`
- tag `deployment_environment = <resolved environment>`

Current structured contexts include:

- `runtime`
- `host`
- `deployment`

Environment resolution currently behaves like this:

1. Use `VIBE_SENTRY_ENVIRONMENT` or `SENTRY_ENVIRONMENT` if set.
2. Else use `VIBE_DEPLOYMENT_ENV` if set.
3. Else use `regression` when `VIBE_REMOTE_HOME` contains `three-regression`.
4. Else use `integration` when `E2E_TEST_MODE=true|1|yes`.
5. Else default to `local`.

Useful query ideas for this repo:

```bash
# Recent unresolved regression issues
sentry issue list <org>/<project> --query 'is:unresolved environment:regression'

# Service-only failures
sentry issue list <org>/<project> --query 'component:service'

# UI-only failures
sentry issue list <org>/<project> --query 'component:ui'

# Platform-focused drilldown
sentry issue list <org>/<project> --query 'primary_platform:slack'
sentry issue list <org>/<project> --query 'primary_platform:discord'
sentry issue list <org>/<project> --query 'primary_platform:lark'

# Deployment mode focused drilldown
sentry issue list <org>/<project> --query 'mode:self_host'
```

Operational note:

- Sentry is useful for grouped failures, stack traces, events, traces, and correlated logs.
- Local logs remain the fallback for configuration mistakes, startup failures before SDK init, and any problem that was only logged but never captured as an exception.
