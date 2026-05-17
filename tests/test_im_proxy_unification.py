"""Unified tests for the IM proxy_url field, resolve_proxy() precedence,
redact_proxy_url() credential stripping, and the Discord auth_test SOCKS path.

Covers:
  - proxy_url is inherited by every IM config dataclass.
  - resolve_proxy() honors explicit config first, then falls back to the
    system SOCKS proxy, otherwise returns None.
  - redact_proxy_url() strips userinfo so logs cannot leak credentials.
  - _discord_api_get_via_aiohttp surfaces non-2xx responses as exceptions
    so discord_auth_test correctly rejects invalid tokens via SOCKS.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.v2_config import (
    DiscordConfig,
    LarkConfig,
    SlackConfig,
    TelegramConfig,
    WeChatConfig,
)
from vibe import proxy as proxy_module
from vibe.proxy import is_socks_proxy, redact_proxy_url, resolve_proxy


CONFIG_FACTORIES = [
    pytest.param(lambda **kw: SlackConfig(bot_token="xoxb-test", **kw), id="slack"),
    pytest.param(lambda **kw: DiscordConfig(bot_token="0123456789abc", **kw), id="discord"),
    pytest.param(lambda **kw: TelegramConfig(bot_token="123456:abc", **kw), id="telegram"),
    pytest.param(lambda **kw: LarkConfig(app_id="cli_x", app_secret="s", **kw), id="lark"),
    pytest.param(lambda **kw: WeChatConfig(bot_token="wx-test", **kw), id="wechat"),
]


# ---------------------------------------------------------------------------
# proxy_url is inherited from BaseIMConfig by every IM config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("factory", CONFIG_FACTORIES)
def test_proxy_url_default_none(factory) -> None:
    """Every IM config defaults proxy_url to None."""
    config = factory()
    assert hasattr(config, "proxy_url")
    assert config.proxy_url is None


@pytest.mark.parametrize(
    "factory",
    [
        pytest.param(lambda **kw: SlackConfig(bot_token="xoxb-test", **kw), id="slack"),
        pytest.param(lambda **kw: DiscordConfig(bot_token="0123456789abc", **kw), id="discord"),
        pytest.param(lambda **kw: TelegramConfig(bot_token="123456:abc", **kw), id="telegram"),
        pytest.param(lambda **kw: WeChatConfig(bot_token="wx-test", **kw), id="wechat"),
    ],
)
def test_proxy_url_accepts_socks5(factory) -> None:
    config = factory(proxy_url="socks5://user:pass@127.0.0.1:1080")
    assert config.proxy_url == "socks5://user:pass@127.0.0.1:1080"


def test_lark_config_accepts_proxy_url() -> None:
    """Lark/Feishu config still accepts proxy_url even though the SDK ignores it.

    The runtime warns at adapter init; the field itself must round-trip so the
    UI and persisted config keep working.
    """
    config = LarkConfig(app_id="cli_x", app_secret="s", proxy_url="socks5://127.0.0.1:1080")
    assert config.proxy_url == "socks5://127.0.0.1:1080"


@pytest.mark.parametrize("factory", CONFIG_FACTORIES)
def test_proxy_url_accepts_http(factory) -> None:
    """HTTP proxies are valid for adapters that honor the field."""
    config = factory(proxy_url="http://127.0.0.1:8080")
    assert config.proxy_url == "http://127.0.0.1:8080"


# ---------------------------------------------------------------------------
# resolve_proxy() precedence
# ---------------------------------------------------------------------------


def test_resolve_proxy_returns_explicit_config() -> None:
    """Explicit config_proxy wins over any system fallback."""
    with patch.object(proxy_module, "get_system_socks_proxy", return_value="socks5://system:1080"):
        assert resolve_proxy("socks5://explicit:1080") == "socks5://explicit:1080"


def test_resolve_proxy_strips_whitespace() -> None:
    """Surrounding whitespace is stripped from explicit config."""
    with patch.object(proxy_module, "get_system_socks_proxy", return_value=None):
        assert resolve_proxy("  socks5://explicit:1080  ") == "socks5://explicit:1080"


def test_resolve_proxy_falls_back_to_system_when_none() -> None:
    """No config_proxy → fall back to the system SOCKS proxy."""
    with patch.object(proxy_module, "get_system_socks_proxy", return_value="socks5://system:1080"):
        assert resolve_proxy(None) == "socks5://system:1080"


def test_resolve_proxy_falls_back_to_system_when_empty_string() -> None:
    """Empty config_proxy is treated as unset and falls back to the system."""
    with patch.object(proxy_module, "get_system_socks_proxy", return_value="socks5://system:1080"):
        assert resolve_proxy("") == "socks5://system:1080"


def test_resolve_proxy_falls_back_to_system_when_whitespace_only() -> None:
    """Whitespace-only config_proxy is treated as unset."""
    with patch.object(proxy_module, "get_system_socks_proxy", return_value="socks5://system:1080"):
        assert resolve_proxy("   ") == "socks5://system:1080"


def test_resolve_proxy_returns_none_when_neither_configured() -> None:
    """No explicit config and no system proxy → None."""
    with patch.object(proxy_module, "get_system_socks_proxy", return_value=None):
        assert resolve_proxy(None) is None
        assert resolve_proxy("") is None
        assert resolve_proxy("   ") is None


# ---------------------------------------------------------------------------
# redact_proxy_url() credential stripping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("socks5://user:pass@127.0.0.1:1080", "socks5://127.0.0.1:1080"),
        ("socks5://user@host:1080", "socks5://host:1080"),
        ("socks5://127.0.0.1:1080", "socks5://127.0.0.1:1080"),
        ("http://proxy.local:8080", "http://proxy.local:8080"),
        ("https://u:p@proxy.local", "https://proxy.local"),
        ("socks5://u:p@[2001:db8::1]:1080", "socks5://[2001:db8::1]:1080"),
    ],
)
def test_redact_proxy_url_strips_userinfo(url, expected) -> None:
    assert redact_proxy_url(url) == expected


@pytest.mark.parametrize("value", [None, ""])
def test_redact_proxy_url_handles_empty(value) -> None:
    assert redact_proxy_url(value) == "<unset>"


def test_redact_proxy_url_handles_garbage() -> None:
    """Unparseable input returns a generic placeholder, never the raw value."""
    assert redact_proxy_url("not a url") == "<configured>"


# ---------------------------------------------------------------------------
# is_socks_proxy() detects by scheme, not substring (regression for round-4 P2)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "socks5://127.0.0.1:1080",
        "socks5h://127.0.0.1:1080",
        "socks4://127.0.0.1:1080",
        "socks4a://127.0.0.1:1080",
        "SOCKS5://127.0.0.1:1080",  # case-insensitive
        "  socks5://127.0.0.1:1080  ",  # leading/trailing whitespace tolerated
        "socks5://user:pass@host:1080",
    ],
)
def test_is_socks_proxy_true_for_socks_schemes(url) -> None:
    assert is_socks_proxy(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # Regression: hostname containing "socks" must not be classified as SOCKS.
        "http://socks-gateway.corp:8080",
        "https://socks-gateway.corp:8080",
        "http://user:socks@proxy.local:8080",  # credentials containing "socks"
        "http://127.0.0.1:8080",
        "https://proxy.local:443",
        "",
        None,
        "not a url",
    ],
)
def test_is_socks_proxy_false_for_non_socks(url) -> None:
    assert is_socks_proxy(url) is False


# ---------------------------------------------------------------------------
# Discord auth_test SOCKS path: non-2xx must raise (regression for P1 review)
# ---------------------------------------------------------------------------


def test_discord_auth_test_socks_rejects_401() -> None:
    """A 401 from Discord over SOCKS must surface as ok=False, not ok=True.

    Before the fix, _discord_api_get_via_aiohttp returned the JSON body for
    any status, so discord_auth_test wrapped Discord's error payload as a
    successful response and let invalid tokens through the wizard.
    """
    import aiohttp

    from vibe import api as vibe_api

    async def raise_401(url, headers, proxy):
        raise aiohttp.ClientResponseError(
            request_info=aiohttp.RequestInfo(url=url, method="GET", headers={}, real_url=url),
            history=(),
            status=401,
            message="Unauthorized",
        )

    with patch.object(vibe_api, "_discord_api_get_via_aiohttp", new=raise_401):
        result = vibe_api.discord_auth_test("invalid", proxy_url="socks5://127.0.0.1:1080")

    assert result["ok"] is False
    assert "401" in result["error"] or "Unauthorized" in result["error"]


def test_discord_api_get_via_aiohttp_raises_for_status() -> None:
    """Direct unit test: the helper must call resp.raise_for_status().

    Pinning this prevents future regressions: even if the auth_test wrapper
    were rewritten, this contract keeps the SOCKS path consistent with the
    urllib path (which raises HTTPError on non-2xx).
    """
    import asyncio

    from unittest.mock import AsyncMock, MagicMock

    from vibe.api import _discord_api_get_via_aiohttp

    raise_for_status = MagicMock(side_effect=RuntimeError("would raise"))
    fake_resp = MagicMock()
    fake_resp.raise_for_status = raise_for_status
    fake_resp.json = AsyncMock(return_value={"id": "should-not-see"})

    fake_resp_cm = MagicMock()
    fake_resp_cm.__aenter__ = AsyncMock(return_value=fake_resp)
    fake_resp_cm.__aexit__ = AsyncMock(return_value=None)

    fake_session = MagicMock()
    fake_session.get = MagicMock(return_value=fake_resp_cm)

    fake_session_cm = MagicMock()
    fake_session_cm.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession", return_value=fake_session_cm), patch(
        "aiohttp_socks.ProxyConnector.from_url", return_value=MagicMock()
    ):
        with pytest.raises(RuntimeError, match="would raise"):
            asyncio.run(
                _discord_api_get_via_aiohttp(
                    "https://discord.com/api/v10/users/@me",
                    {"Authorization": "Bot x"},
                    "socks5://127.0.0.1:1080",
                )
            )

    raise_for_status.assert_called_once()


def test_discord_api_get_sync_path_works_inside_active_event_loop() -> None:
    """Live discovery is sync code that may run on FastAPI's ASGI loop.

    The sync helper must not bridge through run_coroutine_blocking(), which
    intentionally raises inside an active loop.
    """
    import asyncio

    from unittest.mock import MagicMock

    from vibe import api as vibe_api

    fake_resp_cm = MagicMock()
    fake_resp_cm.__enter__ = MagicMock(
        return_value=MagicMock(read=MagicMock(return_value=b'[{"id":"c1","name":"general"}]'))
    )
    fake_resp_cm.__exit__ = MagicMock(return_value=None)
    fake_opener = MagicMock()
    fake_opener.open = MagicMock(return_value=fake_resp_cm)

    async def exercise():
        with patch("urllib.request.build_opener", return_value=fake_opener):
            return vibe_api._discord_api_get("bot-token", "guilds/g1/channels")

    result = asyncio.run(exercise())

    assert result == [{"id": "c1", "name": "general"}]


# ---------------------------------------------------------------------------
# telegram_auth_test must call resolve_proxy() (regression for round-2 review)
# ---------------------------------------------------------------------------


def test_telegram_auth_test_uses_resolve_proxy_for_system_fallback() -> None:
    """When proxy_url is empty, telegram_auth_test must still pick up the
    system SOCKS proxy via resolve_proxy() — same contract as Slack/Discord
    auth_test and the runtime TelegramBot adapter.

    Before the fix, an empty proxy_url skipped the system fallback, causing
    the wizard to report ok=False even though the runtime would have
    succeeded through the detected system proxy.
    """
    from vibe import api as vibe_api

    captured: dict = {}

    async def fake_get_me(bot_token, proxy_url=None):
        captured["bot_token"] = bot_token
        captured["proxy_url"] = proxy_url
        return {"id": 1, "username": "bot"}

    with patch.object(vibe_api, "_telegram_get_me", new=fake_get_me), patch.object(
        proxy_module, "get_system_socks_proxy", return_value="socks5://system:1080"
    ):
        result = vibe_api.telegram_auth_test("123456:abc", proxy_url=None)

    assert result["ok"] is True
    # Must be the resolved system proxy, not the raw None we passed in.
    assert captured["proxy_url"] == "socks5://system:1080"


def test_telegram_auth_test_prefers_explicit_over_system() -> None:
    """Explicit proxy_url still wins over the detected system proxy."""
    from vibe import api as vibe_api

    captured: dict = {}

    async def fake_get_me(bot_token, proxy_url=None):
        captured["proxy_url"] = proxy_url
        return {"id": 1}

    with patch.object(vibe_api, "_telegram_get_me", new=fake_get_me), patch.object(
        proxy_module, "get_system_socks_proxy", return_value="socks5://system:1080"
    ):
        vibe_api.telegram_auth_test("123456:abc", proxy_url="socks5://explicit:1080")

    assert captured["proxy_url"] == "socks5://explicit:1080"


# ---------------------------------------------------------------------------
# lark_auth_test must route through the configured proxy (round-3 P1 review)
# ---------------------------------------------------------------------------


def test_lark_auth_test_threads_proxy_into_token_helper() -> None:
    """lark_auth_test must resolve proxy and forward it to _lark_tenant_token.

    Before the fix, lark_auth_test called the internal helper with no proxy
    info, so users behind a corporate/SOCKS proxy got a false auth failure
    even after providing proxy_url in the wizard.
    """
    from vibe import api as vibe_api

    captured: dict = {}

    def fake_token(app_id, app_secret, domain="feishu", proxy_url=None):
        captured["app_id"] = app_id
        captured["proxy_url"] = proxy_url
        return "tok-123"

    with patch.object(vibe_api, "_lark_tenant_token", new=fake_token), patch.object(
        proxy_module, "get_system_socks_proxy", return_value=None
    ):
        result = vibe_api.lark_auth_test("cli_x", "secret", proxy_url="socks5://explicit:1080")

    assert result == {"ok": True}
    assert captured["proxy_url"] == "socks5://explicit:1080"


def test_lark_auth_test_falls_back_to_system_proxy() -> None:
    """Empty proxy_url should pick up the system SOCKS proxy."""
    from vibe import api as vibe_api

    captured: dict = {}

    def fake_token(app_id, app_secret, domain="feishu", proxy_url=None):
        captured["proxy_url"] = proxy_url
        return "tok-123"

    with patch.object(vibe_api, "_lark_tenant_token", new=fake_token), patch.object(
        proxy_module, "get_system_socks_proxy", return_value="socks5://system:1080"
    ):
        vibe_api.lark_auth_test("cli_x", "secret")

    assert captured["proxy_url"] == "socks5://system:1080"


def test_lark_tenant_token_uses_sync_socks_helper_for_socks() -> None:
    """SOCKS proxy_url branches into the sync SOCKS helper, not urllib."""

    from vibe import api as vibe_api

    socks_called = {"hit": False}

    def fake_socks(proxy, url, *, method="GET", body=None, headers=None, timeout=10):
        socks_called["hit"] = True
        socks_called["proxy"] = proxy
        return {"code": 0, "tenant_access_token": "tok-from-socks"}

    def fail_urlopen(*args, **kwargs):
        raise AssertionError("urllib should not be used for SOCKS proxies")

    with patch.object(vibe_api, "_https_json_request_via_socks", new=fake_socks), patch(
        "urllib.request.build_opener", side_effect=fail_urlopen
    ):
        token = vibe_api._lark_tenant_token(
            "cli_x", "secret", proxy_url="socks5://127.0.0.1:1080"
        )

    assert token == "tok-from-socks"
    assert socks_called["hit"] is True
    assert socks_called["proxy"] == "socks5://127.0.0.1:1080"


def test_https_json_request_via_socks_closes_socket_when_tls_wrap_fails() -> None:
    from vibe import api as vibe_api

    class FakeSocket:
        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class FakeProxy:
        @staticmethod
        def from_url(proxy_url):
            assert proxy_url == "socks5://127.0.0.1:1080"
            return FakeProxy()

        def connect(self, hostname, port, timeout=10):
            assert hostname == "discord.com"
            assert port == 443
            return fake_socket

    class FakeContext:
        def wrap_socket(self, sock, *, server_hostname):
            assert sock is fake_socket
            assert server_hostname == "discord.com"
            raise OSError("tls failed")

    fake_socket = FakeSocket()

    with patch("python_socks.sync.Proxy", FakeProxy), patch(
        "ssl.create_default_context", return_value=FakeContext()
    ), pytest.raises(OSError, match="tls failed"):
        vibe_api._https_json_request_via_socks(
            "socks5://127.0.0.1:1080",
            "https://discord.com/api/v10/users/@me",
        )

    assert fake_socket.closed is True


def test_lark_tenant_token_uses_urllib_proxy_for_http() -> None:
    """HTTP proxy_url uses urllib.ProxyHandler with the proxy applied."""
    from unittest.mock import MagicMock

    from vibe import api as vibe_api

    fake_resp_cm = MagicMock()
    fake_resp_cm.__enter__ = MagicMock(
        return_value=MagicMock(read=MagicMock(return_value=b'{"code":0,"tenant_access_token":"tok-http"}'))
    )
    fake_resp_cm.__exit__ = MagicMock(return_value=None)

    fake_opener = MagicMock()
    fake_opener.open = MagicMock(return_value=fake_resp_cm)

    captured: dict = {}

    def capture_build_opener(*handlers):
        captured["handlers"] = handlers
        return fake_opener

    with patch("urllib.request.build_opener", side_effect=capture_build_opener):
        token = vibe_api._lark_tenant_token(
            "cli_x", "secret", proxy_url="http://proxy.local:8080"
        )

    assert token == "tok-http"
    # ProxyHandler should be in the chain when proxy_url is set
    assert any("ProxyHandler" in type(h).__name__ for h in captured["handlers"])


def test_lark_tenant_token_does_not_misroute_http_with_socks_hostname() -> None:
    """Regression for round-4 P2: an HTTP proxy whose hostname contains
    'socks' (e.g. http://socks-gateway.corp:8080) must take the urllib path,
    not the aiohttp_socks path. The previous substring-based check would
    misclassify this as a SOCKS proxy.
    """
    from unittest.mock import MagicMock

    from vibe import api as vibe_api

    fake_resp_cm = MagicMock()
    fake_resp_cm.__enter__ = MagicMock(
        return_value=MagicMock(
            read=MagicMock(return_value=b'{"code":0,"tenant_access_token":"tok-via-http"}')
        )
    )
    fake_resp_cm.__exit__ = MagicMock(return_value=None)

    fake_opener = MagicMock()
    fake_opener.open = MagicMock(return_value=fake_resp_cm)

    def fail_socks_helper(*args, **kwargs):
        raise AssertionError(
            "SOCKS helper must not be used for HTTP proxies, even when hostname contains 'socks'"
        )

    with patch("urllib.request.build_opener", return_value=fake_opener), patch.object(
        vibe_api, "_https_json_request_via_socks", side_effect=fail_socks_helper
    ):
        token = vibe_api._lark_tenant_token(
            "cli_x", "secret", proxy_url="http://socks-gateway.corp:8080"
        )

    assert token == "tok-via-http"


def test_lark_tenant_token_sync_path_works_inside_active_event_loop() -> None:
    """Live Lark discovery is sync code and may run on FastAPI's ASGI loop."""
    import asyncio

    from unittest.mock import MagicMock

    from vibe import api as vibe_api

    fake_resp_cm = MagicMock()
    fake_resp_cm.__enter__ = MagicMock(
        return_value=MagicMock(read=MagicMock(return_value=b'{"code":0,"tenant_access_token":"tok-loop"}'))
    )
    fake_resp_cm.__exit__ = MagicMock(return_value=None)
    fake_opener = MagicMock()
    fake_opener.open = MagicMock(return_value=fake_resp_cm)

    async def exercise():
        with patch("urllib.request.build_opener", return_value=fake_opener):
            return vibe_api._lark_tenant_token("cli_x", "secret")

    assert asyncio.run(exercise()) == "tok-loop"
