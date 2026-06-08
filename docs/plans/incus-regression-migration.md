# Incus regression migration

## Background

The current regression workflow is Docker-based. It rebuilds an
application image and force-recreates the regression container for normal code
updates. That shape is useful for reproducible application-container testing,
but it is a poor fit for Avibe's product reality:

- Avibe behaves like a long-running local OS service, not a single application
  process.
- Regression state is valuable and should survive code updates.
- Worktree testing should be cheap enough to use during normal PR work.
- Full image rebuilds make routine regression updates slow and make Show Runtime
  cold-start issues harder to separate from container rebuild churn.

Incus is a better runtime model for this because it manages full Linux system
containers and VMs. A system container gives us a real init/systemd environment,
a real home directory, long-lived state, service restarts, and lower overhead
than rebuilding Docker application images for every code change.

This migration should replace the Docker regression runner with an
Incus-backed regression runner. The existing `scripts/incus_tenant.py` tenant
scaffold is useful prior art, but regression needs a different lifecycle model:
one persistent master environment plus temporary worktree environments.

## Goals

1. Run manual regression in Incus instead of Docker.
2. Keep the `master` regression environment long-lived.
3. Allow any git worktree to create an isolated temporary regression
   environment.
4. Update code by syncing source into the instance and restarting Avibe, not by
   rebuilding an image.
5. Keep regression state, agent CLI homes, remote access pairing, sessions, and
   Show Page workspaces stable for the persistent master environment.
6. Prepare and verify Show Runtime as part of every successful regression
   update.
7. Make cleanup deterministic for temporary worktree environments.
8. Remove Docker regression artifacts after the Incus runner owns the workflow.

## Non-goals

- Do not redesign the product's tenant hosting model in this migration.
- Do not require every developer machine to run an Incus daemon locally. On
  macOS, the local machine can be an Incus client, but the daemon should run on a
  Linux host or Linux VM.
- Do not turn regression environments into untrusted multi-tenant production
  isolation. Use resource limits and restricted projects, but treat this as
  developer/operator regression infrastructure.
- Do not reset existing regression config/state unless explicitly requested.

## Runtime model

### Host

Use a Linux Incus host as the regression runtime. For this workstation, macOS is
only the operator/client environment. The runner should support:

- local Linux host with `incus` available,
- remote Incus daemon configured through the standard Incus CLI remote,
- dry-run planning when Incus is unavailable.

The runner should fail early with a clear message if no usable Incus CLI/remote
is configured.

### Base image

Create one reusable Avibe regression base image that contains slow-changing
dependencies, not Avibe source code:

- Ubuntu base image,
- Python, `uv`, build essentials, git,
- Node.js and npm,
- agent CLIs: Claude Code, Codex, OpenCode,
- systemd service template and helper scripts,
- optional browser/runtime packages needed by Show Runtime.

The image should be rebuilt only when base dependencies change. Normal Avibe
code updates must not rebuild this image.

Proposed image alias:

- `avibe-regression-base-<date-or-dependency-hash>`
- `avibe-regression-base-current`

### Instance layout

Inside every regression instance:

- service user: `avibe`
- source checkout: `/opt/avibe/source`
- app venv: `/opt/avibe/venv`
- UI dist: `/opt/avibe/source/ui/dist`
- Avibe home: `/home/avibe/.avibe`
- compatibility home: `/home/avibe/.vibe_remote -> /home/avibe/.avibe`
- operator metadata: `/var/lib/avibe-regression/metadata.json`

The Avibe service should run through systemd:

- service name: `avibe-regression.service`
- environment: `VIBE_DEPLOYMENT_ENV=regression`
- environment: `HOME=/home/avibe`
- environment: `VIBE_REMOTE_HOME=` so default-home behavior is exercised
- UI bind: `0.0.0.0:5123`

## Environment types

### Persistent master environment

The master regression environment is long-lived:

- project: `avr-master`
- instance: `avibe-master`
- host port: default `15130`
- state: persistent under the instance root disk and optional named volume
- lifecycle: create once, then update source and restart service

Normal update flow:

1. Ensure local `master` is current.
2. Sync source into `/opt/avibe/source`.
3. Build UI if UI inputs changed or `--build-ui` is requested.
4. Refresh editable Python install if dependency metadata changed.
5. Restart `avibe-regression.service`.
6. Run health checks.
7. Run `vibe runtime prepare --strict`.
8. Verify Show Runtime status.
9. Print the public/local URL and routing summary.

This environment preserves:

- platform credentials,
- remote access pairing,
- agent CLI homes,
- Harness/session state,
- Show Page workspaces,
- Show Runtime cache where safe.

### Temporary worktree environments

Temporary environments are tied to a worktree identity:

- project: `avr-wt-<slug>`
- instance: `avibe-wt-<slug>`
- host port: allocated from a regression port range, for example `15200-15399`
- state: isolated per worktree
- lifecycle: create/update/delete with the worktree

The slug should be deterministic and collision-resistant:

- prefer branch name when valid,
- include a short hash of the absolute worktree path,
- store the mapping in `.runtime/incus-regression/worktrees.json`.

Temporary environments should support:

- `up`: create or update from the current worktree,
- `status`: show instance, branch, commit, URL, health,
- `logs`: follow Avibe service logs,
- `shell`: enter the instance as the `avibe` user,
- `down`: stop the instance,
- `delete`: delete project, instance, and all temporary state.

By default, deleting a git worktree should not automatically delete Incus state
behind the user's back. Instead, provide a cleanup command that detects missing
worktree paths and offers deletion with `--yes`.

## CLI design

Add a new runner:

```bash
python3 scripts/incus_regression.py doctor
python3 scripts/incus_regression.py init-host
python3 scripts/incus_regression.py build-base
python3 scripts/incus_regression.py up --target master
python3 scripts/incus_regression.py up --target worktree
python3 scripts/incus_regression.py status --target master
python3 scripts/incus_regression.py logs --target master
python3 scripts/incus_regression.py shell --target worktree
python3 scripts/incus_regression.py down --target worktree
python3 scripts/incus_regression.py delete --target worktree --yes
python3 scripts/incus_regression.py cleanup-stale --yes
```

Keep the existing entry point as a thin Incus wrapper:

```bash
./scripts/run_regression.sh
```

The wrapper should not contain Docker fallback logic. Docker-specific flags
should fail as unknown arguments.

## Source sync strategy

Use a host-side sync step rather than git clone inside the instance.

Requirements:

- sync the exact current worktree, including uncommitted files when requested,
- exclude `.git`, `.runtime`, `node_modules`, caches, and generated state,
- preserve `ui/dist` policy explicitly,
- write sync metadata with branch, commit, dirty state, source path, and time.

Implementation options:

1. Preferred: `rsync` to `/opt/avibe/source`.
2. Fallback: tar stream through `incus exec`.

Default behavior:

- include committed and uncommitted source files because manual regression often
  happens before committing,
- mark metadata as dirty when the local worktree is dirty,
- allow `--clean` to remove files in the instance that no longer exist locally.

## Dependency update strategy

Do not reinstall everything on every update. Track fingerprints:

- Python dependencies: `pyproject.toml`, `uv.lock`,
- UI dependencies: `ui/package.json`, `ui/package-lock.json`,
- base image dependencies: runner-owned base image manifest,
- Show Runtime source provider/ref.

Actions:

- If Python metadata changed, run `uv pip install -e /opt/avibe/source` in the
  persistent venv.
- If UI dependency metadata changed, run `npm ci` in `/opt/avibe/source/ui`.
- If UI source changed, run `npm run build`.
- If only Python source changed, skip UI and dependency install.
- If only docs/tests changed, allow `--restart=false`.
- Always run Show Runtime prepare after a service update unless explicitly
  skipped for debugging.

## State preparation

Reuse the existing `prepare_regression.py` logic, but decouple it from
Docker paths.

Needed changes:

- allow target home path `/home/avibe/.avibe`,
- allow host state generation for master and worktree targets,
- keep reset modes: `none`, `config`, `all`,
- preserve current env-file behavior,
- keep platform routing preseed behavior,
- keep agent home import/export behavior for master where relevant.

For master, preparation should only seed missing config by default.

For temporary worktrees, preparation can create a fresh isolated state unless
`--state-root` is provided.

## Networking

Expose the Web UI through an Incus proxy device:

- master: `127.0.0.1:15130 -> instance 127.0.0.1:5123`,
- worktrees: allocated host port -> instance `127.0.0.1:5123`.

Keep remote access behavior intact. If Avibe Cloud is paired in the master
environment, do not overwrite that config during updates.

## Show Runtime handling

Every successful `up` should verify Show Runtime:

```bash
vibe runtime prepare --strict
vibe runtime status --json
```

The runner should fail the update if:

- runtime command is missing,
- runtime provider install fails,
- installed runtime does not match requested provider/ref,
- a smoke endpoint cannot load a basic public Show Page asset.

For worktree environments, support overriding:

- `REGRESSION_SHOW_RUNTIME_SOURCE`
- `REGRESSION_SHOW_RUNTIME_GITHUB_REPO`
- `REGRESSION_SHOW_RUNTIME_GITHUB_REF`

## Migration phases

### Phase 0: Design lock

- Land this plan.
- Confirm Linux Incus host choice for regression.
- Recreate master state from `.env.regression`; old Docker regression
  state roots are not part of the Incus cutover.

### Phase 1: Incus runner scaffold

- Add `scripts/incus_regression.py`.
- Add doctor/status/dry-run tests.
- Reuse naming and project helpers from `incus_tenant.py` where possible.
- Add docs for host setup and command usage.

### Phase 2: Base image and service bootstrap

- Add base image build command.
- Add cloud-init or provisioning script for `avibe` user, venv, Node, agent
  CLIs, systemd unit, and helper commands.
- Add tests for rendered provisioning config.

### Phase 3: Persistent master environment

- Implement `up/status/logs/shell` for `--target master`.
- Implement source sync, dependency fingerprints, UI build, service restart,
  health check, and Show Runtime prepare.
- Validate on the real regression host.

### Phase 4: Temporary worktree environments

- Implement deterministic worktree slugging.
- Implement port allocation and mapping file.
- Implement create/update/delete/cleanup-stale.
- Validate two concurrent worktree environments can run without sharing state.

### Phase 5: Replace Docker path

- Change `run_regression.sh` to call the Incus runner.
- Update `AGENTS.md` and `docs/regression/README.md`.
- Remove Docker fallback from the wrapper.

### Phase 6: Remove Docker regression artifacts

- Remove `docker-compose.regression.yml`.
- Remove Docker-only tests or rewrite them for Incus.
- Update all developer docs and examples.

## Validation

Focused automated checks:

- runner CLI parsing,
- project/instance naming,
- port allocation,
- dry-run command rendering,
- source sync exclude list,
- dependency fingerprint decisions,
- reset-mode state preparation,
- Show Runtime status parsing.

Manual/live checks:

- master update from clean `master`,
- master update from dirty local source,
- temporary worktree creation/update/delete,
- two worktree instances at once,
- preserved master remote access pairing,
- Slack/Discord/Feishu/WeChat routing still present,
- public Show Page loads without private runtime paths,
- Show Runtime cold and warm starts can be measured separately from service
  update time.

## Risks and mitigations

- **Mac host mismatch**: Incus system containers require a Linux host. Mitigate
  by treating macOS as a client and making the runner support remote Incus.
- **State migration mistakes**: master state is valuable. Mitigate with an
  explicit snapshot/export command before first Incus cutover.
- **Source sync drift**: stale files can survive in the instance. Mitigate with
  default clean sync after the first implementation is stable.
- **Dependency fingerprint bugs**: skipped installs can cause confusing runtime
  failures. Mitigate by recording fingerprints and exposing `--force-deps`.
- **Port collisions**: worktree ports must be deterministic but conflict-free.
  Mitigate with a lock file and live socket/Incus device checks.
- **Security boundary overconfidence**: system containers share the host kernel.
  Mitigate with restricted projects, resource limits, and VM mode for stronger
  isolation if needed.

## Open decisions

1. Which Linux host should own the long-running master regression instance?
2. Should temporary worktree environments include real IM credentials by
   default, or start with Web/UI-only validation unless explicitly enabled?
3. Should base image rebuilds happen manually, or via a separate CI-published
   Incus image artifact?

## Recommendation

Proceed with the Incus migration, but do it as a runtime-model replacement, not
a direct Docker command translation. The key architectural move is to make the
base environment long-lived and dependency-prepared, while source code becomes a
cheap synced input. That gives Avibe a regression setup that behaves much closer
to a real installed machine and supports both persistent master verification and
isolated worktree verification.
