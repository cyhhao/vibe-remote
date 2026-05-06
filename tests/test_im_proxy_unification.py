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
from vibe.proxy import redact_proxy_url, resolve_proxy


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
