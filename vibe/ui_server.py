import base64
import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse, urlsplit, urlunsplit

import psutil
from aiohttp import ClientSession, WSMsgType
from fastapi import Request as FastAPIRequest, WebSocket, WebSocketDisconnect
from fastapi.responses import Response as FastAPIResponse

from vibe.ui_compat import CompatApp, Response, TEST_REMOTE_ADDR_HEADER, g, jsonify, redirect, request, send_file

from config import paths
from config.v2_config import CONFIG_LOCK, V2Config
from core.show_pages import (
    SHOW_CLI_EVENT_TOKEN_HEADER,
    SHOW_EVENT_WRITE_TOKEN_COOKIE,
    SHOW_EVENT_WRITE_TOKEN_HEADER,
    show_cli_event_token,
    show_event_write_token,
)
from modules.agents.catalog import AGENT_BACKENDS, supports_runtime_refresh
from vibe.runtime import get_ui_dist_path, get_working_dir
from vibe.sentry_integration import init_sentry

logger = logging.getLogger(__name__)

app = CompatApp(title="Vibe Remote UI", docs_url=None, redoc_url=None, openapi_url=None)

# Global server instance for graceful shutdown on reload
_server = None
_SHOW_RUNTIME_REQUEST_HEADER_ALLOWLIST = {
    "accept",
    "accept-language",
    "cache-control",
    "content-type",
    "if-modified-since",
    "if-none-match",
    "last-event-id",
    "pragma",
    "range",
    "user-agent",
    SHOW_EVENT_WRITE_TOKEN_HEADER.lower(),
}
_SHOW_RUNTIME_RESPONSE_HEADER_ALLOWLIST = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-language",
    "content-range",
    "content-type",
    "etag",
    "expires",
    "last-modified",
    "location",
    "sourcemap",
    "vary",
    "x-sourcemap",
}

STRUCTURED_LOG_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+-\s+([\w.]+)\s+-\s+(\w+)\s+-\s+(.*)$")
LEVEL_HINT_PATTERN = re.compile(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")
TRACEBACK_EXCEPTION_PATTERN = re.compile(
    r"^[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt|Failure|Fault|Group)(?:[:(]|$)"
)
CSRF_COOKIE_NAME = "vibe_csrf_token"
CSRF_HEADER_NAME = "X-Vibe-CSRF-Token"
REMOTE_OAUTH_COOKIE_NAME = "__Host-vibe_remote_oauth"
REMOTE_OAUTH_RETRY_PARAM = "__vibe_oauth_retry"
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOG_SOURCES = (
    ("service", "vibe_remote.log", lambda: paths.get_logs_dir() / "vibe_remote.log"),
    ("service_stdout", "service_stdout.log", lambda: paths.get_runtime_dir() / "service_stdout.log"),
    ("service_stderr", "service_stderr.log", lambda: paths.get_runtime_dir() / "service_stderr.log"),
    ("ui_stdout", "ui_stdout.log", lambda: paths.get_runtime_dir() / "ui_stdout.log"),
    ("ui_stderr", "ui_stderr.log", lambda: paths.get_runtime_dir() / "ui_stderr.log"),
)


def _is_continuation_line(line: str, previous_message: str | None = None) -> bool:
    stripped = line.lstrip()
    return (
        line[:1].isspace()
        or stripped.startswith("Traceback ")
        or stripped.startswith("During handling of the above exception")
        or stripped.startswith("File ")
        or stripped.startswith("task:")
        or stripped.startswith("^")
        or (
            previous_message is not None
            and "Traceback " in previous_message
            and bool(TRACEBACK_EXCEPTION_PATTERN.match(stripped))
        )
    )


def _fallback_log_entry(line: str, source_key: str) -> dict[str, str]:
    level_match = LEVEL_HINT_PATTERN.search(line)
    level = level_match.group(1) if level_match else "INFO"
    if level == "CRITICAL":
        level = "ERROR"
    return {
        "timestamp": "",
        "logger": source_key,
        "level": level,
        "message": line,
        "source": source_key,
    }


def _timestamp_to_sort_ns(timestamp: str) -> int | None:
    try:
        return int(datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f").timestamp() * 1_000_000_000)
    except ValueError:
        return None


def _serialize_log_entries(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "timestamp": str(entry.get("timestamp", "")),
            "logger": str(entry.get("logger", "")),
            "level": str(entry.get("level", "INFO")),
            "message": str(entry.get("message", "")),
            "source": str(entry.get("source", "")),
        }
        for entry in entries
    ]


def _runtime_pid_file_points_to_live_process(pid_path: Path) -> bool:
    from vibe import runtime

    try:
        raw_pid = pid_path.read_text(encoding="utf-8").strip()
        pid = int(raw_pid)
    except (OSError, ValueError):
        return False
    return runtime.pid_alive(pid)


def _stop_runtime_process_or_error(pid_path: Path, label: str) -> tuple[bool, str | None]:
    from vibe import runtime

    was_running = _runtime_pid_file_points_to_live_process(pid_path)
    if pid_path == paths.get_runtime_pid_path():
        stopped = runtime.stop_service()
    else:
        stopped = runtime.stop_process(pid_path)
    if was_running and stopped is False:
        return False, f"{label} did not stop"
    return True, None


def _new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _request_origin(value: str | None) -> str | None:
    if not value:
        return None

    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _current_origin() -> str:
    parsed = urlparse(request.host_url)
    scheme = parsed.scheme
    netloc = parsed.netloc

    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip()
    forwarded_host = request.headers.get("X-Forwarded-Host", "").split(",")[0].strip()

    if forwarded_proto:
        scheme = forwarded_proto
    if forwarded_host:
        netloc = forwarded_host

    return f"{scheme}://{netloc}"


def _is_mutation_guard_exempt() -> bool:
    if request.path in {"/auth/callback"}:
        return True
    if _is_cli_show_event_request():
        return True
    return (
        request.path == "/e2e/simulate-interaction"
        and os.environ.get("E2E_TEST_MODE", "").lower() in ("true", "1", "yes")
    )


def _is_cli_show_event_request() -> bool:
    token = request.headers.get(SHOW_CLI_EVENT_TOKEN_HEADER)
    return (
        request.method == "POST"
        and re.fullmatch(r"/api/show/sessions/[^/]+/events", request.path or "") is not None
        and request.headers.get("X-Vibe-Show-Client") == "cli"
        and bool(token)
        and hmac.compare_digest(token, show_cli_event_token())
    )


def _is_show_api_mutation() -> bool:
    if not (request.path.startswith("/show/") or request.path.startswith("/p/")):
        return False
    return "/api/" in request.path or "/__show/" in request.path


def _ensure_csrf_cookie(response: Response) -> Response:
    if response.headers.getlist("Set-Cookie"):
        for cookie_header in response.headers.getlist("Set-Cookie"):
            if cookie_header.startswith(f"{CSRF_COOKIE_NAME}="):
                return response

    token = request.cookies.get(CSRF_COOKIE_NAME)
    if not token:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            _new_csrf_token(),
            httponly=False,
            secure=request.is_secure,
            samesite="Strict",
            path="/",
        )
    return response


def _load_remote_access_config() -> V2Config | None:
    try:
        from core.services import settings as settings_service

        return settings_service.load_config()
    except Exception:
        logger.warning("Failed to load remote access config", exc_info=True)
        return None


def _has_cloudflare_forwarded_metadata() -> bool:
    return any(
        request.headers.get(header)
        for header in (
            "CF-Connecting-IP",
            "CF-Ray",
            "CF-Visitor",
            "CF-IPCountry",
        )
    )


def _has_forwarded_metadata() -> bool:
    """Detect any sign that the request traversed a reverse proxy.

    When any forwarded header is set, request.remote_addr no longer reliably
    identifies the actual client (a same-host proxy makes external attackers
    look like loopback / private peers), so authorization paths that lean on a
    private/loopback peer must refuse the request unless we have an explicit
    trusted-proxy chain.
    """
    forwarded_headers = (
        "Forwarded",
        "X-Forwarded-For",
        "X-Forwarded-Host",
        "X-Forwarded-Proto",
        "X-Forwarded-Port",
        "X-Real-IP",
        "X-Original-Forwarded-For",
        "True-Client-IP",
    )
    if any(request.headers.get(header) for header in forwarded_headers):
        return True
    return _has_cloudflare_forwarded_metadata()


def _is_loopback_origin_proxy_request() -> bool:
    if not _is_loopback_peer() or not _is_loopback_host(request.host):
        return False
    if request.headers.get("Forwarded") or request.headers.get("X-Forwarded-For"):
        return False
    client_ip_headers = (
        "X-Real-IP",
        "X-Original-Forwarded-For",
        "True-Client-IP",
    )
    if any(request.headers.get(header) for header in client_ip_headers):
        return False
    if _has_cloudflare_forwarded_metadata():
        return False
    return bool(request.headers.get("X-Forwarded-Host") or request.headers.get("X-Forwarded-Proto"))


def _is_loopback_peer() -> bool:
    remote_addr = (request.remote_addr or "").strip()
    if remote_addr == "localhost":
        return True
    try:
        address = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped and mapped.is_loopback)


def _is_loopback_host(value: str | None) -> bool:
    host = _normalized_host(value)
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# RFC 6598 shared address space (CGNAT). Python's ipaddress module classifies
# this range as neither private nor global, but in practice overlay networks
# such as Tailscale assign 100.x.y.z addresses that should be trusted as local
# setup-host peers when the request's Host header otherwise matches.
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")
_TAILSCALE_IPV6_ADDRESS_SPACE = ipaddress.ip_network("fd7a:115c:a1e0::/48")

# Networks that are scoped by the overlay/link itself rather than by the
# kernel's interface routing, so peers anywhere in the block are trusted
# in lieu of a tighter same-subnet check:
#   * 100.64.0.0/10 — Tailscale CGNAT. Tailscale assigns each peer a /32 in
#     this range and routes peers via its overlay; legitimate peers can be
#     anywhere in the /10 even though they share the same logical network.
#   * fd7a:115c:a1e0::/48 — Tailscale IPv6 ULA. Like the IPv4 CGNAT
#     range, Tailscale can assign interface addresses as host routes while
#     legitimate peers live elsewhere in the overlay prefix.
#   * 169.254.0.0/16 / fe80::/10 — link-local. Confined to the same L2
#     segment by the kernel.
_OVERLAY_TRUST_NETWORKS_V4 = (
    ipaddress.IPv4Network("100.64.0.0/10"),
    ipaddress.IPv4Network("169.254.0.0/16"),
)
_OVERLAY_TRUST_NETWORKS_V6 = (
    _TAILSCALE_IPV6_ADDRESS_SPACE,
    ipaddress.IPv6Network("fe80::/10"),
)
_WILDCARD_TRUST_LAN_INTERFACE_PREFIXES = (
    "en",
    "eth",
    "ethernet",
    "local area connection",
    "wi-fi",
    "wifi",
    "wl",
    "wwan",
)
_WILDCARD_TRUST_OVERLAY_INTERFACE_PREFIXES = (
    "tailscale",
)
_TAILSCALE_UTUN_INTERFACE_PREFIXES = ("utun",)
_TAILSCALE_IP_CACHE_TTL_SECONDS = 30.0
_TAILSCALE_IP_CACHE: tuple[float, frozenset[ipaddress._BaseAddress]] | None = None
_TAILSCALE_PEER_CACHE_TTL_SECONDS = 30.0
_TAILSCALE_PEER_CACHE: dict[ipaddress._BaseAddress, tuple[float, bool]] = {}
_CONTAINER_CGROUP_MARKERS = ("docker", "kubepods", "containerd", "libpod", "podman")


def _is_private_address(address: ipaddress._BaseAddress) -> bool:
    if address.is_loopback or address.is_private or address.is_link_local:
        return True
    return isinstance(address, ipaddress.IPv4Address) and address in _SHARED_ADDRESS_SPACE


def _is_containerized_runtime() -> bool:
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    for cgroup_path in (Path("/proc/self/cgroup"), Path("/proc/1/cgroup")):
        try:
            cgroup = cgroup_path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(marker in cgroup for marker in _CONTAINER_CGROUP_MARKERS):
            return True
    return False


def _is_private_peer() -> bool:
    address = _request_peer_address()
    return address is not None and _is_private_address(address)


def _request_peer_address() -> ipaddress._BaseAddress | None:
    remote_addr = (request.remote_addr or "").strip()
    if not remote_addr or remote_addr == "localhost":
        return None
    try:
        address = ipaddress.ip_address(remote_addr)
    except ValueError:
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped or address


def _local_interface_network(
    setup_address: ipaddress._BaseAddress,
    interface_filter: Callable[[str, ipaddress._BaseAddress], bool] | None = None,
) -> ipaddress._BaseNetwork | None:
    """Return the network ``setup_host`` is configured on locally.

    Reads the interface's actual netmask via ``psutil.net_if_addrs`` so
    the trust scope mirrors the kernel's pre-wildcard interface filtering
    exactly — a /16 LAN, a /20 corporate network, and a non-/64 IPv6
    network all get their real prefix instead of a fixed estimate.

    Returns None when ``setup_host`` is not configured on any local
    interface or psutil cannot enumerate them; the caller denies trust
    in that case so we never widen the application-layer scope beyond
    what the kernel would have permitted.
    """
    try:
        interfaces = psutil.net_if_addrs()
    except Exception:
        return None
    target_family = socket.AF_INET if setup_address.version == 4 else socket.AF_INET6
    for interface_name, addrs in interfaces.items():
        for snic in addrs:
            if snic.family != target_family:
                continue
            address_str = (snic.address or "").split("%", 1)[0]
            try:
                addr = ipaddress.ip_address(address_str)
            except ValueError:
                continue
            if addr != setup_address:
                continue
            if interface_filter is not None and not interface_filter(interface_name, addr):
                continue
            netmask = snic.netmask
            if not netmask:
                continue
            prefix = _netmask_to_prefix(netmask, addr.version)
            if prefix is None:
                continue
            try:
                return ipaddress.ip_network(f"{addr}/{prefix}", strict=False)
            except ValueError:
                continue
    return None


def _netmask_to_prefix(netmask: str, version: int) -> int | None:
    """Convert ``psutil``'s netmask string to a prefix length.

    psutil returns IPv4 netmasks as dotted strings (``255.255.255.0``)
    and IPv6 netmasks as hex strings (``ffff:ffff:ffff:ff00::``).
    ``ipaddress.ip_network`` only accepts the dotted form for IPv4 and
    requires an integer prefix for IPv6, so we normalize to a prefix
    length here. Returns None for malformed or non-contiguous masks.
    """
    try:
        if version == 4:
            mask_int = int(ipaddress.IPv4Address(netmask))
            width = 32
        else:
            mask_int = int(ipaddress.IPv6Address(netmask))
            width = 128
    except (ipaddress.AddressValueError, ValueError):
        return None
    if mask_int == 0:
        return 0
    inverted = (~mask_int) & ((1 << width) - 1)
    if inverted & (inverted + 1):
        # Non-contiguous mask — refuse rather than guess.
        return None
    prefix = width - inverted.bit_length()
    return prefix


def _setup_host_trust_network(setup_address: ipaddress._BaseAddress) -> ipaddress._BaseNetwork | None:
    """Return the network setup-host trust should extend to, or None to deny.

    Overlay networks (Tailscale CGNAT, link-local) trust the entire block
    because the overlay routing or kernel link-local scoping handles peer
    isolation; legitimate peers can be anywhere in the block. RFC1918 and
    ULA setup hosts derive the network from the actual interface netmask
    via :func:`_local_interface_network` so the application-layer scope
    matches the kernel's pre-wildcard interface filtering. Returning None
    means the scope cannot be determined and the caller must deny trust.
    """
    if setup_address.version == 4:
        for overlay in _OVERLAY_TRUST_NETWORKS_V4:
            if setup_address in overlay:
                return overlay
    elif setup_address.version == 6:
        for overlay in _OVERLAY_TRUST_NETWORKS_V6:
            if setup_address in overlay:
                return overlay
    return _local_interface_network(setup_address)


def _peer_shares_setup_host_network(setup_address: ipaddress._BaseAddress) -> bool:
    """Require the peer to share setup_host's interface-level subnet.

    Compensates for the wildcard bind in the tunnel-on path. Without this,
    a 192.168/16 LAN peer could spoof ``Host=<tailscale_setup_host>`` on
    a different interface and inherit setup-host trust. Subnet size comes
    from :func:`_setup_host_trust_network`, which keeps overlay networks
    (Tailscale, link-local) broad and otherwise mirrors the actual
    interface netmask via :func:`_local_interface_network`.
    """
    remote_addr = (request.remote_addr or "").strip()
    if not remote_addr or remote_addr == "localhost":
        return False
    try:
        peer = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    if peer.version != setup_address.version:
        mapped = getattr(peer, "ipv4_mapped", None)
        if mapped is None or mapped.version != setup_address.version:
            return False
        peer = mapped
    network = _setup_host_trust_network(setup_address)
    if network is None:
        return False
    return peer in network


def _env_flag_enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def _has_loopback_only_docker_port_binding() -> bool:
    bind_host = os.environ.get("VIBE_REMOTE_DOCKER_LOOPBACK_BIND_HOST")
    if not bind_host:
        return False
    return _is_loopback_host(bind_host)


def _is_trusted_docker_peer() -> bool:
    if not _env_flag_enabled("VIBE_REMOTE_ALLOW_DOCKER_LOOPBACK_PEERS"):
        return False
    if not _has_loopback_only_docker_port_binding():
        return False

    remote_addr = (request.remote_addr or "").strip()
    try:
        address = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    address = getattr(address, "ipv4_mapped", None) or address

    cidrs = os.environ.get("VIBE_REMOTE_DOCKER_LOOPBACK_PEER_CIDRS", "172.16.0.0/12,192.168.65.0/24")
    for raw_network in cidrs.split(","):
        raw_network = raw_network.strip()
        if not raw_network:
            continue
        try:
            network = ipaddress.ip_network(raw_network, strict=False)
        except ValueError:
            continue
        if address in network:
            return True
    return False


def _is_trusted_docker_loopback_probe() -> bool:
    if request.method not in {"GET", "HEAD"}:
        return False
    if request.path not in {"/health", "/status"}:
        return False
    if _has_forwarded_metadata():
        return False
    if not _is_loopback_host(request.host):
        return False
    return _is_trusted_docker_peer()


def _has_docker_loopback_probe_shape() -> bool:
    return (
        request.method in {"GET", "HEAD"}
        and request.path in {"/health", "/status"}
        and not _has_forwarded_metadata()
        and _is_loopback_host(request.host)
        and not _is_loopback_peer()
    )


def _is_wildcard_setup_host(setup_host: str) -> bool:
    return setup_host in {"0.0.0.0", "::", "*"}


def _is_tailscale_overlay_address(address: ipaddress._BaseAddress) -> bool:
    return (
        isinstance(address, ipaddress.IPv4Address)
        and address in _SHARED_ADDRESS_SPACE
        or isinstance(address, ipaddress.IPv6Address)
        and address in _TAILSCALE_IPV6_ADDRESS_SPACE
    )


def _tailscale_cli_candidates() -> list[str]:
    candidates: list[str] = []
    path = shutil.which("tailscale")
    if path:
        candidates.append(path)
    macos_app_cli = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    if macos_app_cli.exists():
        candidates.append(str(macos_app_cli))
    return list(dict.fromkeys(candidates))


def _tailscale_local_addresses() -> frozenset[ipaddress._BaseAddress]:
    global _TAILSCALE_IP_CACHE

    now = time.monotonic()
    if _TAILSCALE_IP_CACHE is not None:
        cached_at, cached_addresses = _TAILSCALE_IP_CACHE
        if now - cached_at < _TAILSCALE_IP_CACHE_TTL_SECONDS:
            return cached_addresses

    addresses: set[ipaddress._BaseAddress] = set()
    env = {**os.environ, "TAILSCALE_BE_CLI": "1"}
    for candidate in _tailscale_cli_candidates():
        try:
            result = subprocess.run(
                [candidate, "ip"],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
                env=env,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        for line in result.stdout.splitlines():
            try:
                address = ipaddress.ip_address(line.strip())
            except ValueError:
                continue
            if _is_tailscale_overlay_address(address):
                addresses.add(address)
        if addresses:
            break

    cached = frozenset(addresses)
    _TAILSCALE_IP_CACHE = (now, cached)
    return cached


def _tailscale_whois(peer_address: ipaddress._BaseAddress) -> dict[str, Any] | None:
    env = {**os.environ, "TAILSCALE_BE_CLI": "1"}
    for candidate in _tailscale_cli_candidates():
        try:
            result = subprocess.run(
                [candidate, "whois", "--json", str(peer_address)],
                capture_output=True,
                text=True,
                timeout=1.5,
                check=False,
                env=env,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        try:
            payload = json.loads(result.stdout)
        except Exception:
            continue
        return payload if isinstance(payload, dict) else None
    return None


def _json_list(payload: dict[str, Any], *keys: str) -> list[Any]:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _is_tailscale_host_route(network: ipaddress._BaseNetwork) -> bool:
    if network.prefixlen != network.max_prefixlen:
        return False
    return _is_tailscale_overlay_address(network.network_address)


def _is_trusted_tailscale_peer(peer_address: ipaddress._BaseAddress) -> bool:
    global _TAILSCALE_PEER_CACHE

    if not _is_tailscale_overlay_address(peer_address):
        return False

    now = time.monotonic()
    cached = _TAILSCALE_PEER_CACHE.get(peer_address)
    if cached is not None:
        cached_at, trusted = cached
        if now - cached_at < _TAILSCALE_PEER_CACHE_TTL_SECONDS:
            return trusted

    payload = _tailscale_whois(peer_address)
    trusted = False
    if payload is not None:
        machine = payload.get("Machine") or payload.get("machine") or {}
        if isinstance(machine, dict):
            addresses = set()
            for raw_address in _json_list(machine, "Addresses", "addresses"):
                try:
                    addresses.add(ipaddress.ip_address(str(raw_address)))
                except ValueError:
                    continue
            allowed_networks = []
            for raw_network in _json_list(machine, "AllowedIPs", "allowedIPs", "allowedIps"):
                try:
                    allowed_networks.append(ipaddress.ip_network(str(raw_network), strict=False))
                except ValueError:
                    continue
            trusted = bool(addresses and peer_address in addresses and allowed_networks)
            if trusted:
                trusted = all(_is_tailscale_host_route(network) for network in allowed_networks)

    _TAILSCALE_PEER_CACHE[peer_address] = (now, trusted)
    return trusted


def _allows_wildcard_setup_host_trust(interface_name: str, address: ipaddress._BaseAddress) -> bool:
    normalized_name = interface_name.lower()
    if _is_tailscale_overlay_address(address):
        if normalized_name.startswith(_WILDCARD_TRUST_OVERLAY_INTERFACE_PREFIXES):
            return True
        if normalized_name.startswith(_TAILSCALE_UTUN_INTERFACE_PREFIXES):
            return address in _tailscale_local_addresses()
        return False
    if _is_containerized_runtime():
        return False
    return normalized_name.startswith(_WILDCARD_TRUST_LAN_INTERFACE_PREFIXES)


def _is_wildcard_setup_host_request(config: V2Config | None) -> bool:
    """Treat wildcard binds as local only through an actual private interface.

    ``0.0.0.0``/``::`` is a listen address, not a trusted browser host. For
    compatibility with LAN direct access, accept requests to a concrete local
    private IP on a small allowlist of LAN/overlay interfaces while keeping
    arbitrary private Host spoofing, container bridge networks, and public-IP
    exposure behind the normal remote-access checks.
    """
    if config is None:
        return False
    setup_host = _normalized_host(getattr(config.ui, "setup_host", ""))
    if not _is_wildcard_setup_host(setup_host):
        return False
    if _has_forwarded_metadata():
        return False

    try:
        host_address = ipaddress.ip_address(_normalized_host(request.host))
    except ValueError:
        return False
    if host_address.is_unspecified:
        return False
    if not _is_private_address(host_address):
        return False
    if _local_interface_network(host_address, interface_filter=_allows_wildcard_setup_host_trust) is None:
        return False
    if not _is_private_peer():
        return False
    if _is_tailscale_overlay_address(host_address):
        peer_address = _request_peer_address()
        return peer_address is not None and _is_trusted_tailscale_peer(peer_address)
    return _peer_shares_setup_host_network(host_address)


def _is_setup_host_request(config: V2Config | None) -> bool:
    if config is None:
        return False
    setup_host = _normalized_host(getattr(config.ui, "setup_host", ""))
    if not setup_host:
        return False
    if _is_wildcard_setup_host(setup_host):
        return _is_wildcard_setup_host_request(config)
    if _is_loopback_host(setup_host):
        return False
    # Only trust setup-host requests when setup_host parses to a private/CGNAT
    # IP. Public hostnames or public IPs cannot be assumed safe: a reverse proxy
    # on the same machine would make request.remote_addr look like a private
    # peer even for external attackers, so the host-match + private-peer pair
    # is not sufficient on its own.
    try:
        setup_address = ipaddress.ip_address(setup_host)
    except ValueError:
        return False
    if not _is_private_address(setup_address):
        return False
    if _normalized_host(request.host) != setup_host:
        return False
    # Any forwarded header (including non-Cloudflare proxies like nginx /
    # Caddy / Traefik) means we cannot trust request.remote_addr to identify
    # the actual client, so refuse the setup-host trust path entirely.
    if _has_forwarded_metadata():
        return False
    if not _is_private_peer():
        return False
    # When the Avibe Cloud tunnel is on, the UI binds to a wildcard so the
    # local cloudflared origin can reach setup_host regardless of which
    # interface it lives on. Wildcard means the kernel no longer drops
    # cross-interface traffic, so we have to re-enforce "peer shares the
    # setup_host interface subnet" at the application layer to prevent a
    # peer on a different interface from spoofing Host=<setup_host>. When
    # the tunnel is off, the kernel binds to setup_host directly and that
    # interface filtering is already in force; adding the subnet gate
    # here would just block legitimate routed peers (e.g. a 10.50/16
    # client reaching setup_host=10.1.2.3 across a routed corporate net).
    if _is_tunnel_wildcard_bind(config):
        return _peer_shares_setup_host_network(setup_address)
    return True


def _is_tunnel_wildcard_bind(config: V2Config) -> bool:
    cloud = getattr(getattr(config, "remote_access", None), "vibe_cloud", None)
    return bool(cloud is not None and cloud.enabled)


def _is_local_request(config: V2Config | None = None) -> bool:
    if _has_forwarded_metadata():
        return False
    if _is_loopback_peer() and _is_loopback_host(request.host):
        return True
    return _is_setup_host_request(config)


def _normalized_host(value: str | None) -> str:
    raw_host = (value or "").lower().strip()
    if raw_host.startswith("[") and "]" in raw_host:
        host = raw_host[1 : raw_host.index("]")]
    elif raw_host.count(":") > 1:
        host = raw_host
    else:
        host = raw_host.split(":", 1)[0]
    return host.rstrip(".")


def _is_remote_access_request(config: V2Config) -> bool:
    public_host = _remote_access_public_host(config)
    if not public_host:
        return False
    return _normalized_host(request.host) == public_host


def _remote_access_public_host(config: V2Config) -> str | None:
    public_url = (config.remote_access.vibe_cloud.public_url or "").strip()
    if not public_url:
        return ""
    parsed = urlparse(public_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        return None
    return _normalized_host(parsed.hostname)


def _remote_access_public_url_invalid(config: V2Config) -> bool:
    cloud = config.remote_access.vibe_cloud
    return bool(cloud.enabled and not _remote_access_public_host(config))


def _remote_access_snapshot(config: V2Config) -> dict[str, Any]:
    return {
        "provider": config.remote_access.provider,
        "vibe_cloud": config.remote_access.vibe_cloud.__dict__.copy(),
    }


def _remote_access_settings_changed(previous: V2Config | None, current: V2Config, payload: dict) -> bool:
    if "remote_access" not in payload:
        return False
    if previous is None:
        return bool(_remote_access_snapshot(current)["vibe_cloud"].get("enabled"))
    return _remote_access_snapshot(previous) != _remote_access_snapshot(current)


def _should_rotate_remote_session_secret(previous: V2Config | None, current: V2Config, payload: dict) -> bool:
    if "remote_access" not in payload or previous is None:
        return False
    previous_cloud = previous.remote_access.vibe_cloud
    current_cloud = current.remote_access.vibe_cloud
    return bool(previous_cloud.enabled and not current_cloud.enabled and current_cloud.session_secret)


def _remote_auth_exempt_path() -> bool:
    path = request.path
    return (
        path == "/health"
        or path == "/auth/callback"
        or path == "/auth/logout"
        or path == "/api/session"
        or path == "/api/csrf-token"
        or path.startswith("/assets/")
        or path.startswith("/p/")
        or path == "/favicon.ico"
    )


def _remote_auth_exempt_before_host_validation() -> bool:
    return request.path in {"/auth/callback", "/auth/logout", "/api/session", "/api/csrf-token"} or request.path.startswith(
        "/assets/"
    ) or request.path == "/favicon.ico"


def _oauth_cookie_signature(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _make_oauth_cookie(secret: str, payload: dict[str, Any]) -> str:
    payload_text = quote(json.dumps(payload, separators=(",", ":")), safe="")
    signature = _oauth_cookie_signature(secret, payload_text)
    return f"{payload_text}.{signature}"


def _read_oauth_cookie(secret: str, value: str | None) -> dict[str, Any] | None:
    if not value or "." not in value:
        return None
    payload_text, signature = value.rsplit(".", 1)
    if not hmac.compare_digest(signature, _oauth_cookie_signature(secret, payload_text)):
        return None
    try:
        payload = json.loads(unquote(payload_text))
    except Exception:
        return None
    if int(payload.get("exp", 0)) <= int(datetime.now().timestamp()):
        return None
    return payload if isinstance(payload, dict) else None


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def _make_oauth_state(secret: str, *, next_target: str, retry: bool = False) -> str:
    payload = {
        "v": 1,
        "r": secrets.token_urlsafe(18),
        "next": next_target,
        "retry": bool(retry),
        "exp": int(datetime.now().timestamp()) + 300,
    }
    payload_text = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _b64url_encode(hmac.new(secret.encode("utf-8"), payload_text.encode("ascii"), hashlib.sha256).digest())
    return f"vr1.{payload_text}.{signature}"


def _read_oauth_state(secret: str, value: str | None) -> dict[str, Any] | None:
    if not value or not value.startswith("vr1."):
        return None
    try:
        _, payload_text, signature = value.split(".", 2)
    except ValueError:
        return None
    expected = _b64url_encode(hmac.new(secret.encode("utf-8"), payload_text.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_b64url_decode(payload_text).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("v") != 1:
        return None
    if int(payload.get("exp", 0)) <= int(datetime.now().timestamp()):
        return None
    return payload


def _safe_remote_redirect_target(value: Any) -> str:
    if not isinstance(value, str):
        return "/"
    target = value.strip()
    if not target.startswith("/") or target.startswith(("//", "/\\")):
        return "/"
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/"):
        return "/"
    return urlunsplit(("", "", parsed.path or "/", parsed.query, ""))


def _strip_oauth_retry_param(value: str) -> str:
    target = _safe_remote_redirect_target(value)
    parsed = urlsplit(target)
    query = urlencode(
        [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True) if key != REMOTE_OAUTH_RETRY_PARAM]
    )
    return urlunsplit(("", "", parsed.path or "/", query, ""))


def _add_oauth_retry_param(value: str) -> str:
    target = _strip_oauth_retry_param(value)
    parsed = urlsplit(target)
    params = parse_qsl(parsed.query, keep_blank_values=True)
    params.append((REMOTE_OAUTH_RETRY_PARAM, "1"))
    return urlunsplit(("", "", parsed.path or "/", urlencode(params), ""))


def _oauth_callback_arg(name: str) -> str | None:
    return request.args.get(name) or request.args.get(f"amp;{name}")


def _redirect_to_vibe_cloud_login(config: V2Config):
    from vibe import remote_access

    cloud = config.remote_access.vibe_cloud
    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    raw_next = request.full_path if request.query_string else request.path
    next_target = _strip_oauth_retry_param(raw_next)
    state = _make_oauth_state(
        cloud.session_secret,
        next_target=next_target,
        retry=request.args.get(REMOTE_OAUTH_RETRY_PARAM) == "1",
    )
    nonce = secrets.token_urlsafe(24)
    oauth_cookie = _make_oauth_cookie(
        cloud.session_secret,
        {
            "state": state,
            "nonce": nonce,
            "code_verifier": code_verifier,
            "next": next_target,
            "exp": int(datetime.now().timestamp()) + 300,
        },
    )
    response = Response(status=302)
    response.headers["Location"] = remote_access.authorization_url(config, state, nonce, code_challenge)
    response.set_cookie(
        REMOTE_OAUTH_COOKIE_NAME,
        oauth_cookie,
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
    )
    return response


def _restart_vibe_cloud_login_from_state(config: V2Config, state: str | None):
    cloud = config.remote_access.vibe_cloud
    payload = _read_oauth_state(cloud.session_secret, state)
    if not payload or payload.get("retry"):
        return None
    next_target = _safe_remote_redirect_target(payload.get("next"))
    response = redirect(_add_oauth_retry_param(next_target))
    response.delete_cookie(REMOTE_OAUTH_COOKIE_NAME, path="/", secure=True, samesite="Lax")
    return response


@app.before_request
def enforce_remote_access_cookie():
    config = _load_remote_access_config()
    if _remote_auth_exempt_before_host_validation():
        return None
    local_request = _is_local_request(config)
    docker_probe_request = _is_trusted_docker_loopback_probe()
    if config is None:
        if local_request or docker_probe_request:
            return None
        return jsonify({"ok": False, "error": "remote_access_config_unavailable"}), 503
    if _remote_access_public_url_invalid(config) and not (local_request or docker_probe_request):
        return jsonify({"ok": False, "error": "remote_access_public_url_invalid"}), 503
    remote_request = _is_remote_access_request(config)
    if not remote_request:
        if _is_loopback_origin_proxy_request():
            return None
        if not local_request and not docker_probe_request:
            return jsonify({"ok": False, "error": "remote_access_host_mismatch"}), 503
        return None
    if _remote_auth_exempt_path():
        return None
    from vibe import remote_access

    if not config.remote_access.vibe_cloud.enabled:
        return jsonify({"ok": False, "error": "remote_access_disabled"}), 503
    if not config.remote_access.vibe_cloud.session_secret:
        return jsonify({"ok": False, "error": "remote_access_session_secret_missing"}), 503
    payload = remote_access.parse_session_cookie(config, request.cookies.get(remote_access.SESSION_COOKIE_NAME))
    if payload is not None:
        if remote_access.session_needs_renewal(payload):
            g.remote_session_renew = (str(payload.get("email", "")), str(payload.get("sub", "")))
        return None
    if request.method == "GET":
        return _redirect_to_vibe_cloud_login(config)
    return jsonify({"ok": False, "error": "remote_access_login_required"}), 401


@app.before_request
def protect_mutating_ui_requests():
    if request.method not in MUTATING_METHODS:
        return None
    if _is_mutation_guard_exempt():
        return None

    source = _request_origin(request.headers.get("Origin")) or _request_origin(request.headers.get("Referer"))
    if not source:
        return jsonify({"ok": False, "message": "Forbidden: missing origin header"}), 403

    if source != _current_origin():
        return jsonify({"ok": False, "message": "Forbidden: invalid origin"}), 403

    if _is_show_api_mutation():
        return None

    csrf_cookie = request.cookies.get(CSRF_COOKIE_NAME, "")
    csrf_header = request.headers.get(CSRF_HEADER_NAME, "")
    if not csrf_cookie or not csrf_header or not hmac.compare_digest(csrf_cookie, csrf_header):
        return jsonify({"ok": False, "message": "Forbidden: invalid csrf token"}), 403

    return None


@app.after_request
def add_csrf_cookie(response: Response) -> Response:
    return _ensure_csrf_cookie(response)


@app.after_request
def renew_remote_access_cookie(response: Response) -> Response:
    # Logout handler explicitly clears the session cookie; never re-issue it.
    if getattr(g, "remote_session_logout", False):
        return response
    renew = getattr(g, "remote_session_renew", None)
    if not renew:
        return response
    # Only slide the session cookie when the request was actually accepted.
    # The renew flag is set in the early `enforce_remote_access_cookie`
    # before-request hook, but later guards (e.g. CSRF/origin checks in
    # `protect_mutating_ui_requests`) may still reject the request. Refreshing
    # the cookie on a rejected response would let repeated failed mutations
    # keep a stolen session alive indefinitely without any successful
    # authenticated action.
    if response.status_code >= 400:
        return response
    config = _load_remote_access_config()
    if config is None or not config.remote_access.vibe_cloud.session_secret:
        return response
    from vibe import remote_access

    email, subject = renew
    response.set_cookie(
        remote_access.SESSION_COOKIE_NAME,
        remote_access.make_session_cookie(config, email, subject),
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
        max_age=remote_access.SESSION_TTL_SECONDS,
    )
    return response


def _read_log_entries(log_path: Path, source_key: str, lines: int) -> tuple[list[dict[str, Any]], int]:
    if not log_path.exists():
        return [], 0

    with log_path.open("r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
    recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
    file_sort_ns = log_path.stat().st_mtime_ns
    first_recent_line_index = len(all_lines) - len(recent_lines)

    logs_list: list[dict[str, Any]] = []
    for line_offset, raw_line in enumerate(recent_lines):
        line = raw_line.rstrip("\n")
        match = STRUCTURED_LOG_PATTERN.match(line)
        if match:
            parsed_timestamp = match.group(1)
            logs_list.append(
                {
                    "timestamp": parsed_timestamp,
                    "logger": match.group(2),
                    "level": match.group(3),
                    "message": match.group(4),
                    "source": source_key,
                    "_sort_ns": _timestamp_to_sort_ns(parsed_timestamp) or file_sort_ns,
                    "_sort_index": first_recent_line_index + line_offset,
                }
            )
            continue

        if not line:
            continue

        if logs_list and _is_continuation_line(line, logs_list[-1]["message"]):
            logs_list[-1]["message"] += "\n" + line
            continue

        fallback_entry = _fallback_log_entry(line, source_key)
        fallback_entry["_sort_ns"] = file_sort_ns
        fallback_entry["_sort_index"] = first_recent_line_index + line_offset
        logs_list.append(fallback_entry)

    return logs_list, len(all_lines)


def _resolve_log_sources() -> list[dict[str, Any]]:
    resolved = [
        {
            "key": "all",
            "filename": "*",
            "path": "",
            "exists": True,
        }
    ]
    for key, filename, path_factory in LOG_SOURCES:
        path = path_factory()
        resolved.append(
            {
                "key": key,
                "filename": filename,
                "path": str(path),
                "exists": path.exists(),
            }
        )
    return resolved


# =============================================================================
# Error Handler
# =============================================================================


@app.errorhandler(Exception)
def handle_exception(e):
    """Global exception handler - ensures all errors return JSON."""
    # Preserve HTTP status codes for client errors (4xx)
    status_code = getattr(e, "status_code", None)
    detail = getattr(e, "detail", None)
    if isinstance(status_code, int) and 400 <= status_code < 500:
        return jsonify({"error": detail or str(e)}), status_code

    # Log and return 500 for unexpected server errors
    logger.exception("Unhandled exception in UI server")
    return jsonify({"error": str(e)}), 500


# =============================================================================
# GET Endpoints
# =============================================================================


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/status")
def status():
    from vibe import runtime

    payload = runtime.read_status()
    pid_path = paths.get_runtime_pid_path()
    pid = pid_path.read_text(encoding="utf-8").strip() if pid_path.exists() else None
    try:
        running = bool(pid and pid.isdigit() and runtime.pid_alive(int(pid)))
    except Exception as exc:
        logger.warning("Failed to inspect service pid %s: %s", pid, exc)
        running = False
    payload["running"] = running
    payload["pid"] = int(pid) if pid and pid.isdigit() else None
    if running:
        payload["service_pid"] = payload.get("service_pid") or payload["pid"]
    elif payload.get("state") == "running":
        runtime.write_status("stopped", "process not running", None, payload.get("ui_pid"))
        payload = runtime.read_status()
        payload["running"] = False
        payload["pid"] = None
    return jsonify(payload)


@app.websocket("/ws/echo")
async def websocket_echo(websocket: WebSocket):
    if os.environ.get("VIBE_UI_ENABLE_WS_ECHO", "").lower() not in {"1", "true", "yes", "on"}:
        await websocket.close(code=1008)
        return

    client_host = websocket.client.host if websocket.client else ""
    if client_host != "testclient":
        try:
            client_address = ipaddress.ip_address(client_host)
        except ValueError:
            client_address = None
        if client_address is None or not client_address.is_loopback:
            await websocket.close(code=1008)
            return

    await websocket.accept()
    try:
        while True:
            message = await websocket.receive_text()
            await websocket.send_text(f"echo: {message}")
    except WebSocketDisconnect:
        return


@app.websocket("/show/{session_id}/__vite_hmr")
async def show_runtime_hmr_websocket(websocket: WebSocket, session_id: str):
    from core.show_pages import ShowPageStore

    if not _show_runtime_websocket_authorized(websocket):
        await websocket.close(code=1008)
        return

    store = ShowPageStore()
    try:
        page = store.get(session_id)
        if page is None or page.visibility != "private":
            await websocket.close(code=1008)
            return
    finally:
        store.close()

    await websocket.accept(subprotocol="vite-hmr")
    try:
        await _proxy_show_runtime_websocket(websocket, session_id)
    except Exception:
        logger.debug("Show runtime HMR websocket unavailable", exc_info=True)
        await websocket.close(code=1011)


@app.websocket("/p/{share_id}/__vite_hmr")
async def public_show_runtime_hmr_websocket(websocket: WebSocket, share_id: str):
    from core.show_pages import ShowPageStore

    store = ShowPageStore()
    try:
        page = store.get_by_share_id(share_id)
        if page is None or page.visibility != "public":
            await websocket.close(code=1008)
            return
        session_id = page.session_id
    finally:
        store.close()

    await websocket.accept(subprotocol="vite-hmr")
    try:
        await _proxy_show_runtime_websocket(
            websocket,
            session_id,
            external_prefix=f"/p/{quote(share_id, safe='')}",
        )
    except Exception:
        logger.debug("Public show runtime HMR websocket unavailable", exc_info=True)
        await websocket.close(code=1011)


def _show_runtime_websocket_authorized(websocket: WebSocket) -> bool:
    config = _load_remote_access_config()
    if config is None:
        return _websocket_is_local_request(websocket)
    if _websocket_is_local_request(websocket, config):
        return True
    if _websocket_normalized_host(websocket) != _remote_access_public_host(config):
        return False
    from vibe import remote_access

    if not config.remote_access.vibe_cloud.enabled or not config.remote_access.vibe_cloud.session_secret:
        return False
    return remote_access.parse_session_cookie(
        config,
        websocket.cookies.get(remote_access.SESSION_COOKIE_NAME),
    ) is not None


def _websocket_is_local_request(websocket: WebSocket, config: V2Config | None = None) -> bool:
    if _websocket_has_forwarded_metadata(websocket):
        return False
    client_host = _websocket_client_host(websocket)
    if client_host == "testclient":
        return _is_loopback_host(websocket.headers.get("host"))
    try:
        client_address = ipaddress.ip_address(client_host)
    except ValueError:
        client_address = None
    if client_address is not None and client_address.is_loopback and _is_loopback_host(websocket.headers.get("host")):
        return True
    return _websocket_is_setup_host_request(websocket, config)


def _websocket_has_forwarded_metadata(websocket: WebSocket) -> bool:
    forwarded_headers = (
        "Forwarded",
        "X-Forwarded-For",
        "X-Forwarded-Host",
        "X-Forwarded-Proto",
        "X-Forwarded-Port",
        "X-Real-IP",
        "X-Original-Forwarded-For",
        "True-Client-IP",
        "CF-Connecting-IP",
        "CF-Ray",
        "CF-Visitor",
        "CF-IPCountry",
    )
    return any(websocket.headers.get(header) for header in forwarded_headers)


def _websocket_client_host(websocket: WebSocket) -> str:
    client_host = websocket.client.host if websocket.client else ""
    if client_host == "testclient":
        return websocket.headers.get(TEST_REMOTE_ADDR_HEADER) or client_host
    return client_host


def _websocket_peer_address(websocket: WebSocket) -> ipaddress._BaseAddress | None:
    client_host = _websocket_client_host(websocket).strip()
    if not client_host or client_host in {"localhost", "testclient"}:
        return None
    try:
        address = ipaddress.ip_address(client_host)
    except ValueError:
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    return mapped or address


def _websocket_is_private_peer(websocket: WebSocket) -> bool:
    address = _websocket_peer_address(websocket)
    return address is not None and _is_private_address(address)


def _websocket_peer_shares_setup_host_network(websocket: WebSocket, setup_address: ipaddress._BaseAddress) -> bool:
    peer = _websocket_peer_address(websocket)
    if peer is None:
        return False
    if peer.version != setup_address.version:
        mapped = getattr(peer, "ipv4_mapped", None)
        if mapped is None or mapped.version != setup_address.version:
            return False
        peer = mapped
    network = _setup_host_trust_network(setup_address)
    if network is None:
        return False
    return peer in network


def _websocket_is_wildcard_setup_host_request(websocket: WebSocket, config: V2Config | None) -> bool:
    if config is None:
        return False
    setup_host = _normalized_host(getattr(config.ui, "setup_host", ""))
    if not _is_wildcard_setup_host(setup_host):
        return False
    if _websocket_has_forwarded_metadata(websocket):
        return False

    try:
        host_address = ipaddress.ip_address(_websocket_normalized_host(websocket))
    except ValueError:
        return False
    if host_address.is_unspecified:
        return False
    if not _is_private_address(host_address):
        return False
    if _local_interface_network(host_address, interface_filter=_allows_wildcard_setup_host_trust) is None:
        return False
    if not _websocket_is_private_peer(websocket):
        return False
    if _is_tailscale_overlay_address(host_address):
        peer_address = _websocket_peer_address(websocket)
        return peer_address is not None and _is_trusted_tailscale_peer(peer_address)
    return _websocket_peer_shares_setup_host_network(websocket, host_address)


def _websocket_is_setup_host_request(websocket: WebSocket, config: V2Config | None) -> bool:
    if config is None:
        return False
    setup_host = _normalized_host(getattr(config.ui, "setup_host", ""))
    if not setup_host:
        return False
    if _is_wildcard_setup_host(setup_host):
        return _websocket_is_wildcard_setup_host_request(websocket, config)
    if _is_loopback_host(setup_host):
        return False
    try:
        setup_address = ipaddress.ip_address(setup_host)
    except ValueError:
        return False
    if not _is_private_address(setup_address):
        return False
    if _websocket_normalized_host(websocket) != setup_host:
        return False
    if _websocket_has_forwarded_metadata(websocket):
        return False
    if not _websocket_is_private_peer(websocket):
        return False
    if _is_tunnel_wildcard_bind(config):
        return _websocket_peer_shares_setup_host_network(websocket, setup_address)
    return True


def _websocket_normalized_host(websocket: WebSocket) -> str:
    return _normalized_host(websocket.headers.get("x-forwarded-host") or websocket.headers.get("host"))


async def _proxy_show_runtime_websocket(
    websocket: WebSocket,
    session_id: str,
    *,
    external_prefix: str | None = None,
) -> None:
    from core.show_runtime import get_show_runtime_manager

    if external_prefix is None:
        external_prefix = f"/show/{quote(session_id, safe='')}"
    runtime_path = f"{external_prefix.rstrip('/')}/__vite_hmr"
    if websocket.url.query:
        runtime_path = f"{runtime_path}?{websocket.url.query}"
    upstream_url = await get_show_runtime_manager().websocket_url(runtime_path)
    async with ClientSession() as session:
        async with session.ws_connect(upstream_url, protocols=["vite-hmr"], autoping=True) as upstream:
            async def client_to_upstream():
                try:
                    while True:
                        message = await websocket.receive()
                        if message["type"] == "websocket.disconnect":
                            await upstream.close()
                            return
                        if "text" in message:
                            await upstream.send_str(message["text"])
                        elif "bytes" in message:
                            await upstream.send_bytes(message["bytes"])
                except WebSocketDisconnect:
                    await upstream.close()

            async def upstream_to_client():
                async for message in upstream:
                    if message.type == WSMsgType.TEXT:
                        await websocket.send_text(message.data)
                    elif message.type == WSMsgType.BINARY:
                        await websocket.send_bytes(message.data)
                    elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                        await websocket.close()
                        return

            await asyncio.gather(client_to_upstream(), upstream_to_client())


@app.route("/api/doctor", methods=["GET"])
def doctor_get():
    payload = {}
    doctor_path = paths.get_runtime_doctor_path()
    if doctor_path.exists():
        payload = json.loads(doctor_path.read_text(encoding="utf-8"))
    return jsonify(payload)


@app.route("/api/config", methods=["GET"])
def config_get():
    from vibe import api

    config = api.load_config()
    return jsonify(api.config_to_payload(config))


@app.route("/api/platforms", methods=["GET"])
def platforms_get():
    from vibe import api

    return jsonify(api.get_platform_catalog())


@app.route("/api/agent-backends", methods=["GET"])
def agent_backends_get():
    from vibe import api

    return jsonify(api.get_agent_backend_catalog())


def _vibe_agent_error_response(exc: ValueError):
    message = str(exc)
    lowered = message.lower()
    if "not found" in lowered:
        return jsonify({"ok": False, "code": "agent_not_found", "message": message}), 404
    if "already exists" in lowered:
        return jsonify({"ok": False, "code": "agent_already_exists", "message": message}), 409
    return jsonify({"ok": False, "code": "invalid_agent_request", "message": message}), 400


def _vibe_agent_result_response(result: dict):
    status = 200
    if not result.get("ok", True):
        code = result.get("code")
        if code == "agent_in_use":
            status = 409
        elif code in {"agent_not_found", "agent_import_source_not_found"}:
            status = 404
        else:
            status = 400
    return jsonify(result), status


# Vibe Agent CRUD lives under /api/agents/* — same /api/* convention as
# every other V2 endpoint (/api/sessions, /api/projects, /api/harness/*,
# /api/inbox, ...). The earlier /agents URL collided with the React SPA
# route at the same path; moving the API to /api/agents/* is the root-
# cause fix and removes the Accept-sniffing hack that lived here.
@app.route("/api/agents", methods=["GET"])
def vibe_agents_get():
    from vibe import api

    try:
        include_disabled = str(request.args.get("include_disabled") or request.args.get("all") or "").lower() in {
            "1",
            "true",
            "yes",
        }
        return jsonify(api.get_vibe_agents(backend=request.args.get("backend") or None, include_disabled=include_disabled))
    except ValueError as exc:
        return _vibe_agent_error_response(exc)


@app.route("/api/agents/<name>", methods=["GET"])
def vibe_agent_get(name):
    from vibe import api

    try:
        return jsonify(api.get_vibe_agent(name))
    except ValueError as exc:
        return _vibe_agent_error_response(exc)


@app.route("/api/agents", methods=["POST"])
def vibe_agents_post():
    from vibe import api

    try:
        return jsonify(api.create_vibe_agent(request.json or {}))
    except ValueError as exc:
        return _vibe_agent_error_response(exc)


@app.route("/api/agents/import", methods=["POST"])
def vibe_agents_import_post():
    from vibe import api

    try:
        return _vibe_agent_result_response(api.import_vibe_agents(request.json or {}))
    except ValueError as exc:
        return _vibe_agent_error_response(exc)


@app.route("/api/agents/default", methods=["POST"])
def vibe_agents_default_post():
    from vibe import api

    payload = request.json or {}
    try:
        return jsonify(api.set_default_vibe_agent(payload.get("name") or ""))
    except ValueError as exc:
        return _vibe_agent_error_response(exc)


@app.route("/api/agents/<name>", methods=["PATCH"])
def vibe_agent_patch(name):
    from vibe import api

    try:
        return jsonify(api.update_vibe_agent(name, request.json or {}))
    except ValueError as exc:
        return _vibe_agent_error_response(exc)


@app.route("/api/agents/<name>", methods=["DELETE"])
def vibe_agent_delete(name):
    from vibe import api

    try:
        return _vibe_agent_result_response(api.remove_vibe_agent(name))
    except ValueError as exc:
        return _vibe_agent_error_response(exc)


@app.route("/api/settings", methods=["GET"])
def settings_get():
    from vibe import api

    return jsonify(api.get_settings(request.args.get("platform") or None))


def _show_page_error_response(exc):
    code = getattr(exc, "code", "invalid_show_page_request")
    status = 409 if code == "not_public" else 400
    return jsonify({"ok": False, "code": code, "message": str(exc)}), status


@app.route("/api/show-pages", methods=["GET"])
def show_pages_list_get():
    from vibe import api

    return jsonify(api.list_show_pages())


@app.route("/api/show-pages/<session_id>/visibility", methods=["POST"])
def show_page_visibility_post(session_id):
    from core.show_pages import ShowPageError
    from vibe import api

    payload = request.json or {}
    try:
        return jsonify(api.set_show_page_visibility(session_id, str(payload.get("visibility") or "")))
    except ShowPageError as exc:
        return _show_page_error_response(exc)


@app.route("/api/show-pages/<session_id>/rotate-share", methods=["POST"])
def show_page_rotate_share_post(session_id):
    from core.show_pages import ShowPageError
    from vibe import api

    try:
        return jsonify(api.rotate_show_page_share(session_id))
    except ShowPageError as exc:
        return _show_page_error_response(exc)


@app.route("/api/csrf-token", methods=["GET"])
def csrf_token_get():
    token = request.cookies.get(CSRF_COOKIE_NAME) or _new_csrf_token()
    response = jsonify({"ok": True, "csrf_token": token})
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        httponly=False,
        secure=request.is_secure,
        samesite="Strict",
        path="/",
    )
    return response


@app.route("/api/cli/detect")
def cli_detect():
    from vibe import api

    binary = request.args.get("binary", "")
    return jsonify(api.detect_cli(binary))


@app.route("/api/slack/manifest")
def slack_manifest():
    from vibe import api

    return jsonify(api.get_slack_manifest())


@app.route("/api/version")
def version():
    from vibe import api

    return jsonify(api.get_version_info())


# =============================================================================
# POST Endpoints
# =============================================================================


@app.route("/api/control", methods=["POST"])
def control():
    from vibe import runtime
    from vibe.cli import _stop_opencode_server
    from vibe.restart_supervisor import schedule_restart

    payload = request.json or {}
    action = payload.get("action")
    status = runtime.read_status()
    status["last_action"] = action
    if action == "start":
        runtime.ensure_config()
        service_pid = runtime.start_service()
        runtime.write_status("running", "started", service_pid, status.get("ui_pid"))
    elif action == "stop":
        runtime.write_status("stopping", "stopping", status.get("service_pid"), status.get("ui_pid"))
        stopped, error = _stop_runtime_process_or_error(paths.get_runtime_pid_path(), "Vibe service")
        if not stopped:
            runtime.write_status("error", error, status.get("service_pid"), status.get("ui_pid"))
            return jsonify({"ok": False, "action": action, "error": error, "status": runtime.read_status()}), 500
        _stop_opencode_server()
        runtime.write_status("stopped", "stopped", None, status.get("ui_pid"))
    elif action == "restart":
        runtime.write_status("restarting", "restarting", status.get("service_pid"), status.get("ui_pid"))
        result = schedule_restart(delay_seconds=0.0, trigger="web-ui")
        return jsonify({"ok": True, "action": action, "restart": result, "status": runtime.read_status()})
    return jsonify({"ok": True, "action": action, "status": runtime.read_status()})


@app.route("/api/config", methods=["POST"])
def config_post():
    from vibe import api
    from vibe import remote_access

    payload = request.json or {}
    remote_access_runtime = None
    should_reconcile_remote_access = False
    with CONFIG_LOCK:
        previous_config = _load_remote_access_config() if "remote_access" in payload else None
        config = api.save_config(payload)
        if _remote_access_settings_changed(previous_config, config, payload):
            if _should_rotate_remote_session_secret(previous_config, config, payload):
                remote_access.rotate_session_secret(config)
            should_reconcile_remote_access = True
    if should_reconcile_remote_access:
        remote_access_runtime = remote_access.reconcile()
    response_payload = api.config_to_payload(config)
    if remote_access_runtime is not None:
        response_payload["remote_access_runtime"] = remote_access_runtime
    return jsonify(response_payload)


@app.route("/api/remote-access/status", methods=["GET"])
def remote_access_status():
    from vibe import remote_access

    return jsonify(remote_access.status())


@app.route("/api/remote-access/vibe-cloud/pair", methods=["POST"])
def remote_access_vibe_cloud_pair():
    from vibe import remote_access

    payload = request.json or {}
    result = remote_access.pair(
        payload.get("pairing_key", ""),
        payload.get("backend_url", "https://avibe.bot"),
        payload.get("device_name", "Vibe Remote"),
    )
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/api/remote-access/start", methods=["POST"])
def remote_access_start():
    from vibe import remote_access

    result = remote_access.start()
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/api/remote-access/stop", methods=["POST"])
def remote_access_stop():
    from vibe import remote_access

    result = remote_access.stop()
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/auth/callback", methods=["GET"])
def remote_access_auth_callback():
    from vibe import remote_access

    config = _load_remote_access_config()
    if config is None or not _is_remote_access_request(config):
        return jsonify({"error": "remote_access_not_enabled"}), 400
    cloud = config.remote_access.vibe_cloud
    if not cloud.enabled:
        return jsonify({"error": "remote_access_disabled"}), 400
    oauth_state = _read_oauth_cookie(cloud.session_secret, request.cookies.get(REMOTE_OAUTH_COOKIE_NAME))
    if not oauth_state or oauth_state.get("state") != _oauth_callback_arg("state"):
        retry_response = _restart_vibe_cloud_login_from_state(config, _oauth_callback_arg("state"))
        if retry_response is not None:
            return retry_response
        return jsonify({"error": "invalid_oauth_state"}), 400
    try:
        result = remote_access.exchange_oauth_code(config, _oauth_callback_arg("code") or "", oauth_state["code_verifier"])
        claims = result["claims"]
    except Exception as exc:
        return jsonify({"error": "oauth_exchange_failed", "detail": str(exc)}), 400
    if claims.get("nonce") != oauth_state.get("nonce"):
        return jsonify({"error": "invalid_oauth_nonce"}), 400
    response = Response(status=302)
    response.headers["Location"] = _safe_remote_redirect_target(oauth_state.get("next"))
    response.set_cookie(
        remote_access.SESSION_COOKIE_NAME,
        remote_access.make_session_cookie(config, str(claims.get("email", "")), str(claims.get("sub", ""))),
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
        max_age=remote_access.SESSION_TTL_SECONDS,
    )
    response.delete_cookie(REMOTE_OAUTH_COOKIE_NAME, path="/", secure=True, samesite="Lax")
    return response


@app.route("/api/session", methods=["GET"])
def api_session():
    from vibe import remote_access

    config = _load_remote_access_config()
    if config is None or not _is_remote_access_request(config):
        response = jsonify({"remote": False})
    else:
        payload = remote_access.parse_session_cookie(
            config, request.cookies.get(remote_access.SESSION_COOKIE_NAME)
        )
        if payload is None:
            response = jsonify({"remote": True, "authenticated": False})
        else:
            response = jsonify(
                {
                    "remote": True,
                    "authenticated": True,
                    "email": str(payload.get("email", "")),
                }
            )
    # Identity payload must never be cached by intermediaries (Cloudflare etc.).
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Vary"] = "Cookie"
    return response


@app.route("/auth/logout", methods=["POST"])
def remote_access_logout():
    from vibe import remote_access

    # Suppress the after-request renewal so we don't re-issue the cookie we're
    # about to clear; flagged so future hook reorderings stay safe.
    g.remote_session_renew = None
    g.remote_session_logout = True
    response = jsonify({"ok": True})
    response.delete_cookie(
        remote_access.SESSION_COOKIE_NAME,
        path="/",
        secure=True,
        httponly=True,
        samesite="Lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/ui/reload", methods=["POST"])
def ui_reload():
    from vibe import runtime

    payload = request.json or {}
    host = payload.get("host")
    port = payload.get("port")
    if not host or not port:
        return jsonify({"error": "host_and_port_required"}), 400
    if not isinstance(host, str):
        return jsonify({"error": "invalid_host"}), 400
    try:
        port = int(port)
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_port"}), 400

    status = runtime.read_status()

    try:
        from core.services import settings as settings_service

        current_config = settings_service.load_config()
    except Exception:
        current_config = None
    if current_config is not None:
        bind_host = runtime.effective_ui_bind_host(current_config, requested_host=host)
    else:
        bind_host = host

    def _restart():
        global _server
        import subprocess
        import sys
        import time
        from config import paths as config_paths

        working_dir = get_working_dir()
        command = f"from vibe.ui_server import run_ui_server; run_ui_server('{bind_host}', {port})"
        stdout_path = config_paths.get_runtime_dir() / "ui_stdout.log"
        stderr_path = config_paths.get_runtime_dir() / "ui_stderr.log"
        stdout = stdout_path.open("ab")
        stderr = stderr_path.open("ab")
        process = subprocess.Popen(
            [sys.executable, "-c", command],
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            cwd=str(working_dir),
            close_fds=True,
        )
        stdout.close()
        stderr.close()
        config_paths.get_runtime_ui_pid_path().write_text(str(process.pid), encoding="utf-8")
        runtime.write_status(
            status.get("state", "running"),
            status.get("detail"),
            status.get("service_pid"),
            process.pid,
        )
        time.sleep(0.2)
        # Shutdown the old server to release the port
        if _server:
            if hasattr(_server, "should_exit"):
                _server.should_exit = True
            else:
                shutdown = getattr(_server, "shutdown", None)
                if callable(shutdown):
                    shutdown()

    # Schedule restart after response is sent
    threading.Thread(target=_restart).start()
    return jsonify({"ok": True, "host": host, "port": port})


@app.route("/api/settings", methods=["POST"])
def settings_post():
    from vibe import api

    payload = request.json or {}
    return jsonify(api.save_settings(payload))


@app.route("/api/slack/auth_test", methods=["POST"])
def slack_auth_test():
    from vibe import api

    payload = request.json or {}
    result = api.slack_auth_test(
        payload.get("bot_token", ""),
        proxy_url=payload.get("proxy_url"),
    )
    return jsonify(result)


@app.route("/api/slack/channels", methods=["POST"])
def slack_channels():
    from vibe import api

    payload = request.json or {}
    return jsonify(
        api.list_channels(
            payload.get("bot_token", ""),
            browse_all=payload.get("browse_all", False),
            force=payload.get("force", False) or request.args.get("force") == "1",
        )
    )


@app.route("/api/discord/auth_test", methods=["POST"])
async def discord_auth_test():
    from vibe import api

    payload = request.json or {}
    result = await api.discord_auth_test_async(
        payload.get("bot_token", ""),
        proxy_url=payload.get("proxy_url"),
    )
    return jsonify(result)


@app.route("/api/discord/guilds", methods=["POST"])
async def discord_guilds():
    from vibe import api

    payload = request.json or {}
    return jsonify(await api.discord_list_guilds_async(payload.get("bot_token", "")))


@app.route("/api/discord/channels", methods=["POST"])
def discord_channels():
    from vibe import api

    payload = request.json or {}
    return jsonify(
        api.discord_list_channels(
            payload.get("bot_token", ""),
            payload.get("guild_id", ""),
            force=payload.get("force", False) or request.args.get("force") == "1",
        )
    )


@app.route("/api/telegram/auth_test", methods=["POST"])
async def telegram_auth_test():
    from vibe import api

    payload = request.json or {}
    result = await api.telegram_auth_test_async(
        payload.get("bot_token", ""),
        proxy_url=payload.get("proxy_url")
    )
    return jsonify(result)


@app.route("/api/telegram/chats", methods=["POST"])
def telegram_chats():
    from vibe import api

    payload = request.json or {}
    return jsonify(api.telegram_list_chats(include_private=payload.get("include_private", False)))


@app.route("/api/lark/auth_test", methods=["POST"])
async def lark_auth_test():
    from vibe import api

    payload = request.json or {}
    result = await api.lark_auth_test_async(
        payload.get("app_id", ""),
        payload.get("app_secret", ""),
        payload.get("domain", "feishu"),
        proxy_url=payload.get("proxy_url"),
    )
    return jsonify(result)


@app.route("/api/lark/chats", methods=["POST"])
def lark_chats():
    from vibe import api

    payload = request.json or {}
    return jsonify(
        api.lark_list_chats(
            payload.get("app_id", ""),
            payload.get("app_secret", ""),
            payload.get("domain", "feishu"),
            force=payload.get("force", False) or request.args.get("force") == "1",
        )
    )


@app.route("/api/lark/temp_ws/start", methods=["POST"])
def lark_temp_ws_start():
    from vibe import api

    payload = request.json or {}
    return jsonify(
        api.lark_temp_ws_start(
            payload.get("app_id", ""), payload.get("app_secret", ""), payload.get("domain", "feishu")
        )
    )


@app.route("/api/lark/temp_ws/stop", methods=["POST"])
def lark_temp_ws_stop():
    from vibe import api

    return jsonify(api.lark_temp_ws_stop())


# WeChat auth singleton
_wechat_auth_manager = None


def _get_wechat_auth():
    global _wechat_auth_manager
    if _wechat_auth_manager is None:
        from modules.im.wechat_auth import WeChatAuthManager

        _wechat_auth_manager = WeChatAuthManager()
    return _wechat_auth_manager


def _schedule_wechat_qr_login_restart() -> dict:
    """Schedule a managed restart after QR-login credentials are persisted."""
    from vibe.restart_supervisor import schedule_restart

    return schedule_restart(delay_seconds=2.0, trigger="wechat-qr-login")


@app.route("/api/wechat/qr_login/start", methods=["POST"])
async def wechat_qr_login_start():
    """Start WeChat QR code login flow."""
    auth = _get_wechat_auth()
    payload = request.json or {}
    base_url = payload.get("base_url", "https://ilinkai.weixin.qq.com")

    result = await auth.start_login(base_url=base_url)
    if result.get("ok") is False:
        return jsonify(result), 500
    return jsonify(result)


@app.route("/api/wechat/qr_login/poll", methods=["POST"])
async def wechat_qr_login_poll():
    """Poll WeChat QR code login status."""
    payload = request.json or {}
    session_key = payload.get("session_key", "")
    if not session_key:
        return jsonify({"error": "session_key required"}), 400

    auth = _get_wechat_auth()
    result = await auth.poll_status(session_key)
    if result.get("ok") is False:
        return jsonify(result), 500

    # If confirmed, auto-bind the WeChat user
    if result.get("status") == "confirmed" and result.get("bot_token"):
        user_id = result.get("user_id", "wechat_user")

        # Auto-bind user
        try:
            from vibe import api as vibe_api

            vibe_api.auto_bind_wechat_user(user_id)
        except Exception as e:
            logger.warning("Failed to auto-bind WeChat user: %s", e)

        try:
            restart = _schedule_wechat_qr_login_restart()
            logger.info("Scheduled service restart after WeChat QR login: %s", restart.get("job_id"))
        except Exception as exc:
            logger.warning("Failed to schedule service restart after WeChat QR login: %s", exc)

    return jsonify(result)


@app.route("/api/doctor", methods=["POST"])
def doctor_post():
    from vibe.cli import _doctor

    result = _doctor()
    return jsonify(result)


@app.route("/api/logs", methods=["POST"])
def logs():
    payload = request.json or {}
    try:
        lines = max(int(payload.get("lines", 500)), 1)
    except (TypeError, ValueError):
        lines = 500
    selected_source = payload.get("source", "service")
    sources = _resolve_log_sources()
    source_map = {source["key"]: source for source in sources}
    active_source = source_map.get(selected_source) or source_map["all"]

    try:
        aggregated_logs: list[dict[str, Any]] = []
        aggregated_total = 0
        for source in sources:
            if source["key"] == "all":
                continue
            source_logs, total = _read_log_entries(Path(source["path"]), source["key"], lines)
            source["total"] = total
            aggregated_logs.extend(source_logs)
            aggregated_total += total
            if source["key"] == active_source["key"]:
                source["logs"] = source_logs
                active_logs = source_logs
                active_total = total
            else:
                source["logs"] = []
        sources[0]["total"] = aggregated_total
        sources[0]["logs"] = []
        if active_source["key"] == "all":
            active_logs = sorted(
                aggregated_logs,
                key=lambda entry: (
                    int(entry.get("_sort_ns", 0)),
                    int(entry.get("_sort_index", 0)),
                    entry.get("source") or "",
                    entry.get("logger") or "",
                ),
            )
            if len(active_logs) > lines:
                active_logs = active_logs[-lines:]
            active_total = aggregated_total
        return jsonify(
            {
                "source": active_source["key"],
                "logs": _serialize_log_entries(active_logs),
                "total": active_total,
                "sources": sources,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/opencode/options", methods=["POST"])
async def opencode_options():
    from vibe import api

    payload = request.json or {}
    result = await api.opencode_options_async(payload.get("cwd", "."))
    return jsonify(result)


@app.route("/api/upgrade", methods=["POST"])
def upgrade():
    from vibe import api

    result = api.do_upgrade()
    return jsonify(result)


@app.route("/api/opencode/setup-permission", methods=["POST"])
def opencode_setup_permission():
    from vibe import api

    return jsonify(api.setup_opencode_permission())


@app.route("/api/claude/agents", methods=["GET"])
def claude_agents():
    from vibe import api

    cwd = request.args.get("cwd")
    if cwd:
        # Expand ~ first, then check if absolute
        expanded = Path(cwd).expanduser()
        if not expanded.is_absolute():
            cwd = str(get_working_dir() / cwd)
        else:
            cwd = str(expanded)

    return jsonify(api.claude_agents(cwd))


@app.route("/api/codex/agents", methods=["GET"])
def codex_agents():
    from vibe import api

    cwd = request.args.get("cwd")
    if cwd:
        expanded = Path(cwd).expanduser()
        if not expanded.is_absolute():
            cwd = str(get_working_dir() / cwd)
        else:
            cwd = str(expanded)

    return jsonify(api.codex_agents(cwd))


@app.route("/api/claude/models", methods=["GET"])
def claude_models():
    from vibe import api

    return jsonify(api.claude_models())


@app.route("/api/codex/models", methods=["GET"])
def codex_models():
    from vibe import api

    return jsonify(api.codex_models())


@app.route("/api/agent/<name>/install", methods=["POST"])
def agent_install(name):
    """Install an agent CLI tool (opencode, claude, codex)."""
    if name not in _ALLOWED_BACKENDS:
        return jsonify({"ok": False, "message": f"Unknown agent: {name}"}), 400

    from vibe import api

    result = api.start_agent_install_job(name)
    return jsonify(result)


@app.route("/api/agent/<name>/install/<job_id>", methods=["GET"])
def agent_install_status(name, job_id):
    """Poll a background agent CLI install/upgrade job."""
    if name not in _ALLOWED_BACKENDS:
        return jsonify({"ok": False, "message": f"Unknown agent: {name}"}), 400

    from vibe import api

    result = api.get_agent_install_job(job_id, backend=name)
    status = 404 if not result.get("ok") and result.get("error") == "job_not_found" else 200
    return jsonify(result), status


_ALLOWED_BACKENDS = set(AGENT_BACKENDS)


@app.route("/api/backend/<name>/runtime")
def backend_runtime(name):
    """Return lifecycle info (version, update, process status) for a backend."""
    if name not in _ALLOWED_BACKENDS:
        return jsonify({"ok": False, "error": f"Unknown backend: {name}"}), 400

    from vibe import api

    return jsonify(api.get_backend_runtime(name))


@app.route("/api/backend/<name>/restart", methods=["POST"])
def backend_restart(name):
    """Refresh a backend's runtime state after settings change."""
    if not supports_runtime_refresh(name):
        return jsonify({"ok": False, "message": f"Restart is not supported for backend: {name}"}), 400

    from vibe import api

    return jsonify(api.restart_backend(name))


_ALLOWED_DEPENDENCIES = {"askill", "show-runtime"}


@app.route("/api/dependencies")
def get_dependencies():
    """Status of required local runtime dependencies (askill, Show runtime, Node)."""
    from vibe import api

    return jsonify(api.dependencies_status())


@app.route("/api/dependencies/<dep>/install", methods=["POST"])
def dependency_install(dep):
    """Install/repair a required local dependency in a background job."""
    if dep not in _ALLOWED_DEPENDENCIES:
        return jsonify({"ok": False, "message": f"Unknown dependency: {dep}"}), 400

    from vibe import api

    return jsonify(api.start_dependency_install_job(dep))


@app.route("/api/dependencies/<dep>/install/<job_id>", methods=["GET"])
def dependency_install_status(dep, job_id):
    """Poll a background dependency install job."""
    if dep not in _ALLOWED_DEPENDENCIES:
        return jsonify({"ok": False, "message": f"Unknown dependency: {dep}"}), 400

    from vibe import api

    result = api.get_agent_install_job(job_id, backend=dep)
    status = 404 if not result.get("ok") and result.get("error") == "job_not_found" else 200
    return jsonify(result), status


@app.route("/api/backend/codex/auth", methods=["GET"])
def backend_codex_auth_get():
    """Read the user-facing Codex auth state (masked secrets)."""
    from vibe import api

    return jsonify(api.get_codex_auth())


@app.route("/api/backend/codex/auth", methods=["POST"])
def backend_codex_auth_post():
    """Persist Codex auth and reload the app-server.

    Body: ``{auth_mode: 'oauth'|'api_key', api_key?: string, base_url?: string}``.
    """
    from vibe import api

    payload = request.json or {}
    return jsonify(api.save_codex_auth(payload))


@app.route("/api/backend/claude/auth", methods=["GET"])
def backend_claude_auth_get():
    """Read the user-facing Claude auth state (masked secrets)."""
    from vibe import api

    return jsonify(api.get_claude_auth())


@app.route("/api/backend/claude/auth", methods=["POST"])
def backend_claude_auth_post():
    """Persist Claude auth into V2Config.

    Body: ``{auth_mode: 'oauth'|'api_key', api_key?: string, base_url?: string}``.
    Claude relaunches per request, so no daemon restart is necessary —
    the next user message picks up the new env injection automatically.
    """
    from vibe import api

    payload = request.json or {}
    return jsonify(api.save_claude_auth(payload))


@app.route("/api/backend/<backend>/auth/oauth/start", methods=["POST"])
async def backend_oauth_web_start(backend: str):
    """Kick off a Settings → Backends OAuth flow for Claude or Codex.

    Body: ``{force_reset?: bool}``. Returns ``{flow_id, state, url?,
    device_code?, awaiting_code?}``. The caller polls ``GET .../status/<flow_id>``
    while the user completes login externally.
    """
    from vibe import api

    payload = request.json or {}
    force_reset = bool(payload.get("force_reset", True))
    return jsonify(await api.start_oauth_web_async(backend, force_reset=force_reset))


@app.route("/api/backend/<backend>/auth/oauth/status/<flow_id>", methods=["GET"])
def backend_oauth_web_status(backend: str, flow_id: str):
    """Poll an in-flight Settings OAuth flow."""
    from vibe import api

    _ = backend  # backend is encoded in the flow itself; path arg kept for symmetry
    return jsonify(api.get_oauth_web_status(flow_id))


@app.route("/api/backend/<backend>/auth/oauth/submit-code", methods=["POST"])
async def backend_oauth_web_submit_code(backend: str):
    """Submit the Claude OAuth callback code (Codex device-auth ignores this)."""
    from vibe import api

    _ = backend
    payload = request.json or {}
    flow_id = str(payload.get("flow_id") or "").strip()
    code = str(payload.get("code") or "")
    return jsonify(await api.submit_oauth_web_code_async(flow_id, code))


@app.route("/api/backend/<backend>/auth/oauth/cancel", methods=["POST"])
async def backend_oauth_web_cancel(backend: str):
    """Cancel an in-flight Settings OAuth flow."""
    from vibe import api

    _ = backend
    payload = request.json or {}
    flow_id = str(payload.get("flow_id") or "").strip()
    return jsonify(await api.cancel_oauth_web_async(flow_id))


@app.route("/api/backend/<backend>/auth/oauth/remove", methods=["POST"])
async def backend_oauth_web_remove(backend: str):
    """Clear stored credentials for a Claude/Codex backend."""
    from vibe import api

    return jsonify(await api.remove_backend_auth_async(backend))


@app.route("/api/backend/<backend>/auth/api-key/remove", methods=["POST"])
def backend_auth_api_key_remove(backend: str):
    """Clear the stored API key (V2Config + Codex auth.json) without
    touching OAuth credentials. Per-backend symmetry of OpenCode's
    per-provider DELETE."""
    from vibe import api

    return jsonify(api.remove_backend_api_key(backend))


@app.route("/api/backend/<backend>/auth/test", methods=["POST"])
async def backend_auth_test(backend: str):
    """Send a single-token probe through the backend CLI to verify auth."""
    from vibe import api

    payload = request.json or {}
    raw_model = payload.get("model")
    model = raw_model.strip() if isinstance(raw_model, str) and raw_model.strip() else None
    return jsonify(await api.test_backend_auth_async(backend, model=model))


@app.route("/api/backend/opencode/providers", methods=["GET"])
async def backend_opencode_providers():
    """Return the merged OpenCode provider catalog for the Settings UI.

    Fans out to the live OpenCode daemon's ``/provider``, ``/provider/auth``,
    and ``/config/providers`` endpoints and merges them into a list of
    ``{id, name, configured, oauth_available, local, models, default_model}``.
    """
    from vibe import api

    return jsonify(await api.get_opencode_providers_async())


@app.route(
    "/api/backend/opencode/provider/<provider_id>/auth/oauth/start",
    methods=["POST"],
)
async def backend_opencode_provider_oauth_start(provider_id: str):
    """Kick off a Settings → Backends OAuth flow for a single OpenCode provider.

    Body: ``{force_reset?: bool}``. Returns ``{flow_id, state, url?,
    device_code?}``. The status/cancel endpoints are the same generic
    ``/api/backend/opencode/auth/oauth/status/<flow_id>`` etc.
    """
    from vibe import api

    payload = request.json or {}
    force_reset = bool(payload.get("force_reset", True))
    return jsonify(await api.start_oauth_web_async("opencode", force_reset=force_reset, provider_id=provider_id))


@app.route("/api/backend/opencode/provider/<provider_id>/auth", methods=["POST"])
async def backend_opencode_provider_auth_post(provider_id: str):
    """Persist an API key for a single OpenCode provider.

    Body: ``{api_key: string}``. The key is forwarded to OpenCode via
    its ``PUT /auth/<id>`` endpoint.
    """
    from vibe import api

    payload = request.json or {}
    return jsonify(await api.save_opencode_provider_auth_async(provider_id, payload))


@app.route("/api/backend/opencode/provider/<provider_id>/auth", methods=["DELETE"])
async def backend_opencode_provider_auth_delete(provider_id: str):
    """Drop the stored API key for a single OpenCode provider."""
    from vibe import api

    return jsonify(await api.delete_opencode_provider_auth_async(provider_id))


@app.route("/api/backend/opencode/provider/<provider_id>/test", methods=["POST"])
async def backend_opencode_provider_test(provider_id: str):
    """Run a per-provider connectivity probe through OpenCode's HTTP API.

    Body: ``{model?: string}``. The model id is wrapped server-side
    into the ``{providerID, modelID}`` shape OpenCode expects.
    """
    from vibe import api

    payload = request.json or {}
    raw_model = payload.get("model")
    model = raw_model.strip() if isinstance(raw_model, str) and raw_model.strip() else None
    return jsonify(await api.test_opencode_provider_async(provider_id, model=model))


@app.route("/api/backend/opencode/default-provider", methods=["POST"])
def backend_opencode_default_provider():
    """Persist the user's default OpenCode provider into V2Config.

    Body: ``{provider_id: string}``. No daemon contact — the default
    is consulted at session-routing time, not by OpenCode itself.
    """
    from vibe import api

    payload = request.json or {}
    return jsonify(api.set_opencode_default_provider(payload))


@app.route("/api/browse", methods=["POST"])
def browse_directory():
    """List sub-directories of a given path for the directory picker UI."""
    from vibe import api

    payload = request.json or {}
    return jsonify(
        api.browse_directory(
            payload.get("path", "~"),
            show_hidden=bool(payload.get("show_hidden", False)),
        )
    )


@app.route("/api/browse/favorites", methods=["GET"])
def browse_favorites():
    """OS-appropriate quick-access directories for the directory picker."""
    from vibe import api

    return jsonify(api.browse_favorites())


# =============================================================================
# Workbench: Projects + folder-picker helpers
# =============================================================================
# Projects are stored as avibe scopes (platform='avibe', scope_type='project')
# with the local folder path on ``scope_settings.workdir``. See
# ``storage/projects_service.py`` for the CRUD semantics; the routes below
# are a thin REST surface over the same service so the workbench UI and any
# future CLI both round-trip the same shape.


def _projects_engine():
    from storage.db import create_sqlite_engine

    return create_sqlite_engine()


@app.route("/api/projects", methods=["GET"])
def projects_list():
    from storage import projects_service

    include_archived = request.args.get("include_archived") in {"1", "true", "yes"}
    engine = _projects_engine()
    with engine.connect() as conn:
        return jsonify({"projects": projects_service.list_projects(conn, include_archived=include_archived)})


@app.route("/api/projects", methods=["POST"])
def projects_create():
    from storage import projects_service

    payload = request.json or {}
    folder_path = (payload.get("folder_path") or "").strip()
    if not folder_path:
        return jsonify({"error": "folder_path is required"}), 400
    display_name = payload.get("display_name")
    engine = _projects_engine()
    try:
        with engine.begin() as conn:
            project = projects_service.create_project(conn, folder_path, display_name=display_name)
    except (FileNotFoundError, NotADirectoryError) as err:
        return jsonify({"error": str(err)}), 400
    return jsonify(project), 201


@app.route("/api/projects/<project_id>", methods=["GET"])
def projects_get(project_id: str):
    from storage import projects_service

    engine = _projects_engine()
    try:
        with engine.connect() as conn:
            return jsonify(projects_service.get_project(conn, project_id))
    except LookupError as err:
        return jsonify({"error": str(err)}), 404


@app.route("/api/projects/<project_id>", methods=["PATCH"])
def projects_update(project_id: str):
    from storage import projects_service

    payload = request.json or {}
    display_name = payload.get("display_name")
    folder_path = payload.get("folder_path")
    if display_name is None and folder_path is None:
        return jsonify({"error": "display_name or folder_path is required"}), 400
    engine = _projects_engine()
    try:
        with engine.begin() as conn:
            project = projects_service.update_project(
                conn,
                project_id,
                display_name=display_name,
                folder_path=folder_path,
            )
    except LookupError as err:
        return jsonify({"error": str(err)}), 404
    except (FileNotFoundError, NotADirectoryError) as err:
        return jsonify({"error": str(err)}), 400
    return jsonify(project)


@app.route("/api/projects/<project_id>", methods=["DELETE"])
def projects_archive(project_id: str):
    """Soft-delete a project by marking ``scope_settings.enabled = 0``.

    The scope row itself sticks around so any related agent_sessions /
    messages keep their foreign-key target. Pass ``include_archived=1``
    on the list endpoint to surface archived projects in the UI.
    """

    from storage import projects_service

    engine = _projects_engine()
    try:
        with engine.begin() as conn:
            project = projects_service.archive_project(conn, project_id)
    except LookupError as err:
        return jsonify({"error": str(err)}), 404
    return jsonify(project)


class _ProjectNoFolder(Exception):
    """A project exists but has no folder configured. Project-scoped skills are
    impossible (askill needs a real cwd), so routes degrade to global or return
    a clear error instead of feeding an empty cwd into the CLI."""


def _resolve_project_dir(project_id):
    """Map a workbench project id to its folder path for project-scoped skills.

    Returns None when no project is given (global scope). Raises LookupError for
    an unknown id (→ 404) and _ProjectNoFolder when the project's folder is
    unset/blank, so callers can degrade gracefully rather than passing an empty
    cwd to askill (which would surface as a raw ``project folder not found:``).
    """
    if not project_id:
        return None
    from storage import projects_service

    engine = _projects_engine()
    with engine.connect() as conn:
        project = projects_service.get_project(conn, project_id)
    folder = (project.get("folder_path") or "").strip()
    if not folder:
        raise _ProjectNoFolder(project_id)
    return folder


def _project_not_found(err):
    return jsonify({"ok": False, "error": {"code": "project_not_found", "message": str(err)}}), 404


def _project_no_folder_error():
    return (
        jsonify(
            {
                "ok": False,
                "error": {
                    "code": "project_no_folder",
                    "message": "This project has no folder configured, so it has no project-scoped skills.",
                },
            }
        ),
        400,
    )


# Agent Skills — thin shells over api.* (which wraps the askill CLI). Pure
# data CRUD, so it stays in the UI-server process via core/services (no
# dispatch-socket round-trip). See docs/plans/workbench-skills-page.md.
@app.route("/api/skills", methods=["GET"])
async def skills_list():
    from vibe import api

    scope = request.args.get("scope") or "all"
    backends = [b for b in (request.args.get("backends") or "").split(",") if b]
    try:
        project_dir = _resolve_project_dir(request.args.get("project_id"))
    except LookupError as err:
        return _project_not_found(err)
    except _ProjectNoFolder:
        # Folderless project: no project-scoped skills are possible — show
        # global skills (with a flag) instead of erroring the whole page.
        result = await api.list_skills(scope="global", backends=backends or None)
        if isinstance(result, dict) and result.get("ok"):
            result = {**result, "project_no_folder": True}
        return jsonify(result)
    return jsonify(await api.list_skills(scope=scope, project_dir=project_dir, backends=backends or None))


@app.route("/api/skills/preview", methods=["POST"])
async def skills_preview():
    from vibe import api

    payload = request.json or {}
    try:
        project_dir = _resolve_project_dir(payload.get("project_id"))
    except LookupError as err:
        return _project_not_found(err)
    except _ProjectNoFolder:
        project_dir = None  # preview doesn't need the project folder (gh/zip sources)
    return jsonify(await api.preview_skill_source(str(payload.get("source") or ""), project_dir=project_dir))


@app.route("/api/skills", methods=["POST"])
async def skills_add():
    from vibe import api

    payload = request.json or {}
    try:
        project_dir = _resolve_project_dir(payload.get("project_id"))
    except LookupError as err:
        return _project_not_found(err)
    except _ProjectNoFolder:
        return _project_no_folder_error()
    return jsonify(
        await api.add_skill(
            str(payload.get("source") or ""),
            scope=payload.get("scope") or "project",
            project_dir=project_dir,
            backends=payload.get("backends") or None,
            all_skills=bool(payload.get("all")),
            skill=payload.get("skill") or None,
            copy=bool(payload.get("copy")),
        )
    )


@app.route("/api/skills/<name>", methods=["DELETE"])
async def skills_remove(name):
    from vibe import api

    backends = [b for b in (request.args.get("backends") or "").split(",") if b]
    try:
        project_dir = _resolve_project_dir(request.args.get("project_id"))
    except LookupError as err:
        return _project_not_found(err)
    except _ProjectNoFolder:
        return _project_no_folder_error()
    return jsonify(
        await api.remove_skill(
            name,
            scope=request.args.get("scope") or "project",
            project_dir=project_dir,
            backends=backends or None,
        )
    )


@app.route("/api/skills/find", methods=["GET"])
async def skills_find():
    from vibe import api

    return jsonify(await api.find_skills(request.args.get("q") or ""))


@app.route("/api/skills/check", methods=["GET"])
async def skills_check():
    from vibe import api

    scope = request.args.get("scope") or "project"
    try:
        project_dir = _resolve_project_dir(request.args.get("project_id"))
    except LookupError as err:
        return _project_not_found(err)
    except _ProjectNoFolder:
        # Folderless project has no project-local skills, so nothing to check.
        return jsonify({"ok": True, "skills": []})
    return jsonify(await api.check_skills(scope=scope, project_dir=project_dir))


@app.route("/api/skills/update", methods=["POST"])
async def skills_update():
    from vibe import api

    payload = request.json or {}
    try:
        project_dir = _resolve_project_dir(payload.get("project_id"))
    except LookupError as err:
        return _project_not_found(err)
    except _ProjectNoFolder:
        return _project_no_folder_error()
    return jsonify(
        await api.update_skill(
            str(payload.get("name") or ""),
            scope=payload.get("scope") or "project",
            project_dir=project_dir,
        )
    )


@app.route("/api/skills/upload", methods=["POST"])
async def skills_upload():
    from vibe import api

    payload = request.json or {}
    try:
        project_dir = _resolve_project_dir(payload.get("project_id"))
    except LookupError as err:
        return _project_not_found(err)
    except _ProjectNoFolder:
        # The zip is unpacked to a temp dir (project-independent); the install
        # step picks the scope. Drop the cwd like preview rather than erroring.
        project_dir = None
    return jsonify(await api.upload_skill_zip(payload, project_dir=project_dir))


@app.route("/api/browse/mkdir", methods=["POST"])
def browse_mkdir():
    """Create a new folder for the directory picker.

    Used by the workbench folder picker's "New Folder" button. Errors
    when the target already exists so the UI never silently selects
    someone else's data dir.
    """

    from storage import projects_service

    payload = request.json or {}
    path = (payload.get("path") or "").strip()
    if not path:
        return jsonify({"error": "path is required"}), 400
    try:
        resolved = projects_service.make_directory(path)
    except FileExistsError:
        return jsonify({"error": f"Folder already exists: {path}"}), 409
    except OSError as err:
        return jsonify({"error": str(err)}), 400
    return jsonify({"path": resolved}), 201


# =============================================================================
# Workbench: Sessions + Messages + Inbox
# =============================================================================
# All endpoints below talk directly to the SQLite store via the workbench
# service modules — ORM all the way down, no CLI shell-outs.
# ``project_id`` (short ``proj_<hex>`` form) is the public id; we expand to
# the full scope_id ``avibe::project::proj_xxx`` inside.


def _project_to_scope_id(project_id: str) -> str:
    return f"avibe::project::{project_id}"


@app.route("/api/sessions", methods=["GET"])
def sessions_list():
    from core.services import sessions as workbench_sessions_service

    project_id = request.args.get("project_id")
    scope_id = _project_to_scope_id(project_id) if project_id else None
    status = request.args.get("status") or "active"
    try:
        limit = int(request.args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    before_id = request.args.get("before_id") or None

    engine = _projects_engine()
    with engine.connect() as conn:
        result = workbench_sessions_service.list_sessions(
            conn,
            scope_id=scope_id,
            status=status,
            limit=limit,
            before_id=before_id,
        )
    return jsonify(result)


@app.route("/api/sessions", methods=["POST"])
def sessions_create():
    from core.services import sessions as workbench_sessions_service
    from vibe.sse_broker import broker

    payload = request.json or {}
    project_id = (payload.get("project_id") or "").strip()
    agent_backend = (payload.get("agent_backend") or "").strip()
    if not project_id:
        return jsonify({"error": "project_id is required"}), 400
    # When the caller doesn't pin a backend/agent (a plain "new chat"), leave
    # agent_backend empty rather than stamping a concrete backend onto the
    # session. A stamped backend is treated by message_handler as an explicit
    # legacy override and bypasses resolve_vibe_agent_for_context(), so the
    # user's configured default Vibe Agent (and its model/system prompt) would
    # be ignored. Leaving it empty lets the shared resolver pick the default
    # Vibe Agent — including default_agent_name — at dispatch time.

    scope_id = _project_to_scope_id(project_id)
    engine = _projects_engine()
    try:
        with engine.begin() as conn:
            session = workbench_sessions_service.create_session(
                conn,
                scope_id=scope_id,
                agent_backend=agent_backend,
                agent_id=payload.get("agent_id"),
                agent_name=payload.get("agent_name"),
                agent_variant=payload.get("agent_variant"),
                model=payload.get("model"),
                reasoning_effort=payload.get("reasoning_effort"),
                title=payload.get("title"),
                metadata=payload.get("metadata"),
            )
    except LookupError as err:
        return jsonify({"error": str(err)}), 404
    except PermissionError as err:
        return jsonify({"error": str(err)}), 403
    broker.publish("session.activity", {"session_id": session["id"], "scope_id": session["scope_id"], "event": "created"})
    return jsonify(session), 201


@app.route("/api/sessions/<session_id>", methods=["GET"])
def sessions_get(session_id: str):
    from core.services import sessions as workbench_sessions_service

    engine = _projects_engine()
    try:
        with engine.connect() as conn:
            return jsonify(workbench_sessions_service.get_session(conn, session_id))
    except LookupError as err:
        return jsonify({"error": str(err)}), 404


@app.route("/api/sessions/<session_id>", methods=["PATCH"])
def sessions_update(session_id: str):
    from core.services import sessions as workbench_sessions_service
    from vibe.sse_broker import broker

    payload = request.json or {}
    updatable = {
        key: payload[key]
        for key in (
            "title",
            "agent_id",
            "agent_name",
            "agent_backend",
            "agent_variant",
            "model",
            "reasoning_effort",
        )
        if key in payload
    }
    if not updatable:
        return jsonify({"error": "no updatable fields supplied"}), 400

    engine = _projects_engine()
    try:
        with engine.begin() as conn:
            session = workbench_sessions_service.update_session(conn, session_id, **updatable)
    except LookupError as err:
        return jsonify({"error": str(err)}), 404
    except workbench_sessions_service.SessionBackendLockedError as err:
        # A session is pinned to its backend once it has a conversation; the UI
        # may switch the agent within the same backend, but not across backends.
        return (
            jsonify(
                {
                    "error": str(err),
                    "code": "backend_locked",
                    "current_backend": err.current_backend,
                    "requested_backend": err.requested_backend,
                }
            ),
            409,
        )
    # Broadcast so other surfaces (e.g. the sidebar session list) reflect the
    # edit live — renaming a session in the chat header should rename its
    # sidebar row without a manual refresh.
    broker.publish(
        "session.activity",
        {
            "session_id": session_id,
            "scope_id": session.get("scope_id"),
            "event": "updated",
            "title": session.get("title"),
        },
    )
    return jsonify(session)


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
def sessions_archive(session_id: str):
    from core.services import sessions as workbench_sessions_service

    engine = _projects_engine()
    try:
        with engine.begin() as conn:
            session = workbench_sessions_service.archive_session(conn, session_id)
    except LookupError as err:
        return jsonify({"error": str(err)}), 404
    return jsonify(session)


@app.route("/api/sessions/<session_id>/messages", methods=["GET"])
def sessions_messages_list(session_id: str):
    from core.services import sessions as workbench_sessions_service
    from storage import messages_service

    after_id = request.args.get("after_id") or None
    try:
        limit = int(request.args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    # ``tail=1`` returns the most-recent window (for the Chat page's gap recovery)
    # instead of the oldest page.
    tail = request.args.get("tail") == "1"

    engine = _projects_engine()
    with engine.connect() as conn:
        try:
            workbench_sessions_service.get_session(conn, session_id)
        except LookupError as err:
            return jsonify({"error": str(err)}), 404
        # Chat transcript = the dialogue + turn-terminal markers. avibe turns
        # persist intermediate assistant / tool_call rows (unified store) that we
        # keep OUT of the conversation view, but ``notify`` rows are kept: a
        # terminal notify (e.g. an agent run that failed and stopped without a
        # result) marks the end of that turn and must stay visible. Show-Page
        # transcript marks (metadata.source='show_page') are kept regardless of
        # type.
        result = messages_service.list_session_messages(
            conn,
            session_id=session_id,
            after_id=after_id,
            limit=limit,
            types=messages_service.TRANSCRIPT_TYPES,
            include_metadata_sources=("show_page",),
            tail=tail,
        )
    return jsonify(result)


@app.route("/api/sessions/<session_id>/messages", methods=["POST"])
async def sessions_messages_create(session_id: str):
    """Persist a user message and fire-and-forget the agent turn.

    Reserves the user's row, then asks the controller to start the turn
    (``/internal/dispatch_async``, 202). The agent's reply — and any
    notify/result — arrives over the persistent ``message.new`` session
    stream, not this response, so the HTTP request returns immediately and a
    closed browser tab can't cancel an in-flight turn. The controller
    atomically either starts the turn (we then promote the row to ``user``)
    or, when a turn is already running, promotes it to ``queued`` itself
    (send-while-busy). The legacy per-turn ``?stream=1`` SSE proxy was retired
    in Step 6 — the session-scoped stream replaced it.
    """

    from core.services import sessions as workbench_sessions_service
    from storage import messages_service
    from vibe import internal_client
    from vibe.sse_broker import broker

    payload = request.json or {}
    text = payload.get("text")
    content = payload.get("content")
    if text is None and not content:
        return jsonify({"error": "text or content is required"}), 400

    engine = _projects_engine()
    try:
        with engine.connect() as conn:
            session = workbench_sessions_service.get_session(conn, session_id)
    except LookupError as err:
        return jsonify({"error": str(err)}), 404

    dispatch_text = (
        (text if isinstance(text, str) else None)
        or (content.get("text") if isinstance(content, dict) else None)
        or ""
    )

    def _persist_user_row() -> dict:
        """Reserve the user's row as ``pending`` (hidden from transcript/queue/
        inbox) + clear any saved draft, WITHOUT publishing. This locks the row's
        ``(created_at, id)`` BEFORE the turn dispatches (so a fast reply can't
        sort ahead of its prompt) yet keeps it invisible during the dispatch
        window, so another tab can't briefly see it as a sent prompt (Codex P2).
        The caller promotes it (→ user / queued) once the outcome is known."""
        with engine.begin() as conn:
            row = messages_service.append(
                conn,
                scope_id=session["scope_id"],
                session_id=session_id,
                platform="avibe",
                author="user",
                source="user",
                message_type=messages_service.PENDING_TYPE,
                text=text if isinstance(text, str) else None,
                content=content if isinstance(content, dict) else None,
                metadata=payload.get("metadata") or {},
                author_id=payload.get("author_id"),
                author_name=payload.get("author_name"),
            )
            messages_service.clear_draft(conn, session_id)
            workbench_sessions_service.touch_session(conn, session_id)
        return row

    def _promote_and_publish(row: dict) -> dict:
        """Promote the reserved pending row to a transcript-visible ``user`` row
        and fan it out (message.new + activity + inbox bump). Returns the row
        with its type corrected. The agent-reply side rides the controller→
        browser bridge, but the user row is persisted in this UI process so the
        controller bus never sees it."""
        with engine.begin() as conn:
            promoted = messages_service.promote_pending(conn, row["id"], "user")
        if not promoted:
            # The row wasn't pending anymore: the controller already promoted it
            # (e.g. enqueued as 'queued' via the busy-session path) before our
            # dispatch call failed/returned. Don't publish a phantom 'user'
            # transcript row alongside the still-queued item — nudge the queue view
            # and report it as queued instead (Codex P2).
            broker.publish("queue.updated", {"session_id": session_id, "scope_id": session["scope_id"]})
            return {**row, "type": "queued"}
        row = {**row, "type": "user"}
        broker.publish("message.new", row)
        broker.publish(
            "session.activity",
            {"session_id": session_id, "scope_id": session["scope_id"], "event": "user_message"},
        )
        try:
            with engine.connect() as conn:
                inbox_row = messages_service.get_inbox_session(conn, session_id, platform="avibe")
            if inbox_row is not None:
                broker.publish("inbox.session.updated", inbox_row)
        except Exception:
            logger.debug("inbox.session.updated publish (user message) failed", exc_info=True)
        return row

    # Reserve the row FIRST (pending), then decide by the dispatch outcome.
    message = _persist_user_row()
    # Content-only message (attachment, no text): nothing to run + the
    # dispatch endpoint requires text, so just promote + publish, no turn.
    if not dispatch_text.strip():
        return jsonify(_promote_and_publish(message)), 201
    # Session/page-scoped model (the web Chat): fire-and-forget the turn; the
    # reply arrives over ``message.new``. The controller atomically either lets
    # the turn start (we then promote the row to user) or — if a turn is already
    # running — promotes this row to queued itself (send-while-busy), so we never
    # write a second row and there's no enqueue/flush race and no transcript flash.
    dispatch_payload = {
        "session_id": session_id,
        "text": dispatch_text,
        "scope_id": session["scope_id"],
        "user_message_id": message.get("id"),
    }
    try:
        result = await internal_client.dispatch_async(dispatch_payload)
    except internal_client.InternalServerUnavailable as exc:
        # Couldn't reach the controller — promote + surface the row so the
        # user still sees their message, plus the failure.
        published = _promote_and_publish(message)
        return jsonify({**published, "dispatch_error": "internal_unavailable", "detail": str(exc)}), 502
    except Exception as exc:
        # The socket existed but the call failed another way (ReadTimeout, a
        # non-JSON / 500 response, etc.). The row is still reserved as hidden
        # ``pending`` and the draft was cleared, so WITHOUT this the user's text
        # would vanish from both transcript and queue behind an error. Promote +
        # publish it with the error, same as the unavailable branch (Codex P2).
        logger.warning("dispatch_async call failed for session %s: %s", session_id, exc, exc_info=True)
        published = _promote_and_publish(message)
        return jsonify({**published, "dispatch_error": "dispatch_failed", "detail": str(exc)}), 502
    status = result.get("status_code", 500)
    body = result.get("body") or {}
    if status == 202 and body.get("queued"):
        # Enqueued behind a running turn: the controller already promoted the
        # row pending→queued, so it stays OUT of the transcript (no
        # message.new); show it above the composer via queue.updated.
        broker.publish("queue.updated", {"session_id": session_id, "scope_id": session["scope_id"]})
        return jsonify({**message, "type": "queued", "queued": True}), 202
    if status == 202:
        # Turn started — promote + publish the prompt.
        return jsonify(_promote_and_publish(message)), 201
    # Dispatch failed: still promote + show the row + the error.
    published = _promote_and_publish(message)
    return jsonify({**published, "dispatch_error": "dispatch_failed", "detail": body}), 502


@app.route("/api/sessions/<session_id>/cancel", methods=["POST"])
async def sessions_cancel(session_id: str):
    """Stop an in-flight ``dispatch_turn`` for this session.

    Proxies to ``POST /internal/cancel/<session_id>`` on the controller's
    Unix socket. Falls back to a 503 if the socket is unreachable so
    the UI can show a sensible "cannot stop right now" state instead
    of pretending the cancel succeeded.
    """

    from vibe import internal_client

    try:
        result = await internal_client.cancel_dispatch(session_id)
    except internal_client.InternalServerUnavailable as exc:
        return jsonify({"ok": False, "code": "internal_unavailable", "detail": str(exc)}), 503
    status = result.get("status_code", 500)
    body = result.get("body") or {}
    body.setdefault("ok", status == 200)
    return jsonify(body), status


@app.route("/api/sessions/<session_id>/mark-read", methods=["POST"])
def sessions_mark_read(session_id: str):
    from core.services import sessions as workbench_sessions_service
    from storage import messages_service
    from vibe.sse_broker import broker

    payload = request.json or {}
    until_message_id = payload.get("until_message_id")

    engine = _projects_engine()
    try:
        with engine.begin() as conn:
            session = workbench_sessions_service.get_session(conn, session_id)
            updated = messages_service.mark_session_read(
                conn, session_id, until_message_id=until_message_id
            )
            unread_counts = messages_service.unread_counts(conn, platform="avibe")
            unread_by_session = messages_service.unread_counts_by_session(conn, platform="avibe")
    except LookupError as err:
        return jsonify({"error": str(err)}), 404
    if updated:
        broker.publish(
            "inbox.unread.changed",
            {
                "session_id": session_id,
                "scope_id": session["scope_id"],
                "delta": -updated,
                "unread_counts": unread_counts,
                "unread_by_session": unread_by_session,
            },
        )
    return jsonify(
        {
            "updated": updated,
            "unread_counts": unread_counts,
            "unread_by_session": unread_by_session,
        }
    )


@app.route("/api/sessions/<session_id>/turn-state", methods=["GET"])
async def sessions_turn_state(session_id: str):
    """Whether a turn is currently in flight (so a freshly loaded / reconnected
    Chat page can restore its Stop/working state). Degrades to idle if the
    controller socket is unreachable."""
    from vibe import internal_client

    try:
        result = await internal_client.turn_state(session_id)
    except internal_client.InternalServerUnavailable:
        return jsonify({"in_flight": False})
    body = result.get("body") or {}
    return jsonify({"in_flight": bool(body.get("in_flight"))})


@app.route("/api/sessions/<session_id>/queue", methods=["GET"])
def sessions_queue_list(session_id: str):
    """Pending send-while-busy messages for a session (shown above the composer)."""
    from storage import messages_service

    engine = _projects_engine()
    with engine.connect() as conn:
        queued = messages_service.list_queued(conn, session_id)
    return jsonify({"queued": queued})


@app.route("/api/sessions/<session_id>/queue/<message_id>", methods=["DELETE"])
def sessions_queue_remove(session_id: str, message_id: str):
    """Drop one queued message (the per-item delete in the queue strip)."""
    from storage import messages_service
    from vibe.sse_broker import broker

    engine = _projects_engine()
    with engine.begin() as conn:
        removed = messages_service.remove_queued(conn, session_id, message_id)
    if removed:
        broker.publish("queue.updated", {"session_id": session_id})
    return jsonify({"removed": bool(removed)})


@app.route("/api/sessions/<session_id>/queue/<message_id>/send-now", methods=["POST"])
async def sessions_queue_send_now(session_id: str, message_id: str):
    """Run the queue now ("立即发送"): interrupt the running turn + flush. The
    queue flushes as one merged turn, so ``message_id`` identifies the button's
    item but the whole queue runs (the merge is the user's chosen behavior)."""
    from vibe import internal_client

    try:
        result = await internal_client.send_now(session_id)
    except internal_client.InternalServerUnavailable as exc:
        return jsonify({"ok": False, "code": "internal_unavailable", "detail": str(exc)}), 503
    status = result.get("status_code", 500)
    body = result.get("body") or {}
    body.setdefault("ok", status < 400)
    return jsonify(body), status


@app.route("/api/sessions/<session_id>/draft", methods=["GET"])
def sessions_draft_get(session_id: str):
    """The session's saved unsent compose text (restored on open / device switch)."""
    from storage import messages_service

    engine = _projects_engine()
    with engine.connect() as conn:
        draft = messages_service.get_draft(conn, session_id)
    return jsonify({"text": (draft or {}).get("text") or ""})


@app.route("/api/sessions/<session_id>/draft", methods=["PUT"])
def sessions_draft_set(session_id: str):
    """Upsert the session's draft (debounced from the composer). Blank clears it."""
    from core.services import sessions as workbench_sessions_service
    from storage import messages_service

    payload = request.json or {}
    text = payload.get("text")
    engine = _projects_engine()
    try:
        with engine.begin() as conn:
            session = workbench_sessions_service.get_session(conn, session_id)
            messages_service.set_draft(
                conn, scope_id=session["scope_id"], session_id=session_id, text=text if isinstance(text, str) else None
            )
    except LookupError as err:
        return jsonify({"error": str(err)}), 404
    return jsonify({"ok": True})


@app.route("/api/events", methods=["GET"])
async def workbench_events():
    """Server-Sent Events stream for the workbench.

    Browsers open this once and keep it open; the route streams JSON
    events (message.new, session.activity, inbox.unread.changed) as
    they happen elsewhere in the app, plus a 15-second keep-alive
    comment line so Cloudflare-style proxies don't kill the idle TCP
    connection.

    Native FastAPI ``StreamingResponse`` so the loop stays async and
    each browser only costs one task, not one OS thread.
    """

    import asyncio

    from fastapi.responses import StreamingResponse

    from vibe.sse_broker import broker

    async def generate():
        sub_id, queue = broker.subscribe()
        try:
            # First chunk = handshake + sub_id so the client can include it in
            # subsequent debug logs / cancel calls if we ever need them.
            yield ": stream connected\n\n"
            yield f"event: connected\ndata: {{\"sub_id\": {sub_id}}}\n\n"
            while True:
                try:
                    event_type, payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: {event_type}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    # 15s keep-alive — Cloudflare Tunnel default idle is well
                    # below 100s but this still keeps mid-tier proxies happy.
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            broker.unsubscribe(sub_id)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            # Disable nginx/cloudflare body buffering on the response side
            # so chunks reach the client immediately.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/inbox", methods=["GET"])
def inbox_list():
    """Per-session ("Slack-like") inbox feed: one row per conversation, newest
    activity first. Defaults to avibe-only per workbench scope."""

    from storage import messages_service

    platform = request.args.get("platform") or "avibe"
    scope_filter = platform if platform != "all" else None
    unread_only = request.args.get("unread_only") in {"1", "true", "yes"}
    try:
        limit = int(request.args.get("limit") or 30)
    except (TypeError, ValueError):
        limit = 30
    before = request.args.get("before") or None

    engine = _projects_engine()
    with engine.connect() as conn:
        result = messages_service.list_inbox_sessions(
            conn,
            platform=scope_filter,
            unread_only=unread_only,
            limit=limit,
            before=before,
        )
        # Pagination-independent unread map for the sidebar badges (a session
        # with unread may sit past the first inbox page) + header totals.
        per_session = messages_service.unread_counts_by_session(conn, platform=scope_filter)
        result["unread_by_session"] = per_session
        result["unread_total"] = sum(per_session.values())
        result["unread_sessions"] = len(per_session)
    return jsonify(result)


# =============================================================================
# Harness Endpoints (read-only v1)
# =============================================================================
#
# Workbench Harness page reads scheduled tasks, watches, and agent runs out
# of the same SQLite store the scheduler writes to. Mutations (delete /
# cancel / pause-resume) need to talk to the live ScheduledTaskService and
# WatchSupervisor so the in-memory schedule stays consistent — that wiring
# lands in a follow-up commit.


@contextmanager
def _harness_store():
    # ``SQLiteBackgroundTaskStore`` opens a dedicated ``SqliteInvalidationProbe``
    # connection in __init__ that only closes when ``store.close()`` is
    # called. Harness routes are polled frequently from the workbench UI,
    # so leaking a connection per request exhausts the SQLite pool. The
    # context manager makes ownership explicit at every call site.
    from storage.background import SQLiteBackgroundTaskStore

    store = SQLiteBackgroundTaskStore()
    try:
        yield store
    finally:
        store.close()


@app.route("/api/harness/tasks", methods=["GET"])
def harness_tasks_list():
    with _harness_store() as store:
        return jsonify({"tasks": store.list_scheduled_tasks()})


@app.route("/api/harness/tasks/<task_id>", methods=["PATCH"])
def harness_task_patch(task_id: str):
    payload = request.json or {}
    if "enabled" not in payload:
        return jsonify({"ok": False, "code": "invalid_payload", "message": "missing 'enabled'"}), 400
    enabled = bool(payload["enabled"])
    with _harness_store() as store:
        if not store.get_scheduled_task(task_id):
            return jsonify({"ok": False, "code": "task_not_found"}), 404
        store.set_definition_enabled(task_id, enabled, definition_type="scheduled")
        task = store.get_scheduled_task(task_id)
    return jsonify({"ok": True, "task": task})


@app.route("/api/harness/tasks/<task_id>", methods=["DELETE"])
def harness_task_delete(task_id: str):
    with _harness_store() as store:
        if not store.get_scheduled_task(task_id):
            return jsonify({"ok": False, "code": "task_not_found"}), 404
        store.remove_task(task_id)
    return jsonify({"ok": True, "id": task_id})


@app.route("/api/harness/watches", methods=["GET"])
def harness_watches_list():
    with _harness_store() as store:
        watches = store.list_watches()
        runtime = store.load_watch_runtime().get("watches") or {}
    for watch in watches:
        watch["runtime"] = runtime.get(watch["id"]) or {"running": False}
    return jsonify({"watches": watches})


@app.route("/api/harness/watches/<watch_id>", methods=["PATCH"])
def harness_watch_patch(watch_id: str):
    payload = request.json or {}
    if "enabled" not in payload:
        return jsonify({"ok": False, "code": "invalid_payload", "message": "missing 'enabled'"}), 400
    enabled = bool(payload["enabled"])
    with _harness_store() as store:
        if not store.get_watch(watch_id):
            return jsonify({"ok": False, "code": "watch_not_found"}), 404
        store.set_definition_enabled(watch_id, enabled, definition_type="watch")
        watch = store.get_watch(watch_id)
        runtime = store.load_watch_runtime().get("watches") or {}
        if watch:
            watch["runtime"] = runtime.get(watch_id) or {"running": False}
    return jsonify({"ok": True, "watch": watch})


@app.route("/api/harness/watches/<watch_id>", methods=["DELETE"])
def harness_watch_delete(watch_id: str):
    with _harness_store() as store:
        if not store.get_watch(watch_id):
            return jsonify({"ok": False, "code": "watch_not_found"}), 404
        store.remove_task(watch_id)
    return jsonify({"ok": True, "id": watch_id})


@app.route("/api/harness/runs", methods=["GET"])
def harness_runs_list():
    from storage.pagination import make_page_request

    try:
        limit = int(request.args.get("limit") or 30)
    except (TypeError, ValueError):
        limit = 30
    try:
        page = int(request.args.get("page") or 1)
    except (TypeError, ValueError):
        page = 1
    status = request.args.get("status") or None
    run_type = request.args.get("run_type") or None
    agent_name = request.args.get("agent_name") or None
    definition_id = request.args.get("definition_id") or None
    query = request.args.get("query") or None

    page_request = make_page_request(page=page, limit=limit)
    with _harness_store() as store:
        page_result = store.list_runs_page(
            status=status,
            run_type=run_type,
            agent_name=agent_name,
            definition_id=definition_id,
            query=query,
            page_request=page_request,
            newest_first=True,
        )
    return jsonify(
        {
            "runs": page_result.items,
            "page": page_result.page,
            "limit": page_result.limit,
            "has_more": page_result.has_more,
        }
    )


@app.route("/api/harness/runs/<run_id>", methods=["GET"])
def harness_run_detail(run_id: str):
    with _harness_store() as store:
        run = store.get_run(run_id)
    if not run:
        return jsonify({"ok": False, "code": "run_not_found"}), 404
    return jsonify({"ok": True, "run": run})


# =============================================================================
# User & Bind Code Endpoints
# =============================================================================


@app.route("/api/users", methods=["GET"])
def users_get():
    from vibe import api

    return jsonify(api.get_users(request.args.get("platform") or None))


@app.route("/api/users", methods=["POST"])
def users_post():
    from vibe import api

    payload = request.json or {}
    return jsonify(api.save_users(payload))


@app.route("/api/users/<user_id>/admin", methods=["POST"])
def users_toggle_admin(user_id):
    from vibe import api

    payload = request.json or {}
    return jsonify(api.toggle_admin(user_id, payload.get("is_admin", False), payload.get("platform") or None))


@app.route("/api/users/<user_id>", methods=["DELETE"])
def users_delete(user_id):
    from vibe import api

    result = api.remove_user(user_id, request.args.get("platform") or None)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/bind-codes", methods=["GET"])
def bind_codes_get():
    from vibe import api

    return jsonify(api.get_bind_codes())


@app.route("/api/bind-codes", methods=["POST"])
def bind_codes_post():
    from vibe import api

    payload = request.json or {}
    result = api.create_bind_code(
        code_type=payload.get("type", "one_time"),
        expires_at=payload.get("expires_at"),
    )
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/bind-codes/<code>", methods=["DELETE"])
def bind_codes_delete(code):
    from vibe import api

    result = api.delete_bind_code(code)
    if not result.get("ok"):
        return jsonify(result), 404
    return jsonify(result)


@app.route("/api/setup/first-bind-code", methods=["GET"])
def setup_first_bind_code():
    from vibe import api

    return jsonify(api.get_first_bind_code())


# =============================================================================
# E2E Test-Only Endpoints (gated by E2E_TEST_MODE env var)
# =============================================================================

if os.environ.get("E2E_TEST_MODE", "").lower() in ("true", "1", "yes"):
    logger.warning(
        "E2E_TEST_MODE is ENABLED. /e2e/* endpoints are registered. "
        "These endpoints allow unauthenticated config mutation. "
        "Do NOT enable in production."
    )

    @app.route("/e2e/simulate-interaction", methods=["POST"])
    def e2e_simulate_interaction():
        """Simulate a modal submission via the settings/config APIs.

        Only registered when E2E_TEST_MODE=true.

        NOTE: Button clicks (cmd_settings, cmd_routing, etc.) should be
        triggered by sending text commands via Bot B (/settings, /routing, etc.).
        This endpoint handles modal *submissions* that Bot B cannot trigger
        because they require UI interaction (select dropdowns, click Save).

        The UI server and the service process are separate processes, so this
        endpoint operates through the SettingsStore (shared JSON file) rather
        than invoking the controller directly.

        JSON fields:
            action (str):       "settings_submit" | "routing_submit" | "cwd_submit"
            modal_values (dict): the values to submit
        """
        payload = request.json or {}
        action = payload.get("action", "")
        modal_values = payload.get("modal_values", {})

        if not action:
            return jsonify({"ok": False, "error": "action required"}), 400

        try:
            if action == "settings_submit":
                # Merge settings into existing store (not wholesale replace)
                from config.v2_settings import ChannelSettings, normalize_show_message_types
                from core.services import settings as settings_service
                from vibe.api import _parse_routing
                from vibe.api import _current_platform

                settings_key = modal_values.get("settings_key") or modal_values.get("channel_id")
                if not settings_key:
                    return jsonify({"ok": False, "error": "settings_key or channel_id required in modal_values"}), 400

                store = settings_service.reload_settings_store()
                platform = _current_platform()
                ch = store.find_channel(settings_key, platform=platform)
                if not ch:
                    ch = ChannelSettings(enabled=True)
                    store.update_channel(settings_key, ch, platform=platform)

                if "show_message_types" in modal_values:
                    ch.show_message_types = normalize_show_message_types(modal_values["show_message_types"])
                if "custom_cwd" in modal_values:
                    ch.custom_cwd = modal_values["custom_cwd"]
                if "require_mention" in modal_values:
                    ch.require_mention = modal_values["require_mention"]
                if "routing" in modal_values:
                    ch.routing = _parse_routing(modal_values["routing"])

                store.save()
                return jsonify({"ok": True, "action": action})

            elif action == "routing_submit":
                # Write routing config for a specific channel/user
                channel_id = modal_values.get("channel_id") or modal_values.get("settings_key")
                if not channel_id:
                    return jsonify({"ok": False, "error": "channel_id required in modal_values"}), 400

                from core.services import settings as settings_service

                store = settings_service.reload_settings_store()
                from vibe.api import _current_platform

                platform = _current_platform()
                ch = store.find_channel(channel_id, platform=platform)
                if ch:
                    from config.v2_settings import RoutingSettings

                    ch.routing = RoutingSettings(
                        agent_backend=modal_values.get("backend", "opencode"),
                        model=(
                            modal_values.get("opencode_model")
                            or modal_values.get("claude_model")
                            or modal_values.get("codex_model")
                        ),
                        reasoning_effort=(
                            modal_values.get("opencode_reasoning_effort")
                            or modal_values.get("claude_reasoning_effort")
                            or modal_values.get("codex_reasoning_effort")
                        ),
                        opencode_agent=modal_values.get("opencode_agent"),
                        claude_agent=modal_values.get("claude_agent"),
                        codex_agent=modal_values.get("codex_agent"),
                    )
                    store.save()
                    return jsonify({"ok": True, "action": action})
                else:
                    return jsonify({"ok": False, "error": f"channel {channel_id} not found in settings"}), 404

            elif action == "cwd_submit":
                # Merge CWD into existing config (load → modify → save)
                from vibe import api as vibe_api

                current = vibe_api.config_to_payload(vibe_api.load_config())
                current.setdefault("runtime", {})
                current["runtime"]["default_cwd"] = modal_values.get("cwd", "/tmp")
                result = vibe_api.save_config(current)
                return jsonify({"ok": True, "action": action})

            elif action == "routing_submit":
                # Write routing config for a specific channel/user
                channel_id = modal_values.get("channel_id") or modal_values.get("settings_key")
                if not channel_id:
                    return jsonify({"ok": False, "error": "channel_id required in modal_values"}), 400

                from core.services import settings as settings_service

                store = settings_service.reload_settings_store()
                from vibe.api import _current_platform

                platform = _current_platform()
                ch = store.find_channel(channel_id, platform=platform)
                if ch:
                    from config.v2_settings import RoutingSettings

                    ch.routing = RoutingSettings(
                        agent_backend=modal_values.get("backend", "opencode"),
                        model=(
                            modal_values.get("opencode_model")
                            or modal_values.get("claude_model")
                            or modal_values.get("codex_model")
                        ),
                        reasoning_effort=(
                            modal_values.get("opencode_reasoning_effort")
                            or modal_values.get("claude_reasoning_effort")
                            or modal_values.get("codex_reasoning_effort")
                        ),
                        opencode_agent=modal_values.get("opencode_agent"),
                        claude_agent=modal_values.get("claude_agent"),
                        codex_agent=modal_values.get("codex_agent"),
                    )
                    store.save()
                    return jsonify({"ok": True, "action": action})
                else:
                    return jsonify({"ok": False, "error": f"channel {channel_id} not found in settings"}), 404

            elif action == "cwd_submit":
                # Update CWD via config API
                new_cwd = modal_values.get("cwd", "/tmp")
                result = vibe_api.save_config({"runtime": {"default_cwd": new_cwd}})
                return jsonify({"ok": True, "action": action, "result": result})

            else:
                return jsonify({"ok": False, "error": f"unknown action: {action}"}), 400

        except Exception as e:
            logger.exception("E2E simulate-interaction failed")
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/e2e/ping", methods=["GET"])
    def e2e_ping():
        """Simple check that E2E test mode is active."""
        return jsonify({"ok": True, "e2e_test_mode": True})

    logger.info("E2E_TEST_MODE enabled: /e2e/* endpoints registered")


# =============================================================================
# Static Files (SPA)
# =============================================================================


def _show_page_offline_response():
    html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Show Page Offline</title>
    <style>
      body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; box-sizing: border-box; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f7f8fb; color: #172033; }
      main { width: min(560px, 100%); border: 1px solid rgba(23, 32, 51, 0.12); border-radius: 12px; background: white; padding: 32px; box-shadow: 0 20px 60px rgba(23, 32, 51, 0.10); }
      h1 { margin: 0; font-size: clamp(28px, 7vw, 42px); line-height: 1.05; letter-spacing: 0; }
      p { margin: 14px 0 0; line-height: 1.65; color: #526078; }
    </style>
  </head>
  <body>
    <main>
      <h1>This Show Page is offline</h1>
      <p>The page owner has taken this page offline. The link is no longer available.</p>
    </main>
  </body>
</html>
"""
    return Response(html, status=401, mimetype="text/html; charset=utf-8")


def _show_page_not_found_response():
    return jsonify({"error": "not_found"}), 404


def _show_page_runtime_unavailable_response():
    return jsonify({"error": "show_runtime_unavailable"}), 503


def _is_show_api_asset(asset_path: str) -> bool:
    relative = (asset_path or "").strip("/")
    return relative == "api" or relative.startswith("api/") or relative == "__show" or relative.startswith("__show/")


def _is_show_page_entry_asset(asset_path: str) -> bool:
    relative = (asset_path or "").strip("/")
    return relative in {"", "index.html"}


def _show_page_recovery_response(session_id: str):
    from core.show_pages import show_page_runtime_recovery_html

    return Response(show_page_runtime_recovery_html(session_id), status=200, mimetype="text/html; charset=utf-8")


def _show_page_file_response(root: Path, asset_path: str):
    relative = (asset_path or "").strip("/")
    if not relative:
        relative = "index.html"
    candidate = (root / unquote(relative)).resolve()
    root_resolved = root.resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        return jsonify({"error": "not_found"}), 404
    if not candidate.exists() or not candidate.is_file():
        return _show_page_not_found_response()
    mime_type, _ = mimetypes.guess_type(str(candidate))
    response = send_file(candidate, mimetype=mime_type or "application/octet-stream")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _show_session_event_error_response(exc: Exception):
    code = getattr(exc, "code", "show_session_event_failed")
    status = 404 if code == "session_not_found" else 400
    return jsonify({"ok": False, "code": code, "error": str(exc)}), status


def _show_session_event_store():
    from core.show_session_events import ShowSessionEventStore

    return ShowSessionEventStore()


def _show_events_payload_from_request() -> dict[str, Any]:
    payload = request.json
    return payload if isinstance(payload, dict) else {}


def _last_event_id_from_request() -> str | None:
    value = request.headers.get("Last-Event-ID")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _show_event_write_authorized(session_id: str) -> bool:
    token = request.headers.get(SHOW_EVENT_WRITE_TOKEN_HEADER)
    if not token:
        return False
    try:
        expected = show_event_write_token(session_id)
    except Exception:
        return False
    return hmac.compare_digest(token, expected)


def _show_event_response_from_payload(session_id: str, payload: dict[str, Any]):
    store = _show_session_event_store()
    try:
        event_payload = store.append(session_id, payload)
    except Exception as exc:
        return _show_session_event_error_response(exc)
    finally:
        store.close()

    _publish_show_session_event(event_payload)
    _dispatch_show_event_if_requested(event_payload)
    return jsonify({"ok": True, "event": event_payload}), 201


def record_local_show_event(session_id: str, payload: dict[str, Any], *, dispatch_sync: bool = False) -> dict[str, Any]:
    store = _show_session_event_store()
    try:
        event_payload = store.append(session_id, payload)
    finally:
        store.close()
    _publish_show_session_event(event_payload)
    if dispatch_sync and _show_event_requests_dispatch(event_payload):
        try:
            asyncio.run(_run_show_event_dispatch(event_payload))
        except RuntimeError:
            _dispatch_show_event_if_requested(event_payload)
    else:
        _dispatch_show_event_if_requested(event_payload)
    return event_payload


def _publish_show_session_event(event_payload: dict[str, Any]) -> None:
    from vibe.sse_broker import broker

    broker.publish("show.event", event_payload)
    message = event_payload.get("message")
    if isinstance(message, dict):
        broker.publish("message.new", message)
    broker.publish(
        "session.activity",
        {
            "session_id": event_payload.get("session_id"),
            "scope_id": event_payload.get("scope_id"),
            "event": "show_event",
        },
    )


def _dispatch_show_event_if_requested(event_payload: dict[str, Any]) -> None:
    if not _show_event_requests_dispatch(event_payload):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        thread = threading.Thread(
            target=lambda: asyncio.run(_run_show_event_dispatch(event_payload)),
            name="show-event-dispatch",
            daemon=True,
        )
        thread.start()
        return
    loop.create_task(_run_show_event_dispatch(event_payload))


def _show_event_requests_dispatch(event_payload: dict[str, Any]) -> bool:
    if event_payload.get("actor") != "human":
        return False
    if event_payload.get("type") not in {"human.intent.submitted", "human.annotation.created"}:
        return False
    payload = event_payload.get("payload")
    if not isinstance(payload, dict):
        return False
    return bool(payload.get("dispatch"))


async def _run_show_event_dispatch(event_payload: dict[str, Any]) -> None:
    from vibe import internal_client

    session_id = event_payload.get("session_id")
    scope_id = event_payload.get("scope_id")
    transcript_text = event_payload.get("transcript_text")
    if not isinstance(session_id, str) or not session_id or not isinstance(transcript_text, str) or not transcript_text.strip():
        return
    dispatch_payload = {
        "session_id": session_id,
        "text": transcript_text,
        "scope_id": scope_id,
        "user_message_id": event_payload.get("message_id"),
        "message_id": event_payload.get("message_id"),
        "platform": "avibe",
        "channel_id": session_id,
    }
    try:
        async for event_name, data in internal_client.stream_dispatch(dispatch_payload):
            _publish_show_dispatch_event(event_payload, event_name, data)
    except internal_client.InternalServerUnavailable as exc:
        _publish_show_dispatch_event(
            event_payload,
            "stream.error",
            {"reason": "internal_server_unavailable", "detail": str(exc)},
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("show event dispatch failed")
        _publish_show_dispatch_event(event_payload, "stream.error", {"reason": "dispatch_failed", "detail": str(exc)})


def _publish_show_dispatch_event(event_payload: dict[str, Any], event_name: str, data: Any) -> None:
    from vibe.sse_broker import broker

    broker.publish(
        "show.dispatch",
        {
            "show_event_id": event_payload.get("id"),
            "session_id": event_payload.get("session_id"),
            "scope_id": event_payload.get("scope_id"),
            "event": event_name,
            "data": data,
        },
    )


def _show_event_response_payload(event_payload: dict[str, Any], *, public: bool = False) -> dict[str, Any]:
    if not public:
        return event_payload
    return {
        key: value
        for key, value in event_payload.items()
        if key not in {"session_id", "scope_id", "message_id", "message"}
    }


def _show_dispatch_response_payload(event_payload: dict[str, Any], *, public: bool = False) -> dict[str, Any]:
    if not public:
        return event_payload
    return {
        key: _redact_public_dispatch_value(value)
        for key, value in event_payload.items()
        if key not in {"session_id", "scope_id", "message_id", "message", "user_message_id"}
    }


def _redact_public_dispatch_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_public_dispatch_value(nested)
            for key, nested in value.items()
            if key not in {"session_id", "scope_id", "message_id", "message", "user_message_id"}
        }
    if isinstance(value, list):
        return [_redact_public_dispatch_value(item) for item in value]
    return value


def _show_events_list_payload(payload: dict[str, Any], *, public: bool = False) -> dict[str, Any]:
    if not public:
        return payload
    return {
        **payload,
        "events": [
            _show_event_response_payload(event_payload, public=True)
            for event_payload in payload.get("events", [])
            if isinstance(event_payload, dict)
        ],
    }


async def _show_events_stream(session_id: str, *, after_id: str | None = None, public: bool = False):
    import asyncio

    from fastapi.responses import StreamingResponse

    from vibe.sse_broker import broker

    def _event_visible(event_payload: dict[str, Any]) -> bool:
        return event_payload.get("session_id") == session_id

    async def generate():
        sub_id, queue = broker.subscribe()
        replayed_ids: set[str] = set()
        try:
            store = _show_session_event_store()
            try:
                cursor = after_id
                yield ": show events connected\n\n"
                while True:
                    batch = store.list(session_id, after_id=cursor, limit=500)
                    events = batch["events"]
                    if not events:
                        break
                    for event_payload in events:
                        if isinstance(event_payload.get("id"), str):
                            replayed_ids.add(event_payload["id"])
                        yield _sse_frame("show.event", _show_event_response_payload(event_payload, public=public))
                    cursor = batch.get("next_after_id")
                    if not cursor:
                        break
            finally:
                store.close()

            while True:
                try:
                    event_type, payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    decoded = json.loads(payload)
                    event_payload = decoded.get("data") if isinstance(decoded, dict) else None
                    if event_type == "show.event" and isinstance(event_payload, dict) and _event_visible(event_payload):
                        event_id = event_payload.get("id")
                        if isinstance(event_id, str) and event_id in replayed_ids:
                            continue
                        if isinstance(event_id, str):
                            replayed_ids.add(event_id)
                        yield _sse_frame("show.event", _show_event_response_payload(event_payload, public=public))
                    elif event_type == "show.dispatch" and isinstance(event_payload, dict) and _event_visible(event_payload):
                        yield _sse_frame("show.dispatch", _show_dispatch_response_payload(event_payload, public=public))
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            raise
        finally:
            broker.unsubscribe(sub_id)

    def _sse_frame(event_type: str, data: Any) -> str:
        event_id = data.get("id") if isinstance(data, dict) else None
        prefix = f"id: {event_id}\n" if isinstance(event_id, str) and event_id else ""
        return f"{prefix}event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _show_events_response(session_id: str, *, public: bool = False):
    if request.method == "GET":
        if request.args.get("stream") == "1":
            return await _show_events_stream(
                session_id,
                after_id=request.args.get("after_id") or _last_event_id_from_request(),
                public=public,
            )
        store = _show_session_event_store()
        try:
            try:
                limit = int(request.args.get("limit") or 100)
            except (TypeError, ValueError):
                limit = 100
            payload = store.list(session_id, after_id=request.args.get("after_id") or None, limit=limit)
            return jsonify(_show_events_list_payload(payload, public=public))
        finally:
            store.close()

    if request.method != "POST":
        return jsonify({"ok": False, "code": "method_not_allowed"}), 405
    if not _show_event_write_authorized(session_id):
        return jsonify({"ok": False, "code": "show_event_write_forbidden"}), 403

    return _show_event_response_from_payload(session_id, _show_events_payload_from_request())


@app.route("/api/show/sessions/<session_id>/events", methods=["POST"])
def show_session_events_create(session_id: str):
    if not _is_cli_show_event_request():
        return jsonify({"ok": False, "code": "forbidden"}), 403
    return _show_event_response_from_payload(session_id, _show_events_payload_from_request())


async def _show_page_runtime_response(
    session_id: str,
    asset_path: str,
    starlette_request: FastAPIRequest,
    *,
    external_prefix: str | None = None,
):
    from core.show_runtime import get_show_runtime_manager

    session_part = quote(session_id, safe="")
    asset_part = quote(asset_path.lstrip("/"), safe="/@:-._~")
    runtime_path = f"/sessions/{session_part}/app/"
    if asset_part:
        runtime_path = f"{runtime_path}{asset_part}"
    if starlette_request.url.query:
        runtime_path = f"{runtime_path}?{starlette_request.url.query}"
    forwarded_headers = {
        key: value
        for key, value in starlette_request.headers.items()
        if key.lower() in _SHOW_RUNTIME_REQUEST_HEADER_ALLOWLIST
    }
    if external_prefix:
        forwarded_headers["x-vibe-show-base"] = f"{external_prefix.rstrip('/')}/"
    body = await starlette_request.body()
    proxied = await get_show_runtime_manager().request(
        starlette_request.method,
        runtime_path,
        headers=forwarded_headers,
        body=body or None,
    )
    response_headers = {
        key: value
        for key, value in proxied.headers.items()
        if key.lower() in _SHOW_RUNTIME_RESPONSE_HEADER_ALLOWLIST
    }
    if location := response_headers.get("location"):
        response_headers["location"] = _rewrite_show_runtime_location(
            session_id,
            location,
            external_prefix=external_prefix,
        )
    response_headers["X-Content-Type-Options"] = "nosniff"
    response_headers["Referrer-Policy"] = "no-referrer"
    return FastAPIResponse(content=proxied.content, status_code=proxied.status_code, headers=response_headers)


def _rewrite_show_runtime_location(session_id: str, location: str, *, external_prefix: str | None = None) -> str:
    parsed = urlsplit(location)
    internal_prefix = f"/sessions/{quote(session_id, safe='')}/app"
    external_prefix = (external_prefix or f"/show/{quote(session_id, safe='')}").rstrip("/")
    if parsed.path == internal_prefix:
        public_path = f"{external_prefix}/"
    elif parsed.path.startswith(f"{internal_prefix}/"):
        suffix = parsed.path[len(internal_prefix) :].lstrip("/")
        public_path = f"{external_prefix}/{suffix}"
    else:
        return location
    return urlunsplit(("", "", public_path, parsed.query, parsed.fragment))


def _with_show_event_write_cookie(response: Response, session_id: str, *, enabled: bool) -> Response:
    if enabled:
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.set_cookie(
            SHOW_EVENT_WRITE_TOKEN_COOKIE,
            show_event_write_token(session_id),
            httponly=False,
            secure=request.is_secure,
            samesite="Strict",
            path=f"/show/{quote(session_id, safe='')}/",
        )
    else:
        response.delete_cookie(SHOW_EVENT_WRITE_TOKEN_COOKIE, path=f"/show/{quote(session_id, safe='')}/")
    return response


def stop_show_runtime_on_shutdown() -> None:
    from core.show_runtime import stop_show_runtime_manager

    stop_show_runtime_manager()


app.add_event_handler("shutdown", stop_show_runtime_on_shutdown)


@app.route("/show/<session_id>")
def redirect_private_show_page_to_canonical_path(session_id):
    from core.show_pages import ShowPageStore

    store = ShowPageStore()
    try:
        page = store.get(session_id)
        if page is None:
            return _show_page_not_found_response()
        if page.visibility not in {"private", "offline"}:
            return _show_page_not_found_response()
        return redirect(f"/show/{quote(session_id, safe='')}/")
    finally:
        store.close()


@app.route("/show/<session_id>/", defaults={"asset_path": ""})
@app.route(
    "/show/<session_id>/",
    defaults={"asset_path": ""},
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@app.route(
    "/show/<session_id>/<path:asset_path>",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def serve_private_show_page(session_id, asset_path):
    from core.show_pages import ShowPageStore, show_page_dir

    store = ShowPageStore()
    try:
        page = store.get(session_id)
        if page is None:
            return _show_page_not_found_response()
        if page.visibility == "offline":
            return _show_page_offline_response()
        if page.visibility != "private":
            return _show_page_not_found_response()
        if asset_path.strip("/") in {"__show/events", "__events"}:
            return await _show_events_response(page.session_id)
        response = None
        if request.method in {"GET", "HEAD"} or _is_show_api_asset(asset_path):
            try:
                starlette_request = request._request
                response = await _show_page_runtime_response(page.session_id, asset_path, starlette_request)
            except Exception:
                if _is_show_api_asset(asset_path):
                    return _show_page_runtime_unavailable_response()
                if _is_show_page_entry_asset(asset_path):
                    response = _show_page_recovery_response(page.session_id)
                    logger.debug("Show runtime unavailable; serving recovery Show Page", exc_info=True)
                else:
                    logger.debug("Show runtime unavailable; serving static Show Page", exc_info=True)
        if response is None:
            response = _show_page_file_response(show_page_dir(page.session_id), asset_path)
        if request.method in {"GET", "HEAD"}:
            return _with_show_event_write_cookie(response, page.session_id, enabled=True)
        return response
    finally:
        store.close()


@app.route("/p/<share_id>")
def redirect_public_show_page_to_canonical_path(share_id):
    from core.show_pages import ShowPageStore

    store = ShowPageStore()
    try:
        page = store.get_by_share_id(share_id)
        if page is None:
            return _show_page_not_found_response()
        if page.visibility not in {"public", "offline"}:
            return _show_page_not_found_response()
        return redirect(f"/p/{quote(share_id, safe='')}/")
    finally:
        store.close()


@app.route(
    "/p/<share_id>/",
    defaults={"asset_path": ""},
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@app.route(
    "/p/<share_id>/<path:asset_path>",
    methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def serve_public_show_page(share_id, asset_path):
    from core.show_pages import ShowPageStore, show_page_dir

    store = ShowPageStore()
    try:
        page = store.get_by_share_id(share_id)
        if page is None:
            return _show_page_not_found_response()
        if page.visibility == "offline":
            return _show_page_offline_response()
        if page.visibility != "public":
            return _show_page_not_found_response()
        if asset_path.strip("/") in {"__show/events", "__events"}:
            if request.method != "GET":
                return jsonify({"ok": False, "code": "public_show_events_read_only"}), 403
            return await _show_events_response(page.session_id, public=True)
        response = None
        if request.method in {"GET", "HEAD"} or _is_show_api_asset(asset_path):
            try:
                starlette_request = request._request
                response = await _show_page_runtime_response(
                    page.session_id,
                    asset_path,
                    starlette_request,
                    external_prefix=f"/p/{quote(share_id, safe='')}",
                )
            except Exception:
                if _is_show_api_asset(asset_path):
                    return _show_page_runtime_unavailable_response()
                if _is_show_page_entry_asset(asset_path):
                    response = _show_page_recovery_response(page.session_id)
                    logger.debug("Show runtime unavailable; serving recovery public Show Page", exc_info=True)
                else:
                    logger.debug("Show runtime unavailable; serving static public Show Page", exc_info=True)
        if response is None:
            response = _show_page_file_response(show_page_dir(page.session_id), asset_path)
        if request.method in {"GET", "HEAD"}:
            return _with_show_event_write_cookie(response, page.session_id, enabled=False)
        return response
    finally:
        store.close()


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_static(path):
    """Serve static files from ui/dist, with SPA fallback to index.html."""
    ui_dist = get_ui_dist_path()

    if path.startswith("assets/"):
        file_path = ui_dist / path
    elif not path or path == "index.html":
        file_path = ui_dist / "index.html"
    else:
        file_path = ui_dist / path

    resolved_path = file_path.resolve()

    # Security check: ensure path is within ui_dist
    if ui_dist.resolve() not in resolved_path.parents and resolved_path != ui_dist.resolve():
        return jsonify({"error": "not_found"}), 404

    if resolved_path.exists() and resolved_path.is_file():
        mime_type, _ = mimetypes.guess_type(str(resolved_path))
        return send_file(resolved_path, mimetype=mime_type or "application/octet-stream")

    # SPA fallback: serve index.html for routes without file extension
    if "." not in path:
        index_path = ui_dist / "index.html"
        if index_path.exists():
            return send_file(index_path, mimetype="text/html")

    return jsonify({"error": "not_found"}), 404


# =============================================================================
# Server Entry Point
# =============================================================================


def _reconcile_remote_access_for_ui_start(config: V2Config | None) -> None:
    if config is None:
        return
    try:
        from vibe import remote_access

        result = remote_access.reconcile(config)
        if isinstance(result, dict) and result.get("ok") is False:
            logger.warning("Remote access reconcile after UI start failed: %s", result.get("error"))
    except Exception:
        logger.warning("Failed to reconcile remote access after UI start", exc_info=True)


# --- Realtime inbox bridge --------------------------------------------------
# Relays the controller's cross-process inbox events into the local SSE broker
# (see vibe/inbox_bridge.py). One task per UI-server process, owned by the ASGI
# lifecycle so it starts after the loop is alive and is cancelled cleanly on
# shutdown/reload instead of leaking a pending task.

_inbox_bridge_task: "asyncio.Task | None" = None


async def _start_inbox_bridge() -> None:
    global _inbox_bridge_task
    from vibe.inbox_bridge import run_inbox_bridge

    if _inbox_bridge_task is None or _inbox_bridge_task.done():
        _inbox_bridge_task = asyncio.create_task(run_inbox_bridge(), name="inbox-events-bridge")


async def _stop_inbox_bridge() -> None:
    global _inbox_bridge_task
    task, _inbox_bridge_task = _inbox_bridge_task, None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("inbox bridge shutdown raised", exc_info=True)


app.add_event_handler("startup", _start_inbox_bridge)
app.add_event_handler("shutdown", _stop_inbox_bridge)


def _bind_ui_socket(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if host and ":" in host else socket.AF_INET
    sock = socket.socket(family)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    sock.set_inheritable(True)
    return sock


def run_ui_server(host: str, port: int) -> None:
    """Start the FastAPI UI server."""
    global _server
    import time
    import uvicorn

    paths.ensure_data_dirs()
    try:
        from core.services import settings as settings_service

        config = settings_service.load_config()
    except FileNotFoundError:
        config = None
    except Exception as exc:
        logger.warning("Skipping UI Sentry init because config load failed: %s", exc)
        config = None
    if config is not None:
        init_sentry(config, component="ui", enable_fastapi=True)
        try:
            from vibe import remote_access

            remote_access.start_status_heartbeat(config)
        except Exception:
            logger.warning("Failed to start remote access status heartbeat", exc_info=True)
    print(f"UI Server running at http://{host}:{port}")

    # Retry binding in case of TIME_WAIT or port still held by old server during reload
    for attempt in range(10):
        bound_socket: socket.socket | None = None
        try:
            uvicorn_config = uvicorn.Config(
                app,
                host=host,
                port=port,
                log_config=None,
                access_log=False,
                loop="asyncio",
                lifespan="on",
                workers=1,
            )
            bound_socket = _bind_ui_socket(host, port)
            _server = uvicorn.Server(uvicorn_config)
            # Reconcile remote_access in the background so cloudflared download/
            # connector start does not block /health and the rest of the UI
            # from coming up after restart/reload.
            threading.Thread(
                target=_reconcile_remote_access_for_ui_start,
                args=(config,),
                daemon=True,
                name="remote-access-reconcile-on-start",
            ).start()
            _server.run(sockets=[bound_socket])
            break
        except OSError as e:
            if bound_socket is not None:
                bound_socket.close()
            if e.errno == 48 and attempt < 9:  # Address already in use (macOS)
                print(f"Port {port} in use, retrying in 1s... (attempt {attempt + 1})")
                time.sleep(1)
            elif e.errno == 98 and attempt < 9:  # Address already in use (Linux)
                print(f"Port {port} in use, retrying in 1s... (attempt {attempt + 1})")
                time.sleep(1)
            else:
                raise
