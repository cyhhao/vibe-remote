from __future__ import annotations

import ipaddress
import socket
from collections import namedtuple

from config.v2_config import AgentsConfig, PlatformsConfig, RemoteAccessConfig, RuntimeConfig, SlackConfig, UiConfig, V2Config
from config.v2_config import CONFIG_LOCK
from tests.ui_server_test_helpers import csrf_headers
from vibe import api
from vibe import remote_access
from vibe import ui_server
from vibe.ui_server import app


_FakeSnicaddr = namedtuple("snicaddr", ["family", "address", "netmask", "broadcast", "ptp"])


def _mock_interface(monkeypatch, ip: str, prefix: int, name: str = "en0") -> None:
    """Make ``psutil.net_if_addrs()`` report ``ip`` with the given prefix
    length so ``_local_interface_network`` returns the expected subnet.
    Tests that exercise the RFC1918/ULA trust path need this because the
    real test runner does not have the synthetic addresses (192.168.2.3
    etc.) configured on any interface."""
    address = ipaddress.ip_address(ip)
    if address.version == 4:
        family = socket.AF_INET
        netmask = str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
    else:
        family = socket.AF_INET6
        netmask = str(ipaddress.IPv6Network(f"::/{prefix}").netmask)
    snic = _FakeSnicaddr(family=family, address=ip, netmask=netmask, broadcast=None, ptp=None)
    monkeypatch.setattr("vibe.ui_server.psutil.net_if_addrs", lambda: {name: [snic]})


def _mock_no_interfaces(monkeypatch) -> None:
    monkeypatch.setattr("vibe.ui_server.psutil.net_if_addrs", lambda: {})


def _save_config(tmp_path) -> V2Config:
    config = V2Config(
        mode="self_host",
        version="v2",
        platform="slack",
        platforms=PlatformsConfig(enabled=["slack"], primary="slack"),
        slack=SlackConfig(bot_token=""),
        runtime=RuntimeConfig(default_cwd="."),
        agents=AgentsConfig(),
        ui=UiConfig(),
        remote_access=RemoteAccessConfig(),
    )
    cloud = config.remote_access.vibe_cloud
    cloud.enabled = True
    cloud.public_url = "https://alex.avibe.bot"
    cloud.client_id = "vr_client_123"
    cloud.instance_id = "inst_123"
    cloud.session_secret = "session-secret"
    cloud.authorization_endpoint = "https://backend.test/oauth/authorize"
    cloud.redirect_uri = "https://alex.avibe.bot/auth/callback"
    config.save()
    return config


def _remote_peer() -> dict[str, str]:
    return {"REMOTE_ADDR": "203.0.113.10"}


def _cloudflare_headers() -> dict[str, str]:
    return {"CF-Connecting-IP": "198.51.100.10", "CF-Ray": "test-ray"}


def test_remote_host_redirects_to_vibe_cloud_login(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get(
        "/dashboard",
        base_url="https://alex.avibe.bot",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://backend.test/oauth/authorize?")


def test_remote_host_with_explicit_port_still_requires_login(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get("/dashboard", base_url="https://alex.avibe.bot:443", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://backend.test/oauth/authorize?")


def test_remote_host_with_trailing_dot_still_requires_login(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get("/dashboard", base_url="https://alex.avibe.bot.", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://backend.test/oauth/authorize?")


def test_localhost_does_not_require_remote_access_cookie(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get("/health", base_url="http://127.0.0.1:5123")

    assert response.status_code == 200


def test_docker_loopback_host_requires_explicit_trust(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    monkeypatch.delenv("VIBE_REMOTE_ALLOW_DOCKER_LOOPBACK_PEERS", raising=False)
    _save_config(tmp_path)

    response = app.test_client().get(
        "/health",
        base_url="http://127.0.0.1:15130",
        environ_base={"REMOTE_ADDR": "172.17.0.1"},
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_docker_loopback_health_probe_is_allowed_when_explicitly_trusted(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    monkeypatch.setenv("VIBE_REMOTE_ALLOW_DOCKER_LOOPBACK_PEERS", "1")
    monkeypatch.setenv("VIBE_REMOTE_DOCKER_LOOPBACK_BIND_HOST", "127.0.0.1")
    _save_config(tmp_path)

    response = app.test_client().get(
        "/health",
        base_url="http://127.0.0.1:15130",
        environ_base={"REMOTE_ADDR": "172.17.0.1"},
    )

    assert response.status_code == 200


def test_docker_loopback_status_probe_is_allowed_when_explicitly_trusted(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    monkeypatch.setenv("VIBE_REMOTE_ALLOW_DOCKER_LOOPBACK_PEERS", "1")
    monkeypatch.setenv("VIBE_REMOTE_DOCKER_LOOPBACK_BIND_HOST", "127.0.0.1")
    _save_config(tmp_path)

    response = app.test_client().get(
        "/status",
        base_url="http://127.0.0.1:15130",
        environ_base={"REMOTE_ADDR": "172.17.0.1"},
    )

    assert response.status_code == 200


def test_docker_loopback_probe_accepts_ipv4_mapped_peer(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    monkeypatch.setenv("VIBE_REMOTE_ALLOW_DOCKER_LOOPBACK_PEERS", "1")
    monkeypatch.setenv("VIBE_REMOTE_DOCKER_LOOPBACK_BIND_HOST", "127.0.0.1")
    _save_config(tmp_path)

    response = app.test_client().get(
        "/health",
        base_url="http://127.0.0.1:15130",
        environ_base={"REMOTE_ADDR": "::ffff:172.17.0.1"},
    )

    assert response.status_code == 200


def test_docker_loopback_trust_does_not_bypass_ui_auth(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    monkeypatch.setenv("VIBE_REMOTE_ALLOW_DOCKER_LOOPBACK_PEERS", "1")
    monkeypatch.setenv("VIBE_REMOTE_DOCKER_LOOPBACK_BIND_HOST", "127.0.0.1")
    _save_config(tmp_path)

    response = app.test_client().get(
        "/dashboard",
        base_url="http://127.0.0.1:15130",
        environ_base={"REMOTE_ADDR": "172.17.0.1"},
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_docker_loopback_trust_requires_loopback_port_binding(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    monkeypatch.setenv("VIBE_REMOTE_ALLOW_DOCKER_LOOPBACK_PEERS", "1")
    monkeypatch.setenv("VIBE_REMOTE_DOCKER_LOOPBACK_BIND_HOST", "0.0.0.0")
    _save_config(tmp_path)

    response = app.test_client().get(
        "/health",
        base_url="http://127.0.0.1:15130",
        environ_base={"REMOTE_ADDR": "172.17.0.1"},
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_docker_loopback_trust_still_rejects_non_local_host(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    monkeypatch.setenv("VIBE_REMOTE_ALLOW_DOCKER_LOOPBACK_PEERS", "1")
    monkeypatch.setenv("VIBE_REMOTE_DOCKER_LOOPBACK_BIND_HOST", "127.0.0.1")
    _save_config(tmp_path)

    response = app.test_client().get(
        "/health",
        base_url="https://old-alex.avibe.bot",
        environ_base={"REMOTE_ADDR": "172.17.0.1"},
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_docker_loopback_trust_rejects_untrusted_peer(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    monkeypatch.setenv("VIBE_REMOTE_ALLOW_DOCKER_LOOPBACK_PEERS", "1")
    monkeypatch.setenv("VIBE_REMOTE_DOCKER_LOOPBACK_BIND_HOST", "127.0.0.1")
    _save_config(tmp_path)

    response = app.test_client().get(
        "/health",
        base_url="http://127.0.0.1:15130",
        environ_base={"REMOTE_ADDR": "8.8.8.8"},
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_docker_loopback_trust_supports_configured_peer_cidrs(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    monkeypatch.setenv("VIBE_REMOTE_ALLOW_DOCKER_LOOPBACK_PEERS", "1")
    monkeypatch.setenv("VIBE_REMOTE_DOCKER_LOOPBACK_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("VIBE_REMOTE_DOCKER_LOOPBACK_PEER_CIDRS", "100.64.0.0/10")
    _save_config(tmp_path)

    response = app.test_client().get(
        "/health",
        base_url="http://127.0.0.1:15130",
        environ_base={"REMOTE_ADDR": "100.97.103.112"},
    )

    assert response.status_code == 200


def test_unmatched_non_local_host_fails_closed_when_remote_access_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get(
        "/dashboard",
        base_url="https://old-alex.avibe.bot",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_loopback_proxy_with_public_host_mismatch_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get(
        "/dashboard",
        base_url="https://old-alex.avibe.bot",
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_remote_host_allows_valid_remote_session(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    client = app.test_client()
    client.set_cookie(remote_access.SESSION_COOKIE_NAME, remote_access.make_session_cookie(config, "alex@example.com", "user-1"), domain="alex.avibe.bot")

    response = client.get("/dashboard", base_url="https://alex.avibe.bot", follow_redirects=False)

    assert response.status_code != 302


def _forged_session_cookie(config: V2Config, exp: int, *, email: str = "alex@example.com", subject: str = "user-1") -> str:
    import json
    import urllib.parse

    cloud = config.remote_access.vibe_cloud
    payload = {
        "email": email,
        "sub": subject,
        "instance_id": cloud.instance_id,
        "iat": exp - remote_access.SESSION_TTL_SECONDS,
        "exp": exp,
    }
    payload_text = urllib.parse.quote(json.dumps(payload, separators=(",", ":")), safe="")
    signature = remote_access._session_signature(cloud.session_secret, payload_text)
    return f"{payload_text}.{signature}"


def test_remote_host_does_not_renew_fresh_cookie(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    client = app.test_client()
    client.set_cookie(
        remote_access.SESSION_COOKIE_NAME,
        remote_access.make_session_cookie(config, "alex@example.com", "user-1"),
        domain="alex.avibe.bot",
    )

    response = client.get("/dashboard", base_url="https://alex.avibe.bot", follow_redirects=False)

    set_cookie_headers = response.headers.getlist("Set-Cookie")
    assert not any(h.startswith(f"{remote_access.SESSION_COOKIE_NAME}=") for h in set_cookie_headers)


def test_remote_host_renews_cookie_past_half_ttl(monkeypatch, tmp_path):
    import time as _time

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    near_exp = int(_time.time()) + (remote_access.SESSION_TTL_SECONDS // 2) - 60
    cookie = _forged_session_cookie(config, near_exp)
    client = app.test_client()
    client.set_cookie(remote_access.SESSION_COOKIE_NAME, cookie, domain="alex.avibe.bot")

    response = client.get("/dashboard", base_url="https://alex.avibe.bot", follow_redirects=False)

    refreshed = next(
        (h for h in response.headers.getlist("Set-Cookie") if h.startswith(f"{remote_access.SESSION_COOKIE_NAME}=")),
        None,
    )
    assert refreshed is not None
    assert "HttpOnly" in refreshed
    assert "Secure" in refreshed
    new_value = refreshed.split(";", 1)[0].split("=", 1)[1]
    assert new_value != cookie
    payload = remote_access.parse_session_cookie(config, new_value)
    assert payload is not None
    assert payload["email"] == "alex@example.com"
    assert payload["sub"] == "user-1"
    assert payload["exp"] > near_exp


def test_remote_host_does_not_renew_cookie_on_rejected_post(monkeypatch, tmp_path):
    """A near-expiry cookie must NOT be slid by a request that later fails
    a guard like CSRF/origin. Otherwise repeated rejected mutations could
    keep a stolen session alive indefinitely."""
    import time as _time

    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    near_exp = int(_time.time()) + (remote_access.SESSION_TTL_SECONDS // 2) - 60
    cookie = _forged_session_cookie(config, near_exp)
    client = app.test_client()
    client.set_cookie(remote_access.SESSION_COOKIE_NAME, cookie, domain="alex.avibe.bot")

    # POST /config without CSRF/origin headers — protect_mutating_ui_requests
    # will reject this with 403 inside the same request lifecycle that already
    # set g.remote_session_renew in enforce_remote_access_cookie.
    response = client.post(
        "/config",
        json={"remote_access": {"vibe_cloud": {"enabled": False}}},
        base_url="https://alex.avibe.bot",
    )

    assert response.status_code == 403
    refreshed = next(
        (h for h in response.headers.getlist("Set-Cookie") if h.startswith(f"{remote_access.SESSION_COOKIE_NAME}=")),
        None,
    )
    assert refreshed is None


def test_remote_host_fails_closed_when_config_load_fails(monkeypatch):
    def fail_load():
        raise ValueError("corrupt config")

    monkeypatch.setattr(ui_server.V2Config, "load", fail_load)

    response = app.test_client().get(
        "/dashboard",
        base_url="https://alex.avibe.bot",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_config_unavailable"


def test_host_starting_with_127_but_not_ip_is_not_local_when_config_load_fails(monkeypatch):
    def fail_load():
        raise ValueError("corrupt config")

    monkeypatch.setattr(ui_server.V2Config, "load", fail_load)

    response = app.test_client().get(
        "/dashboard",
        base_url="https://127.attacker.example",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_config_unavailable"


def test_spoofed_loopback_host_is_not_local_when_peer_is_remote(monkeypatch):
    def fail_load():
        raise ValueError("corrupt config")

    monkeypatch.setattr(ui_server.V2Config, "load", fail_load)

    response = app.test_client().get(
        "/dashboard",
        base_url="https://127.0.0.1",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_config_unavailable"


def test_cloudflare_forwarded_request_with_loopback_host_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get(
        "/dashboard",
        base_url="https://127.0.0.1",
        headers=_cloudflare_headers(),
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_remote_host_fails_closed_when_disabled_but_hostname_still_matches(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.enabled = False
    config.save()

    response = app.test_client().get(
        "/dashboard",
        base_url="https://alex.avibe.bot",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_disabled"


def test_unmatched_non_local_host_fails_closed_when_remote_access_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.enabled = False
    config.save()

    response = app.test_client().get(
        "/dashboard",
        base_url="https://old-alex.avibe.bot",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_remote_host_fails_closed_when_public_url_is_invalid(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.public_url = "alex.avibe.bot"
    config.save()

    response = app.test_client().get(
        "/dashboard",
        base_url="https://alex.avibe.bot",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_public_url_invalid"


def test_remote_host_fails_closed_when_public_url_is_http(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.public_url = "http://alex.avibe.bot"
    config.save()

    response = app.test_client().get(
        "/dashboard",
        base_url="https://alex.avibe.bot",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_public_url_invalid"


def test_remote_host_fails_closed_when_public_url_contains_userinfo(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.public_url = "https://user:pass@alex.avibe.bot"
    config.save()

    response = app.test_client().get(
        "/dashboard",
        base_url="https://alex.avibe.bot",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_public_url_invalid"


def test_remote_host_fails_closed_when_public_url_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.public_url = ""
    config.save()

    response = app.test_client().get(
        "/dashboard",
        base_url="https://alex.avibe.bot",
        environ_base=_remote_peer(),
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_public_url_invalid"


def test_remote_host_fails_closed_when_session_secret_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.session_secret = ""
    config.save()

    response = app.test_client().get("/dashboard", base_url="https://alex.avibe.bot", follow_redirects=False)

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_session_secret_missing"


def test_config_post_rotates_session_secret_when_remote_access_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    old_secret = config.remote_access.vibe_cloud.session_secret
    client = app.test_client()

    monkeypatch.setattr(remote_access, "reconcile", lambda: {"ok": True, "stopped": True})

    response = client.post(
        "/config",
        json={"remote_access": {"vibe_cloud": {"enabled": False}}},
        headers=csrf_headers(client, "http://127.0.0.1:5123"),
        base_url="http://127.0.0.1:5123",
    )
    saved = V2Config.load()

    assert response.status_code == 200
    assert saved.remote_access.vibe_cloud.enabled is False
    assert saved.remote_access.vibe_cloud.session_secret
    assert saved.remote_access.vibe_cloud.session_secret != old_secret


def test_config_post_skips_reconcile_when_remote_access_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    client = app.test_client()
    reconcile_calls = []

    monkeypatch.setattr(remote_access, "reconcile", lambda: reconcile_calls.append(True) or {"ok": True})

    response = client.post(
        "/config",
        json=api.config_to_payload(config),
        headers=csrf_headers(client, "http://127.0.0.1:5123"),
        base_url="http://127.0.0.1:5123",
    )

    assert response.status_code == 200
    assert reconcile_calls == []


def test_config_post_returns_saved_config_when_remote_reconcile_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    old_secret = config.remote_access.vibe_cloud.session_secret
    client = app.test_client()

    monkeypatch.setattr(remote_access, "reconcile", lambda: {"ok": False, "error": "cloudflared_stop_failed"})

    response = client.post(
        "/config",
        json={"remote_access": {"vibe_cloud": {"enabled": False}}},
        headers=csrf_headers(client, "http://127.0.0.1:5123"),
        base_url="http://127.0.0.1:5123",
    )
    saved = V2Config.load()
    body = response.get_json()

    assert response.status_code == 200
    assert body["remote_access_runtime"]["ok"] is False
    assert body["remote_access_runtime"]["error"] == "cloudflared_stop_failed"
    assert saved.remote_access.vibe_cloud.enabled is False
    assert saved.remote_access.vibe_cloud.session_secret != old_secret


def test_config_post_reconciles_after_releasing_config_lock(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    client = app.test_client()
    lock_states = []

    def reconcile():
        lock_states.append(CONFIG_LOCK._is_owned())
        return {"ok": True, "stopped": True}

    monkeypatch.setattr(remote_access, "reconcile", reconcile)

    response = client.post(
        "/config",
        json={"remote_access": {"vibe_cloud": {"enabled": False}}},
        headers=csrf_headers(client, "http://127.0.0.1:5123"),
        base_url="http://127.0.0.1:5123",
    )

    assert response.status_code == 200
    assert lock_states == [False]


def test_config_post_reconciles_from_fresh_config(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)
    client = app.test_client()
    reconcile_args = []

    def reconcile(*args):
        reconcile_args.append(args)
        return {"ok": True, "stopped": True}

    monkeypatch.setattr(remote_access, "reconcile", reconcile)

    response = client.post(
        "/config",
        json={"remote_access": {"vibe_cloud": {"enabled": False}}},
        headers=csrf_headers(client, "http://127.0.0.1:5123"),
        base_url="http://127.0.0.1:5123",
    )

    assert response.status_code == 200
    assert reconcile_args == [()]


def test_remote_callback_rejects_nonce_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    client = app.test_client()

    with app.test_request_context("/dashboard", base_url="https://alex.avibe.bot"):
        redirect = ui_server._redirect_to_vibe_cloud_login(config)
    oauth_cookie = redirect.headers["Set-Cookie"].split(";", 1)[0].split("=", 1)[1]
    client.set_cookie(ui_server.REMOTE_OAUTH_COOKIE_NAME, oauth_cookie, domain="alex.avibe.bot")

    monkeypatch.setattr(
        remote_access,
        "exchange_oauth_code",
        lambda cfg, code, verifier: {
            "claims": {
                "email": "alex@example.com",
                "sub": "user-1",
                "nonce": "wrong-nonce",
            }
        },
    )

    state = ui_server._read_oauth_cookie(config.remote_access.vibe_cloud.session_secret, oauth_cookie)["state"]
    response = client.get(f"/auth/callback?code=test-code&state={state}", base_url="https://alex.avibe.bot")

    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_oauth_nonce"


def test_remote_callback_rejects_when_remote_access_is_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    client = app.test_client()
    oauth_cookie = ui_server._make_oauth_cookie(
        config.remote_access.vibe_cloud.session_secret,
        {
            "state": "state-1",
            "nonce": "nonce-1",
            "code_verifier": "verifier-1",
            "next": "/dashboard",
            "exp": int(ui_server.datetime.now().timestamp()) + 300,
        },
    )
    config.remote_access.vibe_cloud.enabled = False
    config.save()
    exchange_calls = []
    client.set_cookie(ui_server.REMOTE_OAUTH_COOKIE_NAME, oauth_cookie, domain="alex.avibe.bot")

    monkeypatch.setattr(
        remote_access,
        "exchange_oauth_code",
        lambda *args, **kwargs: exchange_calls.append(args) or {"claims": {"nonce": "nonce-1"}},
    )

    response = client.get("/auth/callback?code=test-code&state=state-1", base_url="https://alex.avibe.bot")

    assert response.status_code == 400
    assert response.get_json()["error"] == "remote_access_disabled"
    assert exchange_calls == []


def test_remote_callback_accepts_html_escaped_state_separator(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    client = app.test_client()
    oauth_cookie = ui_server._make_oauth_cookie(
        config.remote_access.vibe_cloud.session_secret,
        {
            "state": "state-1",
            "nonce": "nonce-1",
            "code_verifier": "verifier-1",
            "next": "/dashboard",
            "exp": int(ui_server.datetime.now().timestamp()) + 300,
        },
    )
    exchange_calls = []
    client.set_cookie(ui_server.REMOTE_OAUTH_COOKIE_NAME, oauth_cookie, domain="alex.avibe.bot")

    def exchange(cfg, code, verifier):
        exchange_calls.append((code, verifier))
        return {
            "claims": {
                "email": "alex@example.com",
                "sub": "user-1",
                "nonce": "nonce-1",
            }
        }

    monkeypatch.setattr(remote_access, "exchange_oauth_code", exchange)

    response = client.get("/auth/callback?code=test-code&amp;state=state-1", base_url="https://alex.avibe.bot")

    assert response.status_code == 302
    assert response.headers["Location"] == "/dashboard"
    assert exchange_calls == [("test-code", "verifier-1")]


def test_remote_callback_sanitizes_protocol_relative_next(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    config = _save_config(tmp_path)
    client = app.test_client()
    oauth_cookie = ui_server._make_oauth_cookie(
        config.remote_access.vibe_cloud.session_secret,
        {
            "state": "state-1",
            "nonce": "nonce-1",
            "code_verifier": "verifier-1",
            "next": "//attacker.example",
            "exp": int(ui_server.datetime.now().timestamp()) + 300,
        },
    )
    client.set_cookie(ui_server.REMOTE_OAUTH_COOKIE_NAME, oauth_cookie, domain="alex.avibe.bot")

    monkeypatch.setattr(
        remote_access,
        "exchange_oauth_code",
        lambda cfg, code, verifier: {
            "claims": {
                "email": "alex@example.com",
                "sub": "user-1",
                "nonce": "nonce-1",
            }
        },
    )

    response = client.get("/auth/callback?code=test-code&state=state-1", base_url="https://alex.avibe.bot")

    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def _save_config_with_setup_host(tmp_path, host: str) -> V2Config:
    config = _save_config(tmp_path)
    config.ui.setup_host = host
    config.save()
    return config


def test_setup_host_lan_request_is_treated_as_local(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "192.168.2.3")
    _mock_interface(monkeypatch, "192.168.2.3", 24)

    response = app.test_client().get(
        "/health",
        base_url="http://192.168.2.3:5123",
        environ_base={"REMOTE_ADDR": "192.168.2.5"},
    )

    assert response.status_code == 200


def test_setup_host_request_from_self_is_treated_as_local(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "192.168.2.3")
    _mock_interface(monkeypatch, "192.168.2.3", 24)

    response = app.test_client().get(
        "/health",
        base_url="http://192.168.2.3:5123",
        environ_base={"REMOTE_ADDR": "192.168.2.3"},
    )

    assert response.status_code == 200


def test_setup_host_with_public_peer_is_not_local(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "192.168.2.3")

    response = app.test_client().get(
        "/dashboard",
        base_url="http://192.168.2.3:5123",
        environ_base={"REMOTE_ADDR": "8.8.8.8"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_lan_peer_with_tailscale_setup_is_not_local(monkeypatch, tmp_path):
    """Wildcard-bind regression guard: a LAN peer cannot inherit setup-host
    trust by spoofing the Host header to a Tailscale setup_host that lives
    in a different private block."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "100.97.103.112")

    response = app.test_client().get(
        "/dashboard",
        base_url="http://100.97.103.112:5123",
        environ_base={"REMOTE_ADDR": "192.168.1.5"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_tailscale_peer_with_lan_setup_is_not_local(monkeypatch, tmp_path):
    """Inverse of the LAN-vs-Tailscale check: a Tailscale peer cannot inherit
    setup-host trust by spoofing the Host header to a LAN setup_host."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "192.168.2.3")

    response = app.test_client().get(
        "/dashboard",
        base_url="http://192.168.2.3:5123",
        environ_base={"REMOTE_ADDR": "100.97.103.5"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_tailscale_peer_with_tailscale_setup_is_local(monkeypatch, tmp_path):
    """Same-block trust still works: a Tailscale peer can inherit setup-host
    trust when setup_host is also in 100.64/10."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "100.97.103.112")

    response = app.test_client().get(
        "/health",
        base_url="http://100.97.103.112:5123",
        environ_base={"REMOTE_ADDR": "100.97.103.5"},
    )

    assert response.status_code == 200


def test_setup_host_rfc1918_peer_outside_interface_subnet_is_not_local(monkeypatch, tmp_path):
    """RFC1918 trust must not span the entire /8: a 10.50/16 peer cannot
    inherit setup-host trust from a 10.1.2.3 setup_host configured with a
    /24 mask. Pre-wildcard, the kernel only let in peers on the same
    interface subnet — _local_interface_network restores that scoping
    using the actual netmask."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "10.1.2.3")
    _mock_interface(monkeypatch, "10.1.2.3", 24)

    response = app.test_client().get(
        "/dashboard",
        base_url="http://10.1.2.3:5123",
        environ_base={"REMOTE_ADDR": "10.50.0.5"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_rfc1918_peer_in_same_interface_subnet_is_local(monkeypatch, tmp_path):
    """Same-subnet RFC1918 peer still inherits trust (typical home/office LAN)."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "10.1.2.3")
    _mock_interface(monkeypatch, "10.1.2.3", 24)

    response = app.test_client().get(
        "/health",
        base_url="http://10.1.2.3:5123",
        environ_base={"REMOTE_ADDR": "10.1.2.50"},
    )

    assert response.status_code == 200


def test_setup_host_192168_peer_outside_interface_subnet_is_not_local(monkeypatch, tmp_path):
    """A peer on 192.168.2/24 cannot spoof Host=192.168.1.5 when the
    interface mask is /24."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "192.168.1.5")
    _mock_interface(monkeypatch, "192.168.1.5", 24)

    response = app.test_client().get(
        "/dashboard",
        base_url="http://192.168.1.5:5123",
        environ_base={"REMOTE_ADDR": "192.168.2.5"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_with_16_prefix_includes_peer_in_same_16(monkeypatch, tmp_path):
    """When the interface mask is /16, a peer on a different /24 within
    the same /16 still inherits trust — fixed-/24 estimates were too
    narrow for /16 LANs."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "192.168.1.5")
    _mock_interface(monkeypatch, "192.168.1.5", 16)

    response = app.test_client().get(
        "/health",
        base_url="http://192.168.1.5:5123",
        environ_base={"REMOTE_ADDR": "192.168.7.20"},
    )

    assert response.status_code == 200


def test_setup_host_with_20_prefix_includes_peer_in_same_20(monkeypatch, tmp_path):
    """/20 corporate networks (4096 addresses) are honored without
    artificially narrowing to /24."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "10.1.16.5")
    _mock_interface(monkeypatch, "10.1.16.5", 20)

    response = app.test_client().get(
        "/health",
        base_url="http://10.1.16.5:5123",
        environ_base={"REMOTE_ADDR": "10.1.31.250"},
    )

    assert response.status_code == 200


def test_setup_host_with_20_prefix_excludes_peer_outside_20(monkeypatch, tmp_path):
    """/20 still excludes peers outside the /20 (peer in next /20 is not
    on the same routed subnet)."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "10.1.16.5")
    _mock_interface(monkeypatch, "10.1.16.5", 20)

    response = app.test_client().get(
        "/dashboard",
        base_url="http://10.1.16.5:5123",
        environ_base={"REMOTE_ADDR": "10.1.32.5"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_unknown_to_local_interfaces_is_not_local(monkeypatch, tmp_path):
    """If setup_host is not configured on any local interface, deny trust
    rather than guess a subnet — this preserves the kernel's pre-wildcard
    "no matching interface, no traffic" semantics."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "192.168.99.99")
    _mock_no_interfaces(monkeypatch)

    response = app.test_client().get(
        "/dashboard",
        base_url="http://192.168.99.99:5123",
        environ_base={"REMOTE_ADDR": "192.168.99.50"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_ipv6_with_56_prefix_includes_peer_in_same_56(monkeypatch, tmp_path):
    """A non-/64 IPv6 LAN (e.g. /56 prefix delegated to the home network)
    is honored without artificially narrowing to /64."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "fd00:0:0:1::5")
    _mock_interface(monkeypatch, "fd00:0:0:1::5", 56)

    response = app.test_client().get(
        "/health",
        base_url="http://[fd00:0:0:1::5]:5123",
        environ_base={"REMOTE_ADDR": "fd00:0:0:7::20"},
    )

    assert response.status_code == 200


def test_setup_host_ipv6_with_64_prefix_excludes_peer_outside_64(monkeypatch, tmp_path):
    """Default IPv6 LAN /64 still scopes peers correctly."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "fd00::5")
    _mock_interface(monkeypatch, "fd00::5", 64)

    response = app.test_client().get(
        "/dashboard",
        base_url="http://[fd00::5]:5123",
        environ_base={"REMOTE_ADDR": "fd00:0:0:1::20"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def _save_config_tunnel_off_with_setup_host(tmp_path, host: str) -> V2Config:
    config = _save_config(tmp_path)
    config.remote_access.vibe_cloud.enabled = False
    config.ui.setup_host = host
    config.save()
    return config


def test_setup_host_tunnel_off_allows_routed_peer_outside_interface_subnet(monkeypatch, tmp_path):
    """When the tunnel is off, the UI binds directly to setup_host and the
    kernel already enforces interface filtering — a routed peer reaching
    setup_host across a /16 corporate or campus net must have been routed
    legitimately, so the application layer should not add a second-pass
    subnet gate (regression noted in Codex review of #252)."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_tunnel_off_with_setup_host(tmp_path, "10.1.2.3")
    _mock_interface(monkeypatch, "10.1.2.3", 24)

    response = app.test_client().get(
        "/health",
        base_url="http://10.1.2.3:5123",
        environ_base={"REMOTE_ADDR": "10.50.0.5"},
    )

    assert response.status_code == 200


def test_setup_host_tunnel_off_still_rejects_public_peer(monkeypatch, tmp_path):
    """Tunnel-off relaxation of the subnet gate must not relax the
    private-peer requirement: a public peer is still untrusted."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_tunnel_off_with_setup_host(tmp_path, "10.1.2.3")

    response = app.test_client().get(
        "/dashboard",
        base_url="http://10.1.2.3:5123",
        environ_base={"REMOTE_ADDR": "8.8.8.8"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_tunnel_on_still_enforces_subnet_gate(monkeypatch, tmp_path):
    """Mirror of the tunnel-off test above: with the tunnel on, the
    wildcard bind requires the application-layer subnet gate, so the same
    cross-subnet peer that is allowed when the tunnel is off must be
    rejected when the tunnel is on."""
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "10.1.2.3")
    _mock_interface(monkeypatch, "10.1.2.3", 24)

    response = app.test_client().get(
        "/dashboard",
        base_url="http://10.1.2.3:5123",
        environ_base={"REMOTE_ADDR": "10.50.0.5"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_mismatched_host_header_is_not_local(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "192.168.2.3")

    response = app.test_client().get(
        "/dashboard",
        base_url="http://10.0.0.5:5123",
        environ_base={"REMOTE_ADDR": "192.168.2.5"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_wildcard_allows_actual_lan_interface_host(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "0.0.0.0")
    _mock_interface(monkeypatch, "192.168.2.3", 24)

    response = app.test_client().get(
        "/health",
        base_url="http://192.168.2.3:5123",
        environ_base={"REMOTE_ADDR": "192.168.2.5"},
    )

    assert response.status_code == 200


def test_setup_host_wildcard_does_not_trust_unconfigured_lan_host(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "0.0.0.0")
    _mock_no_interfaces(monkeypatch)

    response = app.test_client().get(
        "/dashboard",
        base_url="http://192.168.2.3:5123",
        environ_base={"REMOTE_ADDR": "192.168.2.5"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_wildcard_does_not_trust_docker_bridge_interface(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "0.0.0.0")
    _mock_interface(monkeypatch, "172.17.0.1", 16, name="docker0")

    response = app.test_client().get(
        "/dashboard",
        base_url="http://172.17.0.1:5123",
        environ_base={"REMOTE_ADDR": "172.17.0.2"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_wildcard_does_not_trust_cni_bridge_interface(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "0.0.0.0")
    _mock_interface(monkeypatch, "192.168.2.3", 24, name="cni0")

    response = app.test_client().get(
        "/dashboard",
        base_url="http://192.168.2.3:5123",
        environ_base={"REMOTE_ADDR": "192.168.2.5"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_wildcard_does_not_trust_flannel_interface(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "0.0.0.0")
    _mock_interface(monkeypatch, "10.244.0.1", 24, name="flannel.1")

    response = app.test_client().get(
        "/dashboard",
        base_url="http://10.244.0.1:5123",
        environ_base={"REMOTE_ADDR": "10.244.0.2"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_wildcard_does_not_trust_bridge_interface_in_cgnat_range(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "0.0.0.0")
    _mock_interface(monkeypatch, "100.97.103.112", 32, name="docker0")

    response = app.test_client().get(
        "/dashboard",
        base_url="http://100.97.103.112:5123",
        environ_base={"REMOTE_ADDR": "100.97.103.5"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_wildcard_rejects_peer_outside_interface_subnet(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "0.0.0.0")
    _mock_interface(monkeypatch, "192.168.1.5", 24)

    response = app.test_client().get(
        "/dashboard",
        base_url="http://192.168.1.5:5123",
        environ_base={"REMOTE_ADDR": "192.168.2.5"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_wildcard_rejects_public_peer(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "0.0.0.0")
    _mock_interface(monkeypatch, "192.168.2.3", 24)

    response = app.test_client().get(
        "/dashboard",
        base_url="http://192.168.2.3:5123",
        environ_base={"REMOTE_ADDR": "8.8.8.8"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_wildcard_with_reverse_proxy_header_is_not_local(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "0.0.0.0")
    _mock_interface(monkeypatch, "192.168.2.3", 24)

    response = app.test_client().get(
        "/dashboard",
        base_url="http://192.168.2.3:5123",
        environ_base={"REMOTE_ADDR": "192.168.2.5"},
        headers={"X-Forwarded-For": "203.0.113.10"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_wildcard_allows_actual_tailscale_interface_host(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "0.0.0.0")
    _mock_interface(monkeypatch, "100.97.103.112", 32, name="tailscale0")

    response = app.test_client().get(
        "/health",
        base_url="http://100.97.103.112:5123",
        environ_base={"REMOTE_ADDR": "100.97.103.5"},
    )

    assert response.status_code == 200


def test_setup_host_wildcard_does_not_trust_unconfigured_tailscale_host(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "0.0.0.0")
    _mock_no_interfaces(monkeypatch)

    response = app.test_client().get(
        "/dashboard",
        base_url="http://100.97.103.112:5123",
        environ_base={"REMOTE_ADDR": "100.97.103.5"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_ipv6_wildcard_allows_actual_private_interface_host(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "::")
    _mock_interface(monkeypatch, "fd00::5", 64)

    response = app.test_client().get(
        "/health",
        base_url="http://[fd00::5]:5123",
        environ_base={"REMOTE_ADDR": "fd00::20"},
    )

    assert response.status_code == 200


def test_setup_host_ipv6_wildcard_allows_tailscale_ula_interface_host(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "::")
    _mock_interface(monkeypatch, "fd7a:115c:a1e0::5", 128, name="tailscale0")

    response = app.test_client().get(
        "/health",
        base_url="http://[fd7a:115c:a1e0::5]:5123",
        environ_base={"REMOTE_ADDR": "fd7a:115c:a1e0::20"},
    )

    assert response.status_code == 200


def test_setup_host_ipv6_wildcard_does_not_trust_bridge_in_tailscale_ula_range(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "::")
    _mock_interface(monkeypatch, "fd7a:115c:a1e0::5", 64, name="docker0")

    response = app.test_client().get(
        "/dashboard",
        base_url="http://[fd7a:115c:a1e0::5]:5123",
        environ_base={"REMOTE_ADDR": "fd7a:115c:a1e0::20"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_with_cloudflare_metadata_is_not_local(monkeypatch, tmp_path):
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "192.168.2.3")

    response = app.test_client().get(
        "/dashboard",
        base_url="http://192.168.2.3:5123",
        environ_base={"REMOTE_ADDR": "192.168.2.5"},
        headers=_cloudflare_headers(),
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_setup_host_with_reverse_proxy_header_is_not_local(monkeypatch, tmp_path):
    """A non-Cloudflare reverse proxy on the same host (nginx, Caddy, ...)
    fronts vibe and an attacker spoofs Host=setup_host. Flask sees a private
    peer (the proxy) and the Host matches setup_host, so the host+peer pair
    looks "local" — but X-Forwarded-For (or any other forwarded header) tells
    us the actual client is unknown, so the request must not be trusted.
    """
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config_with_setup_host(tmp_path, "192.168.2.3")

    response = app.test_client().get(
        "/dashboard",
        base_url="http://192.168.2.3:5123",
        environ_base={"REMOTE_ADDR": "127.0.0.1"},
        headers={"X-Forwarded-For": "203.0.113.10"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert response.get_json()["error"] == "remote_access_host_mismatch"


def test_settings_get_redirects_browser_navigation_to_spa(monkeypatch, tmp_path):
    """A browser bookmark / hard refresh of /settings sends Accept: text/html
    and must be redirected to the SPA settings page rather than receiving the
    JSON API payload, so the user lands in the UI as expected.
    """
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get(
        "/settings",
        base_url="http://127.0.0.1:5123",
        headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        follow_redirects=False,
    )

    assert response.status_code in (301, 302, 303, 307, 308)
    assert response.headers["Location"].endswith("/settings/service")


def test_settings_get_returns_json_for_fetch_callers(monkeypatch, tmp_path):
    """fetch() from the SPA hits /settings without an explicit text/html in
    Accept; the handler must keep returning JSON so getSettings() works.
    """
    monkeypatch.setenv("VIBE_REMOTE_HOME", str(tmp_path))
    _save_config(tmp_path)

    response = app.test_client().get(
        "/settings",
        base_url="http://127.0.0.1:5123",
        headers={"Accept": "*/*"},
    )

    assert response.status_code == 200
    assert response.is_json
