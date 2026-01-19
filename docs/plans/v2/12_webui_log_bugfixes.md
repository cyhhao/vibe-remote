# Web UI Log Bugfixes

## Background
Recent Web UI runs show repeated errors in `~/.vibe_remote/logs/vibe_remote.log`, notably settings load failures and unclosed aiohttp client sessions during shutdown.

## Goals
- Load channel-based settings without errors or data loss.
- Eliminate aiohttp client session leaks on shutdown.
- Keep Slack routing and settings behavior consistent with V2 channel settings.

## Solution Outline
- Make `SettingsManager` read/write the new `config.v2_settings.SettingsStore` format (channels map), while preserving current APIs used by handlers.
- Provide a best-effort close path for Slack async clients and OpenCode HTTP sessions during controller shutdown.
- Add lightweight migration/compat logic where needed to avoid breaking older settings payloads.

## Todo
- Update `SettingsManager` to wrap `SettingsStore` for channel settings (hidden types, custom CWD, routing).
- Adjust `SettingsManager` serialization/deserialization to tolerate legacy payloads.
- Add shutdown cleanup to close Slack `AsyncWebClient`/`SocketModeClient` sessions.
- Verify OpenCode HTTP session is closed on shutdown.
- Run minimal validation (launch service, check log for removed errors).
