from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from config import paths


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
    ) -> None:
        self.command = command or os.environ.get("VIBE_SHOW_RUNTIME_BIN") or "avibe-show-runtime"
        self.workspace_root = workspace_root or paths.get_show_pages_dir()
        self.runtime_dir = runtime_dir or paths.get_runtime_dir() / "show-runtime"
        self.stdout_path = self.runtime_dir / "stdout.log"
        self.stderr_path = self.runtime_dir / "stderr.log"
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
                return ShowRuntimeResult(False, reason="runtime_command_missing")
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
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


_manager: ShowRuntimeManager | None = None


def get_show_runtime_manager() -> ShowRuntimeManager:
    global _manager
    if _manager is None:
        _manager = ShowRuntimeManager()
    return _manager


def set_show_runtime_manager_for_tests(manager: ShowRuntimeManager | None) -> None:
    global _manager
    _manager = manager


def _resolve_command(command: str) -> list[str] | None:
    parts = shlex.split(command)
    if not parts:
        return None
    executable = parts[0]
    resolved = shutil.which(executable) if os.path.sep not in executable else executable
    if not resolved:
        return None
    return [resolved, *parts[1:]]
