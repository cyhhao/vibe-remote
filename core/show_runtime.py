from __future__ import annotations

import atexit
import asyncio
import importlib.resources as package_resources
import logging
import os
import shlex
import shutil
import signal
import subprocess
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from sysconfig import get_platform
from typing import Any

import httpx

from config import paths
from core.process_isolation import KILL_SIGNAL, isolated_subprocess_kwargs, signal_process_tree


logger = logging.getLogger(__name__)
_RUNTIME_BIN = "avibe-show-runtime"
_RUNTIME_PACKAGE = "@avibe/show-runtime"
_RUNTIME_ARCHIVE_PREFIX = "vibe-show-runtime-node"
_RUNTIME_ARCHIVE_RELEASE_BASE_URL = "https://github.com/avibe-bot/vibe-show-runtime/releases/latest/download"
_RUNTIME_GITHUB_REPO = "https://github.com/avibe-bot/vibe-show-runtime.git"
_RUNTIME_GITHUB_REF = "main"
_RUNTIME_SOURCE_ARCHIVE = "archive"
_RUNTIME_SOURCE_GITHUB = "github"
_RUNTIME_SOURCE_NPM = "npm"
_FALSE_VALUES = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class ShowRuntimeResult:
    available: bool
    base_url: str | None = None
    reason: str | None = None


class ShowRuntimeManager:
    def __init__(
        self,
        *,
        command: str | None = None,
        workspace_root: Path | None = None,
        runtime_dir: Path | None = None,
        auto_install: bool | None = None,
        package_spec: str | None = None,
        runtime_source: str | None = None,
        archive_path: Path | str | None = None,
        archive_url: str | None = None,
        github_repo: str | None = None,
        github_ref: str | None = None,
    ) -> None:
        self.command = command or os.environ.get("VIBE_SHOW_RUNTIME_BIN") or _RUNTIME_BIN
        self.workspace_root = workspace_root or paths.get_show_pages_dir()
        self.runtime_dir = runtime_dir or paths.get_runtime_dir() / "show-runtime"
        self.auto_install = _auto_install_enabled() if auto_install is None else auto_install
        self.package_spec = package_spec or os.environ.get("VIBE_SHOW_RUNTIME_PACKAGE_SPEC") or _RUNTIME_PACKAGE
        self.runtime_source = _normalize_runtime_source(runtime_source or os.environ.get("VIBE_SHOW_RUNTIME_SOURCE"))
        archive_path_value = archive_path or os.environ.get("VIBE_SHOW_RUNTIME_ARCHIVE_PATH")
        self.archive_path = Path(archive_path_value).expanduser() if archive_path_value else None
        self.archive_url = archive_url if archive_url is not None else os.environ.get(
            "VIBE_SHOW_RUNTIME_ARCHIVE_URL",
            _default_runtime_archive_url(),
        )
        self.github_repo = github_repo or os.environ.get("VIBE_SHOW_RUNTIME_GITHUB_REPO") or _RUNTIME_GITHUB_REPO
        self.github_ref = github_ref or os.environ.get("VIBE_SHOW_RUNTIME_GITHUB_REF") or _RUNTIME_GITHUB_REF
        self.stdout_path = self.runtime_dir / "stdout.log"
        self.stderr_path = self.runtime_dir / "stderr.log"
        self.install_log_path = self.runtime_dir / "install.log"
        self._install_attempted = False
        self._install_reason: str | None = None
        self._managed_command: list[str] | None = None
        self._process: subprocess.Popen[str] | None = None
        self._base_url: str | None = None
        self._lock = asyncio.Lock()

    async def ensure(self) -> ShowRuntimeResult:
        if self._base_url and await self._healthy(self._base_url):
            return ShowRuntimeResult(True, self._base_url)
        async with self._lock:
            if self._base_url and await self._healthy(self._base_url):
                return ShowRuntimeResult(True, self._base_url)
            self.stop()
            command = _resolve_command(self.command)
            if not command:
                command = await self._resolve_managed_command()
            if not command:
                return ShowRuntimeResult(False, reason=self._install_reason or "runtime_command_missing")
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            self.workspace_root.mkdir(parents=True, exist_ok=True)
            with self.stdout_path.open("w", encoding="utf-8") as stdout, self.stderr_path.open(
                "w", encoding="utf-8"
            ) as stderr:
                self._process = subprocess.Popen(
                    [
                        *command,
                        "--workspace-root",
                        str(self.workspace_root),
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "0",
                    ],
                    stdout=stdout,
                    stderr=stderr,
                    text=True,
                    **isolated_subprocess_kwargs(),
                )
            base_url = await self._read_startup_url()
            if not base_url:
                self.stop()
                return ShowRuntimeResult(False, reason="runtime_start_failed")
            self._base_url = base_url
            return ShowRuntimeResult(True, base_url)

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ) -> httpx.Response:
        ready = await self.ensure()
        if not ready.available or not ready.base_url:
            raise RuntimeError(ready.reason or "show runtime unavailable")
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            return await client.request(method, f"{ready.base_url}{path}", headers=headers, content=body)

    async def websocket_url(self, path: str) -> str:
        ready = await self.ensure()
        if not ready.available or not ready.base_url:
            raise RuntimeError(ready.reason or "show runtime unavailable")
        return f"{ready.base_url.replace('http://', 'ws://', 1).replace('https://', 'wss://', 1)}{path}"

    async def _healthy(self, base_url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(2.0, connect=0.5)) as client:
                response = await client.get(f"{base_url}/health")
            return response.status_code == 200
        except Exception:
            return False

    async def _read_startup_url(self) -> str | None:
        deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < deadline:
            if self._process and self._process.poll() is not None:
                return None
            try:
                text = self.stdout_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                text = ""
            for line in reversed(text.splitlines()):
                marker = "Vibe Show Runtime listening at "
                if marker in line:
                    return line.split(marker, 1)[1].strip()
            await asyncio.sleep(0.05)
        return None

    def stop(self) -> None:
        process = self._process
        self._process = None
        self._base_url = None
        if not process or process.poll() is not None:
            return
        signal_process_tree(process, signal.SIGTERM, logger, "show runtime")
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            signal_process_tree(process, KILL_SIGNAL, logger, "show runtime")

    async def _resolve_managed_command(self) -> list[str] | None:
        if self.command != _RUNTIME_BIN:
            self._install_reason = "runtime_command_missing"
            return None
        if self.runtime_source == _RUNTIME_SOURCE_ARCHIVE:
            command = self._installed_archive_runtime_command()
            if command:
                self._managed_command = command
                return command
            if self._managed_command:
                return self._managed_command
        else:
            managed = self._managed_bin_path()
            resolved = _resolve_executable_path(managed)
            if resolved:
                return [resolved]
            if self._managed_command:
                return self._managed_command
        if self.runtime_source == _RUNTIME_SOURCE_GITHUB:
            command = self._installed_github_runtime_command()
            if command:
                self._managed_command = command
                return command
        if not self.auto_install:
            self._install_reason = "runtime_command_missing"
            return None
        if self._install_attempted:
            return None
        self._install_attempted = True
        command = await asyncio.to_thread(self._install_managed_runtime)
        if command:
            self._managed_command = command
        return command

    def _install_managed_runtime(self) -> list[str] | None:
        if self.runtime_source == _RUNTIME_SOURCE_ARCHIVE:
            return self._install_archive_runtime()
        if self.runtime_source == _RUNTIME_SOURCE_GITHUB:
            return self._install_github_runtime()
        if self.runtime_source == _RUNTIME_SOURCE_NPM:
            return self._install_npm_runtime()
        self._install_reason = "runtime_source_unsupported"
        return None

    def _installed_archive_runtime_command(self) -> list[str] | None:
        node = _resolve_node_command()
        if not node:
            return None
        return self._archive_runtime_command(self._archive_install_dir(), node)

    def _install_archive_runtime(self) -> list[str] | None:
        node = _resolve_node_command()
        if not node:
            self._install_reason = "runtime_node_missing"
            return None
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        install_dir = self._archive_install_dir()
        existing_command = self._archive_runtime_command(install_dir, node)
        archive = self._resolve_prebuilt_archive()
        if not archive:
            return self._reuse_existing_archive_runtime(existing_command)
        tmp_dir = Path(tempfile.mkdtemp(prefix="prebuilt-", dir=self.runtime_dir))
        try:
            with tarfile.open(archive, "r:gz") as tar:
                _safe_extract_tar(tar, tmp_dir)
            command = self._archive_runtime_command(tmp_dir, node)
            if not command:
                self._install_reason = "runtime_install_missing_bin"
                return self._reuse_existing_archive_runtime(existing_command)
            if install_dir.exists():
                shutil.rmtree(install_dir)
            install_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_dir), str(install_dir))
            self._install_reason = None
            return self._archive_runtime_command(install_dir, node)
        except Exception:
            logger.exception("Failed to install prebuilt Show Runtime")
            self._install_reason = "runtime_install_failed"
            return self._reuse_existing_archive_runtime(existing_command)
        finally:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _resolve_prebuilt_archive(self) -> Path | None:
        if self.archive_path:
            if self.archive_path.exists():
                return self.archive_path
            self._install_reason = "runtime_archive_missing"
            return None
        packaged = self._copy_packaged_runtime_archive()
        if packaged:
            return packaged
        if not self.archive_url:
            self._install_reason = "runtime_archive_missing"
            return None
        return self._download_runtime_archive(self.archive_url)

    def _copy_packaged_runtime_archive(self) -> Path | None:
        try:
            resource = package_resources.files("vibe").joinpath("show_runtime", _runtime_archive_name())
        except Exception:
            return None
        if not resource.is_file():
            return None
        target = self.runtime_dir / "downloads" / _runtime_archive_name()
        target.parent.mkdir(parents=True, exist_ok=True)
        with resource.open("rb") as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)
        return target

    def _download_runtime_archive(self, archive_url: str) -> Path | None:
        target = self.runtime_dir / "downloads" / _runtime_archive_name()
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(archive_url, timeout=60) as response, target.open("wb") as destination:
                shutil.copyfileobj(response, destination)
        except Exception:
            logger.exception("Failed to download prebuilt Show Runtime from %s", archive_url)
            self._install_reason = "runtime_archive_download_failed"
            return None
        return target

    def _archive_install_dir(self) -> Path:
        return self.runtime_dir / "prebuilt" / "current"

    def _archive_runtime_command(self, install_dir: Path, node: list[str]) -> list[str] | None:
        cli_path = install_dir / "node_modules" / "@avibe" / "show-runtime" / "dist" / "cli.js"
        if not cli_path.exists():
            return None
        return [*node, str(cli_path)]

    def _reuse_existing_archive_runtime(self, command: list[str] | None) -> list[str] | None:
        if command:
            self._install_reason = None
            return command
        return None

    def _installed_github_runtime_command(self) -> list[str] | None:
        node = _resolve_node_command()
        if not node:
            return None
        return self._github_runtime_command(self._github_source_dir(), node)

    def _install_github_runtime(self) -> list[str] | None:
        node = _resolve_node_command()
        if not node:
            self._install_reason = "runtime_node_missing"
            return None
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        source_dir = self._github_source_dir()
        existing_command = self._github_runtime_command(source_dir, node)
        git = _resolve_command("git")
        npm = _resolve_command("npm")
        if not git:
            if existing_command:
                self._install_reason = None
                return existing_command
            self._install_reason = "runtime_git_missing"
            return None
        if not npm:
            if existing_command:
                self._install_reason = None
                return existing_command
            self._install_reason = "runtime_npm_missing"
            return None
        if not source_dir.exists():
            source_dir.parent.mkdir(parents=True, exist_ok=True)
            if not self._run_install_command([*git, "clone", "--depth", "1", "--branch", self.github_ref, self.github_repo, str(source_dir)]):
                return None
        else:
            if not self._run_install_command([*git, "-C", str(source_dir), "fetch", "--depth", "1", "origin", self.github_ref]):
                return self._reuse_existing_github_runtime(existing_command)
            if not self._run_install_command([*git, "-C", str(source_dir), "checkout", "FETCH_HEAD"]):
                return self._reuse_existing_github_runtime(existing_command)
        if not self._run_install_command([*npm, "ci"], cwd=source_dir):
            return self._reuse_existing_github_runtime(existing_command)
        if not self._run_install_command([*npm, "run", "build"], cwd=source_dir):
            return self._reuse_existing_github_runtime(existing_command)
        command = self._github_runtime_command(source_dir, node)
        if not command:
            self._install_reason = "runtime_install_missing_bin"
            return None
        return command

    def _github_runtime_command(self, source_dir: Path, node: list[str]) -> list[str] | None:
        cli_path = source_dir / "packages" / "runtime" / "dist" / "cli.js"
        if not cli_path.exists():
            return None
        return [*node, str(cli_path)]

    def _reuse_existing_github_runtime(self, command: list[str] | None) -> list[str] | None:
        if command:
            self._install_reason = None
            return command
        return None

    def _install_npm_runtime(self) -> list[str] | None:
        npm = _resolve_command("npm")
        if not npm:
            self._install_reason = "runtime_npm_missing"
            return None
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        install_root = self.runtime_dir / "package"
        install_root.mkdir(parents=True, exist_ok=True)
        package_json = install_root / "package.json"
        if not package_json.exists():
            package_json.write_text('{"private":true,"type":"module"}\n', encoding="utf-8")
        with self.install_log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(
                [
                    *npm,
                    "install",
                    "--prefix",
                    str(install_root),
                    "--no-audit",
                    "--no-fund",
                    self.package_spec,
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=180,
                check=False,
                **isolated_subprocess_kwargs(),
            )
        if result.returncode != 0:
            self._install_reason = "runtime_install_failed"
            return None
        resolved = _resolve_executable_path(self._managed_bin_path())
        if not resolved:
            self._install_reason = "runtime_install_missing_bin"
            return None
        return [resolved]

    def _managed_bin_path(self) -> Path:
        suffix = ".cmd" if os.name == "nt" else ""
        return self.runtime_dir / "package" / "node_modules" / ".bin" / f"{_RUNTIME_BIN}{suffix}"

    def _github_source_dir(self) -> Path:
        repo_slug = self.github_repo.removesuffix(".git").rstrip("/").rsplit("/", 2)[-2:]
        repo_part = "_".join(repo_slug) if len(repo_slug) == 2 else "vibe-show-runtime"
        ref_part = _safe_path_part(self.github_ref)
        return self.runtime_dir / "source" / "github" / repo_part / ref_part

    def _run_install_command(self, command: list[str], *, cwd: Path | None = None) -> bool:
        with self.install_log_path.open("a", encoding="utf-8") as log:
            log.write(f"$ {' '.join(command)}\n")
            result = subprocess.run(
                command,
                cwd=str(cwd) if cwd else None,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=300,
                check=False,
                **isolated_subprocess_kwargs(),
            )
        if result.returncode != 0:
            self._install_reason = "runtime_install_failed"
            return False
        return True


_manager: ShowRuntimeManager | None = None


def get_show_runtime_manager() -> ShowRuntimeManager:
    global _manager
    if _manager is None:
        _manager = ShowRuntimeManager()
    return _manager


def stop_show_runtime_manager() -> None:
    if _manager is not None:
        _manager.stop()


def set_show_runtime_manager_for_tests(manager: ShowRuntimeManager | None) -> None:
    global _manager
    _manager = manager


def _auto_install_enabled() -> bool:
    value = os.environ.get("VIBE_SHOW_RUNTIME_AUTO_INSTALL")
    return value is None or value.strip().lower() not in _FALSE_VALUES


def _normalize_runtime_source(value: str | None) -> str:
    normalized = (value or _RUNTIME_SOURCE_ARCHIVE).strip().lower()
    return normalized or _RUNTIME_SOURCE_ARCHIVE


def _runtime_archive_name() -> str:
    return f"{_RUNTIME_ARCHIVE_PREFIX}-{_runtime_platform_tag()}.tgz"


def _default_runtime_archive_url() -> str:
    return f"{_RUNTIME_ARCHIVE_RELEASE_BASE_URL}/{_runtime_archive_name()}"


def _runtime_platform_tag() -> str:
    raw = get_platform().lower()
    machine = raw.rsplit("-", 1)[-1]
    if machine in {"amd64", "x86_64"}:
        arch = "x64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        arch = machine
    if raw.startswith("macosx"):
        os_name = "darwin"
    elif raw.startswith("linux"):
        os_name = "linux"
    elif raw.startswith("win"):
        os_name = "win32"
    else:
        os_name = os.name
    return f"{os_name}-{arch}"


def _safe_extract_tar(tar: tarfile.TarFile, destination: Path) -> None:
    destination_resolved = destination.resolve()
    for member in tar.getmembers():
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"Unsafe archive member type: {member.name}")
        target = (destination / member.name).resolve()
        if target != destination_resolved and destination_resolved not in target.parents:
            raise ValueError(f"Unsafe archive member path: {member.name}")
    try:
        tar.extractall(destination, filter="data")
    except TypeError:
        tar.extractall(destination)


def _safe_path_part(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value.strip())
    return cleaned or "main"


def _resolve_command(command: str) -> list[str] | None:
    parts = shlex.split(command)
    if not parts:
        return None
    executable = parts[0]
    if os.path.sep in executable or (os.altsep is not None and os.altsep in executable):
        path = Path(executable).expanduser()
        resolved = str(path) if path.exists() and os.access(path, os.X_OK) else None
    else:
        resolved = shutil.which(executable)
    if not resolved:
        return None
    return [resolved, *parts[1:]]


def _resolve_node_command() -> list[str] | None:
    configured = os.environ.get("VIBE_SHOW_RUNTIME_NODE_BIN")
    if configured:
        return _resolve_command(configured)
    return _resolve_command("node")


def _resolve_executable_path(path: Path) -> str | None:
    expanded = path.expanduser()
    return str(expanded) if expanded.exists() and os.access(expanded, os.X_OK) else None


atexit.register(stop_show_runtime_manager)
