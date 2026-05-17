import base64
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
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit

import psutil
from fastapi import WebSocket, WebSocketDisconnect

from vibe.ui_compat import CompatApp, Response, g, jsonify, redirect, request, send_file

from config import paths
from config.v2_config import CONFIG_LOCK, V2Config
from modules.agents.catalog import AGENT_BACKENDS, supports_runtime_refresh
from vibe.runtime import get_ui_dist_path, get_working_dir
from vibe.sentry_integration import init_sentry

logger = logging.getLogger(__name__)

app = CompatApp(title="Vibe Remote UI", docs_url=None, redoc_url=None, openapi_url=None)

# Global server instance for graceful shutdown on reload
_server = None

STRUCTURED_LOG_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+-\s+([\w.]+)\s+-\s+(\w+)\s+-\s+(.*)$")
LEVEL_HINT_PATTERN = re.compile(r"\b(DEBUG|INFO|WARNING|ERROR|CRITICAL)\b")
TRACEBACK_EXCEPTION_PATTERN = re.compile(
    r"^[A-Za-z_][\w.]*(?:Error|Exception|Warning|Exit|Interrupt|Failure|Fault|Group)(?:[:(]|$)"
)
CSRF_COOKIE_NAME = "vibe_csrf_token"
CSRF_HEADER_NAME = "X-Vibe-CSRF-Token"
REMOTE_OAUTH_COOKIE_NAME = "__Host-vibe_remote_oauth"
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
    return (
        request.path == "/e2e/simulate-interaction"
        and os.environ.get("E2E_TEST_MODE", "").lower() in ("true", "1", "yes")
    )


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
        return V2Config.load()
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
    # When the Vibe Cloud tunnel is on, the UI binds to a wildcard so the
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


def _oauth_callback_arg(name: str) -> str | None:
    return request.args.get(name) or request.args.get(f"amp;{name}")


def _redirect_to_vibe_cloud_login(config: V2Config):
    from vibe import remote_access

    cloud = config.remote_access.vibe_cloud
    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    oauth_cookie = _make_oauth_cookie(
        cloud.session_secret,
        {
            "state": state,
            "nonce": nonce,
            "code_verifier": code_verifier,
            "next": request.full_path if request.query_string else request.path,
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


@app.route("/doctor", methods=["GET"])
def doctor_get():
    payload = {}
    doctor_path = paths.get_runtime_doctor_path()
    if doctor_path.exists():
        payload = json.loads(doctor_path.read_text(encoding="utf-8"))
    return jsonify(payload)


@app.route("/config", methods=["GET"])
def config_get():
    from vibe import api

    config = api.load_config()
    return jsonify(api.config_to_payload(config))


@app.route("/platforms", methods=["GET"])
def platforms_get():
    from vibe import api

    return jsonify(api.get_platform_catalog())


@app.route("/agent-backends", methods=["GET"])
def agent_backends_get():
    from vibe import api

    return jsonify(api.get_agent_backend_catalog())


@app.route("/settings", methods=["GET"])
def settings_get():
    # /settings doubles as a backend JSON API and a user-facing URL the SPA
    # owns (it lives under /settings/<page>). Browser navigations send
    # Accept: text/html..., while fetch() callers from the SPA send Accept:
    # */* (no explicit text/html), so we can distinguish the two and redirect
    # bookmarked / hard-refreshed browser hits to the canonical settings page
    # instead of serving raw JSON.
    if "text/html" in request.headers.get("Accept", ""):
        return redirect("/settings/service")
    from vibe import api

    return jsonify(api.get_settings(request.args.get("platform") or None))


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


@app.route("/cli/detect")
def cli_detect():
    from vibe import api

    binary = request.args.get("binary", "")
    return jsonify(api.detect_cli(binary))


@app.route("/slack/manifest")
def slack_manifest():
    from vibe import api

    return jsonify(api.get_slack_manifest())


@app.route("/version")
def version():
    from vibe import api

    return jsonify(api.get_version_info())


# =============================================================================
# POST Endpoints
# =============================================================================


@app.route("/control", methods=["POST"])
def control():
    from vibe import runtime
    from vibe.cli import _stop_opencode_server

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
        runtime.stop_service()
        _stop_opencode_server()
        runtime.write_status("stopped", "stopped", None, status.get("ui_pid"))
    elif action == "restart":
        import time

        runtime.write_status("restarting", "restarting", status.get("service_pid"), status.get("ui_pid"))
        runtime.stop_service()
        _stop_opencode_server()
        time.sleep(3)
        runtime.ensure_config()
        service_pid = runtime.start_service()
        runtime.write_status("running", "restarted", service_pid, status.get("ui_pid"))
    return jsonify({"ok": True, "action": action, "status": runtime.read_status()})


@app.route("/config", methods=["POST"])
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


@app.route("/remote-access/status", methods=["GET"])
def remote_access_status():
    from vibe import remote_access

    return jsonify(remote_access.status())


@app.route("/remote-access/vibe-cloud/pair", methods=["POST"])
def remote_access_vibe_cloud_pair():
    from vibe import remote_access

    payload = request.json or {}
    result = remote_access.pair(
        payload.get("pairing_key", ""),
        payload.get("backend_url", "https://avibe.bot"),
        payload.get("device_name", "Vibe Remote"),
    )
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/remote-access/start", methods=["POST"])
def remote_access_start():
    from vibe import remote_access

    result = remote_access.start()
    return jsonify(result), 200 if result.get("ok") else 400


@app.route("/remote-access/stop", methods=["POST"])
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


@app.route("/ui/reload", methods=["POST"])
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
        current_config = V2Config.load()
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


@app.route("/settings", methods=["POST"])
def settings_post():
    from vibe import api

    payload = request.json or {}
    return jsonify(api.save_settings(payload))


@app.route("/slack/auth_test", methods=["POST"])
def slack_auth_test():
    from vibe import api

    payload = request.json or {}
    result = api.slack_auth_test(
        payload.get("bot_token", ""),
        proxy_url=payload.get("proxy_url"),
    )
    return jsonify(result)


@app.route("/slack/channels", methods=["POST"])
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


@app.route("/discord/auth_test", methods=["POST"])
async def discord_auth_test():
    from vibe import api

    payload = request.json or {}
    result = await api.discord_auth_test_async(
        payload.get("bot_token", ""),
        proxy_url=payload.get("proxy_url"),
    )
    return jsonify(result)


@app.route("/discord/guilds", methods=["POST"])
async def discord_guilds():
    from vibe import api

    payload = request.json or {}
    return jsonify(await api.discord_list_guilds_async(payload.get("bot_token", "")))


@app.route("/discord/channels", methods=["POST"])
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


@app.route("/telegram/auth_test", methods=["POST"])
async def telegram_auth_test():
    from vibe import api

    payload = request.json or {}
    result = await api.telegram_auth_test_async(
        payload.get("bot_token", ""),
        proxy_url=payload.get("proxy_url")
    )
    return jsonify(result)


@app.route("/telegram/chats", methods=["POST"])
def telegram_chats():
    from vibe import api

    payload = request.json or {}
    return jsonify(api.telegram_list_chats(include_private=payload.get("include_private", False)))


@app.route("/lark/auth_test", methods=["POST"])
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


@app.route("/lark/chats", methods=["POST"])
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


@app.route("/lark/temp_ws/start", methods=["POST"])
def lark_temp_ws_start():
    from vibe import api

    payload = request.json or {}
    return jsonify(
        api.lark_temp_ws_start(
            payload.get("app_id", ""), payload.get("app_secret", ""), payload.get("domain", "feishu")
        )
    )


@app.route("/lark/temp_ws/stop", methods=["POST"])
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


@app.route("/wechat/qr_login/start", methods=["POST"])
async def wechat_qr_login_start():
    """Start WeChat QR code login flow."""
    auth = _get_wechat_auth()
    payload = request.json or {}
    base_url = payload.get("base_url", "https://ilinkai.weixin.qq.com")

    result = await auth.start_login(base_url=base_url)
    if result.get("ok") is False:
        return jsonify(result), 500
    return jsonify(result)


@app.route("/wechat/qr_login/poll", methods=["POST"])
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

        # Schedule service restart so the new token takes effect
        def _restart_after_login():
            import time

            time.sleep(2)  # let the response go out first
            try:
                from vibe import runtime

                runtime.stop_service()
                time.sleep(1)
                runtime.ensure_config()
                service_pid = runtime.start_service()
                st = runtime.read_status()
                runtime.write_status("running", "restarted", service_pid, st.get("ui_pid"))
                logger.info("Service restarted after WeChat QR login")
            except Exception as exc:
                logger.warning("Failed to restart service after QR login: %s", exc)

        threading.Thread(target=_restart_after_login, daemon=True).start()

    return jsonify(result)


@app.route("/doctor", methods=["POST"])
def doctor_post():
    from vibe.cli import _doctor

    result = _doctor()
    return jsonify(result)


@app.route("/logs", methods=["POST"])
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


@app.route("/opencode/options", methods=["POST"])
async def opencode_options():
    from vibe import api

    payload = request.json or {}
    result = await api.opencode_options_async(payload.get("cwd", "."))
    return jsonify(result)


@app.route("/upgrade", methods=["POST"])
def upgrade():
    from vibe import api

    result = api.do_upgrade()
    return jsonify(result)


@app.route("/opencode/setup-permission", methods=["POST"])
def opencode_setup_permission():
    from vibe import api

    return jsonify(api.setup_opencode_permission())


@app.route("/claude/agents", methods=["GET"])
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


@app.route("/codex/agents", methods=["GET"])
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


@app.route("/claude/models", methods=["GET"])
def claude_models():
    from vibe import api

    return jsonify(api.claude_models())


@app.route("/codex/models", methods=["GET"])
def codex_models():
    from vibe import api

    return jsonify(api.codex_models())


@app.route("/agent/<name>/install", methods=["POST"])
def agent_install(name):
    """Install an agent CLI tool (opencode, claude, codex)."""
    if name not in _ALLOWED_BACKENDS:
        return jsonify({"ok": False, "message": f"Unknown agent: {name}"}), 400

    from vibe import api

    result = api.install_agent(name)
    return jsonify(result)


_ALLOWED_BACKENDS = set(AGENT_BACKENDS)


@app.route("/backend/<name>/runtime")
def backend_runtime(name):
    """Return lifecycle info (version, update, process status) for a backend."""
    if name not in _ALLOWED_BACKENDS:
        return jsonify({"ok": False, "error": f"Unknown backend: {name}"}), 400

    from vibe import api

    return jsonify(api.get_backend_runtime(name))


@app.route("/backend/<name>/restart", methods=["POST"])
def backend_restart(name):
    """Refresh a backend's runtime state after settings change."""
    if not supports_runtime_refresh(name):
        return jsonify({"ok": False, "message": f"Restart is not supported for backend: {name}"}), 400

    from vibe import api

    return jsonify(api.restart_backend(name))


@app.route("/backend/codex/auth", methods=["GET"])
def backend_codex_auth_get():
    """Read the user-facing Codex auth state (masked secrets)."""
    from vibe import api

    return jsonify(api.get_codex_auth())


@app.route("/backend/codex/auth", methods=["POST"])
def backend_codex_auth_post():
    """Persist Codex auth and reload the app-server.

    Body: ``{auth_mode: 'oauth'|'api_key', api_key?: string, base_url?: string}``.
    """
    from vibe import api

    payload = request.json or {}
    return jsonify(api.save_codex_auth(payload))


@app.route("/backend/claude/auth", methods=["GET"])
def backend_claude_auth_get():
    """Read the user-facing Claude auth state (masked secrets)."""
    from vibe import api

    return jsonify(api.get_claude_auth())


@app.route("/backend/claude/auth", methods=["POST"])
def backend_claude_auth_post():
    """Persist Claude auth into V2Config.

    Body: ``{auth_mode: 'oauth'|'api_key', api_key?: string, base_url?: string}``.
    Claude relaunches per request, so no daemon restart is necessary —
    the next user message picks up the new env injection automatically.
    """
    from vibe import api

    payload = request.json or {}
    return jsonify(api.save_claude_auth(payload))


@app.route("/backend/<backend>/auth/oauth/start", methods=["POST"])
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


@app.route("/backend/<backend>/auth/oauth/status/<flow_id>", methods=["GET"])
def backend_oauth_web_status(backend: str, flow_id: str):
    """Poll an in-flight Settings OAuth flow."""
    from vibe import api

    _ = backend  # backend is encoded in the flow itself; path arg kept for symmetry
    return jsonify(api.get_oauth_web_status(flow_id))


@app.route("/backend/<backend>/auth/oauth/submit-code", methods=["POST"])
async def backend_oauth_web_submit_code(backend: str):
    """Submit the Claude OAuth callback code (Codex device-auth ignores this)."""
    from vibe import api

    _ = backend
    payload = request.json or {}
    flow_id = str(payload.get("flow_id") or "").strip()
    code = str(payload.get("code") or "")
    return jsonify(await api.submit_oauth_web_code_async(flow_id, code))


@app.route("/backend/<backend>/auth/oauth/cancel", methods=["POST"])
async def backend_oauth_web_cancel(backend: str):
    """Cancel an in-flight Settings OAuth flow."""
    from vibe import api

    _ = backend
    payload = request.json or {}
    flow_id = str(payload.get("flow_id") or "").strip()
    return jsonify(await api.cancel_oauth_web_async(flow_id))


@app.route("/backend/<backend>/auth/oauth/remove", methods=["POST"])
async def backend_oauth_web_remove(backend: str):
    """Clear stored credentials for a Claude/Codex backend."""
    from vibe import api

    return jsonify(await api.remove_backend_auth_async(backend))


@app.route("/backend/<backend>/auth/api-key/remove", methods=["POST"])
def backend_auth_api_key_remove(backend: str):
    """Clear the stored API key (V2Config + Codex auth.json) without
    touching OAuth credentials. Per-backend symmetry of OpenCode's
    per-provider DELETE."""
    from vibe import api

    return jsonify(api.remove_backend_api_key(backend))


@app.route("/backend/<backend>/auth/test", methods=["POST"])
async def backend_auth_test(backend: str):
    """Send a single-token probe through the backend CLI to verify auth."""
    from vibe import api

    payload = request.json or {}
    raw_model = payload.get("model")
    model = raw_model.strip() if isinstance(raw_model, str) and raw_model.strip() else None
    return jsonify(await api.test_backend_auth_async(backend, model=model))


@app.route("/backend/opencode/providers", methods=["GET"])
async def backend_opencode_providers():
    """Return the merged OpenCode provider catalog for the Settings UI.

    Fans out to the live OpenCode daemon's ``/provider``, ``/provider/auth``,
    and ``/config/providers`` endpoints and merges them into a list of
    ``{id, name, configured, oauth_available, local, models, default_model}``.
    """
    from vibe import api

    return jsonify(await api.get_opencode_providers_async())


@app.route(
    "/backend/opencode/provider/<provider_id>/auth/oauth/start",
    methods=["POST"],
)
async def backend_opencode_provider_oauth_start(provider_id: str):
    """Kick off a Settings → Backends OAuth flow for a single OpenCode provider.

    Body: ``{force_reset?: bool}``. Returns ``{flow_id, state, url?,
    device_code?}``. The status/cancel endpoints are the same generic
    ``/backend/opencode/auth/oauth/status/<flow_id>`` etc.
    """
    from vibe import api

    payload = request.json or {}
    force_reset = bool(payload.get("force_reset", True))
    return jsonify(await api.start_oauth_web_async("opencode", force_reset=force_reset, provider_id=provider_id))


@app.route("/backend/opencode/provider/<provider_id>/auth", methods=["POST"])
async def backend_opencode_provider_auth_post(provider_id: str):
    """Persist an API key for a single OpenCode provider.

    Body: ``{api_key: string}``. The key is forwarded to OpenCode via
    its ``PUT /auth/<id>`` endpoint.
    """
    from vibe import api

    payload = request.json or {}
    return jsonify(await api.save_opencode_provider_auth_async(provider_id, payload))


@app.route("/backend/opencode/provider/<provider_id>/auth", methods=["DELETE"])
async def backend_opencode_provider_auth_delete(provider_id: str):
    """Drop the stored API key for a single OpenCode provider."""
    from vibe import api

    return jsonify(await api.delete_opencode_provider_auth_async(provider_id))


@app.route("/backend/opencode/provider/<provider_id>/test", methods=["POST"])
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


@app.route("/backend/opencode/default-provider", methods=["POST"])
def backend_opencode_default_provider():
    """Persist the user's default OpenCode provider into V2Config.

    Body: ``{provider_id: string}``. No daemon contact — the default
    is consulted at session-routing time, not by OpenCode itself.
    """
    from vibe import api

    payload = request.json or {}
    return jsonify(api.set_opencode_default_provider(payload))


@app.route("/browse", methods=["POST"])
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
                from config.v2_settings import SettingsStore, ChannelSettings, normalize_show_message_types
                from vibe.api import _parse_routing
                from vibe.api import _current_platform

                settings_key = modal_values.get("settings_key") or modal_values.get("channel_id")
                if not settings_key:
                    return jsonify({"ok": False, "error": "settings_key or channel_id required in modal_values"}), 400

                store = SettingsStore.get_instance()
                store.maybe_reload()
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

                store = SettingsStore.get_instance()
                store.maybe_reload()
                from vibe.api import _current_platform

                platform = _current_platform()
                ch = store.find_channel(channel_id, platform=platform)
                if ch:
                    from config.v2_settings import RoutingSettings

                    ch.routing = RoutingSettings(
                        agent_backend=modal_values.get("backend", "opencode"),
                        opencode_agent=modal_values.get("opencode_agent"),
                        opencode_model=modal_values.get("opencode_model"),
                        opencode_reasoning_effort=modal_values.get("opencode_reasoning_effort"),
                        claude_agent=modal_values.get("claude_agent"),
                        claude_model=modal_values.get("claude_model"),
                        claude_reasoning_effort=modal_values.get("claude_reasoning_effort"),
                        codex_agent=modal_values.get("codex_agent"),
                        codex_model=modal_values.get("codex_model"),
                        codex_reasoning_effort=modal_values.get("codex_reasoning_effort"),
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

                store = SettingsStore.get_instance()
                store.maybe_reload()
                from vibe.api import _current_platform

                platform = _current_platform()
                ch = store.find_channel(channel_id, platform=platform)
                if ch:
                    from config.v2_settings import RoutingSettings

                    ch.routing = RoutingSettings(
                        agent_backend=modal_values.get("backend", "opencode"),
                        opencode_agent=modal_values.get("opencode_agent"),
                        opencode_model=modal_values.get("opencode_model"),
                        opencode_reasoning_effort=modal_values.get("opencode_reasoning_effort"),
                        claude_agent=modal_values.get("claude_agent"),
                        claude_model=modal_values.get("claude_model"),
                        claude_reasoning_effort=modal_values.get("claude_reasoning_effort"),
                        codex_agent=modal_values.get("codex_agent"),
                        codex_model=modal_values.get("codex_model"),
                        codex_reasoning_effort=modal_values.get("codex_reasoning_effort"),
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
        config = V2Config.load()
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
