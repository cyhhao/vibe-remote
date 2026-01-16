"""OpenCode Server API integration as an agent backend."""

import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import time
from asyncio.subprocess import Process
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from modules.agents.base import AgentRequest, BaseAgent

logger = logging.getLogger(__name__)

DEFAULT_OPENCODE_PORT = 4096
DEFAULT_OPENCODE_HOST = "127.0.0.1"
SERVER_START_TIMEOUT = 15


class OpenCodeServerManager:
    """Manages a singleton OpenCode server process shared across all working directories."""

    _instance: Optional["OpenCodeServerManager"] = None
    _class_lock: asyncio.Lock = asyncio.Lock()

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
        self._lock = asyncio.Lock()
        self._pid_file = (
            Path(__file__).resolve().parents[2] / "logs" / "opencode_server.json"
        )

    @classmethod
    async def get_instance(
        cls,
        binary: str = "opencode",
        port: int = DEFAULT_OPENCODE_PORT,
        request_timeout_seconds: int = 60,
    ) -> "OpenCodeServerManager":
        async with cls._class_lock:
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
        if self._http_session is None or self._http_session.closed:
            total_timeout: Optional[int] = (
                None
                if self.request_timeout_seconds <= 0
                else self.request_timeout_seconds
            )
            self._http_session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=total_timeout)
            )
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
        return "opencode" in command and " serve" in command and f"--port={port}" in command

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

        if self._process and self._process.returncode is None and self._process.pid == pid:
            return

        cmd = self._get_pid_command(pid)
        if cmd and self._is_opencode_serve_cmd(cmd, self.port) and self._pid_exists(pid):
            await self._terminate_pid(pid, reason="orphaned from previous run")
        self._clear_pid_file()

    async def ensure_running(self) -> str:
        async with self._lock:
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
        async with self._lock:
            if self._http_session:
                await self._http_session.close()
                self._http_session = None

            if self._process and self._process.returncode is None:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    self._process.kill()
                logger.info("OpenCode server stopped")
            else:
                info = self._read_pid_file()
                pid = info.get("pid") if isinstance(info, dict) else None
                port = info.get("port") if isinstance(info, dict) else None
                if isinstance(pid, int) and port == self.port and self._pid_exists(pid):
                    cmd = self._get_pid_command(pid)
                    if cmd and self._is_opencode_serve_cmd(cmd, self.port):
                        await self._terminate_pid(pid, reason="shutdown")

            self._clear_pid_file()
            self._process = None

    def stop_sync(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.terminate()
            logger.info("OpenCode server terminated (sync)")
        else:
            info = self._read_pid_file()
            pid = info.get("pid") if isinstance(info, dict) else None
            port = info.get("port") if isinstance(info, dict) else None
            if isinstance(pid, int) and port == self.port and self._pid_exists(pid):
                cmd = self._get_pid_command(pid)
                if cmd and self._is_opencode_serve_cmd(cmd, self.port):
                    try:
                        os.kill(pid, signal.SIGTERM)
                        logger.info("OpenCode server terminated (sync via pid file)")
                    except Exception as e:
                        logger.debug(f"Failed to terminate OpenCode server pid={pid}: {e}")

        self._clear_pid_file()
        self._process = None

    @classmethod
    def stop_instance_sync(cls) -> None:
        if cls._instance:
            cls._instance.stop_sync()
            return

        pid_file = Path(__file__).resolve().parents[2] / "logs" / "opencode_server.json"
        try:
            raw = pid_file.read_text()
        except FileNotFoundError:
            return
        except Exception as e:
            logger.debug(f"Failed to read OpenCode pid file: {e}")
            return

        try:
            info = json.loads(raw)
        except Exception:
            info = None

        pid = info.get("pid") if isinstance(info, dict) else None
        port = info.get("port") if isinstance(info, dict) else None

        if isinstance(pid, int) and isinstance(port, int) and cls._pid_exists(pid):
            cmd = cls._get_pid_command(pid)
            if cmd and cls._is_opencode_serve_cmd(cmd, port):
                try:
                    os.kill(pid, signal.SIGTERM)
                    logger.info("OpenCode server terminated (sync via pid file)")
                except Exception as e:
                    logger.debug(f"Failed to terminate OpenCode server pid={pid}: {e}")

        try:
            pid_file.unlink()
        except Exception:
            pass

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
                raise RuntimeError(
                    f"Failed to get message: {resp.status} {error_text}"
                )
            return await resp.json()

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
                        a for a in agents
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

        Returns:
            Parsed config dict, or None if file doesn't exist or is invalid.
        """
        import json
        from pathlib import Path

        config_path = Path.home() / ".config" / "opencode" / "opencode.json"
        if not config_path.exists():
            return None

        try:
            with open(config_path, "r") as f:
                config = json.load(f)
            if not isinstance(config, dict):
                logger.warning("opencode.json root is not a dict")
                return None
            return config
        except Exception as e:
            logger.warning(f"Failed to load opencode.json: {e}")
            return None

    def _get_agent_config(
        self, config: Dict[str, Any], agent_name: Optional[str]
    ) -> Dict[str, Any]:
        """Get agent-specific config from opencode.json with type safety.

        Args:
            config: Parsed opencode.json config
            agent_name: Name of the agent, or None

        Returns:
            Agent config dict, or empty dict if not found/invalid.
        """
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

        Args:
            agent_name: Name of the agent (e.g., "build", "plan"), or None for global default

        Returns:
            Model string in "provider/model" format, or None if not configured.
        """
        config = self._load_opencode_user_config()
        if not config:
            return None

        # Try agent-specific model first
        agent_config = self._get_agent_config(config, agent_name)
        model = agent_config.get("model")
        if isinstance(model, str) and model:
            logger.debug(f"Found model '{model}' for agent '{agent_name}' in opencode.json")
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
        """Read agent's reasoningEffort from user's opencode.json config file.

        Args:
            agent_name: Name of the agent (e.g., "build", "plan"), or None for global default

        Returns:
            reasoningEffort string (e.g., "low", "medium", "high", "xhigh"), or None if not configured.
        """
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
                logger.debug(f"Ignoring unknown reasoningEffort '{reasoning_effort}' for agent '{agent_name}'")

        # Fall back to global default reasoningEffort
        reasoning_effort = config.get("reasoningEffort")
        if isinstance(reasoning_effort, str) and reasoning_effort:
            if reasoning_effort in valid_efforts:
                logger.debug(
                    f"Using global default reasoningEffort '{reasoning_effort}' from opencode.json"
                )
                return reasoning_effort
            else:
                logger.debug(f"Ignoring unknown global reasoningEffort '{reasoning_effort}'")
        return None

    def get_default_agent_from_config(self) -> Optional[str]:
        """Read the default agent from user's opencode.json config file.

        OpenCode server doesn't automatically use its configured default agent
        when called via API, so we need to read and pass it explicitly.

        Returns:
            Default agent name (e.g., "build", "plan"), or "build" as fallback.
        """
        # OpenCode doesn't have an explicit "default agent" config field.
        # Users can override via channel settings or agent_routes.yaml.
        # Default to "build" agent which uses the agent's configured model,
        # avoiding fallback to global model which may use restricted credentials.
        return "build"


class OpenCodeAgent(BaseAgent):
    """OpenCode Server API integration via HTTP."""

    name = "opencode"

    def __init__(self, controller, opencode_config):
        super().__init__(controller)
        self.opencode_config = opencode_config
        self._server_manager: Optional[OpenCodeServerManager] = None
        self._active_requests: Dict[str, asyncio.Task] = {}
        self._request_sessions: Dict[str, Tuple[str, str, str]] = {}
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._initialized_sessions: set[str] = set()

    async def _get_server(self) -> OpenCodeServerManager:
        if self._server_manager is None:
            self._server_manager = await OpenCodeServerManager.get_instance(
                binary=self.opencode_config.binary,
                port=self.opencode_config.port,
                request_timeout_seconds=self.opencode_config.request_timeout_seconds,
            )
        return self._server_manager

    def _get_session_lock(self, base_session_id: str) -> asyncio.Lock:
        if base_session_id not in self._session_locks:
            self._session_locks[base_session_id] = asyncio.Lock()
        return self._session_locks[base_session_id]

    async def handle_message(self, request: AgentRequest) -> None:
        lock = self._get_session_lock(request.base_session_id)
        async with lock:
            existing_task = self._active_requests.get(request.base_session_id)
            if existing_task and not existing_task.done():
                await self.controller.emit_agent_message(
                    request.context,
                    "notify",
                    "OpenCode is already processing a task in this thread. "
                    "Cancelling the previous run...",
                )
                req_info = self._request_sessions.get(request.base_session_id)
                if req_info:
                    server = await self._get_server()
                    await server.abort_session(req_info[0], req_info[1])
                existing_task.cancel()
                try:
                    await existing_task
                except asyncio.CancelledError:
                    pass
                await self.controller.emit_agent_message(
                    request.context,
                    "notify",
                    "Previous OpenCode task cancelled. Starting the new request...",
                )

            task = asyncio.create_task(self._process_message(request))
            self._active_requests[request.base_session_id] = task

        try:
            await task
        except asyncio.CancelledError:
            # Task was cancelled (e.g. by /stop), exit gracefully without bubbling
            logger.debug(f"OpenCode task cancelled for {request.base_session_id}")
        finally:
            if self._active_requests.get(request.base_session_id) is task:
                self._active_requests.pop(request.base_session_id, None)
                self._request_sessions.pop(request.base_session_id, None)

    async def _process_message(self, request: AgentRequest) -> None:
        try:
            server = await self._get_server()
            await server.ensure_running()
        except Exception as e:
            logger.error(f"Failed to start OpenCode server: {e}", exc_info=True)
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                f"Failed to start OpenCode server: {e}",
            )
            return

        await self._delete_ack(request)

        if not os.path.exists(request.working_path):
            os.makedirs(request.working_path, exist_ok=True)

        session_id = self.settings_manager.get_agent_session_id(
            request.settings_key,
            request.base_session_id,
            request.working_path,
            agent_name=self.name,
        )

        if not session_id:
            try:
                session_data = await server.create_session(
                    directory=request.working_path,
                    title=f"vibe-remote:{request.base_session_id}",
                )
                session_id = session_data.get("id")
                if session_id:
                    self.settings_manager.set_agent_session_mapping(
                        request.settings_key,
                        self.name,
                        request.base_session_id,
                        request.working_path,
                        session_id,
                    )
                    logger.info(
                        f"Created OpenCode session {session_id} for {request.base_session_id}"
                    )
            except Exception as e:
                logger.error(f"Failed to create OpenCode session: {e}", exc_info=True)
                await self.controller.emit_agent_message(
                    request.context,
                    "notify",
                    f"Failed to create OpenCode session: {e}",
                )
                return
        else:
            existing = await server.get_session(session_id, request.working_path)
            if not existing:
                try:
                    session_data = await server.create_session(
                        directory=request.working_path,
                        title=f"vibe-remote:{request.base_session_id}",
                    )
                    session_id = session_data.get("id")
                    if session_id:
                        self.settings_manager.set_agent_session_mapping(
                            request.settings_key,
                            self.name,
                            request.base_session_id,
                            request.working_path,
                            session_id,
                        )
                        logger.info(
                            f"Recreated OpenCode session {session_id} for {request.base_session_id}"
                        )
                except Exception as e:
                    logger.error(f"Failed to recreate session: {e}", exc_info=True)
                    await self.controller.emit_agent_message(
                        request.context,
                        "notify",
                        f"Failed to create OpenCode session: {e}",
                    )
                    return

        if not session_id:
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "Failed to obtain OpenCode session ID",
            )
            return

        self._request_sessions[request.base_session_id] = (
            session_id,
            request.working_path,
            request.settings_key,
        )

        if session_id not in self._initialized_sessions:
            self._initialized_sessions.add(session_id)
            system_text = self.im_client.formatter.format_system_message(
                request.working_path, "init", session_id
            )
            await self.controller.emit_agent_message(
                request.context,
                "system",
                system_text,
                parse_mode="markdown",
            )

        try:
            # Get per-channel overrides from user_settings.json
            override_agent, override_model, override_reasoning = (
                self.controller.get_opencode_overrides(request.context)
            )

            # Determine agent to use
            # Priority: 1) channel override, 2) opencode.json default, 3) None (let OpenCode decide)
            agent_to_use = override_agent
            if not agent_to_use:
                agent_to_use = server.get_default_agent_from_config()

            # Determine model to use
            # Priority: 1) channel override, 2) agent's config model, 3) global opencode.json model
            model_dict = None
            model_str = override_model
            if not model_str:
                # OpenCode server doesn't use agent's configured model when called via API,
                # so we read it from opencode.json explicitly
                model_str = server.get_agent_model_from_config(agent_to_use)
            if model_str:
                parts = model_str.split("/", 1)
                if len(parts) == 2:
                    model_dict = {"providerID": parts[0], "modelID": parts[1]}

            # Determine reasoningEffort to use
            # Priority: 1) channel override, 2) agent's config, 3) global opencode.json config
            reasoning_effort = override_reasoning
            if not reasoning_effort:
                reasoning_effort = server.get_agent_reasoning_effort_from_config(agent_to_use)

            # Use OpenCode's async prompt API so long-running turns don't hold a single HTTP request.
            baseline_message_ids: set[str] = set()
            try:
                baseline_messages = await server.list_messages(
                    session_id=session_id,
                    directory=request.working_path,
                )
                for message in baseline_messages:
                    message_id = message.get("info", {}).get("id")
                    if message_id:
                        baseline_message_ids.add(message_id)
            except Exception as err:
                logger.debug(
                    f"Failed to snapshot OpenCode messages before prompt: {err}"
                )

            await server.prompt_async(
                session_id=session_id,
                directory=request.working_path,
                text=request.message,
                agent=agent_to_use,
                model=model_dict,
                reasoning_effort=reasoning_effort,
            )

            seen_tool_calls: set[str] = set()
            emitted_assistant_messages: set[str] = set()
            poll_interval_seconds = 2.0
            final_text: Optional[str] = None

            def _relative_path(path: str) -> str:
                return self._to_relative_path(path, request.working_path)

            while True:
                try:
                    messages = await server.list_messages(
                        session_id=session_id,
                        directory=request.working_path,
                    )
                except Exception as poll_err:
                    logger.warning(f"Failed to poll OpenCode messages: {poll_err}")
                    await asyncio.sleep(poll_interval_seconds)
                    continue

                for message in messages:
                    info = message.get("info", {})
                    message_id = info.get("id")
                    if not message_id or message_id in baseline_message_ids:
                        continue
                    if info.get("role") != "assistant":
                        continue

                    for part in message.get("parts", []) or []:
                        if part.get("type") != "tool":
                            continue
                        call_key = part.get("callID") or part.get("id")
                        if not call_key or call_key in seen_tool_calls:
                            continue
                        tool_name = part.get("tool") or "tool"
                        tool_input = part.get("state", {}).get("input") or {}
                        toolcall = self.im_client.formatter.format_toolcall(
                            tool_name,
                            tool_input,
                            get_relative_path=_relative_path,
                        )
                        await self.controller.emit_agent_message(
                            request.context,
                            "toolcall",
                            toolcall,
                            parse_mode="markdown",
                        )
                        seen_tool_calls.add(call_key)

                    if (
                        info.get("time", {}).get("completed")
                        and message_id not in emitted_assistant_messages
                        and info.get("finish") == "tool-calls"
                    ):
                        text = self._extract_response_text(message)
                        if text:
                            await self.controller.emit_agent_message(
                                request.context,
                                "assistant",
                                text,
                                parse_mode="markdown",
                            )
                        emitted_assistant_messages.add(message_id)

                if messages:
                    last_message = messages[-1]
                    last_info = last_message.get("info", {})
                    last_id = last_info.get("id")
                    if (
                        last_id
                        and last_id not in baseline_message_ids
                        and last_info.get("role") == "assistant"
                        and last_info.get("time", {}).get("completed")
                        and last_info.get("finish") != "tool-calls"
                    ):
                        final_text = self._extract_response_text(last_message)
                        break

                await asyncio.sleep(poll_interval_seconds)

            if final_text:
                await self.emit_result_message(
                    request.context,
                    final_text,
                    subtype="success",
                    started_at=request.started_at,
                    parse_mode="markdown",
                )
            else:
                await self.emit_result_message(
                    request.context,
                    "(No response from OpenCode)",
                    subtype="warning",
                    started_at=request.started_at,
                )

        except asyncio.CancelledError:
            logger.info(f"OpenCode request cancelled for {request.base_session_id}")
            raise
        except Exception as e:
            error_name = type(e).__name__
            error_details = str(e).strip()
            error_text = f"{error_name}: {error_details}" if error_details else error_name

            logger.error(f"OpenCode request failed: {error_text}", exc_info=True)
            try:
                await server.abort_session(session_id, request.working_path)
            except Exception as abort_err:
                logger.warning(
                    f"Failed to abort OpenCode session after error: {abort_err}"
                )

            await self.controller.emit_agent_message(
                request.context,
                "notify",
                f"OpenCode request failed: {error_text}",
            )

    def _extract_response_text(self, response: Dict[str, Any]) -> str:
        parts = response.get("parts", [])
        text_parts = []

        for part in parts:
            part_type = part.get("type")
            if part_type == "text":
                text = part.get("text", "")
                if text:
                    text_parts.append(text)

        if not text_parts and parts:
            part_types = [p.get("type") for p in parts]
            logger.debug(f"OpenCode response has no text parts; part types: {part_types}")

        return "\n\n".join(text_parts).strip()

    def _to_relative_path(self, abs_path: str, cwd: str) -> str:
        """Convert absolute file paths to relative paths under cwd."""
        try:
            abs_path = os.path.abspath(os.path.expanduser(abs_path))
            cwd = os.path.abspath(os.path.expanduser(cwd))
            rel_path = os.path.relpath(abs_path, cwd)
            if rel_path.startswith("../.."):
                return abs_path
            if not rel_path.startswith(".") and rel_path != ".":
                rel_path = "./" + rel_path
            return rel_path
        except Exception:
            return abs_path

    async def handle_stop(self, request: AgentRequest) -> bool:


        task = self._active_requests.get(request.base_session_id)
        if not task or task.done():
            return False

        req_info = self._request_sessions.get(request.base_session_id)
        if req_info:
            try:
                server = await self._get_server()
                await server.abort_session(req_info[0], req_info[1])
            except Exception as e:
                logger.warning(f"Failed to abort OpenCode session: {e}")

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        await self.controller.emit_agent_message(
            request.context, "notify", "Terminated OpenCode execution."
        )
        logger.info(f"OpenCode session {request.base_session_id} terminated via /stop")
        return True

    async def clear_sessions(self, settings_key: str) -> int:
        self.settings_manager.clear_agent_sessions(settings_key, self.name)
        terminated = 0
        for base_id, task in list(self._active_requests.items()):
            req_info = self._request_sessions.get(base_id)
            if req_info and len(req_info) >= 3 and req_info[2] == settings_key:
                if not task.done():
                    try:
                        server = await self._get_server()
                        await server.abort_session(req_info[0], req_info[1])
                    except Exception:
                        pass
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    terminated += 1
        return terminated

    async def _delete_ack(self, request: AgentRequest):
        ack_id = request.ack_message_id
        if ack_id and hasattr(self.im_client, "delete_message"):
            try:
                await self.im_client.delete_message(request.context.channel_id, ack_id)
            except Exception as err:
                logger.debug(f"Could not delete ack message: {err}")
            finally:
                request.ack_message_id = None
