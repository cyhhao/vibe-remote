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


_REASONING_FALLBACK_OPTIONS = [
    {"value": "low", "label": "Low"},
    {"value": "medium", "label": "Medium"},
    {"value": "high", "label": "High"},
]

_REASONING_VARIANT_ORDER = ["none", "minimal", "low", "medium", "high", "xhigh", "max"]

_REASONING_VARIANT_LABELS = {
    "none": "None",
    "minimal": "Minimal",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "Extra High",
    "max": "Max",
}


def _parse_model_key(model_key: Optional[str]) -> tuple[str, str]:
    if not model_key:
        return "", ""
    parts = model_key.split("/", 1)
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


def _find_model_variants(opencode_models: dict, target_model: Optional[str]) -> Dict[str, Any]:
    target_provider, target_model_id = _parse_model_key(target_model)
    if not target_provider or not target_model_id or not isinstance(opencode_models, dict):
        return {}
    providers_data = opencode_models.get("providers", [])
    for provider in providers_data:
        provider_id = provider.get("id") or provider.get("provider_id") or provider.get("name")
        if provider_id != target_provider:
            continue

        models = provider.get("models", {})
        model_info: Optional[dict] = None
        if isinstance(models, dict):
            candidate = models.get(target_model_id)
            if isinstance(candidate, dict):
                model_info = candidate
        elif isinstance(models, list):
            for entry in models:
                if isinstance(entry, dict) and entry.get("id") == target_model_id:
                    model_info = entry
                    break

        if isinstance(model_info, dict):
            variants = model_info.get("variants", {})
            if isinstance(variants, dict):
                return variants
        break
    return {}


def _build_reasoning_options_from_variants(variants: Dict[str, Any]) -> List[Dict[str, str]]:
    sorted_variants = sorted(
        variants.keys(),
        key=lambda variant: (
            _REASONING_VARIANT_ORDER.index(variant)
            if variant in _REASONING_VARIANT_ORDER
            else len(_REASONING_VARIANT_ORDER),
            variant,
        ),
    )
    return [
        {
            "value": variant_key,
            "label": _REASONING_VARIANT_LABELS.get(
                variant_key, variant_key.capitalize()
            ),
        }
        for variant_key in sorted_variants
    ]


def build_reasoning_effort_options(
    opencode_models: dict,
    target_model: Optional[str],
) -> List[Dict[str, str]]:
    """Build reasoning effort options from OpenCode model metadata."""
    options = [{"value": "__default__", "label": "(Default)"}]
    variants = _find_model_variants(opencode_models, target_model)
    if variants:
        options.extend(_build_reasoning_options_from_variants(variants))
        return options
    options.extend(_REASONING_FALLBACK_OPTIONS)
    return options


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
        self._http_session_loop: Optional[asyncio.AbstractEventLoop] = None
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
            self._http_session_loop = asyncio.get_running_loop()
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
                self._http_session_loop = None

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
        # Users can override via channel settings.
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
        self._pending_questions: Dict[str, Dict[str, Any]] = {}

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

    async def _wait_for_session_idle(
        self,
        server: OpenCodeServerManager,
        session_id: str,
        directory: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                messages = await server.list_messages(session_id, directory)
            except Exception as err:
                logger.debug(f"Failed to poll OpenCode session {session_id} for idle: {err}")
                await asyncio.sleep(1.0)
                continue

            in_progress = False
            for message in messages:
                info = message.get("info", {})
                if info.get("role") != "assistant":
                    continue
                time_info = info.get("time") or {}
                if not time_info.get("completed"):
                    in_progress = True
                    break

            if not in_progress:
                return

            await asyncio.sleep(1.0)

        logger.warning(
            "OpenCode session %s did not reach idle state within %.1fs",
            session_id,
            timeout_seconds,
        )

    async def handle_message(self, request: AgentRequest) -> None:
        lock = self._get_session_lock(request.base_session_id)
        open_modal_task: Optional[asyncio.Task] = None
        task: Optional[asyncio.Task] = None
        async with lock:
            pending = self._pending_questions.get(request.base_session_id)
            is_modal_open = pending and request.message == "opencode_question:open_modal"
            is_question_action = pending and request.message.startswith("opencode_question:")

            existing_task = self._active_requests.get(request.base_session_id)
            if existing_task and not existing_task.done():
                if is_modal_open:
                    logger.info(
                        "OpenCode session %s running; opening modal without cancel",
                        request.base_session_id,
                    )
                elif is_question_action:
                    logger.info(
                        "OpenCode session %s running; cancelling poll task for question reply",
                        request.base_session_id,
                    )
                    existing_task.cancel()
                    try:
                        await existing_task
                    except asyncio.CancelledError:
                        pass
                else:
                    logger.info(
                        "OpenCode session %s already running; cancelling before new request",
                        request.base_session_id,
                    )
                    req_info = self._request_sessions.get(request.base_session_id)
                    if req_info:
                        server = await self._get_server()
                        await server.abort_session(req_info[0], req_info[1])
                        await self._wait_for_session_idle(
                            server, req_info[0], req_info[1]
                        )
                    existing_task.cancel()
                    try:
                        await existing_task
                    except asyncio.CancelledError:
                        pass
                    logger.info(
                        "OpenCode session %s cancelled; continuing with new request",
                        request.base_session_id,
                    )

            if is_modal_open:
                if hasattr(self.im_client, "open_opencode_question_modal"):
                    open_modal_task = asyncio.create_task(
                        self._open_question_modal(request, pending or {})
                    )
                else:
                    task = asyncio.create_task(self._process_message(request))
                    self._active_requests[request.base_session_id] = task
            elif pending:
                pending_payload = self._pending_questions.pop(request.base_session_id, None)
                task = asyncio.create_task(
                    self._process_question_answer(request, pending_payload or {})
                )
                self._active_requests[request.base_session_id] = task
            else:
                task = asyncio.create_task(self._process_message(request))
                self._active_requests[request.base_session_id] = task

        if open_modal_task:
            await open_modal_task
            return

        if not task:
            return

        try:
            await task
        except asyncio.CancelledError:
            # Task was cancelled (e.g. by /stop), exit gracefully without bubbling
            logger.debug(f"OpenCode task cancelled for {request.base_session_id}")
        finally:
            if self._active_requests.get(request.base_session_id) is task:
                self._active_requests.pop(request.base_session_id, None)
                self._request_sessions.pop(request.base_session_id, None)

    def _build_question_selection_note(
        self, answers_payload: List[List[str]]
    ) -> str:
        if not answers_payload:
            return ""

        if len(answers_payload) == 1:
            joined = ", ".join([value for value in answers_payload[0] if value])
            return f"已选择：{joined}" if joined else ""

        lines = []
        for idx, answers in enumerate(answers_payload, start=1):
            joined = ", ".join([value for value in answers if value])
            if joined:
                lines.append(f"Q{idx}: {joined}")
        if not lines:
            return ""
        return "已选择：\n" + "\n".join(lines)

    async def _open_question_modal(
        self, request: AgentRequest, pending: Dict[str, Any]
    ) -> None:
        trigger_id = None
        if request.context.platform_specific:
            trigger_id = request.context.platform_specific.get("trigger_id")
        if not trigger_id:
            await self.im_client.send_message(
                request.context,
                "Slack did not provide a trigger_id for the modal. Please reply with a custom message.",
            )
            return

        if not hasattr(self.im_client, "open_opencode_question_modal"):
            await self.im_client.send_message(
                request.context,
                "Modal UI is not available. Please reply with a custom message.",
            )
            return

        try:
            await self.im_client.open_opencode_question_modal(
                trigger_id=trigger_id,
                context=request.context,
                pending=pending,
            )
        except Exception as err:
            logger.error(f"Failed to open OpenCode question modal: {err}", exc_info=True)
            await self.im_client.send_message(
                request.context,
                f"Failed to open modal: {err}. Please reply with a custom message.",
            )

    async def _process_question_answer(
        self, request: AgentRequest, pending: Dict[str, Any]
    ) -> None:
        # pending contains: session_id, directory, question_id, questions
        session_id = pending.get("session_id")
        directory = pending.get("directory")
        question_id = pending.get("question_id")
        option_labels = pending.get("option_labels")
        option_labels = option_labels if isinstance(option_labels, list) else []
        question_count = pending.get("question_count")
        pending_thread_id = pending.get("thread_id")
        question_message_id = pending.get("prompt_message_id")
        if pending_thread_id and not request.context.thread_id:
            request.context.thread_id = pending_thread_id
        try:
            question_count_int = int(question_count) if question_count is not None else 1
        except Exception:
            question_count_int = 1
        question_count_int = max(1, question_count_int)

        if not session_id or not directory:
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "OpenCode question context is missing; please reply with a custom message.",
            )
            return

        server = await self._get_server()

        answer_text = None
        if request.message.startswith("opencode_question:choose:"):
            try:
                choice_idx = int(request.message.rsplit(":", 1)[-1]) - 1
                if 0 <= choice_idx < len(option_labels):
                    answer_text = str(option_labels[choice_idx]).strip()
            except Exception:
                pass

        is_modal_payload = False
        answers_payload: Optional[List[List[str]]] = None
        if request.message.startswith("opencode_question:modal:"):
            is_modal_payload = True
            try:
                payload = json.loads(request.message.split(":", 2)[-1])
                answers = payload.get("answers") if isinstance(payload, dict) else None
                if isinstance(answers, list) and answers:
                    normalized: List[List[str]] = []
                    for answer in answers:
                        if isinstance(answer, list):
                            normalized.append([str(x) for x in answer if x])
                        elif answer:
                            normalized.append([str(answer)])
                        else:
                            normalized.append([])
                    answers_payload = normalized
                    if normalized:
                        answer_text = " ".join(normalized[0])
            except Exception:
                logger.debug("Failed to parse modal answers payload")

        if answer_text is None and request.message.startswith("opencode_question:"):
            raw_payload = request.message.split(":", 2)[-1]
            answer_text = raw_payload.strip() if raw_payload else ""
        # Otherwise user replied with free text.
        if not answer_text:
            answer_text = (request.message or "").strip()

        if not answer_text:
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "Please reply with an answer.",
            )
            return

        if pending:
            self._pending_questions.pop(request.base_session_id, None)

        if not question_id:
            # Fallback resolution if the /question listing wasn't available when we first saw the toolcall.
            call_id = pending.get("call_id")
            message_id = pending.get("message_id")
            try:
                questions = await server.list_questions(directory)
                if not questions:
                    questions = await server.list_questions()
                for item in questions:
                    tool = item.get("tool") or {}
                    item_session_id = (
                        item.get("sessionID")
                        or item.get("sessionId")
                        or item.get("session_id")
                    )
                    if item_session_id != session_id:
                        continue
                    if call_id and tool.get("callID") != call_id:
                        continue
                    if message_id and tool.get("messageID") != message_id:
                        continue
                    question_id = item.get("id")
                    questions_obj = item.get("questions")
                    if isinstance(questions_obj, list):
                        question_count_int = max(1, len(questions_obj))
                    break
            except Exception as err:
                logger.warning(f"Failed to resolve OpenCode question id: {err}")

        if not question_id:
            self._pending_questions[request.base_session_id] = pending
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "OpenCode is waiting for input, but the question id could not be resolved. Please retry.",
            )
            return

        if is_modal_payload and answers_payload is not None:
            padded = answers_payload[:question_count_int]
            if len(padded) < question_count_int:
                padded.extend([[] for _ in range(question_count_int - len(padded))])
            answers_payload = padded
        else:
            answers_payload = [[answer_text] for _ in range(question_count_int)]

        if question_message_id:
            note = self._build_question_selection_note(answers_payload)
            fallback_text = pending.get("prompt_text") if isinstance(pending, dict) else None
            if note:
                try:
                    updated_text = f"{fallback_text}\n\n{note}" if fallback_text else note
                    await self.im_client.remove_inline_keyboard(
                        request.context,
                        question_message_id,
                        text=updated_text,
                        parse_mode="markdown",
                    )
                except Exception as err:
                    logger.debug(f"Failed to update question message: {err}")
            else:
                try:
                    await self.im_client.remove_inline_keyboard(
                        request.context,
                        question_message_id,
                        text=fallback_text,
                        parse_mode="markdown",
                    )
                except Exception as err:
                    logger.debug(f"Failed to remove question buttons: {err}")

        try:
            ok = await server.reply_question(question_id, directory, answers_payload)
        except Exception as err:
            logger.warning(f"Failed to reply OpenCode question: {err}")
            self._pending_questions[request.base_session_id] = pending
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                f"Failed to submit answer to OpenCode: {err}",
            )
            return

        if not ok:
            self._pending_questions[request.base_session_id] = pending
            await self.controller.emit_agent_message(
                request.context,
                "notify",
                "OpenCode did not accept the answer. Please retry.",
            )
            return

        # After replying, continue polling for final assistant output.
        baseline_message_ids: set[str] = set()
        try:
            baseline_messages = await server.list_messages(session_id, directory)
            for m in baseline_messages:
                mid = m.get("info", {}).get("id")
                if mid:
                    baseline_message_ids.add(mid)
        except Exception:
            pass

        poll_interval_seconds = 2.0
        while True:
            messages = await server.list_messages(session_id, directory)
            if messages:
                last = messages[-1]
                last_info = last.get("info", {})
                if (
                    last_info.get("role") == "assistant"
                    and last_info.get("time", {}).get("completed")
                    and last_info.get("finish") != "tool-calls"
                    and last_info.get("id") not in baseline_message_ids
                ):
                    text = self._extract_response_text(last)
                    if text:
                        await self.emit_result_message(
                            request.context,
                            text,
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
                    return

            await asyncio.sleep(poll_interval_seconds)

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
            # Get per-channel overrides from settings.json
            override_agent, override_model, override_reasoning = (
                self.controller.get_opencode_overrides(request.context)
            )

            override_agent = request.subagent_name or override_agent
            if request.subagent_name:
                override_model = request.subagent_model
                override_reasoning = request.subagent_reasoning_effort

            if request.subagent_name and not override_model:
                override_model = server.get_agent_model_from_config(request.subagent_name)
            if request.subagent_name and not override_reasoning:
                override_reasoning = server.get_agent_reasoning_effort_from_config(
                    request.subagent_name
                )

            # Determine agent to use
            # Priority: 1) channel override or prefix subagent, 2) opencode.json default, 3) None (let OpenCode decide)
            agent_to_use = override_agent
            if not agent_to_use:
                agent_to_use = server.get_default_agent_from_config()

            # Determine model to use
            # Priority: 1) channel override or prefix subagent, 2) agent's config model, 3) global opencode.json model
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
            # Priority: 1) channel override or prefix subagent, 2) agent's config, 3) global opencode.json config
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

            logger.info(
                "Starting OpenCode poll loop for %s (thread=%s, cwd=%s)",
                session_id,
                request.base_session_id,
                request.working_path,
            )

            seen_tool_calls: set[str] = set()
            emitted_assistant_messages: set[str] = set()
            poll_interval_seconds = 2.0
            final_text: Optional[str] = None

            def _relative_path(path: str) -> str:
                return self._to_relative_path(path, request.working_path)

            poll_iter = 0
            while True:
                poll_iter += 1
                try:
                    messages = await server.list_messages(
                        session_id=session_id,
                        directory=request.working_path,
                    )
                    if poll_iter % 5 == 0:
                        last_info = messages[-1].get("info", {}) if messages else {}
                        logger.info(
                            "OpenCode poll heartbeat %s iter=%s last=%s role=%s completed=%s finish=%s",
                            session_id,
                            poll_iter,
                            last_info.get("id"),
                            last_info.get("role"),
                            bool(last_info.get("time", {}).get("completed")),
                            last_info.get("finish"),
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
                        tool_state = part.get("state") or {}
                        tool_input = tool_state.get("input") or {}

                        if tool_name == "question" and tool_state.get("status") != "completed":
                            logger.info(
                                "Detected question toolcall for %s message=%s callID=%s",
                                session_id,
                                message_id,
                                part.get("callID"),
                            )

                            # Always render the question text from the tool input so the
                            # user can answer even if /question listing is temporarily empty.
                            qlist = tool_input.get("questions") if isinstance(tool_input, dict) else None
                            qlist = qlist if isinstance(qlist, list) else []

                            question_fetch_deadline = time.monotonic() + 60.0
                            question_fetch_delays = [
                                1.0,
                                2.0,
                                3.0,
                                4.0,
                                6.0,
                                8.0,
                                10.0,
                                12.0,
                                14.0,
                            ]
                            max_question_attempts = 10

                            def _question_delay(attempt_index: int) -> float:
                                if attempt_index >= len(question_fetch_delays):
                                    return 0.0
                                remaining = question_fetch_deadline - time.monotonic()
                                if remaining <= 0:
                                    return 0.0
                                return min(question_fetch_delays[attempt_index], remaining)

                            question_id = None
                            questions_listing: List[Dict[str, Any]] = []
                            list_attempts = 0
                            last_list_err: Optional[Exception] = None
                            for attempt in range(max_question_attempts):
                                list_attempts = attempt + 1
                                try:
                                    questions_listing = await server.list_questions(
                                        request.working_path
                                    )
                                    if not questions_listing:
                                        questions_listing = await server.list_questions()
                                    last_list_err = None
                                except Exception as err:
                                    last_list_err = err
                                    questions_listing = []
                                if questions_listing:
                                    break
                                if attempt < max_question_attempts - 1:
                                    delay = _question_delay(attempt)
                                    if delay <= 0:
                                        break
                                    await asyncio.sleep(delay)

                            if last_list_err and not questions_listing:
                                logger.warning(
                                    f"Failed to fetch questions listing for prompt fallback: {last_list_err}"
                                )

                            logger.info(
                                "Question list fetch for %s: dir=%s attempts=%s items=%s",
                                session_id,
                                request.working_path,
                                list_attempts,
                                len(questions_listing),
                            )

                            if questions_listing:
                                try:
                                    item_sessions = [
                                        (
                                            item.get("sessionID")
                                            or item.get("sessionId")
                                            or item.get("session_id")
                                        )
                                        for item in questions_listing
                                    ]
                                    logger.info(
                                        "Question list sessions for %s: %s",
                                        session_id,
                                        item_sessions,
                                    )
                                except Exception:
                                    pass

                            if questions_listing and not qlist:
                                try:
                                    first_questions = questions_listing[0].get("questions")
                                    if not isinstance(first_questions, list):
                                        first_questions = []
                                    listing_preview = {
                                        "id": questions_listing[0].get("id"),
                                        "sessionID": questions_listing[0].get("sessionID"),
                                        "tool": questions_listing[0].get("tool"),
                                        "questions_len": len(first_questions),
                                    }
                                    logger.info(
                                        "Question list preview for %s: %s",
                                        session_id,
                                        listing_preview,
                                    )
                                except Exception:
                                    pass

                            matched_item = None
                            if questions_listing:
                                session_items: List[Dict[str, Any]] = []
                                for item in questions_listing:
                                    item_session_id = (
                                        item.get("sessionID")
                                        or item.get("sessionId")
                                        or item.get("session_id")
                                    )
                                    if item_session_id == session_id:
                                        session_items.append(item)

                                for item in session_items:
                                    tool_meta = item.get("tool") or {}
                                    if part.get("callID") and tool_meta.get("callID") == part.get("callID"):
                                        matched_item = item
                                        break
                                    if message_id and tool_meta.get("messageID") == message_id:
                                        matched_item = item
                                        break

                                if matched_item is None and session_items:
                                    matched_item = session_items[0]

                                if matched_item is None and part.get("callID"):
                                    for item in questions_listing:
                                        tool_meta = item.get("tool") or {}
                                        if tool_meta.get("callID") == part.get("callID"):
                                            matched_item = item
                                            break

                                if matched_item is None and message_id:
                                    for item in questions_listing:
                                        tool_meta = item.get("tool") or {}
                                        if tool_meta.get("messageID") == message_id:
                                            matched_item = item
                                            break

                                if matched_item is None and len(questions_listing) == 1:
                                    matched_item = questions_listing[0]

                            if matched_item:
                                question_id = matched_item.get("id")
                                if not qlist:
                                    q_obj = matched_item.get("questions")
                                    if isinstance(q_obj, list):
                                        qlist = q_obj

                            if not qlist and message_id:
                                msg_attempts = 0
                                last_msg_err: Optional[Exception] = None
                                full_message: Optional[Dict[str, Any]] = None
                                for attempt in range(max_question_attempts):
                                    msg_attempts = attempt + 1
                                    try:
                                        full_message = await server.get_message(
                                            session_id=session_id,
                                            message_id=message_id,
                                            directory=request.working_path,
                                        )
                                        last_msg_err = None
                                    except Exception as err:
                                        last_msg_err = err
                                        full_message = None

                                    if full_message:
                                        for msg_part in full_message.get("parts", []) or []:
                                            if msg_part.get("type") != "tool":
                                                continue
                                            if msg_part.get("tool") != "question":
                                                continue
                                            msg_call_id = msg_part.get("callID") or msg_part.get("id")
                                            if call_key and msg_call_id and msg_call_id != call_key:
                                                continue
                                            msg_state = msg_part.get("state") or {}
                                            msg_input = msg_state.get("input") or {}
                                            msg_questions = (
                                                msg_input.get("questions")
                                                if isinstance(msg_input, dict)
                                                else None
                                            )
                                            if isinstance(msg_questions, list):
                                                qlist = msg_questions
                                                break
                                    if qlist:
                                        break
                                    if attempt < max_question_attempts - 1:
                                        delay = _question_delay(attempt)
                                        if delay <= 0:
                                            break
                                        await asyncio.sleep(delay)

                                if last_msg_err and not qlist:
                                    logger.warning(
                                        f"Failed to fetch full question input from message {message_id}: {last_msg_err}"
                                    )
                                if full_message is not None:
                                    parts = full_message.get("parts", []) or []
                                    tool_parts = [
                                        p for p in parts if p.get("type") == "tool"
                                    ]
                                    logger.info(
                                        "Question message fetch for %s: attempts=%s parts=%s tool_parts=%s",
                                        session_id,
                                        msg_attempts,
                                        len(parts),
                                        len(tool_parts),
                                    )

                            option_labels: list[str] = []
                            lines: list[str] = []
                            for q_idx, q in enumerate(qlist or []):
                                if not isinstance(q, dict):
                                    continue
                                title = (q.get("header") or f"Question {q_idx + 1}").strip()
                                prompt = (q.get("question") or "").strip()
                                options_raw = q.get("options")
                                options: List[Dict[str, Any]] = (
                                    options_raw if isinstance(options_raw, list) else []
                                )

                                lines.append(f"**{title}**")
                                if prompt:
                                    lines.append(prompt)

                                for idx, opt in enumerate(options, start=1):
                                    if not isinstance(opt, dict):
                                        continue
                                    label = (opt.get("label") or f"Option {idx}").strip()
                                    desc = (opt.get("description") or "").strip()
                                    if q_idx == 0:
                                        option_labels.append(label)
                                    if desc:
                                        lines.append(f"{idx}. *{label}* - {desc}")
                                    else:
                                        lines.append(f"{idx}. *{label}*")

                                if q_idx < len(qlist) - 1:
                                    lines.append("")

                            first_q = qlist[0] if qlist and isinstance(qlist[0], dict) else {}
                            multiple = bool(first_q.get("multiple"))
                            text = "\n".join(lines)
                            logger.info(
                                "Question prompt built for %s: len=%s preview=%r",
                                session_id,
                                len(text),
                                text[:200],
                            )

                            logger.info(
                                "Question prompt data for %s: qlist=%s options=%s question_id=%s call_id=%s",
                                session_id,
                                len(qlist),
                                len(option_labels),
                                question_id,
                                part.get("callID"),
                            )

                            # question_id was resolved via /question listing above when possible.
                            if not option_labels:
                                logger.warning(
                                    "Question toolcall had no options in tool_input; session=%s question_id=%s",
                                    session_id,
                                    question_id,
                                )

                            question_count = len(qlist) if qlist else 1
                            multiple = bool(first_q.get("multiple"))

                            pending_payload = {
                                "session_id": session_id,
                                "directory": request.working_path,
                                "question_id": question_id,
                                "call_id": part.get("callID"),
                                "message_id": message_id,
                                "prompt_message_id": None,
                                "prompt_text": text,
                                "option_labels": option_labels,
                                "question_count": question_count,
                                "multiple": multiple,
                                "questions": qlist,
                                "thread_id": request.context.thread_id,
                            }
                            self._pending_questions[request.base_session_id] = pending_payload

                            if multiple or question_count != 1 or len(option_labels) > 10:
                                # Multi-select or multi-question: show full text + modal button
                                modal_keyboard = None
                                if hasattr(self.im_client, "send_message_with_buttons"):
                                    from modules.im import InlineButton, InlineKeyboard

                                    modal_keyboard = InlineKeyboard(
                                        buttons=[[InlineButton(text="Choose…", callback_data="opencode_question:open_modal")]]
                                    )

                                if modal_keyboard:
                                    try:
                                        logger.info(
                                            "Sending modal open button for %s (multiple=%s questions=%s)",
                                            session_id,
                                            multiple,
                                            question_count,
                                        )
                                        question_message_id = await self.im_client.send_message_with_buttons(
                                            request.context,
                                            text,
                                            modal_keyboard,
                                            parse_mode="markdown",
                                        )
                                        if question_message_id:
                                            pending_payload["prompt_message_id"] = question_message_id
                                        return
                                    except Exception as err:
                                        logger.warning(
                                            f"Failed to send modal button, falling back to text: {err}",
                                            exc_info=True,
                                        )

                                await self.im_client.send_message(
                                    request.context,
                                    text,
                                    parse_mode="markdown",
                                )
                                return

                            # single question + single select + <=10 options -> buttons
                            if (
                                question_count == 1
                                and isinstance(first_q, dict)
                                and not multiple
                                and len(option_labels) <= 10
                                and hasattr(self.im_client, "send_message_with_buttons")
                            ):
                                from modules.im import InlineButton, InlineKeyboard

                                buttons: list[list[InlineButton]] = []
                                row: list[InlineButton] = []
                                for idx, label in enumerate(option_labels, start=1):
                                    callback = f"opencode_question:choose:{idx}"
                                    row.append(InlineButton(text=label, callback_data=callback))
                                    if len(row) == 5:
                                        buttons.append(row)
                                        row = []
                                if row:
                                    buttons.append(row)

                                keyboard = InlineKeyboard(buttons=buttons)
                                try:
                                    logger.info(
                                        "Sending single-select buttons for %s (options=%s)",
                                        session_id,
                                        len(option_labels),
                                    )
                                    question_message_id = await self.im_client.send_message_with_buttons(
                                        request.context,
                                        text,
                                        keyboard,
                                        parse_mode="markdown",
                                    )
                                    if question_message_id:
                                        pending_payload["prompt_message_id"] = question_message_id
                                    return
                                except Exception as err:
                                    logger.warning(
                                        f"Failed to send Slack buttons, falling back to text: {err}",
                                        exc_info=True,
                                    )

                            # fallback: text-only
                            try:
                                await self.im_client.send_message(
                                    request.context,
                                    text,
                                    parse_mode="markdown",
                                )
                            except Exception as err:
                                logger.error(
                                    f"Failed to send question prompt to Slack: {err}",
                                    exc_info=True,
                                )
                            return

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
