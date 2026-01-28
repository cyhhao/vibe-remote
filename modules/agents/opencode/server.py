"""OpenCode server lifecycle + HTTP API wrapper."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import time
import threading
from asyncio.subprocess import Process
from typing import Any, Dict, List, Optional

import aiohttp

from config import paths

logger = logging.getLogger(__name__)

DEFAULT_OPENCODE_PORT = 4096
DEFAULT_OPENCODE_HOST = "127.0.0.1"
SERVER_START_TIMEOUT = 15


class OpenCodeServerManager:
    """Manages a singleton OpenCode server process shared across all working directories."""

    _instance: Optional["OpenCodeServerManager"] = None
    _class_lock: threading.Lock = threading.Lock()

    def __init__(
        self,
        binary: str = "opencode",
        port: int = DEFAULT_OPENCODE_PORT,
        request_timeout_seconds: int = 60,
    ):
        self.binary = binary
        self.port = port
        self.request_timeout_seconds = request_timeout_seconds
        self.host = DEFAULT_OPENCODE_HOST
        self._process: Optional[Process] = None
        self._base_url: Optional[str] = None
        self._http_session: Optional[aiohttp.ClientSession] = None
        self._http_session_loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop: Optional[asyncio.AbstractEventLoop] = None
        self._pid_file = paths.get_logs_dir() / "opencode_server.json"

    def _get_lock(self) -> asyncio.Lock:
        """Get or create an asyncio.Lock bound to the current event loop."""
        current_loop = asyncio.get_event_loop()
        if self._lock is None or self._lock_loop is not current_loop:
            self._lock = asyncio.Lock()
            self._lock_loop = current_loop
        return self._lock

    @classmethod
    async def get_instance(
        cls,
        binary: str = "opencode",
        port: int = DEFAULT_OPENCODE_PORT,
        request_timeout_seconds: int = 60,
    ) -> "OpenCodeServerManager":
        with cls._class_lock:
            if cls._instance is None:
                cls._instance = cls(
                    binary=binary,
                    port=port,
                    request_timeout_seconds=request_timeout_seconds,
                )
            elif (
                cls._instance.binary != binary
                or cls._instance.port != port
                or cls._instance.request_timeout_seconds != request_timeout_seconds
            ):
                logger.warning(
                    "OpenCodeServerManager already initialized with "
                    f"binary={cls._instance.binary}, port={cls._instance.port}, "
                    f"request_timeout_seconds={cls._instance.request_timeout_seconds}; "
                    f"ignoring new params binary={binary}, port={port}, "
                    f"request_timeout_seconds={request_timeout_seconds}"
                )
            return cls._instance

    @property
    def base_url(self) -> str:
        if self._base_url:
            return self._base_url
        return f"http://{self.host}:{self.port}"

    async def _get_http_session(self) -> aiohttp.ClientSession:
        current_loop = asyncio.get_running_loop()
        # Recreate session if it's closed or bound to a different event loop
        if (
            self._http_session is None
            or self._http_session.closed
            or self._http_session_loop is not current_loop
        ):
            # Close old session if it exists and is not closed
            if self._http_session is not None and not self._http_session.closed:
                try:
                    await self._http_session.close()
                except Exception:
                    pass
            total_timeout: Optional[int] = (
                None
                if self.request_timeout_seconds <= 0
                else self.request_timeout_seconds
            )
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=total_timeout)
            )
            self._http_session_loop = current_loop
        return self._http_session

    def _read_pid_file(self) -> Optional[Dict[str, Any]]:
        try:
            raw = self._pid_file.read_text()
        except FileNotFoundError:
            return None
        except Exception as e:
            logger.debug(f"Failed to read OpenCode pid file: {e}")
            return None

        try:
            data = json.loads(raw)
        except Exception as e:
            logger.debug(f"Failed to parse OpenCode pid file: {e}")
            return None

        return data if isinstance(data, dict) else None

    def _write_pid_file(self, pid: int) -> None:
        try:
            self._pid_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "pid": pid,
                "port": self.port,
                "host": self.host,
                "started_at": time.time(),
            }
            self._pid_file.write_text(json.dumps(payload))
        except Exception as e:
            logger.debug(f"Failed to write OpenCode pid file: {e}")

    def _clear_pid_file(self) -> None:
        try:
            if self._pid_file.exists():
                self._pid_file.unlink()
        except Exception as e:
            logger.debug(f"Failed to clear OpenCode pid file: {e}")

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        if not isinstance(pid, int) or pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    @staticmethod
    def _get_pid_command(pid: int) -> Optional[str]:
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return None
        cmd = (result.stdout or "").strip()
        return cmd or None

    @staticmethod
    def _is_opencode_serve_cmd(command: str, port: int) -> bool:
        if not command:
            return False
        return (
            "opencode" in command
            and " serve" in command
            and f"--port={port}" in command
        )

    def _is_port_available(self) -> bool:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.host, self.port))
            return True
        except OSError:
            return False

    @staticmethod
    def _find_opencode_serve_pids(port: int) -> List[int]:
        try:
            result = subprocess.run(
                ["ps", "-ax", "-o", "pid=,command="],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            return []

        needle = f"--port={port}"
        pids: List[int] = []
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                continue
            pid_str, cmd = parts
            if "opencode" in cmd and " serve" in cmd and needle in cmd:
                try:
                    pids.append(int(pid_str))
                except ValueError:
                    continue
        return pids

    async def _terminate_pid(self, pid: int, reason: str) -> None:
        logger.info(f"Stopping OpenCode server pid={pid} ({reason})")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except Exception as e:
            logger.debug(f"Failed to terminate OpenCode server pid={pid}: {e}")
            return

        start_time = time.monotonic()
        while time.monotonic() - start_time < 5:
            if not self._pid_exists(pid):
                return
            await asyncio.sleep(0.25)

        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

    async def _cleanup_orphaned_managed_server(self) -> None:
        info = self._read_pid_file()
        if not info:
            return

        pid = info.get("pid")
        port = info.get("port")
        if not isinstance(pid, int) or port != self.port:
            self._clear_pid_file()
            return

        if (
            self._process
            and self._process.returncode is None
            and self._process.pid == pid
        ):
            return

        # Check if the server is healthy before deciding to kill it.
        # If it's healthy, we should adopt it rather than kill it.
        if await self._is_healthy():
            # Update PID file to reflect the actual running process.
            # The PID in the file may be stale if OpenCode was restarted externally.
            actual_pids = self._find_opencode_serve_pids(self.port)
            if actual_pids:
                actual_pid = actual_pids[0]
                if actual_pid != pid:
                    logger.info(
                        f"Adopting healthy OpenCode server (updating stale PID {pid} -> {actual_pid})"
                    )
                    self._write_pid_file(actual_pid)
                else:
                    logger.info(
                        f"Adopting healthy OpenCode server pid={pid} from previous run"
                    )
            else:
                # Server is healthy but we can't find its PID - clear stale file
                logger.info(
                    f"Adopting healthy OpenCode server (clearing stale PID file, pid={pid} not found)"
                )
                self._clear_pid_file()
            return

        cmd = self._get_pid_command(pid)
        if (
            cmd
            and self._is_opencode_serve_cmd(cmd, self.port)
            and self._pid_exists(pid)
        ):
            await self._terminate_pid(pid, reason="orphaned and unhealthy")
        self._clear_pid_file()

    async def ensure_running(self) -> str:
        async with self._get_lock():
            await self._cleanup_orphaned_managed_server()

            if await self._is_healthy():
                # If the server is already running (e.g., started by a previous run),
                # record its PID so shutdown can clean it up.
                if not self._read_pid_file():
                    pids = self._find_opencode_serve_pids(self.port)
                    if pids:
                        pid = pids[0]
                        cmd = self._get_pid_command(pid)
                        if cmd and self._is_opencode_serve_cmd(cmd, self.port):
                            self._write_pid_file(pid)

                self._base_url = f"http://{self.host}:{self.port}"
                return self.base_url

            if not self._is_port_available():
                for pid in self._find_opencode_serve_pids(self.port):
                    await self._terminate_pid(pid, reason="port occupied but unhealthy")
                await asyncio.sleep(0.5)

            if not self._is_port_available():
                raise RuntimeError(
                    f"OpenCode port {self.port} is already in use but the server is not responding. "
                    "Stop the process using this port or set OPENCODE_PORT to a free port."
                )

            await self._start_server()
            return self.base_url

    async def _is_healthy(self) -> bool:
        try:
            session = await self._get_http_session()
            async with session.get(
                f"{self.base_url}/global/health", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("healthy", False)
        except Exception as e:
            logger.debug(f"Health check failed: {e}")
        return False

    async def _start_server(self) -> None:
        if self._process and self._process.returncode is None:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except Exception:
                self._process.kill()

        # Ensure any stale pid file is cleared before starting.
        self._clear_pid_file()

        cmd = [
            self.binary,
            "serve",
            f"--hostname={self.host}",
            f"--port={self.port}",
        ]

        logger.info(f"Starting OpenCode server: {' '.join(cmd)}")

        env = os.environ.copy()
        env["OPENCODE_ENABLE_EXA"] = "1"

        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
            if self._process and self._process.pid:
                self._write_pid_file(self._process.pid)
        except FileNotFoundError:
            raise RuntimeError(
                f"OpenCode CLI not found at '{self.binary}'. "
                "Please install OpenCode or set OPENCODE_CLI_PATH."
            )

        start_time = time.monotonic()
        while time.monotonic() - start_time < SERVER_START_TIMEOUT:
            if await self._is_healthy():
                self._base_url = f"http://{self.host}:{self.port}"
                logger.info(f"OpenCode server started at {self._base_url}")
                return
            await asyncio.sleep(0.5)

        exit_code = self._process.returncode
        self._clear_pid_file()
        self._process = None
        raise RuntimeError(
            f"OpenCode server failed to start within {SERVER_START_TIMEOUT}s. "
            f"Process exit code: {exit_code}"
        )

    async def stop(self) -> None:
        async with self._get_lock():
            if self._http_session:
                await self._http_session.close()
                self._http_session = None
                self._http_session_loop = None

            # Don't terminate OpenCode server on vibe-remote shutdown.
            # Let it continue running so the next vibe-remote instance can adopt it.
            # This prevents interrupting tasks that are still in progress.
            logger.info(
                "OpenCode server left running for next vibe-remote instance to adopt"
            )

            # Keep pid_file so next instance knows about the running server.
            self._process = None

    def stop_sync(self) -> None:
        if self._http_session and self._http_session_loop:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self._http_session.close(), self._http_session_loop
                )
                future.result(timeout=5)
            except Exception as e:
                logger.debug(f"Failed to close OpenCode HTTP session: {e}")
            finally:
                self._http_session = None
                self._http_session_loop = None

        # Don't terminate OpenCode server on vibe-remote shutdown.
        # Let it continue running so the next vibe-remote instance can adopt it.
        # This prevents interrupting tasks that are still in progress.
        logger.info(
            "OpenCode server left running for next vibe-remote instance to adopt"
        )

        # Keep pid_file so next instance knows about the running server.
        # Don't clear _process reference - just let it be garbage collected.
        self._process = None

    @classmethod
    def stop_instance_sync(cls) -> None:
        if cls._instance:
            cls._instance.stop_sync()
            return

        # Don't terminate OpenCode server on vibe-remote shutdown.
        # Let it continue running so the next vibe-remote instance can adopt it.
        logger.info(
            "OpenCode server left running for next vibe-remote instance to adopt"
        )

    async def create_session(
        self, directory: str, title: Optional[str] = None
    ) -> Dict[str, Any]:
        session = await self._get_http_session()
        body: Dict[str, Any] = {}
        if title:
            body["title"] = title

        async with session.post(
            f"{self.base_url}/session",
            json=body,
            headers={"x-opencode-directory": directory},
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Failed to create session: {resp.status} {text}")
            return await resp.json()

    async def send_message(
        self,
        session_id: str,
        directory: str,
        text: str,
        agent: Optional[str] = None,
        model: Optional[Dict[str, str]] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Dict[str, Any]:
        session = await self._get_http_session()

        body: Dict[str, Any] = {
            "parts": [{"type": "text", "text": text}],
        }
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = model
        if reasoning_effort:
            body["reasoningEffort"] = reasoning_effort

        async with session.post(
            f"{self.base_url}/session/{session_id}/message",
            json=body,
            headers={"x-opencode-directory": directory},
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(
                    f"Failed to send message: {resp.status} {error_text}"
                )
            return await resp.json()

    async def prompt_async(
        self,
        session_id: str,
        directory: str,
        text: str,
        agent: Optional[str] = None,
        model: Optional[Dict[str, str]] = None,
        reasoning_effort: Optional[str] = None,
    ) -> None:
        """Start a prompt asynchronously without holding the HTTP request open."""

        session = await self._get_http_session()

        body: Dict[str, Any] = {
            "parts": [{"type": "text", "text": text}],
        }
        if agent:
            body["agent"] = agent
        if model:
            body["model"] = model
        if reasoning_effort:
            body["reasoningEffort"] = reasoning_effort

        async with session.post(
            f"{self.base_url}/session/{session_id}/prompt_async",
            json=body,
            headers={"x-opencode-directory": directory},
        ) as resp:
            # OpenCode returns 204 when accepted.
            if resp.status not in (200, 204):
                error_text = await resp.text()
                raise RuntimeError(
                    f"Failed to start async prompt: {resp.status} {error_text}"
                )

    async def list_messages(
        self, session_id: str, directory: str
    ) -> List[Dict[str, Any]]:
        session = await self._get_http_session()
        async with session.get(
            f"{self.base_url}/session/{session_id}/message",
            headers={"x-opencode-directory": directory},
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(
                    f"Failed to list messages: {resp.status} {error_text}"
                )
            return await resp.json()

    async def get_message(
        self, session_id: str, message_id: str, directory: str
    ) -> Dict[str, Any]:
        session = await self._get_http_session()
        async with session.get(
            f"{self.base_url}/session/{session_id}/message/{message_id}",
            headers={"x-opencode-directory": directory},
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(f"Failed to get message: {resp.status} {error_text}")
            return await resp.json()

    async def list_questions(
        self, directory: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        session = await self._get_http_session()
        params = {"directory": directory} if directory else None
        async with session.get(
            f"{self.base_url}/question",
            params=params,
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(
                    f"Failed to list questions: {resp.status} {error_text}"
                )
            data = await resp.json()
            return data if isinstance(data, list) else []

    async def reply_question(
        self, question_id: str, directory: str, answers: List[List[str]]
    ) -> bool:
        session = await self._get_http_session()
        async with session.post(
            f"{self.base_url}/question/{question_id}/reply",
            params={"directory": directory},
            json={"answers": answers},
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                raise RuntimeError(
                    f"Failed to reply question: {resp.status} {error_text}"
                )
            data = await resp.json()
            return bool(data)

    async def abort_session(self, session_id: str, directory: str) -> bool:
        session = await self._get_http_session()

        try:
            async with session.post(
                f"{self.base_url}/session/{session_id}/abort",
                headers={"x-opencode-directory": directory},
            ) as resp:
                return resp.status == 200
        except Exception as e:
            logger.warning(f"Failed to abort session {session_id}: {e}")
            return False

    async def get_session(
        self, session_id: str, directory: str
    ) -> Optional[Dict[str, Any]]:
        session = await self._get_http_session()
        try:
            async with session.get(
                f"{self.base_url}/session/{session_id}",
                headers={"x-opencode-directory": directory},
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None
        except Exception as e:
            logger.debug(f"Failed to get session {session_id}: {e}")
            return None

    async def get_available_agents(self, directory: str) -> List[Dict[str, Any]]:
        """Fetch available agents from OpenCode server.

        Returns:
            List of agent dicts with 'name', 'mode', 'native', etc.
        """

        session = await self._get_http_session()
        try:
            async with session.get(
                f"{self.base_url}/agent",
                headers={"x-opencode-directory": directory},
            ) as resp:
                if resp.status == 200:
                    agents = await resp.json()
                    # Filter to primary agents (build, plan), exclude hidden/subagent
                    return [
                        a
                        for a in agents
                        if a.get("mode") == "primary" and not a.get("hidden", False)
                    ]
                return []
        except Exception as e:
            logger.warning(f"Failed to get available agents: {e}")
            return []

    async def get_available_models(self, directory: str) -> Dict[str, Any]:
        """Fetch available models from OpenCode server.

        Returns:
            Dict with 'providers' list and 'default' dict mapping provider to default model.
        """

        session = await self._get_http_session()
        try:
            async with session.get(
                f"{self.base_url}/config/providers",
                headers={"x-opencode-directory": directory},
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"providers": [], "default": {}}
        except Exception as e:
            logger.warning(f"Failed to get available models: {e}")
            return {"providers": [], "default": {}}

    async def get_default_config(self, directory: str) -> Dict[str, Any]:
        """Fetch current default config from OpenCode server.

        Returns:
            Config dict including 'model' (current default), 'agent' configs, etc.
        """

        session = await self._get_http_session()
        try:
            async with session.get(
                f"{self.base_url}/config",
                headers={"x-opencode-directory": directory},
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {}
        except Exception as e:
            logger.warning(f"Failed to get default config: {e}")
            return {}

    def _load_opencode_user_config(self) -> Optional[Dict[str, Any]]:
        """Load and cache opencode.json config file.

        Checks both ~/.config/opencode/opencode.json and ~/.opencode/opencode.json
        since OpenCode supports multiple config locations.

        Returns:
            Parsed config dict, or None if file doesn't exist or is invalid.
        """

        from pathlib import Path

        config_paths = [
            Path.home() / ".config" / "opencode" / "opencode.json",
            Path.home() / ".opencode" / "opencode.json",
        ]

        for config_path in config_paths:
            if not config_path.exists():
                continue
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                if not isinstance(config, dict):
                    logger.warning(f"{config_path}: root is not a dict")
                    continue
                return config
            except Exception as e:
                logger.warning(f"Failed to load {config_path}: {e}")
                continue

        return None

    def _get_agent_config(
        self, config: Dict[str, Any], agent_name: Optional[str]
    ) -> Dict[str, Any]:
        """Get agent-specific config from opencode.json with type safety."""

        if not agent_name:
            return {}
        agents = config.get("agent", {})
        if not isinstance(agents, dict):
            return {}
        agent_config = agents.get(agent_name, {})
        if not isinstance(agent_config, dict):
            return {}
        return agent_config

    def get_agent_model_from_config(self, agent_name: Optional[str]) -> Optional[str]:
        """Read agent's default model from user's opencode.json config file.

        This is a workaround for OpenCode server not using agent-specific models
        when only the agent parameter is passed to the message API.
        """

        config = self._load_opencode_user_config()
        if not config:
            return None

        # Try agent-specific model first
        agent_config = self._get_agent_config(config, agent_name)
        model = agent_config.get("model")
        if isinstance(model, str) and model:
            logger.debug(
                f"Found model '{model}' for agent '{agent_name}' in opencode.json"
            )
            return model

        # Fall back to global default model
        model = config.get("model")
        if isinstance(model, str) and model:
            logger.debug(f"Using global default model '{model}' from opencode.json")
            return model
        return None

    def get_agent_reasoning_effort_from_config(
        self, agent_name: Optional[str]
    ) -> Optional[str]:
        """Read agent's reasoningEffort from user's opencode.json config file."""

        config = self._load_opencode_user_config()
        if not config:
            return None

        # Valid reasoning effort values
        valid_efforts = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}

        # Try agent-specific reasoningEffort first
        agent_config = self._get_agent_config(config, agent_name)
        reasoning_effort = agent_config.get("reasoningEffort")
        if isinstance(reasoning_effort, str) and reasoning_effort:
            if reasoning_effort in valid_efforts:
                logger.debug(
                    f"Found reasoningEffort '{reasoning_effort}' for agent '{agent_name}' in opencode.json"
                )
                return reasoning_effort
            else:
                logger.debug(
                    f"Ignoring unknown reasoningEffort '{reasoning_effort}' for agent '{agent_name}'"
                )

        # Fall back to global default reasoningEffort
        reasoning_effort = config.get("reasoningEffort")
        if isinstance(reasoning_effort, str) and reasoning_effort:
            if reasoning_effort in valid_efforts:
                logger.debug(
                    f"Using global default reasoningEffort '{reasoning_effort}' from opencode.json"
                )
                return reasoning_effort
            else:
                logger.debug(
                    f"Ignoring unknown global reasoningEffort '{reasoning_effort}'"
                )
        return None

    def get_default_agent_from_config(self) -> Optional[str]:
        """Read the default agent from user's opencode.json config file.

        OpenCode server doesn't automatically use its configured default agent
        when called via API, so we need to read and pass it explicitly.
        """

        # OpenCode doesn't have an explicit "default agent" config field.
        # Users can override via channel settings.
        # Default to "build" agent which uses the agent's configured model,
        # avoiding fallback to global model which may use restricted credentials.
        return "build"
