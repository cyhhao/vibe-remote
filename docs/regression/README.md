# Regression Testing

`回归测试` is the manual regression workflow for this repository. It runs Avibe
inside Incus so the environment behaves like a real long-running Linux machine:
systemd service, real home directory, persistent state, source sync, service
restart, and Show Runtime preparation.

It complements automated `E2E` tests instead of replacing them:

- `E2E testing` keeps using scripts and pytest for automatable scenarios.
- capability scenario metadata lives under `tests/scenarios/`
- multi-step auth/setup journeys should add or update
  `tests/scenarios/auth_setup/catalog.yaml` and
  `tests/scenarios/auth_setup/test_auth_setup_scenarios.py`
- `docs/regression/` is a human-facing entry layer, not the canonical source of
  truth for scenario metadata
- `Regression testing` is for human-triggered checks on real IM platforms.

## Scenario Metadata Navigation

Start here only if you are doing manual regression or need the human-readable
index.

For deterministic scenario metadata, read:

1. `tests/scenarios/INDEX.yaml`
2. `tests/scenarios/<capability>/catalog.yaml`
3. `tests/scenarios/<capability>/observations.yaml`
4. `tests/scenarios/<capability>/test_*.py`

## Runtime Model

The regression runner manages two Incus environment types:

- `master`: a long-running persistent regression environment.
- `worktree`: a temporary isolated environment for the current git worktree.

The master environment keeps product state across normal updates:

- platform credentials,
- Avibe Cloud remote-access pairing,
- agent CLI homes,
- Harness/session state,
- Show Page workspaces,
- Show Runtime cache where safe.

Worktree environments get their own Incus project/instance and host port. Their
mapping is recorded under `.runtime/incus-regression/worktrees.json` in the
primary checkout.

On macOS, use the local machine as an Incus client. The Incus daemon itself
should run on a Linux host or Linux VM.

## Setup

1. Configure an Incus host or remote.

   ```bash
   python3 scripts/incus_regression.py doctor
   ```

   If you are initializing a fresh Linux host directly:

   ```bash
   python3 scripts/incus_regression.py init-host --minimal
   ```

2. Build or provide the reusable base image.

   ```bash
   python3 scripts/incus_regression.py build-base
   ```

   The base image contains slow-changing dependencies such as Python, Node,
   build tools, and agent CLIs. Normal code updates do not rebuild this image.

3. Copy the local env template:

   ```bash
   cp .env.three-regression.example .env.three-regression
   ```

4. Fill in `.env.three-regression` with:

- shared LLM credentials: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`
- optional API base URLs: `ANTHROPIC_BASE_URL`, `OPENAI_BASE_URL`, `OPENAI_API_BASE`
- optional UI host override: `THREE_REGRESSION_UI_HOST`
- platform-specific bot credentials for Slack, Discord, Feishu, and WeChat
- the target regression channel for each platform, if you want channel routing
  preseeded at startup
- the backend that each platform's channel should pin to by default

Channel IDs are optional. If you leave them empty, the environment still starts
and you can configure channels later from the Web UI.

5. Keep these local-only files out of git:

- `.env.three-regression`
- `.runtime/incus-regression/`
- `.runtime/three-regression/` if you still use the Docker fallback

## Usage

The compatibility entry point now uses Incus by default:

```bash
./scripts/run_three_regression.sh
```

Direct runner commands:

```bash
python3 scripts/incus_regression.py up --target master
python3 scripts/incus_regression.py status --target master
python3 scripts/incus_regression.py logs --target master
python3 scripts/incus_regression.py shell --target master
python3 scripts/incus_regression.py down --target master
```

Temporary worktree environment:

```bash
python3 scripts/incus_regression.py up --target worktree
python3 scripts/incus_regression.py status --target worktree
python3 scripts/incus_regression.py delete --target worktree --yes
python3 scripts/incus_regression.py cleanup-stale --yes
```

Useful flags:

- `--remote <name>`: use an Incus remote configured in the Incus CLI.
- `--host-port <port>`: set the host-side Web UI proxy port.
- `--slug <slug>`: set the worktree environment slug.
- `--reset-mode config`: re-seed config/state/runtime.
- `--reset-mode all`: wipe and re-seed the environment state.
- `--clean`: remove stale source files before sync.
- `--force-deps`: force Python dependency refresh.
- `--no-build-ui`: skip UI asset build.
- `--dry-run`: print the planned Incus commands without changing the host.

The wrapper maps common legacy flags:

```bash
./scripts/run_three_regression.sh --status
./scripts/run_three_regression.sh --logs
./scripts/run_three_regression.sh --worktree
./scripts/run_three_regression.sh --reset-config
./scripts/run_three_regression.sh --dry-run
```

## What You Get

On success, the runner prints one local UI URL:

```text
Incus regression environment is ready:
  URL: http://127.0.0.1:15130
  Target: master
  Project: avr-master
  Instance: avibe-master
  Show Runtime source: github-source
```

Default names:

- master project: `avr-master`
- master instance: `avibe-master`
- master URL: `http://127.0.0.1:15130`
- worktree project: `avr-wt-<slug>`
- worktree instance: `avibe-wt-<slug>`
- worktree ports: allocated from `15200-15399` unless overridden

## Architecture

The Incus runner separates slow-changing dependencies from fast-changing source:

- **Base image**: Ubuntu plus Python, Node, build tools, systemd unit helpers,
  and agent CLI prerequisites.
- **Source sync**: current worktree source is streamed into
  `/opt/avibe/source`, excluding `.git`, `.runtime`, dependency directories, and
  generated assets.
- **Service**: Avibe runs under `avibe-regression.service` as user `avibe`.
- **Home**: `/home/avibe/.avibe` is the active product state home;
  `/home/avibe/.vibe_remote` is a compatibility symlink.
- **Show Runtime**: every successful update runs `vibe runtime prepare --strict`
  and then verifies `vibe runtime status --json`.

The runner fingerprints dependency inputs:

- Python dependencies: `pyproject.toml`, `uv.lock`
- UI dependencies: `ui/package.json`, `ui/package-lock.json`
- UI source: `ui/src`, `ui/index.html`, `ui/vite.config.ts`
- Show Runtime provider/ref

If fingerprints are unchanged, the runner skips unnecessary dependency
installation and UI builds.

## Docker Fallback

Docker is no longer the default regression runtime. During the transition,
the old Docker path is still available explicitly:

```bash
./scripts/run_three_regression.sh --docker
```

Use this only as a temporary fallback while the Incus host is being provisioned
or if a regression investigation specifically needs the old Docker image path.

## Secret Safety

- Never commit `.env.three-regression`.
- Never commit generated files under `.runtime/`.
- Runtime secrets are written into the Incus instance through stdin to
  `/etc/avibe-regression.env`; they should not appear in command-line logs.
- Share `.env.three-regression.example` if you only need to show the structure.
