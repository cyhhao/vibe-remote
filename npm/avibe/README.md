# avibe

NPM entrypoint for [Vibe Remote](https://github.com/cyhhao/vibe-remote).

Vibe Remote is still installed and upgraded as the Python package `vibe-remote`.
This package is a thin bootstrapper for developers who expect npm-native entry
points.

## Quick Start

```bash
npx avibe
```

Or install the npm entrypoint globally:

```bash
npm install -g avibe
avibe
```

The first run installs the underlying `vibe` command if needed, then starts the
local Vibe Remote setup wizard.

## Commands

```bash
avibe install   # install or refresh the underlying vibe-remote Python CLI
avibe init      # start the setup wizard
avibe start     # start Vibe Remote
avibe status    # show runtime status
avibe doctor    # diagnose local setup issues
avibe remote    # configure remote Web UI access
avibe upgrade   # upgrade the underlying vibe-remote Python package
```

After bootstrap, `avibe` delegates to the real `vibe` command.

## Requirements

- Node.js 16+
- macOS / Linux: `curl` or `wget`
- Windows: PowerShell

The installer downloads `uv` automatically when it is missing. `uv` manages the
Python runtime for `vibe-remote`.

## Docs

- <https://docs.avibe.bot>
- <https://github.com/cyhhao/vibe-remote>
