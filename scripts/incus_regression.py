#!/usr/bin/env python3
"""Manage Incus-backed Avibe regression environments.

The runner uses Incus as a long-lived system environment, not as a Docker-like
image rebuild wrapper. Slow-moving dependencies live in a reusable base image;
Avibe source is synced into the instance and the service is restarted.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import textwrap
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


PROJECT_PREFIX = "avr-"
WORKTREE_PROJECT_PREFIX = "avr-wt-"
INSTANCE_PREFIX = "avibe-"
WORKTREE_INSTANCE_PREFIX = "avibe-wt-"
MASTER_TARGET = "master"
WORKTREE_TARGET = "worktree"
TARGETS = {MASTER_TARGET, WORKTREE_TARGET}
SERVICE_USER = "avibe"
SERVICE_HOME = f"/home/{SERVICE_USER}"
AVIBE_HOME = f"{SERVICE_HOME}/.avibe"
LEGACY_HOME = f"{SERVICE_HOME}/.vibe_remote"
SOURCE_DIR = "/opt/avibe/source"
VENV_DIR = "/opt/avibe/venv"
METADATA_DIR = "/var/lib/avibe-regression"
METADATA_PATH = f"{METADATA_DIR}/metadata.json"
FINGERPRINT_PATH = f"{METADATA_DIR}/fingerprints.json"
SERVICE_NAME = "avibe-regression.service"
DEFAULT_IMAGE = "avibe-regression-base-current"
DEFAULT_BASE_SOURCE_IMAGE = "images:ubuntu/24.04/cloud"
DEFAULT_NETWORK = "incusbr0"
DEFAULT_STORAGE_POOL = "default"
DEFAULT_UI_PORT = 5123
DEFAULT_MASTER_HOST_PORT = 15130
DEFAULT_WORKTREE_PORT_START = 15200
DEFAULT_WORKTREE_PORT_END = 15399
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,38}[a-z0-9]$")


class RegressionError(RuntimeError):
    """A user-correctable regression runner error."""


@dataclass(frozen=True)
class RegressionTarget:
    target: str
    slug: str
    project: str
    instance: str
    host_port: int
    ui_host: str
    ui_port: int


class Runner:
    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    def run(
        self,
        command: Sequence[str],
        *,
        input_bytes: bytes | None = None,
        input_text: str | None = None,
        check: bool = True,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        print("+ " + shlex.join(command))
        if self.dry_run:
            return subprocess.CompletedProcess(list(command), 0, "", "")
        kwargs: dict = {
            "check": check,
            "text": input_bytes is None,
            "capture_output": capture,
        }
        if input_bytes is not None:
            kwargs["input"] = input_bytes
        elif input_text is not None:
            kwargs["input"] = input_text
        return subprocess.run(list(command), **kwargs)

    def exists(self, command: Sequence[str]) -> bool:
        if self.dry_run:
            return False
        result = subprocess.run(list(command), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0


def incus(*args: str, project: str | None = None) -> list[str]:
    command = shlex.split(os.environ.get("INCUS_CMD", "incus"))
    if project:
        command.extend(["--project", project])
    command.extend(args)
    return command


def remote_ref(remote: str | None, name: str = "") -> str:
    if not remote:
        return name
    return f"{remote}:{name}"


def optional_remote_ref(remote: str | None) -> list[str]:
    return [remote_ref(remote)] if remote else []


def require_incus() -> None:
    command = shlex.split(os.environ.get("INCUS_CMD", "incus"))
    executable = command[0] if command else "incus"
    if shutil.which(executable) is None:
        raise RegressionError(f"The Incus CLI executable was not found: {executable}")


def validate_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        raise RegressionError("Slug must be 3-40 chars, lowercase, and contain only letters, numbers, and hyphens.")


def env_int(name: str) -> int | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise RegressionError(f"{name} must be an integer.") from exc


def current_repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return Path.cwd().resolve()
    return Path(result.stdout.strip()).resolve()


def git_common_root(repo_root: Path) -> Path:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return repo_root
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = repo_root / common
    return common.resolve().parent


def runtime_root(repo_root: Path) -> Path:
    return git_common_root(repo_root) / ".runtime" / "incus-regression"


def load_env_file(repo_root: Path, env_file: Path | None) -> Path | None:
    candidates = [env_file] if env_file else [repo_root / ".env.three-regression", git_common_root(repo_root) / ".env.three-regression"]
    for candidate in candidates:
        if not candidate:
            continue
        path = candidate.resolve()
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line.removeprefix("export ").strip()
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            os.environ.setdefault(key, value)
        return path
    return None


def branch_name(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def commit_sha(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def is_dirty(repo_root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    return bool(result.stdout.strip())


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    slug = re.sub(r"-+", "-", slug)
    if not slug or not slug[0].isalpha():
        slug = f"wt-{slug}" if slug else "wt"
    return slug[:32].strip("-")


def worktree_slug(repo_root: Path, explicit: str | None = None) -> str:
    if explicit:
        slug = slugify(explicit)
    else:
        source = branch_name(repo_root) or repo_root.name
        digest = hashlib.sha1(str(repo_root).encode("utf-8")).hexdigest()[:8]
        slug = f"{slugify(source)[:24]}-{digest}"
    validate_slug(slug)
    return slug


def project_name_for(target: str, slug: str) -> str:
    if target == MASTER_TARGET:
        return f"{PROJECT_PREFIX}master"
    validate_slug(slug)
    return f"{WORKTREE_PROJECT_PREFIX}{slug}"


def instance_name_for(target: str, slug: str) -> str:
    if target == MASTER_TARGET:
        return f"{INSTANCE_PREFIX}master"
    validate_slug(slug)
    return f"{WORKTREE_INSTANCE_PREFIX}{slug}"


def unbracket_host(host: str) -> str:
    if host.startswith("[") and host.endswith("]"):
        return host[1:-1]
    return host


def is_ipv6_host(host: str) -> bool:
    try:
        return ":" in unbracket_host(host)
    except ValueError:
        return False


def tcp_endpoint(host: str, port: int) -> str:
    return f"tcp:[{unbracket_host(host)}]:{port}" if is_ipv6_host(host) else f"tcp:{host}:{port}"


def ensure_host_port_available(host: str, port: int) -> None:
    family = socket.AF_INET6 if is_ipv6_host(host) else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((unbracket_host(host), port))
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EPERM}:
                print(f"Warning: cannot preflight privileged host port {host}:{port}; continuing.", file=sys.stderr)
                return
            raise RegressionError(f"Host port {host}:{port} is not available: {exc}") from exc


def mapping_path(repo_root: Path) -> Path:
    return runtime_root(repo_root) / "worktrees.json"


def load_worktree_mapping(repo_root: Path) -> dict:
    path = mapping_path(repo_root)
    if not path.is_file():
        return {"schema_version": 1, "worktrees": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"schema_version": 1, "worktrees": {}}
    if not isinstance(payload, dict):
        return {"schema_version": 1, "worktrees": {}}
    payload.setdefault("schema_version", 1)
    payload.setdefault("worktrees", {})
    return payload


def save_worktree_mapping(repo_root: Path, payload: dict) -> None:
    path = mapping_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def allocated_worktree_ports(repo_root: Path) -> set[int]:
    payload = load_worktree_mapping(repo_root)
    ports: set[int] = set()
    for item in (payload.get("worktrees") or {}).values():
        if isinstance(item, dict) and isinstance(item.get("host_port"), int):
            ports.add(item["host_port"])
    return ports


def allocate_worktree_port(repo_root: Path, ui_host: str, start: int, end: int, *, dry_run: bool, preflight: bool) -> int:
    used = allocated_worktree_ports(repo_root)
    for port in range(start, end + 1):
        if port in used:
            continue
        if not dry_run and preflight:
            try:
                ensure_host_port_available(ui_host, port)
            except RegressionError:
                continue
        return port
    raise RegressionError(f"No available worktree regression port in range {start}-{end}.")


def mapped_worktree_port(repo_root: Path, slug: str) -> int | None:
    item = (load_worktree_mapping(repo_root).get("worktrees") or {}).get(slug)
    if isinstance(item, dict) and isinstance(item.get("host_port"), int):
        return item["host_port"]
    return None


def resolve_target(
    args: argparse.Namespace,
    repo_root: Path,
    *,
    dry_run: bool,
    preflight_ports: bool = True,
    allocate_port: bool = True,
) -> RegressionTarget:
    if args.target not in TARGETS:
        raise RegressionError(f"target must be one of: {', '.join(sorted(TARGETS))}")
    ui_host = args.ui_host or os.environ.get("THREE_REGRESSION_PORT_BIND_HOST", "127.0.0.1")
    ui_port = args.ui_port
    if args.target == MASTER_TARGET:
        slug = "master"
        host_port = args.host_port or env_int("THREE_REGRESSION_PORT") or DEFAULT_MASTER_HOST_PORT
    else:
        slug = worktree_slug(repo_root, args.slug)
        host_port = args.host_port or mapped_worktree_port(repo_root, slug)
        if host_port is None and allocate_port:
            host_port = allocate_worktree_port(
                repo_root,
                ui_host,
                args.worktree_port_start,
                args.worktree_port_end,
                dry_run=dry_run,
                preflight=preflight_ports,
            )
        if host_port is None:
            host_port = 0
    return RegressionTarget(
        target=args.target,
        slug=slug,
        project=project_name_for(args.target, slug),
        instance=instance_name_for(args.target, slug),
        host_port=host_port,
        ui_host=ui_host,
        ui_port=ui_port,
    )


def project_create_config(target: RegressionTarget) -> list[str]:
    return [
        "features.images=false",
        "features.profiles=true",
        "features.storage.volumes=true",
        "restricted=true",
        "restricted.devices.proxy=allow",
        "limits.instances=1",
        "limits.containers=1",
        f"user.avibe_regression.target={target.target}",
        f"user.avibe_regression.slug={target.slug}",
        f"user.avibe_regression.instance={target.instance}",
        f"user.avibe_regression.host_port={target.host_port}",
    ]


def profile_yaml(storage_pool: str, network: str, cpus: str, memory: str, disk: str, processes: str) -> str:
    return textwrap.dedent(
        f"""\
        config:
          limits.cpu: "{cpus}"
          limits.memory: "{memory}"
          limits.processes: "{processes}"
        description: Avibe regression profile
        devices:
          eth0:
            name: eth0
            network: {network}
            type: nic
          root:
            path: /
            pool: {storage_pool}
            size: {disk}
            type: disk
        name: default
        """
    )


def cloud_init_user_data() -> str:
    service = textwrap.dedent(
        f"""\
        [Unit]
        Description=Avibe regression service
        Wants=network-online.target
        After=network-online.target

        [Service]
        Type=simple
        User={SERVICE_USER}
        Group={SERVICE_USER}
        WorkingDirectory={SOURCE_DIR}
        Environment=HOME={SERVICE_HOME}
        Environment=VIBE_REMOTE_HOME=
        Environment=VIBE_DEPLOYMENT_ENV=regression
        Environment=VIBE_INTERNAL_DISPATCH_SOCKET=/tmp/vibe_remote/dispatch.sock
        Environment=PYTHONUNBUFFERED=1
        Environment=PATH={VENV_DIR}/bin:{SERVICE_HOME}/.local/bin:/usr/local/bin:/usr/bin:/bin
        EnvironmentFile=-/etc/avibe-regression.env
        ExecStart={VENV_DIR}/bin/python scripts/incus_regression_supervisor.py
        Restart=on-failure
        RestartSec=2
        TimeoutStopSec=60

        [Install]
        WantedBy=multi-user.target
        """
    ).rstrip()
    helper = textwrap.dedent(
        f"""\
        #!/usr/bin/env bash
        set -euo pipefail
        echo "service={SERVICE_NAME}"
        echo "source={SOURCE_DIR}"
        echo "home={AVIBE_HOME}"
        echo "metadata={METADATA_PATH}"
        """
    ).rstrip()
    lines = [
        "#cloud-config",
        "package_update: true",
        "packages:",
        "  - bash",
        "  - ca-certificates",
        "  - curl",
        "  - git",
        "  - build-essential",
        "  - python3",
        "  - python3-pip",
        "  - python3-venv",
        "  - rsync",
        "  - sudo",
        "users:",
        f"  - name: {SERVICE_USER}",
        "    groups: sudo",
        "    shell: /bin/bash",
        "    sudo: ALL=(ALL) NOPASSWD:ALL",
        "    lock_passwd: true",
        "write_files:",
        f"  - path: /etc/systemd/system/{SERVICE_NAME}",
        "    owner: root:root",
        "    permissions: '0644'",
        "    content: |",
        yaml_block(service),
        "  - path: /usr/local/bin/avibe-regression-info",
        "    owner: root:root",
        "    permissions: '0755'",
        "    content: |",
        yaml_block(helper),
        "runcmd:",
        f"  - [mkdir, -p, {SOURCE_DIR}, {VENV_DIR}, {METADATA_DIR}, {AVIBE_HOME}]",
        f"  - [chown, -R, {SERVICE_USER}:{SERVICE_USER}, {SERVICE_HOME}, /opt/avibe, {METADATA_DIR}]",
        f"  - [ln, -sfn, {AVIBE_HOME}, {LEGACY_HOME}]",
        "  - [systemctl, daemon-reload]",
        f'final_message: "Avibe regression base is ready."',
    ]
    return "\n".join(lines)


def yaml_block(value: str, indent: int = 6) -> str:
    prefix = " " * indent
    return "\n".join(prefix + line if line else prefix for line in value.splitlines())


def proxy_device_args(target: RegressionTarget, *, remote: str | None = None) -> list[str]:
    return [
        "config",
        "device",
        "add",
        remote_ref(remote, target.instance),
        "ui",
        "proxy",
        f"listen={tcp_endpoint(target.ui_host, target.host_port)}",
        f"connect=tcp:127.0.0.1:{target.ui_port}",
    ]


def ensure_proxy_device(runner: Runner, target: RegressionTarget, *, remote: str | None) -> None:
    instance_ref = remote_ref(remote, target.instance)
    runner.run(incus("config", "device", "remove", instance_ref, "ui", project=target.project), check=False)
    runner.run(incus(*proxy_device_args(target, remote=remote), project=target.project))


def ensure_project_and_instance(
    runner: Runner,
    target: RegressionTarget,
    *,
    image: str,
    storage_pool: str,
    network: str,
    cpus: str,
    memory: str,
    disk: str,
    processes: str,
    remote: str | None,
) -> None:
    if not runner.exists(incus("project", "show", remote_ref(remote, target.project))):
        command = incus("project", "create", remote_ref(remote, target.project))
        for item in project_create_config(target):
            command.extend(["--config", item])
        runner.run(command)
        runner.run(
            incus("profile", "edit", remote_ref(remote, "default"), project=target.project),
            input_text=profile_yaml(storage_pool, network, cpus, memory, disk, processes),
        )
    if not runner.exists(incus("info", remote_ref(remote, target.instance), project=target.project)):
        runner.run(
            incus(
                "init",
                remote_ref(remote, image) if remote and ":" not in image else image,
                remote_ref(remote, target.instance),
                "--profile",
                "default",
                "--config",
                f"cloud-init.user-data={cloud_init_user_data()}",
                project=target.project,
            )
        )
    ensure_proxy_device(runner, target, remote=remote)
    runner.run(incus("start", remote_ref(remote, target.instance), project=target.project), check=False)
    runner.run(
        root_exec(
            target,
            (
                "if command -v cloud-init >/dev/null 2>&1; then cloud-init status --wait || true; fi; "
                f"mkdir -p {SOURCE_DIR} {VENV_DIR} {METADATA_DIR} {AVIBE_HOME}; "
                f"chown -R {SERVICE_USER}:{SERVICE_USER} {SERVICE_HOME} /opt/avibe {METADATA_DIR}; "
                f"ln -sfn {AVIBE_HOME} {LEGACY_HOME}; "
                "systemctl daemon-reload"
            ),
            remote=remote,
        )
    )


def tenant_exec(target: RegressionTarget, command: str, *args: str, remote: str | None = None) -> list[str]:
    bash_command = (
        "set -a; [ ! -f /etc/avibe-regression.env ] || . /etc/avibe-regression.env; "
        f"set +a; cd {shlex.quote(SOURCE_DIR)} && {command}"
    )
    return incus(
        "exec",
        remote_ref(remote, target.instance),
        "--",
        "sudo",
        "-H",
        "-u",
        SERVICE_USER,
        "--",
        "bash",
        "-lc",
        bash_command,
        "bash",
        *args,
        project=target.project,
    )


def root_exec(target: RegressionTarget, command: str, *, remote: str | None = None) -> list[str]:
    return incus("exec", remote_ref(remote, target.instance), "--", "bash", "-lc", command, project=target.project)


def source_excludes() -> tuple[str, ...]:
    return (
        ".git",
        ".runtime",
        ".worktrees",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "node_modules",
        "ui/node_modules",
        "ui/.vite",
        "ui/dist",
        "_tmp",
        "tmp",
        "logs",
    )


def is_env_file(relative: str) -> bool:
    return any(part == ".env" or part.startswith(".env.") for part in relative.split("/"))


def should_exclude(relative: str) -> bool:
    if is_env_file(relative):
        return True
    parts = relative.split("/")
    for pattern in source_excludes():
        pattern_parts = pattern.split("/")
        if relative == pattern or relative.startswith(pattern + "/"):
            return True
        if len(pattern_parts) == 1 and pattern in parts:
            return True
    return False


def build_source_tar(repo_root: Path) -> bytes:
    with tempfile.TemporaryFile() as fh:
        with tarfile.open(fileobj=fh, mode="w") as tar:
            for path in sorted(repo_root.rglob("*")):
                relative = path.relative_to(repo_root).as_posix()
                if should_exclude(relative):
                    continue
                tar.add(path, arcname=relative, recursive=False)
        fh.seek(0)
        return fh.read()


def sync_source(runner: Runner, target: RegressionTarget, repo_root: Path, *, remote: str | None, clean: bool) -> None:
    runner.run(root_exec(target, f"mkdir -p {shlex.quote(SOURCE_DIR)} && find {shlex.quote(SOURCE_DIR)} -mindepth 1 -maxdepth 1 -exec rm -rf {{}} +", remote=remote))
    runner.run(root_exec(target, f"mkdir -p {shlex.quote(SOURCE_DIR)} && chown -R {SERVICE_USER}:{SERVICE_USER} /opt/avibe", remote=remote))
    tar_bytes = b"" if runner.dry_run else build_source_tar(repo_root)
    runner.run(
        incus("exec", remote_ref(remote, target.instance), "--", "tar", "-C", SOURCE_DIR, "-xf", "-", project=target.project),
        input_bytes=tar_bytes,
    )
    runner.run(root_exec(target, f"chown -R {SERVICE_USER}:{SERVICE_USER} {shlex.quote(SOURCE_DIR)}", remote=remote))


def stop_service_for_update(runner: Runner, target: RegressionTarget, *, remote: str | None) -> None:
    runner.run(root_exec(target, f"systemctl stop {SERVICE_NAME} || true", remote=remote), check=False)


def file_hash(repo_root: Path, relative_paths: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for relative in relative_paths:
        path = repo_root / relative
        digest.update(relative.encode("utf-8"))
        if path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"<missing>")
    return digest.hexdigest()


def compute_fingerprints(repo_root: Path) -> dict:
    ui_source_parts = [
        tree_hash(repo_root / "ui" / "src"),
        tree_hash(repo_root / "ui" / "public"),
        file_hash(
            repo_root,
            [
                "ui/index.html",
                "ui/vite.config.ts",
                "ui/tsconfig.json",
                "ui/tsconfig.app.json",
                "ui/tsconfig.node.json",
            ],
        ),
    ]
    return {
        "python": file_hash(repo_root, ["pyproject.toml", "uv.lock"]),
        "ui_deps": file_hash(repo_root, ["ui/package.json", "ui/package-lock.json"]),
        "ui_source": "|".join(ui_source_parts),
        "show_runtime": "|".join(
            [
                os.environ.get("THREE_REGRESSION_SHOW_RUNTIME_SOURCE", "github-source"),
                os.environ.get("THREE_REGRESSION_SHOW_RUNTIME_GITHUB_REPO", "https://github.com/avibe-bot/vibe-show-runtime.git"),
                os.environ.get("THREE_REGRESSION_SHOW_RUNTIME_GITHUB_REF", "main"),
            ]
        ),
    }


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    if not root.exists():
        return "<missing>"
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_metadata(runner: Runner, target: RegressionTarget, repo_root: Path, fingerprints: dict, *, remote: str | None) -> None:
    payload = {
        "schema_version": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "target": target.target,
        "slug": target.slug,
        "project": target.project,
        "instance": target.instance,
        "repo_root": str(repo_root),
        "branch": branch_name(repo_root),
        "commit": commit_sha(repo_root),
        "dirty": is_dirty(repo_root),
        "fingerprints": fingerprints,
    }
    encoded = json.dumps(payload, indent=2)
    command = f"mkdir -p {METADATA_DIR} && cat > {METADATA_PATH} <<'EOF'\n{encoded}\nEOF\ncat > {FINGERPRINT_PATH} <<'EOF'\n{json.dumps(fingerprints, indent=2)}\nEOF"
    runner.run(root_exec(target, command, remote=remote))


def runtime_env_payload(repo_root: Path | None = None) -> bytes:
    scm_version = "0.0.0.dev0"
    if repo_root is not None:
        sha = commit_sha(repo_root)
        if sha:
            scm_version = f"0.0.0.dev0+{sha[:12]}"
    mappings = {
        "SETUPTOOLS_SCM_PRETEND_VERSION": scm_version,
        "SETUPTOOLS_SCM_PRETEND_VERSION_FOR_AVIBE_OS": scm_version,
        "VIBE_SHOW_RUNTIME_SOURCE": os.environ.get("THREE_REGRESSION_SHOW_RUNTIME_SOURCE", "github-source"),
        "VIBE_SHOW_RUNTIME_GITHUB_REPO": os.environ.get(
            "THREE_REGRESSION_SHOW_RUNTIME_GITHUB_REPO",
            "https://github.com/avibe-bot/vibe-show-runtime.git",
        ),
        "VIBE_SHOW_RUNTIME_GITHUB_REF": os.environ.get("THREE_REGRESSION_SHOW_RUNTIME_GITHUB_REF", "main"),
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
        "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL", ""),
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY", ""),
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL", ""),
        "OPENAI_API_BASE": os.environ.get("OPENAI_API_BASE", ""),
    }
    for key, value in os.environ.items():
        if key.startswith("THREE_REGRESSION_"):
            mappings[key] = value
    lines = [f"{key}={shlex.quote(value)}" for key, value in mappings.items() if value]
    return ("\n".join(lines) + "\n").encode("utf-8")


def require_runtime_seed_env() -> None:
    missing = [key for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY") if not os.environ.get(key, "").strip()]
    if missing:
        joined = ", ".join(missing)
        raise SystemExit(f"Missing required regression seed environment variables: {joined}")


def write_runtime_env(runner: Runner, target: RegressionTarget, *, repo_root: Path | None = None, remote: str | None) -> None:
    runner.run(
        incus(
            "exec",
            remote_ref(remote, target.instance),
            "--",
            "bash",
            "-lc",
            f"cat > /etc/avibe-regression.env && chown root:{SERVICE_USER} /etc/avibe-regression.env && chmod 0640 /etc/avibe-regression.env",
            project=target.project,
        ),
        input_bytes=b"" if runner.dry_run else runtime_env_payload(repo_root),
    )


def read_existing_fingerprints(runner: Runner, target: RegressionTarget, *, remote: str | None) -> dict:
    if runner.dry_run:
        return {}
    result = runner.run(
        root_exec(target, f"test -f {FINGERPRINT_PATH} && cat {FINGERPRINT_PATH} || true", remote=remote),
        capture=True,
        check=False,
    )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def should_seed_state(runner: Runner, target: RegressionTarget, *, reset_mode: str, remote: str | None) -> bool:
    if runner.dry_run or reset_mode != "none":
        return True
    result = runner.run(
        root_exec(target, f"test -f {AVIBE_HOME}/config/config.json", remote=remote),
        check=False,
    )
    return result.returncode != 0


def run_prepare_state(runner: Runner, target: RegressionTarget, *, reset_mode: str, remote: str | None) -> None:
    if not should_seed_state(runner, target, reset_mode=reset_mode, remote=remote):
        print("Existing Avibe state found; skipping regression state seed.")
        return
    runner.run(root_exec(target, f"rm -rf /home/{SERVICE_USER}/.regression-seed", remote=remote))
    runner.run(
        tenant_exec(
            target,
            f"{VENV_DIR}/bin/python scripts/prepare_three_regression.py --output-root /home/{SERVICE_USER}/.regression-seed --reset-mode {shlex.quote(reset_mode)}",
            remote=remote,
        )
    )
    if reset_mode == "config":
        runner.run(
            root_exec(
                target,
                f"rm -rf {AVIBE_HOME}/config {AVIBE_HOME}/state {AVIBE_HOME}/runtime",
                remote=remote,
            )
        )
    elif reset_mode == "all":
        runner.run(
            root_exec(
                target,
                "rm -rf "
                f"{AVIBE_HOME} {LEGACY_HOME} "
                f"{SERVICE_HOME}/.claude {SERVICE_HOME}/.claude.json {SERVICE_HOME}/.codex "
                f"{SERVICE_HOME}/.config/opencode {SERVICE_HOME}/.local/share/opencode",
                remote=remote,
            )
        )
    runner.run(
        root_exec(
            target,
            f"mkdir -p {AVIBE_HOME} && "
            f"cp -a /home/{SERVICE_USER}/.regression-seed/home/. {SERVICE_HOME}/ && "
            f"chown -R {SERVICE_USER}:{SERVICE_USER} {SERVICE_HOME} && "
            f"ln -sfn {AVIBE_HOME} {LEGACY_HOME} && chown -h {SERVICE_USER}:{SERVICE_USER} {LEGACY_HOME}",
            remote=remote,
        )
    )


def update_dependencies_and_build(
    runner: Runner,
    target: RegressionTarget,
    *,
    previous_fingerprints: dict,
    next_fingerprints: dict,
    force_deps: bool,
    build_ui: bool,
    force_ui: bool,
    remote: str | None,
) -> None:
    runner.run(root_exec(target, f"python3 -m venv {shlex.quote(VENV_DIR)} || true", remote=remote))
    runner.run(root_exec(target, f"chown -R {SERVICE_USER}:{SERVICE_USER} {shlex.quote(VENV_DIR)}", remote=remote))
    python_changed = (
        force_deps
        or previous_fingerprints.get("python") != next_fingerprints.get("python")
        or not previous_fingerprints
    )
    if python_changed:
        runner.run(tenant_exec(target, f"{VENV_DIR}/bin/python -m pip install -U pip wheel", remote=remote))
    else:
        print("Python dependency fingerprint unchanged; skipping pip install.")
    if build_ui:
        ui_deps_changed = force_ui or previous_fingerprints.get("ui_deps") != next_fingerprints.get("ui_deps") or not previous_fingerprints
        if ui_deps_changed:
            runner.run(tenant_exec(target, "cd ui && npm ci", remote=remote))
        else:
            print("UI dependency fingerprint unchanged; skipping npm ci.")
        if force_ui or ui_deps_changed or previous_fingerprints.get("ui_source") != next_fingerprints.get("ui_source"):
            runner.run(tenant_exec(target, "cd ui && npm run build", remote=remote))
        else:
            print("UI source fingerprint unchanged; skipping npm run build.")
    if python_changed:
        runner.run(tenant_exec(target, f"{VENV_DIR}/bin/pip install -e .", remote=remote))


def restart_and_verify(runner: Runner, target: RegressionTarget, *, remote: str | None) -> None:
    runner.run(root_exec(target, "systemctl daemon-reload", remote=remote))
    runner.run(root_exec(target, f"systemctl enable --now {SERVICE_NAME}", remote=remote))
    runner.run(root_exec(target, f"systemctl restart {SERVICE_NAME}", remote=remote))
    runner.run(
        root_exec(
            target,
            (
                "for i in $(seq 1 60); do "
                f"systemctl is-active --quiet {SERVICE_NAME} && "
                f"curl -fsS http://127.0.0.1:{target.ui_port}/health >/dev/null && "
                f"curl -fsS http://127.0.0.1:{target.ui_port}/status | grep -q '\"state\":\"running\"' && "
                "exit 0; "
                "sleep 2; "
                f"done; journalctl -u {SERVICE_NAME} --no-pager -n 120; exit 1"
            ),
            remote=remote,
        )
    )


def prepare_show_runtime(runner: Runner, target: RegressionTarget, *, remote: str | None) -> None:
    runner.run(tenant_exec(target, f"{VENV_DIR}/bin/vibe runtime prepare --strict", remote=remote))
    runner.run(tenant_exec(target, f"{VENV_DIR}/bin/vibe runtime status --json", remote=remote))


def update_worktree_mapping(repo_root: Path, target: RegressionTarget) -> None:
    if target.target != WORKTREE_TARGET:
        return
    payload = load_worktree_mapping(repo_root)
    payload.setdefault("worktrees", {})[target.slug] = {
        "path": str(repo_root),
        "project": target.project,
        "instance": target.instance,
        "host_port": target.host_port,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "branch": branch_name(repo_root),
        "commit": commit_sha(repo_root),
    }
    save_worktree_mapping(repo_root, payload)


def cmd_doctor(args: argparse.Namespace) -> int:
    if not args.dry_run:
        require_incus()
    runner = Runner(dry_run=args.dry_run)
    checks = [
        ("version", incus("version")),
        ("daemon info", incus("info", *optional_remote_ref(args.remote))),
        ("projects", incus("project", "list", *optional_remote_ref(args.remote))),
        ("storage", incus("storage", "list", *optional_remote_ref(args.remote))),
        ("network", incus("network", "list", *optional_remote_ref(args.remote))),
    ]
    failed: list[str] = []
    for name, command in checks:
        result = runner.run(command, check=False)
        if result.returncode != 0:
            failed.append(name)
    if failed:
        print("Failed checks: " + ", ".join(failed), file=sys.stderr)
        return 1
    return 0


def cmd_init_host(args: argparse.Namespace) -> int:
    if not args.dry_run:
        require_incus()
    runner = Runner(dry_run=args.dry_run)
    if args.minimal:
        if args.remote:
            raise RegressionError("init-host --minimal must run on the Incus host itself, not through a remote.")
        runner.run(incus("admin", "init", "--minimal"))
    return cmd_doctor(args)


def cmd_build_base(args: argparse.Namespace) -> int:
    if not args.dry_run:
        require_incus()
    runner = Runner(dry_run=args.dry_run)
    runner.run(incus("delete", remote_ref(args.remote, args.temp_instance), "--force"), check=False)
    runner.run(
        incus(
            "launch",
            remote_ref(args.remote, args.source_image) if args.remote and ":" not in args.source_image else args.source_image,
            remote_ref(args.remote, args.temp_instance),
            "--storage",
            args.storage_pool,
            "--network",
            args.network,
        )
    )
    runner.run(
        incus(
            "exec",
            remote_ref(args.remote, args.temp_instance),
            "--",
            "bash",
            "-lc",
            textwrap.dedent(
                """\
                set -euo pipefail
                apt-get update
                apt-get install -y bash ca-certificates curl git build-essential python3 python3-pip python3-venv rsync sudo
                curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
                apt-get install -y nodejs
                npm install -g askill @anthropic-ai/claude-code @openai/codex
                curl -fsSL https://opencode.ai/install | bash
                ln -sfn /root/.opencode/bin/opencode /usr/local/bin/opencode || true
                node --version
                npm --version
                """
            ),
        )
    )
    runner.run(
        incus(
            "exec",
            remote_ref(args.remote, args.temp_instance),
            "--",
            "bash",
            "-lc",
            "cloud-init clean --logs || true",
        )
    )
    runner.run(incus("stop", remote_ref(args.remote, args.temp_instance), "--force"), check=False)
    runner.run(incus("image", "delete", remote_ref(args.remote, args.image)), check=False)
    publish_command = incus("publish", remote_ref(args.remote, args.temp_instance))
    if args.remote:
        publish_command.append(remote_ref(args.remote))
    publish_command.extend(["--alias", args.image])
    runner.run(publish_command)
    runner.run(incus("delete", remote_ref(args.remote, args.temp_instance), "--force"))
    return 0


def cmd_up(args: argparse.Namespace) -> int:
    repo_root = current_repo_root()
    load_env_file(repo_root, args.env_file)
    if not args.dry_run:
        require_incus()
    target = resolve_target(args, repo_root, dry_run=args.dry_run, preflight_ports=args.remote is None)
    runner = Runner(dry_run=args.dry_run)
    target_exists = runner.exists(incus("info", remote_ref(args.remote, target.instance), project=target.project))
    if not args.dry_run and not target_exists and args.remote is None:
        ensure_host_port_available(target.ui_host, target.host_port)
    ensure_project_and_instance(
        runner,
        target,
        image=args.image,
        storage_pool=args.storage_pool,
        network=args.network,
        cpus=args.cpus,
        memory=args.memory,
        disk=args.disk,
        processes=args.processes,
        remote=args.remote,
    )
    stop_service_for_update(runner, target, remote=args.remote)
    write_runtime_env(runner, target, repo_root=repo_root, remote=args.remote)
    if should_seed_state(runner, target, reset_mode=args.reset_mode, remote=args.remote):
        require_runtime_seed_env()
    sync_source(runner, target, repo_root, remote=args.remote, clean=args.clean)
    fingerprints = compute_fingerprints(repo_root)
    previous_fingerprints = read_existing_fingerprints(runner, target, remote=args.remote)
    update_dependencies_and_build(
        runner,
        target,
        previous_fingerprints=previous_fingerprints,
        next_fingerprints=fingerprints,
        force_deps=args.force_deps,
        build_ui=not args.no_build_ui,
        force_ui=True,
        remote=args.remote,
    )
    run_prepare_state(runner, target, reset_mode=args.reset_mode, remote=args.remote)
    write_metadata(runner, target, repo_root, fingerprints, remote=args.remote)
    restart_and_verify(runner, target, remote=args.remote)
    prepare_show_runtime(runner, target, remote=args.remote)
    update_worktree_mapping(repo_root, target)
    print_summary(target)
    return 0


def print_summary(target: RegressionTarget) -> None:
    print("")
    print("Incus regression environment is ready:")
    print(f"  URL: http://{target.ui_host}:{target.host_port}")
    print(f"  Target: {target.target}")
    print(f"  Project: {target.project}")
    print(f"  Instance: {target.instance}")
    print(f"  Show Runtime source: {os.environ.get('THREE_REGRESSION_SHOW_RUNTIME_SOURCE', 'github-source')}")


def cmd_status(args: argparse.Namespace) -> int:
    repo_root = current_repo_root()
    target = resolve_target(args, repo_root, dry_run=args.dry_run, allocate_port=False, preflight_ports=False)
    if not args.dry_run:
        require_incus()
    runner = Runner(dry_run=args.dry_run)
    failed = 0
    for command in (
        incus("list", *optional_remote_ref(args.remote), project=target.project),
        root_exec(target, "avibe-regression-info && systemctl status avibe-regression --no-pager", remote=args.remote),
        tenant_exec(target, f"{VENV_DIR}/bin/vibe status", remote=args.remote),
    ):
        result = runner.run(command, check=False)
        failed += 1 if result.returncode != 0 else 0
    return 1 if failed else 0


def cmd_logs(args: argparse.Namespace) -> int:
    repo_root = current_repo_root()
    target = resolve_target(args, repo_root, dry_run=args.dry_run, allocate_port=False, preflight_ports=False)
    if not args.dry_run:
        require_incus()
    Runner(dry_run=args.dry_run).run(
        root_exec(target, f"journalctl -u {SERVICE_NAME} -f --no-pager", remote=args.remote),
        check=False,
    )
    return 0


def cmd_shell(args: argparse.Namespace) -> int:
    repo_root = current_repo_root()
    target = resolve_target(args, repo_root, dry_run=args.dry_run, allocate_port=False, preflight_ports=False)
    if not args.dry_run:
        require_incus()
    Runner(dry_run=args.dry_run).run(tenant_exec(target, "exec bash -l", remote=args.remote))
    return 0


def cmd_down(args: argparse.Namespace) -> int:
    repo_root = current_repo_root()
    target = resolve_target(args, repo_root, dry_run=args.dry_run, allocate_port=False, preflight_ports=False)
    if not args.dry_run:
        require_incus()
    Runner(dry_run=args.dry_run).run(incus("stop", remote_ref(args.remote, target.instance), project=target.project), check=False)
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    repo_root = current_repo_root()
    target = resolve_target(args, repo_root, dry_run=args.dry_run, allocate_port=False, preflight_ports=False)
    if target.target == MASTER_TARGET and not args.yes:
        raise RegressionError("Deleting the master regression environment requires --yes.")
    if not args.dry_run:
        require_incus()
    runner = Runner(dry_run=args.dry_run)
    runner.run(incus("delete", remote_ref(args.remote, target.instance), "--force", project=target.project), check=False)
    runner.run(incus("project", "delete", remote_ref(args.remote, target.project)), check=False)
    if target.target == WORKTREE_TARGET and not args.dry_run:
        payload = load_worktree_mapping(repo_root)
        (payload.get("worktrees") or {}).pop(target.slug, None)
        save_worktree_mapping(repo_root, payload)
    return 0


def cmd_cleanup_stale(args: argparse.Namespace) -> int:
    repo_root = current_repo_root()
    payload = load_worktree_mapping(repo_root)
    stale = []
    for slug, item in (payload.get("worktrees") or {}).items():
        path = Path(str(item.get("path", "")))
        if not path.exists():
            stale.append((slug, item))
    if not stale:
        print("No stale worktree regression environments found.")
        return 0
    if not args.yes and not args.dry_run:
        raise RegressionError("Stale worktree cleanup requires --yes.")
    runner = Runner(dry_run=args.dry_run)
    for slug, item in stale:
        project = str(item.get("project") or project_name_for(WORKTREE_TARGET, slug))
        instance = str(item.get("instance") or instance_name_for(WORKTREE_TARGET, slug))
        runner.run(incus("delete", remote_ref(args.remote, instance), "--force", project=project), check=False)
        runner.run(incus("project", "delete", remote_ref(args.remote, project)), check=False)
        if not args.dry_run:
            payload["worktrees"].pop(slug, None)
    if not args.dry_run:
        save_worktree_mapping(repo_root, payload)
    return 0


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true", help="Print commands without changing Incus.")
    parser.add_argument("--remote", help="Optional Incus remote name.")


def add_target_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", choices=sorted(TARGETS), default=MASTER_TARGET)
    parser.add_argument("--slug", help="Explicit worktree slug for --target worktree.")
    parser.add_argument("--host-port", type=int, help="Host port for the Web UI proxy.")
    parser.add_argument("--ui-host", help="Host/interface for the Incus UI proxy. Defaults to THREE_REGRESSION_PORT_BIND_HOST or 127.0.0.1 after env loading.")
    parser.add_argument("--ui-port", type=int, default=DEFAULT_UI_PORT)
    parser.add_argument("--worktree-port-start", type=int, default=DEFAULT_WORKTREE_PORT_START)
    parser.add_argument("--worktree-port-end", type=int, default=DEFAULT_WORKTREE_PORT_END)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="Check Incus readiness.")
    add_common(doctor)
    doctor.set_defaults(func=cmd_doctor)

    init_host = subparsers.add_parser("init-host", help="Optionally initialize Incus and check readiness.")
    init_host.add_argument("--minimal", action="store_true")
    add_common(init_host)
    init_host.set_defaults(func=cmd_init_host)

    build_base = subparsers.add_parser("build-base", help="Build/publish the reusable regression base image.")
    build_base.add_argument("--image", default=DEFAULT_IMAGE)
    build_base.add_argument("--source-image", default=DEFAULT_BASE_SOURCE_IMAGE)
    build_base.add_argument("--temp-instance", default="avibe-regression-base-build")
    build_base.add_argument("--storage-pool", default=DEFAULT_STORAGE_POOL)
    build_base.add_argument("--network", default=DEFAULT_NETWORK)
    add_common(build_base)
    build_base.set_defaults(func=cmd_build_base)

    up = subparsers.add_parser("up", help="Create/update a regression environment.")
    add_common(up)
    add_target_args(up)
    up.add_argument("--env-file", type=Path)
    up.add_argument("--image", default=DEFAULT_IMAGE)
    up.add_argument("--storage-pool", default=DEFAULT_STORAGE_POOL)
    up.add_argument("--network", default=DEFAULT_NETWORK)
    up.add_argument("--cpus", default="4")
    up.add_argument("--memory", default="8GiB")
    up.add_argument("--disk", default="80GiB")
    up.add_argument("--processes", default="8192")
    up.add_argument("--reset-mode", choices=["none", "config", "all"], default="none")
    up.add_argument("--clean", action="store_true", help="Remove stale files before source sync.")
    up.add_argument("--force-deps", action="store_true", help="Force Python dependency refresh.")
    up.add_argument("--no-build-ui", action="store_true", help="Skip npm ci/build for UI assets.")
    up.set_defaults(func=cmd_up)

    for name, func in (
        ("status", cmd_status),
        ("logs", cmd_logs),
        ("shell", cmd_shell),
        ("down", cmd_down),
        ("delete", cmd_delete),
    ):
        sub = subparsers.add_parser(name)
        add_common(sub)
        add_target_args(sub)
        if name == "delete":
            sub.add_argument("--yes", action="store_true")
        sub.set_defaults(func=func)

    cleanup = subparsers.add_parser("cleanup-stale", help="Delete environments for missing worktree paths.")
    add_common(cleanup)
    cleanup.add_argument("--yes", action="store_true")
    cleanup.set_defaults(func=cmd_cleanup_stale)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except RegressionError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
