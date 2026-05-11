# Incus tenants for Vibe Remote

This scaffold creates one Vibe Remote tenant per Incus project. It is meant for
small hosted bot deployments and operator-run pilots before the full avibe.bot
control plane exists.

Use containers for trusted pilots. Use `--type vm` when the tenant is less
trusted or needs a stronger isolation boundary.

## Host setup

Install Incus on the host, then run:

```bash
python3 scripts/incus_tenant.py init-host --minimal
```

`--minimal` runs `incus admin init --minimal`. Skip it if the host is already
initialized. The script assumes the default storage pool is `default` and the
managed bridge is `incusbr0`; override them per tenant if your host uses
different names.

Check the host without changing it:

```bash
python3 scripts/incus_tenant.py doctor
```

## Create a tenant

```bash
python3 scripts/incus_tenant.py create alice \
  --cpus 2 \
  --memory 4GiB \
  --disk 30GiB \
  --processes 4096 \
  --backend codex \
  --ui-host-port 15123
```

This creates:

- Incus project: `vr-alice`
- Instance: `vibe`
- Linux user inside the instance: `vibe`
- Work directory: `/home/vibe/work`
- Vibe Remote Web UI inside the instance: port `5123`
- Optional host Web UI proxy: `http://127.0.0.1:15123`

Wait for first boot:

```bash
python3 scripts/incus_tenant.py wait-ready alice
```

Then open the Web UI URL printed by `create` and complete the normal Vibe
Remote setup wizard for that tenant.

## Operate tenants

```bash
python3 scripts/incus_tenant.py status alice
python3 scripts/incus_tenant.py shell alice
python3 scripts/incus_tenant.py exec alice -- pwd
python3 scripts/incus_tenant.py stop alice
python3 scripts/incus_tenant.py start alice
python3 scripts/incus_tenant.py restart alice
python3 scripts/incus_tenant.py list
```

Delete a tenant and all tenant data:

```bash
python3 scripts/incus_tenant.py delete alice
```

Use `--dry-run` on any command to print the Incus commands without running
them.

The Web UI proxy binds to `127.0.0.1` by default. To expose it on another host
address, pass `--ui-host <address>` explicitly and protect it with a firewall or
reverse proxy.

## Resource model

The scaffold sets limits on both the Incus project and the project `default`
profile:

- `limits.cpu`
- `limits.memory`
- `limits.processes`
- root disk `size`
- one instance per project

Example with a larger tenant:

```bash
python3 scripts/incus_tenant.py create buildbot \
  --cpus 8 \
  --memory 16GiB \
  --disk 120GiB \
  --processes 16384 \
  --ui-host-port 15124
```

Example with a VM tenant:

```bash
python3 scripts/incus_tenant.py create paid-01 \
  --type vm \
  --cpus 4 \
  --memory 8GiB \
  --disk 80GiB \
  --ui-host-port 15125
```

## Install source

By default, cloud-init installs the latest Vibe Remote via the public installer.
To test a branch or fork:

```bash
python3 scripts/incus_tenant.py create branch-test \
  --install-package-spec 'git+https://github.com/cyhhao/vibe-remote.git@master' \
  --ui-host-port 15126
```

## Security boundary

Tenants get root-like control inside their own Ubuntu environment, not on the
host. Do not give tenants access to the host Incus socket, the `incus-admin`
group, host Docker socket, host SSH keys, or host secret files.

Container tenants share the host kernel. For arbitrary untrusted paid users,
prefer `--type vm`, strict resource limits, egress policy, backup/restore
procedures, and operational monitoring before calling the setup production
SaaS-ready.
