from __future__ import annotations

import atexit
import asyncio
import logging
import os
import shlex
import shutil
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from config import paths
from core.process_isolation import KILL_SIGNAL, isolated_subprocess_kwargs, signal_process_tree


logger = logging.getLogger(__name__)
_RUNTIME_BIN = "avibe-show-runtime"
_RUNTIME_PACKAGE = "@avibe/show-runtime"
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
    ) -> None:
        self.command = command or os.environ.get("VIBE_SHOW_RUNTIME_BIN") or _RUNTIME_BIN
        self.workspace_root = workspace_root or paths.get_show_pages_dir()
        self.runtime_dir = runtime_dir or paths.get_runtime_dir() / "show-runtime"
        self.auto_install = _auto_install_enabled() if auto_install is None else auto_install
        self.package_spec = package_spec or os.environ.get("VIBE_SHOW_RUNTIME_PACKAGE_SPEC") or _RUNTIME_PACKAGE
        self.stdout_path = self.runtime_dir / "stdout.log"
        self.stderr_path = self.runtime_dir / "stderr.log"
        self.install_log_path = self.runtime_dir / "install.log"
        self._install_attempted = False
        self._install_reason: str | None = None
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
        managed = self._managed_bin_path()
        resolved = _resolve_executable_path(managed)
        if resolved:
            return [resolved]
        if not self.auto_install:
            self._install_reason = "runtime_command_missing"
            return None
        if self._install_attempted:
            return None
        self._install_attempted = True
        return await asyncio.to_thread(self._install_managed_runtime)

    def _install_managed_runtime(self) -> list[str] | None:
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


def _resolve_executable_path(path: Path) -> str | None:
    expanded = path.expanduser()
    return str(expanded) if expanded.exists() and os.access(expanded, os.X_OK) else None


atexit.register(stop_show_runtime_manager)
